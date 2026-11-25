"""후속 리뷰(fd5c42c)에서 재현된 사례 — 접수한 값과 확인 카드의 뜻이 같고, 그리는 쪽과 납품하는 쪽이 같은 장수를 쓴다."""
import tempfile
import unittest
from pathlib import Path

from mapcg import board, export, intake, request, server

OUT = {"width": 960, "height": 540, "fps": 30}
PDFS = [{"name": "가.pdf", "sent": 3, "total": 3}, {"name": "나.pdf", "sent": 3, "total": 3}]


def card(rows, **kw):
    return {"kind": board.KIND, "title": "가상 순위", "rows": [{"name": n, "value_text": v} for n, v in rows],
            "duration_seconds": 8, "output": OUT, **kw}


class UnitAndRank(unittest.TestCase):
    def test_공통_단위는_숫자만_온_값에_넣어_견준다(self):
        req = board.normalize(card([("나래", "1,000,000원"), ("가람", "150")], unit="만원", order="desc"))
        rows, _ = board.arrange(req)
        self.assertEqual([(r["name"], r["rank"], r["show"]) for r in rows], [("가람", 1, "150만원"), ("나래", 2, "1,000,000원")])
        self.assertGreater(rows[0]["value"], rows[1]["value"])        # 막대도 가람이 더 길다

    def test_단위가_섞이면_값으로_정렬하지_않는다(self):
        with self.assertRaises(request.RequestError):
            board.normalize(card([("가", "95"), ("나", "12초")], unit="점", order="desc"))
        rows, notes = board.arrange(board.normalize(card([("가", "95"), ("나", "12초")], unit="점")))
        self.assertTrue(all(r["value"] is None for r in rows))
        self.assertTrue(any("견주지 않았습니다" in n for n in notes))


class TableOrMap(unittest.TestCase):
    def test_이름_칸의_말은_지도_의뢰로_보지_않는다(self):
        got = board.read("이름\t점수\n지도팀\t95\n자막팀\t90")
        self.assertEqual([r["name"] for r in got["draft"]["rows"]], ["지도팀", "자막팀"])

    def test_한_점수가_비어도_두_줄이_카드에_남는다(self):
        got = board.read("이름\t점수\n지도팀\t95\n자막팀\t")
        self.assertEqual([(r["name"], r["value_text"]) for r in got["draft"]["rows"]], [("지도팀", "95"), ("자막팀", "")])

    def test_출발지_도착지_표는_지도로_넘긴다(self):
        self.assertIsNone(board.read("출발지\t도착지\t길이\n홍대\t여의도\t8초"))

    def test_표_아래_지시는_줄이_아니다(self):
        got = board.read("가람\t10\n나래\t9\n높은 순으로")
        self.assertEqual(len(got["draft"]["rows"]), 2)
        self.assertEqual(got["draft"]["order"], "desc")
        got = board.read("가람\t10\n나래\t9\n8초·25fps")
        self.assertEqual(len(got["draft"]["rows"]), 2)
        self.assertEqual((got["draft"]["duration_seconds"], got["draft"]["output"]["fps"]), (8, 25))

    def test_표_아래_점수가_빈_참가자는_남긴다(self):
        self.assertEqual([r["name"] for r in board.parse_table("가람\t10\n나래\t9\n다솜")["rows"]], ["가람", "나래", "다솜"])


class Frames(unittest.TestCase):
    def test_총_장수를_초당_프레임으로_읽지_않는다(self):
        self.assertEqual(intake._fps("2초 총 50프레임, 25fps"), 25)
        self.assertEqual(intake._fps("10초 총 250프레임, 25fps"), 25)
        self.assertEqual(intake._fps("8초, 초당 24프레임"), 24)
        self.assertEqual(intake._fps("8초 30프레임"), 30)
        self.assertIsNone(intake._fps("2초 총 50프레임"))

    def test_반은_올린다_페이지와_같은_장수(self):
        self.assertEqual(export.frame_count(2.5, 25), 63)
        self.assertEqual(export.frame_count(8, 30), 240)


class Coverage(unittest.TestCase):
    def test_글만_읽었으면_그렇게_적는다(self):
        note = server.coverage(PDFS, read=False, audit=None)
        self.assertIn("첨부는 읽지 않았습니다(글만 읽음)", note)
        self.assertIn("가.pdf 1~3쪽, 나.pdf 1~3쪽", note)
        self.assertIn("판독 감사도 하지 않았습니다", note)

    def test_감사의_실제_상태를_적는다(self):
        self.assertIn("판독 감사도 읽은 쪽만 대조했습니다", server.coverage(PDFS, audit="done"))
        self.assertIn("판독 감사는 하지 않았습니다", server.coverage(PDFS, audit="skipped"))
        self.assertIn("판독 감사는 실패했습니다", server.coverage(PDFS, audit="failed"))
        self.assertIn("읽은 쪽: 가.pdf 1~3쪽, 나.pdf 1쪽", server.coverage(PDFS, audit="skipped"))
        self.assertIn("판독 감사는 실패했습니다", server.coverage(PDFS[:1], audit="failed"))      # 다 읽었어도 감사가 안 됐으면 적는다
        self.assertEqual(server.coverage(PDFS[:1], audit="done"), "")


class DeskCards(unittest.TestCase):
    """카드의 문구는 읽기·감사가 끝난 뒤의 실제 상태로 정한다(모의 판독기)."""

    def desk(self, analyze=None, audit=None, liner=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return server.Desk(Path(tmp.name), analyze=analyze, audit=audit, liner=liner)

    def read(self, d, text):
        p = d.add("desk", "progress", title="", lines=[], state="run", error="")
        d._read(p, text, ["data:image/jpeg;base64,AAAA"] * 4, PDFS)
        return d.msgs[-1]

    def test_글로_된_순위표는_그림을_읽지도_감사하지도_않는다(self):
        calls = {"read": 0, "audit": 0}
        d = self.desk(analyze=lambda *a, **k: calls.__setitem__("read", calls["read"] + 1),
                      audit=lambda *a, **k: calls.__setitem__("audit", calls["audit"] + 1))
        msg = self.read(d, "점수 순위판\n가람\t10\n나래\t9")
        self.assertEqual((msg["kind"], calls), ("board", {"read": 0, "audit": 0}))
        self.assertIn("첨부는 읽지 않았습니다(글만 읽음)", msg["notes"][0])
        self.assertIn("판독 감사도 하지 않았습니다", msg["notes"][0])

    def test_지도_카드는_감사_결과에_맞춰_적는다(self):
        seen = {}
        for state, words in (("done", "판독 감사도 읽은 쪽만"), ("skipped", "판독 감사는 하지 않았습니다"), ("failed", "판독 감사는 실패했습니다")):
            def analyze(text, images=()):
                r = intake.analyze(text, (), use_liner=False)
                return {**r, "source": "liner"}
            d = self.desk(analyze=analyze, audit=lambda *a, state=state, **k: {"state": state, "reason": "", "flags": 0, "unverified": 0}, liner=True)
            seen[state] = self.read(d, "홍대에서 여의도로 버스, 8초")
            self.assertEqual(seen[state]["kind"], "card")
            self.assertIn(words, seen[state]["notes"][0])
            self.assertIn("읽은 쪽: 가.pdf 1~3쪽, 나.pdf 1쪽", seen[state]["notes"][0])


if __name__ == "__main__":
    unittest.main()
