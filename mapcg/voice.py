"""음성 의뢰 — 말로 한 의뢰(음성 메모 · 통화 녹음)를 Liner 로 받아써서 글 의뢰와 같은 길에 올린다.

흐름: 음성 파일 → 형식 · 크기 확인 → Liner 받아쓰기(호출 1번) → 받아쓴 글 → (여기서부터는 글 의뢰와 같다) 읽기 → 감사 → 확인 카드

지키는 것
- 받아쓴 글은 '의뢰서 글'과 똑같이 다룬다. 지시문이 아니라 자료다. 그대로 만들지 않고 확인 카드를 거친다.
- 고유명사(지명 · 사람 이름 · 프로그램 이름)와 숫자는 잘못 들릴 수 있다. 받아쓴 글을 사람이 볼 수 있게 그대로 돌려주고, 확인하라는 말을 붙인다.
- 같은 음성을 두 번째 모델로 한 번 더 받아써서, 두 모델이 다르게 들은 낱말을 '확인할 낱말'(doubts)로 따로 낸다.
  (2026-11-20 실측: 전화 음질 + 잡음에서 '조연출'을 한 모델은 '조현출', 다른 모델은 '도연출'로 들었다 — 틀리는 자리가 모델마다 달라 견주면 드러난다.)
  받아쓴 글 자체는 고치지 않는다. 쉼표가 빠져 '1 250점'처럼 들린 숫자도 고치지 않고 확인할 낱말로만 낸다.
- 말소리가 없을 때 받아쓰기 모델이 지어내는 굳은 문장('시청해 주셔서 감사합니다' 따위)은 '못 알아들음'으로 돌린다.
- 답을 기다리다 끊기면 다시 보내지 않는다(접수됐는지 알 수 없다 — liner.py 와 같은 규칙).
- 오프라인이면 부르지 않는다. 음성 파일의 내용과 받아쓴 글은 호출 기록에 남기지 않는다.

hear() 는 예외를 내지 않는다. 안 되면 사유를 담아 돌아간다 — 그때는 글로 적어 달라고 안내한다.
아직 의뢰 메신저(첨부 · 읽기)에는 연결하지 않았다(2026-11-20). 연결할 때 여기의 전송 부분은 liner.py 로 옮긴다.

혼자 돌려 보기:  python -m mapcg.voice 의뢰.m4a
"""
from __future__ import annotations

import difflib
import http.client
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import intake

PATH = "/audio/transcriptions"
MODEL = "openai/gpt-transcribe"     # MAPCG_VOICE_MODEL 로 바꾼다. 2026-11-20 실측: 한국어 의뢰 문장을 문장부호까지 그대로 받아씀(15초 음성 · 2초 · 0.0013달러)
SECOND = "openai/whisper-large-v3-turbo"   # 견줘 볼 두 번째 모델. MAPCG_VOICE_SECOND 로 바꾸고, off 면 한 모델만 쓴다(실측 1초 · 0.0002달러)
MAX_BYTES = 20 * 1024 * 1024        # 문서의 한도는 25MiB. 여유를 둔다
MIN_BYTES = 2000                    # 이보다 작으면 말이 담겼다고 보기 어렵다
WAIT = 180                          # 받아쓰기를 기다리는 한도(초)
MIN_CHARS = 6                       # 받아쓴 글이 이보다 짧으면 의뢰로 보지 않는다
TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "m4a": "audio/mp4", "ogg": "audio/ogg", "webm": "audio/webm"}

# 말소리가 없거나 잡음뿐일 때 받아쓰기 모델이 흔히 지어내는 문장(빈칸 · 문장부호를 뺀 모양으로 견준다)
_EMPTY_TALK = ("시청해주셔서감사합니다", "구독과좋아요부탁드립니다", "다음영상에서만나요", "한글자막by", "자막제공", "mbc뉴스", "감사합니다", "thankyouforwatching", "thanksforwatching")
NOTE = "음성을 받아쓴 글입니다 — 지명 · 사람 이름 · 숫자는 잘못 들렸을 수 있으니 확인 카드에서 봐 주세요."


class VoiceError(RuntimeError):
    """음성 의뢰를 받아쓰지 못했다(파일이 맞지 않음 · 못 알아들음). 밖에서는 '글로 적어 달라'로 다룬다."""


def sniff(data: bytes) -> str | None:
    """파일 내용의 머리글자로 음성 형식을 가린다(이름의 확장자는 믿지 않는다). 모르는 형식이면 None."""
    head = bytes(data[:16])
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[4:8] == b"ftyp":
        # 같은 그릇(ISO 미디어)을 사진도 쓴다 — HEIC · AVIF 사진은 음성이 아니다.
        # MP4 영상은 머리글자만으로 M4A 음성과 가를 수 없다. 그래서 부르는 쪽(server)이 화면에서 음성으로 고른 첨부만 여기로 보낸다.
        return None if head[8:11].lower() in (b"hei", b"hev", b"mif", b"msf", b"avi") else "m4a"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) > 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        # MP3 프레임 머리: 판(version) 01 은 없는 값, 층(layer)은 II·III 만, 비트율 1111 · 표본율 11 은 없는 값.
        # (이 검사가 없으면 UTF-16 글 파일의 머리 FF FE 가 MP3 로 보인다)
        version, layer = (head[1] >> 3) & 3, (head[1] >> 1) & 3
        if version != 1 and layer in (1, 2) and head[2] >> 4 != 15 and (head[2] >> 2) & 3 != 3:
            return "mp3"
    return None


def model() -> str:
    return os.environ.get("MAPCG_VOICE_MODEL", "").strip() or MODEL


def _form(data: bytes, kind: str, name: str) -> tuple[bytes, str]:
    """multipart 본문. 파일 이름은 영문으로 고쳐 보낸다(원래 이름에는 사람 · 프로그램 이름이 들어 있을 수 있다)."""
    mark = "mapcg" + secrets.token_hex(12)
    out = b""
    for k, v in (("model", name), ("language", "ko"), ("response_format", "json")):
        out += f'--{mark}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
    out += (f'--{mark}\r\nContent-Disposition: form-data; name="file"; filename="request.{kind}"\r\n'
            f"Content-Type: {TYPES[kind]}\r\n\r\n").encode("ascii") + data + f"\r\n--{mark}--\r\n".encode("ascii")
    return out, f"multipart/form-data; boundary={mark}"


def _send(body: bytes, ctype: str, name: str) -> dict:
    """Liner 받아쓰기에 보낸다. 의뢰 번호 · 거절 차단 · 호출 기록은 liner.py 의 것을 그대로 쓴다."""
    liner = intake._liner()
    key = liner.api_key()
    call, rid = secrets.token_hex(3), getattr(liner._ctx, "request", None)
    tag = {"model": name, "response_format": {"type": "voice_transcription"}}      # 기록용 이름표(보내는 본문이 아니다)
    refused = liner._refused(rid)
    if refused:
        liner._log(PATH, tag, refused, 0, 0.0, None, call=call, note="보내지 않음: 같은 의뢰의 앞선 호출이 거절됨")
        raise liner.LinerError(refused, "같은 의뢰의 앞선 호출이 거절되어(키·잔액·권한) 다시 보내지 않았습니다.")
    for attempt in range(2):
        req = urllib.request.Request(liner.BASE_URL + PATH, data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": ctype}, method="POST")
        t1 = time.perf_counter()
        try:
            with liner._open(req, WAIT) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            liner._log(PATH, tag, 200, attempt, time.perf_counter() - t1, {"model": name, "usage": out.get("usage") if isinstance(out, dict) else None}, call=call)
            return out if isinstance(out, dict) else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            e.close()
            again = e.code in liner._RETRYABLE and attempt == 0
            liner._log(PATH, tag, e.code, attempt, time.perf_counter() - t1, None, call=call, final=not again)
            if again:
                wait = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(10.0, float(wait)) if wait and wait.replace(".", "", 1).isdigit() else 2.0)
                continue
            if e.code in (401, 402) and rid:      # liner._post 와 같은 규칙: 키 · 잔액만 의뢰째 막고, 403 은 이 호출만 실패로
                liner._blocked[rid] = (e.code, time.monotonic())
            raise liner.LinerError(e.code, text) from None
        except urllib.error.URLError as e:      # 보내지 못했다 — 접수된 것이 없으니 한 번 더 보낸다
            again = attempt == 0
            liner._log(PATH, tag, 0, attempt, time.perf_counter() - t1, None, call=call, final=not again, note="보내지 못함")
            if again:
                time.sleep(2.0)
                continue
            raise liner.LinerError(0, f"연결 실패: {e.reason}") from None
        except (TimeoutError, OSError, http.client.HTTPException):     # 보낸 뒤 끊겼다 — 접수됐는지 알 수 없어 다시 보내지 않는다
            liner._log(PATH, tag, 0, attempt, time.perf_counter() - t1, None, call=call, note="접수 여부 불명: 답을 기다리다 끊김")
            raise liner.LinerError(0, "Liner 의 답을 기다리다 끊겼습니다 — 요청이 접수됐는지 알 수 없어 다시 보내지 않았습니다.") from None
        except ValueError:
            liner._log(PATH, tag, 0, attempt, time.perf_counter() - t1, None, call=call, note="답을 알아볼 수 없음(응답 200 · JSON 아님)")
            raise liner.LinerError(0, "Liner 의 답을 알아볼 수 없습니다(JSON 이 아님).") from None
    raise liner.LinerError(0, "재시도 횟수를 넘었습니다.")


def tidy(text) -> str:
    """받아쓴 글을 다듬는다: 줄바꿈 · 겹친 빈칸을 한 칸으로. 내용은 건드리지 않는다."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def reflow(text: str) -> str:
    """받아쓴 한 문단을 의뢰서처럼 줄로 나눈다: 문장 끝과 'N위' 앞에서 줄을 바꾸고, 줄 끝의 쉼표를 뗀다. 글자는 바꾸지 않는다.

    말로 한 순위표('1위 김가상 250점, 2위 …')는 한 줄로 받아써진다. 기존 순위판 읽기는 줄마다 한 순위인 글을 읽으므로 줄만 나눠 준다.
    """
    s = re.sub(r"(?<=[.?!])\s+", "\n", tidy(text))
    s = re.sub(r"[,\s]+(?=\d{1,2}\s?위[\s,])", "\n", s)
    s = re.sub(r"(?m)^(\d{1,2}\s?위\s[^,\n]*),\s+(?=\D)", "\\1\n", s)       # 순위 줄 뒤에 붙은 다른 말('…410점, 길이는 8초')을 떼어 낸다. '1, 250점'은 그대로 둔다
    return "\n".join(line.strip().rstrip(",").strip() for line in s.split("\n") if line.strip(" ,"))


def is_empty_talk(text: str) -> bool:
    """말소리가 없을 때 모델이 지어내는 굳은 문장뿐인가."""
    flat = re.sub(r"[\s\W_]+", "", text.lower())
    if not flat:
        return True
    for phrase in _EMPTY_TALK:
        flat = flat.replace(phrase, "")
    return not flat


def _words(text: str) -> list[str]:
    return [w for w in (re.sub(r"[^\w%]+", "", t) for t in text.split()) if w]


def doubts(heard: str, other: str) -> list[dict]:
    """두 모델이 받아쓴 글에서 서로 다른 낱말. [{'heard': 첫 모델, 'other': 둘째 모델}] — 띄어쓰기 · 문장부호만 다른 곳은 뺀다."""
    a, b = _words(heard), _words(other)
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b], autojunk=False).get_opcodes():
        if op == "equal":
            continue
        x, y = " ".join(a[a1:a2]), " ".join(b[b1:b2])
        if x.replace(" ", "").lower() != y.replace(" ", "").lower():
            out.append({"heard": x, "other": y})
    return out


def number_doubts(text: str) -> list[dict]:
    """쉼표가 빠져 둘로 갈라져 들린 것으로 보이는 큰 숫자('1 250점' → 1,250점일 수 있다). 글은 고치지 않고 알리기만 한다."""
    return [{"heard": m.group(0), "other": f"{m.group(1)},{m.group(2)}{m.group(3)}"}
            for m in re.finditer(r"(?<![\d,.])(\d{1,3}) (\d{3})(?!\d)([가-힣a-zA-Z%]{0,3})", text)]


def transcribe(data: bytes, send=None, name: str | None = None) -> dict:
    """음성 하나를 받아쓴다. {'text', 'format', 'bytes', 'model', 'cost_usd'}. 파일이 맞지 않거나 못 알아들으면 VoiceError."""
    data = bytes(data or b"")
    if len(data) < MIN_BYTES:
        raise VoiceError("음성 파일이 비어 있거나 너무 짧습니다.")
    if len(data) > MAX_BYTES:
        raise VoiceError(f"음성 파일이 너무 큽니다({len(data) / 1048576:.0f}MB · 한도 {MAX_BYTES // 1048576}MB). 의뢰 부분만 잘라서 보내 주세요.")
    kind = sniff(data)
    if not kind:
        raise VoiceError("음성 파일로 보이지 않습니다. WAV · MP3 · M4A · FLAC · OGG · WebM 가운데 하나로 보내 주세요.")
    name = name or model()
    body, ctype = _form(data, kind, name)
    out = (send or _send)(body, ctype, name)
    if out.get("error"):
        raise VoiceError("Liner 가 받아쓰지 못했습니다: " + str((out["error"] or {}).get("message") if isinstance(out["error"], dict) else out["error"])[:200])
    text = tidy(out.get("text"))
    if len(text) < MIN_CHARS or is_empty_talk(text):
        raise VoiceError("말소리를 알아듣지 못했습니다. 조용한 곳에서 다시 녹음하거나 글로 적어 주세요.")
    return {"text": text, "format": kind, "bytes": len(data), "model": name, "cost_usd": (out.get("usage") or {}).get("cost")}


def hear(source, *, offline: bool = False, send=None) -> dict:
    """음성 의뢰를 받아쓴다(파일 경로나 바이트). 예외를 내지 않는다.

    돌려주는 것: {'state': '받아씀' | '못 받아씀' | '건너뜀', 'text': 받아쓴 글 그대로(사람에게 보여 준다),
                 'request_text': 줄을 나눈 글(의뢰 읽기에 넘긴다), 'note': 사유나 확인 안내,
                 'doubts': [{'heard', 'other'}] 확인할 낱말(두 모델이 다르게 들은 곳 · 쉼표가 빠진 듯한 숫자), 'second_model',
                 'format', 'bytes', 'model', 'cost_usd', 'seconds'}
    """
    out = {"state": "건너뜀", "text": "", "request_text": "", "note": "", "doubts": [], "second_model": "", "format": None, "bytes": 0, "model": "", "cost_usd": None, "seconds": 0.0}
    if offline:
        out["note"] = "오프라인에서는 음성을 받아쓰지 못합니다 — 글로 적어 주세요."
        return out
    t0 = time.perf_counter()
    try:
        data = Path(source).read_bytes() if isinstance(source, (str, os.PathLike)) else bytes(source)
        out.update(transcribe(data, send), state="받아씀", note=NOTE)
        out["request_text"] = reflow(out["text"])
        out["doubts"] = number_doubts(out["text"])
        second = os.environ.get("MAPCG_VOICE_SECOND", "").strip() or SECOND
        if second.lower() not in ("off", "0", "no", "false") and second != out["model"]:
            try:        # 견주는 쪽이 안 돼도 받아쓴 글은 그대로 쓴다
                again = transcribe(data, send, second)
                out["doubts"] = doubts(out["text"], again["text"]) + out["doubts"]
                out["second_model"] = second
                if isinstance(out["cost_usd"], (int, float)) and isinstance(again["cost_usd"], (int, float)):
                    out["cost_usd"] = round(out["cost_usd"] + again["cost_usd"], 6)
            except Exception:
                pass
        if out["doubts"]:
            out["note"] = NOTE + " 특히: " + " · ".join(f"'{d['heard']}'↔'{d['other']}'" for d in out["doubts"][:8])
    except VoiceError as e:
        out.update(state="못 받아씀", note=str(e))
    except OSError as e:
        out.update(state="못 받아씀", note=f"음성 파일을 열지 못했습니다({type(e).__name__}).")
    except Exception as e:      # 키 · 잔액 · 접속 — 무엇이든 글 의뢰로 돌린다
        out.update(state="못 받아씀", note=f"Liner 가 받아쓰지 못했습니다({intake.reason(e, typed=False)[:160]}) — 글로 적어 주세요.")
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [a for a in args if not a.startswith("--")]
    if len(paths) != 1:
        print("쓰는 법: python -m mapcg.voice <음성 파일> [--offline]")
        return 2
    got = hear(paths[0], offline="--offline" in args)
    print(f"음성 의뢰: {got['state']} ({got['format']} · {got['bytes']:,}바이트 · {got['seconds']}초 · {got['model']} · {got['cost_usd']}달러)")
    if got["text"]:
        print(got["text"])
    for d in got["doubts"]:
        print(f"   확인할 낱말: '{d['heard']}' ↔ '{d['other']}'")
    if got["note"]:
        print(" -", got["note"])
    return 0 if got["state"] == "받아씀" else 1


if __name__ == "__main__":
    raise SystemExit(main())
