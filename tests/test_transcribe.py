"""의뢰서 받아쓰기(11-23) — 그림을 먼저 틀 없이 받아 적고, 그 글을 읽기에는 넘기되 감사에는 넘기지 않는다.

전부 가짜 응답·임시 폴더로 본다(Liner 를 실제로 부르지 않는다).
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import server, transcribe

IMG = "data:image/jpeg;base64,AAAA"
SHEET = "① DMC역 → 수색역\n② 수색역 → 서부운전면허\n③ 가양대교 북단 → 일산으로 빠지는 길\n각각 5~8초\n요약\n따로 떨어진 세 구간"


def answer(text, model="gpt-6.2-sol"):
    seen = []

    def chat(messages, **opts):
        seen.append((messages, opts))
        return {"model": model, "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    return chat, seen


class Run(unittest.TestCase):
    def test_그림을_틀_없이_받아_적는다(self):
        chat, seen = answer(f"  {SHEET}  ")
        got = transcribe.run([IMG, "data:application/pdf;base64,AAAA"], chat=chat)
        self.assertEqual((got["ok"], got["text"], got["model"]), (True, SHEET, "gpt-6.2-sol"))
        messages, opts = seen[0]
        self.assertEqual(opts["task"], transcribe.TASK)
        self.assertNotIn("response_format", opts)                       # 칸 채우기 틀 없이
        parts = messages[0]["content"]
        self.assertEqual([p["type"] for p in parts], ["text", "image_url"])     # 그림이 아닌 첨부는 넣지 않는다

    def test_실패하면_빈_글로_돌려준다(self):
        def broken(messages, **opts):
            raise RuntimeError("연결 끊김")
        got = transcribe.run([IMG], chat=broken)
        self.assertEqual((got["ok"], got["text"]), (False, ""))
        self.assertIn("연결 끊김", got["why"])
        self.assertFalse(transcribe.run([], chat=broken)["ok"])

    def test_빈_답이나_잘린_답은_실패다(self):
        chat, _ = answer("")
        self.assertFalse(transcribe.run([IMG], chat=chat)["ok"])

    def test_한_번만_붙인다(self):
        once = transcribe.attach("의뢰", SHEET)
        self.assertIn(transcribe.HEAD, once)
        self.assertEqual(transcribe.attach(once, SHEET), once)
        self.assertEqual(transcribe.attach("의뢰", ""), "의뢰")


class DeskFlow(unittest.TestCase):
    """서버: 받아 적은 글은 의뢰서 읽기에 넘기고, 판독 감사에는 원래 글만 넘긴다."""

    def desk(self, transcriber):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.got = {"read": [], "audit": []}

        def analyze(text, images):
            self.got["read"].append(text)
            return {"draft": {"from": {"name": "DMC역"}, "to": {"name": "수색역"}}, "missing": [], "source": "liner", "notes": []}

        def audit(text, images, r, **k):
            self.got["audit"].append(text)
            return {"state": "done"}
        d = server.Desk(Path(tmp.name), analyze=analyze, audit=audit, liner=True, transcriber=transcriber)
        patch = mock.patch.object(server.board, "read", lambda *a, **k: None)      # 순위판 판별은 부르지 않는다(Liner 호출)
        patch.start()
        self.addCleanup(patch.stop)
        return d

    def read(self, d, text="박PD 의뢰"):
        p = d.add("desk", "progress", title="", lines=[], state="run", error="")
        d._read(p, text, [IMG], [])
        return p, d.msgs[-1]

    def test_받아쓴_글은_읽기에만_넘긴다(self):
        calls = []
        d = self.desk(lambda pics: calls.append(pics) or {"ok": True, "text": SHEET, "model": "gpt-6.2-sol", "seconds": 9.5})
        p, card = self.read(d)
        self.assertEqual(calls, [[IMG]])
        self.assertIn(transcribe.HEAD, self.got["read"][0])
        self.assertIn("가양대교 북단 → 일산으로 빠지는 길", self.got["read"][0])
        self.assertEqual(self.got["audit"], ["박PD 의뢰"])                 # 감사는 그림을 따로 읽는다
        self.assertEqual(card["kind"], "card")
        self.assertTrue(any("먼저 받아 적어" in n for n in card["notes"]), card["notes"])
        self.assertIn("  ① DMC역 → 수색역", p["lines"])                   # 받아 적은 글은 진행 표시에 보인다

    def test_같은_그림은_다시_받아_적지_않는다(self):
        calls = []
        d = self.desk(lambda pics: calls.append(pics) or {"ok": True, "text": SHEET, "model": "m", "seconds": 1})
        self.read(d)
        self.read(d, "박PD 의뢰\n\n[답] 컷 3은 빼 주세요")
        self.assertEqual(len(calls), 1)
        self.assertIn(transcribe.HEAD, self.got["read"][1])

    def test_받아쓰기가_안_되면_그림만으로_읽는다(self):
        d = self.desk(lambda pics: {"ok": False, "why": "503"})
        _, card = self.read(d)
        self.assertEqual(self.got["read"], ["박PD 의뢰"])
        self.assertTrue(any("받아쓰기를 하지 못해" in n for n in card["notes"]), card["notes"])

    def test_받아쓰기를_붙이지_않은_Desk_는_예전과_같다(self):
        d = self.desk(None)
        _, card = self.read(d)
        self.assertEqual(self.got["read"], ["박PD 의뢰"])
        self.assertFalse(any("받아" in n for n in card["notes"]), card["notes"])


if __name__ == "__main__":
    unittest.main()
