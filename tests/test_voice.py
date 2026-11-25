"""음성 의뢰 — 형식 가리기 · 받아쓰기 · 못 알아들은 경우 · 호출 기록(가짜 응답으로 본다. Liner 를 부르지 않는다)."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from mapcg import intake, voice

liner = intake._liner()
WAV = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x01" * 4000
MP3 = b"ID3" + b"\x02" * 4000
SAID = "안녕하세요, 조연출 김가상입니다. 노들섬에서 여의도까지 이동 지도 8초로 부탁드려요."


class Reply:
    def __init__(self, body):
        self.buf = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def read(self, n=-1):
        return self.buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body="{}", headers=None):
    return urllib.error.HTTPError("https://x", code, "오류", headers or {}, io.BytesIO(body.encode("utf-8")))


def answers(*steps):
    """liner._open 대신 끼우는 가짜. steps 는 차례대로 내놓을 답(딕셔너리 · 바이트)이나 던질 예외."""
    seen, queue = [], list(steps)

    def opener(req, timeout):
        seen.append((req, timeout))
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return Reply(step)
    opener.seen = seen
    return opener


class Sniff(unittest.TestCase):
    def test_머리글자로_형식을_가린다(self):
        cases = {"wav": WAV, "mp3": MP3, "flac": b"fLaC" + b"0" * 20, "ogg": b"OggS" + b"0" * 20, "webm": b"\x1a\x45\xdf\xa3" + b"0" * 20,
                 "m4a": b"\x00\x00\x00\x20ftypM4A " + b"0" * 20}
        for kind, data in cases.items():
            self.assertEqual(voice.sniff(data), kind)
        self.assertEqual(voice.sniff(b"\xff\xfb\x90\x00" + b"0" * 20), "mp3")       # ID3 꼬리표 없는 MP3
        self.assertEqual(voice.sniff(b"\x00\x00\x00\x20ftypisom" + b"0" * 20), "m4a")       # 안드로이드 녹음기의 M4A(영상 MP4 와 머리가 같다 — 부르는 쪽이 가른다)

    def test_사진과_글_파일은_음성으로_보지_않는다(self):
        for brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif", b"avis"):
            self.assertIsNone(voice.sniff(b"\x00\x00\x00\x18ftyp" + brand + b"0" * 20), brand)
        self.assertIsNone(voice.sniff(b"\xff\xfeh\x00i\x00" + b"0" * 20))         # UTF-16 글 파일의 머리(FF FE)
        self.assertIsNone(voice.sniff(b"\xff\xf1\x50\x80" + b"0" * 20))           # 그릇 없는 AAC — Liner 가 받는 형식이 아니다
        self.assertIsNone(voice.sniff(b"\xff\xfb\xf0\x00" + b"0" * 20))           # 비트율 칸이 없는 값
        for other in (b"\x89PNG\r\n\x1a\n" + b"0" * 20, b"%PDF-1.7" + b"0" * 20, b"hello world, this is text", b""):
            self.assertIsNone(voice.sniff(other))


class Talk(unittest.TestCase):
    def test_지어낸_굳은_문장은_빈_말로_본다(self):
        for empty in ("시청해 주셔서 감사합니다.", "감사합니다", " Thank you for watching! ", "구독과 좋아요 부탁드립니다. 감사합니다.", "…", ""):
            self.assertTrue(voice.is_empty_talk(empty), empty)
        self.assertFalse(voice.is_empty_talk(SAID))
        self.assertFalse(voice.is_empty_talk("감사합니다. 순위판 하나만 더 부탁드려요."))

    def test_문장_끝과_순위_앞에서_줄을_나눈다(self):
        said = "순위판 하나 부탁드립니다. 제목은 오늘의 점수. 1위 김가상 1 250점, 2위 이가상 1 180점, 10 위 박가상 965점, 길이는 8초로 해주세요."
        lines = voice.reflow(said).split("\n")
        self.assertEqual(lines, ["순위판 하나 부탁드립니다.", "제목은 오늘의 점수.", "1위 김가상 1 250점", "2위 이가상 1 180점", "10 위 박가상 965점", "길이는 8초로 해주세요."])
        self.assertEqual(voice.reflow("1위 김가상 1, 250점, 2위 이가상 980점"), "1위 김가상 1, 250점\n2위 이가상 980점")     # 숫자 사이의 쉼표에서는 나누지 않는다
        strip = lambda t: "".join(c for c in t if c not in " ,\n")
        self.assertEqual(strip(voice.reflow(said)), strip(said))                      # 글자는 그대로
        self.assertEqual(voice.reflow("노들섬에서 여의도까지 8초, 버스로 1위 팀이 탑니다"), "노들섬에서 여의도까지 8초, 버스로\n1위 팀이 탑니다")

    def test_줄바꿈과_겹친_빈칸만_다듬는다(self):
        self.assertEqual(voice.tidy("  노들섬에서\n여의도까지   8초 \n"), "노들섬에서 여의도까지 8초")


class Transcribe(unittest.TestCase):
    def test_받아쓴_글과_비용을_돌려준다(self):
        sent = []

        def send(body, ctype, name):
            sent.append((body, ctype, name))
            return {"text": "  " + SAID + "\n", "usage": {"cost": 0.0013}}
        got = voice.transcribe(MP3, send)
        self.assertEqual(got, {"text": SAID, "format": "mp3", "bytes": len(MP3), "model": voice.MODEL, "cost_usd": 0.0013})
        body, ctype, name = sent[0]
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        mark = ctype.split("boundary=")[1].encode()
        self.assertEqual(body.count(b"--" + mark), 5)                                # 칸 넷 + 맺음
        self.assertIn(b'name="language"\r\n\r\nko\r\n', body)
        self.assertIn(f'name="model"\r\n\r\n{voice.MODEL}\r\n'.encode(), body)
        self.assertIn(b'filename="request.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n' + MP3, body)

    def test_파일_이름은_보내지_않는다(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "김가상PD_오늘도출발_의뢰.wav"
            p.write_bytes(WAV)
            sent = []
            got = voice.hear(p, send=lambda body, ctype, name: sent.append(body) or {"text": SAID})
        self.assertEqual(got["state"], "받아씀")
        self.assertNotIn("김가상".encode("utf-8"), sent[0])
        self.assertIn(b'filename="request.wav"', sent[0])

    def test_보내기_전에_거르는_것(self):
        send = mock.Mock()
        cases = {"비어 있거나 너무 짧": b"RIFF", "음성 파일로 보이지 않습니다": b"\x89PNG\r\n\x1a\n" + b"0" * 5000}
        for words, data in cases.items():
            with self.assertRaises(voice.VoiceError) as e:
                voice.transcribe(data, send)
            self.assertIn(words, str(e.exception))
        with mock.patch.object(voice, "MAX_BYTES", 3000), self.assertRaises(voice.VoiceError) as e:
            voice.transcribe(WAV, send)
        self.assertIn("너무 큽니다", str(e.exception))
        send.assert_not_called()

    def test_못_알아들었으면_의뢰로_보지_않는다(self):
        for text in ("", "네", "시청해 주셔서 감사합니다.", None):
            with self.assertRaises(voice.VoiceError) as e:
                voice.transcribe(WAV, lambda *a: {"text": text})
            self.assertIn("알아듣지 못했습니다", str(e.exception))

    def test_상태_200_에_담겨_온_오류도_실패다(self):
        with self.assertRaises(voice.VoiceError) as e:
            voice.transcribe(WAV, lambda *a: {"error": {"message": "model_not_found"}})
        self.assertIn("model_not_found", str(e.exception))

    def test_모델은_환경_변수로_바꾼다(self):
        names = []
        with mock.patch.dict("os.environ", {"MAPCG_VOICE_MODEL": "openai/whisper-large-v3-turbo"}):
            got = voice.transcribe(WAV, lambda body, ctype, name: names.append(name) or {"text": SAID})
        self.assertEqual((names, got["model"]), (["openai/whisper-large-v3-turbo"], "openai/whisper-large-v3-turbo"))


class Doubts(unittest.TestCase):
    def test_두_모델이_다르게_들은_낱말만_낸다(self):
        a = "안녕하세요 가상애는 오늘도 출발 조현출 김가상입니다. 지도 하나 부탁드려요. 이동 경로고요."
        b = "안녕하세요. 가상예능 오늘도 출발 도연출 김가상입니다 기도 하나 부탁드려요 이동경로고요"
        self.assertEqual(voice.doubts(a, b), [{"heard": "가상애는", "other": "가상예능"}, {"heard": "조현출", "other": "도연출"}, {"heard": "지도", "other": "기도"}])

    def test_띄어쓰기와_문장부호만_다르면_내지_않는다(self):
        self.assertEqual(voice.doubts("가상 예능, 1위 김가상 1 250점.", "가상예능 1위 김가상 1, 250점"), [])
        self.assertEqual(voice.doubts(SAID, SAID), [])

    def test_한쪽만_들은_낱말도_낸다(self):
        self.assertEqual(voice.doubts("버스로 해 주세요", "빨간 버스로 해 주세요"), [{"heard": "", "other": "빨간"}])

    def test_쉼표가_빠진_듯한_숫자를_알린다(self):
        got = voice.number_doubts("1위 김가상 1 250점, 2위 이가상 1 180점, 3위 박가상 965점, 길이는 8초")
        self.assertEqual(got, [{"heard": "1 250점", "other": "1,250점"}, {"heard": "1 180점", "other": "1,180점"}])
        self.assertEqual(voice.number_doubts("8초 720점, 1,250점, 컷 4개 2026년"), [])


class Hear(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict("os.environ", {"MAPCG_VOICE_SECOND": "off"})
        p.start()
        self.addCleanup(p.stop)

    def test_받아쓰면_확인_안내를_붙인다(self):
        got = voice.hear(WAV, send=lambda *a: {"text": SAID, "usage": {"cost": 0.001}})
        self.assertEqual((got["state"], got["text"], got["note"], got["format"], got["doubts"]), ("받아씀", SAID, voice.NOTE, "wav", []))
        self.assertEqual(got["request_text"].split("\n"), ["안녕하세요, 조연출 김가상입니다.", "노들섬에서 여의도까지 이동 지도 8초로 부탁드려요."])

    def test_두_번째_모델과_견줘_확인할_낱말을_낸다(self):
        said = {voice.MODEL: "가상애는 오늘도 출발 조현출 김가상입니다. 1위 김가상 1 250점", voice.SECOND: "가상예능 오늘도 출발 도연출 김가상입니다 1위 김가상 1, 250점"}
        names = []

        def send(body, ctype, name):
            names.append(name)
            return {"text": said[name], "usage": {"cost": 0.001}}
        with mock.patch.dict("os.environ", {"MAPCG_VOICE_SECOND": ""}):
            got = voice.hear(WAV, send=send)
        self.assertEqual(names, [voice.MODEL, voice.SECOND])
        self.assertEqual(got["text"], said[voice.MODEL])               # 받아쓴 글은 첫 모델 것 그대로
        self.assertEqual(got["doubts"], [{"heard": "가상애는", "other": "가상예능"}, {"heard": "조현출", "other": "도연출"}, {"heard": "1 250점", "other": "1,250점"}])
        self.assertEqual((got["second_model"], got["cost_usd"]), (voice.SECOND, 0.002))
        self.assertIn("특히: '가상애는'↔'가상예능'", got["note"])

    def test_견주는_쪽이_안_돼도_받아쓴_글은_쓴다(self):
        def send(body, ctype, name):
            if name == voice.SECOND:
                raise liner.LinerError(502, "잠깐 안 됨")
            return {"text": SAID}
        with mock.patch.dict("os.environ", {"MAPCG_VOICE_SECOND": ""}):
            got = voice.hear(WAV, send=send)
        self.assertEqual((got["state"], got["text"], got["doubts"], got["second_model"]), ("받아씀", SAID, [], ""))

    def test_첫_모델이_못_알아들으면_견주지_않는다(self):
        names = []
        with mock.patch.dict("os.environ", {"MAPCG_VOICE_SECOND": ""}):
            got = voice.hear(WAV, send=lambda body, ctype, name: names.append(name) or {"text": "감사합니다"})
        self.assertEqual((got["state"], names), ("못 받아씀", [voice.MODEL]))

    def test_오프라인이면_부르지_않는다(self):
        send = mock.Mock()
        got = voice.hear(WAV, offline=True, send=send)
        send.assert_not_called()
        self.assertEqual((got["state"], got["text"]), ("건너뜀", ""))
        self.assertIn("글로 적어", got["note"])

    def test_안_되면_예외_없이_사유를_돌려준다(self):
        def broke(*a):
            raise liner.LinerError(402, "잔액 부족")
        self.assertEqual(voice.hear(WAV, send=broke)["state"], "못 받아씀")
        self.assertIn("알아듣지 못했습니다", voice.hear(WAV, send=lambda *a: {"text": "감사합니다"})["note"])
        self.assertIn("열지 못했습니다", voice.hear("없는_파일.m4a", send=mock.Mock())["note"])
        self.assertIn("보이지 않습니다", voice.hear(b"x" * 5000, send=mock.Mock())["note"])


class Send(unittest.TestCase):
    """실제 전송 부분: 의뢰 번호 · 거절 차단 · 다시 보내기 규칙 · 호출 기록."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "calls.jsonl"
        for p in (mock.patch.object(liner, "LOG_PATH", self.log), mock.patch.dict("os.environ", {"LINER_API_KEY": "test-key-not-real", "LINER_LOG": "on", "MAPCG_VOICE_SECOND": "off"}),
                  mock.patch.object(voice.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        liner._blocked.clear()
        self.addCleanup(liner._blocked.clear)

    def lines(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]

    def hear(self, opener, rid="의뢰-음성-1"):
        with mock.patch.object(liner, "_open", opener), liner.request(rid):
            return voice.hear(WAV)

    def test_한_번에_되면_기록_한_줄(self):
        opener = answers({"text": SAID, "usage": {"cost": 0.0013}})
        got = self.hear(opener)
        self.assertEqual((got["state"], got["cost_usd"]), ("받아씀", 0.0013))
        req, timeout = opener.seen[0]
        self.assertTrue(req.full_url.endswith("/audio/transcriptions"))
        self.assertEqual(req.get_header("Authorization"), "Bearer test-key-not-real")
        self.assertEqual(timeout, voice.WAIT)
        line, = self.lines()
        self.assertEqual((line["request"], line["task"], line["status"], line["asked_model"]), ("의뢰-음성-1", "voice_transcription", 200, voice.MODEL))
        logged = self.log.read_text(encoding="utf-8")
        self.assertNotIn("노들섬", logged)             # 받아쓴 글은 기록에 남기지 않는다
        self.assertNotIn("test-key", logged)
        self.assertEqual(liner.tally("의뢰-음성-1", self.log)["voice_transcription"]["calls"], 1)

    def test_잠깐_막히면_한_번만_다시_보낸다(self):
        opener = answers(http_error(429, headers={"Retry-After": "5"}), {"text": SAID})
        self.assertEqual(self.hear(opener)["state"], "받아씀")
        self.assertEqual([(x["status"], x["final"]) for x in self.lines()], [(429, False), (200, True)])
        opener = answers(http_error(502), http_error(502))
        self.assertEqual(self.hear(opener, "의뢰-음성-2")["state"], "못 받아씀")
        self.assertEqual(len(opener.seen), 2)

    def test_답을_기다리다_끊기면_다시_보내지_않는다(self):
        opener = answers(TimeoutError("timed out"), {"text": SAID})
        got = self.hear(opener)
        self.assertEqual((got["state"], len(opener.seen)), ("못 받아씀", 1))
        self.assertIn("접수 여부 불명", self.lines()[0]["note"])

    def test_보내지_못했으면_한_번_더_보낸다(self):
        opener = answers(urllib.error.URLError("이름 풀이 실패"), {"text": SAID})
        self.assertEqual((self.hear(opener)["state"], len(opener.seen)), ("받아씀", 2))

    def test_키나_잔액으로_거절되면_같은_의뢰는_다시_보내지_않는다(self):
        opener = answers(http_error(402, '{"error":"잔액"}'), {"text": SAID})
        self.assertEqual(self.hear(opener)["state"], "못 받아씀")
        self.assertEqual(self.hear(opener)["state"], "못 받아씀")       # 두 번째는 보내지 않는다
        self.assertEqual(len(opener.seen), 1)
        self.assertTrue(self.lines()[-1]["note"].startswith("보내지 않음"))
        self.assertEqual(self.hear(opener, "의뢰-음성-다른것")["state"], "받아씀")     # 다른 의뢰는 보낸다


if __name__ == "__main__":
    unittest.main()
