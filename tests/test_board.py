"""순위판 — 표 읽기, 내용을 지키는 규칙(빈 값·순위·동점·단위), 작업 폴더."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, build, request

PASTED = "순위\t이름\t점수(점)\n1\t김가상\t1,250\n2\t이가상\t1,180\n3\t박가상\t965"
MAP_TEXT = "출연진 4명이 버스를 타고 노들섬에서 여의도로 이동하는 장면 지도 부탁드립니다. 960*540 전체화면, 총 8초입니다."


def raw(**over):
    base = {"kind": "rank_board", "title": "게임 점수 순위", "unit": "", "order": "input", "duration_seconds": 8,
            "output": {"width": 960, "height": 540, "fps": 30},
            "rows": [{"name": "김가상", "value_text": "1,250"}, {"name": "이가상", "value_text": "1,180"}, {"name": "박가상", "value_text": "965"}]}
    base.update(over)
    return base


def answering(obj):
    """Liner 모델 자리에 끼우는 가짜 — 정해 둔 답을 돌려준다."""
    return lambda messages, **kw: {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}, "finish_reason": "stop"}]}


class ParseTest(unittest.TestCase):
    def test_탭으로_나눈_표와_머리글(self):
        t = board.parse_table(PASTED)
        self.assertEqual(t["rows"], [{"rank": 1, "name": "김가상", "value_text": "1,250"}, {"rank": 2, "name": "이가상", "value_text": "1,180"},
                                     {"rank": 3, "name": "박가상", "value_text": "965"}])
        self.assertEqual(t["unit"], "점")

    def test_줄글로_적은_순위(self):
        t = board.parse_table("제목: 오늘의 순위\n1위 김가상 98.5점\n2위 이가상 91점")
        self.assertEqual(t["title"], "오늘의 순위")
        self.assertEqual(t["rows"], [{"rank": 1, "name": "김가상", "value_text": "98.5점"}, {"rank": 2, "name": "이가상", "value_text": "91점"}])

    def test_천_단위_쉼표가_있는_줄은_쉼표로_나누지_않는다(self):
        t = board.parse_table("김가상 12,450표\n이가상 9,800표")
        self.assertEqual([r["value_text"] for r in t["rows"]], ["12,450표", "9,800표"])
        self.assertEqual([r["rank"] for r in t["rows"]], [None, None])

    def test_쉼표로_나눈_표(self):
        t = board.parse_table("이름,점수\n김가상,98.5\n이가상,91")
        self.assertEqual(t["rows"], [{"rank": None, "name": "김가상", "value_text": "98.5"}, {"rank": None, "name": "이가상", "value_text": "91"}])

    def test_머리글_없는_세_칸_표(self):
        t = board.parse_table("1 | 김가상 | 98.5\n2 | 이가상 | 91")
        self.assertEqual([(r["rank"], r["name"], r["value_text"]) for r in t["rows"]], [(1, "김가상", "98.5"), (2, "이가상", "91")])

    def test_숫자로_바꾸기는_숫자_하나일_때만(self):
        self.assertEqual(board.number("12,450점"), 12450)
        self.assertEqual(board.number("98.50"), 98.5)
        self.assertIsNone(board.number("3승 2패"))
        self.assertIsNone(board.number(""))


class ReadTest(unittest.TestCase):
    def setUp(self):       # 읽는 쪽 설정이 바깥 환경에 따라 달라지지 않게
        env = mock.patch.dict(os.environ, {"MAPCG_READER": "auto"})
        env.start()
        self.addCleanup(env.stop)

    def test_지도_의뢰는_넘긴다(self):
        self.assertIsNone(board.read(MAP_TEXT))

    def test_글에_표가_있으면_모델_없이_초안을_만든다(self):
        r = board.read("게임 점수 순위판 부탁드립니다. 960*540, 6초.\n" + PASTED, available={"liner": True})
        d = r["draft"]
        self.assertEqual((r["source"], d["kind"], d["unit"], d["duration_seconds"]), ("simple", "rank_board", "점", 6))
        self.assertEqual(d["output"], {"width": 960, "height": 540, "fps": 30})
        self.assertEqual([(x["id"], x["rank"], x["name"], x["value_text"]) for x in d["rows"]],
                         [("r1", 1, "김가상", "1,250"), ("r2", 2, "이가상", "1,180"), ("r3", 3, "박가상", "965")])

    def test_그림_속_표는_Liner_모델이_읽는다(self):
        got = {"is_rank_board": True, "title": "득표 순위", "unit": "표", "duration_seconds": 5, "unclear": ["3번째 줄의 값이 흐립니다"],
               "rows": [{"rank": None, "name": "김가상", "value_text": "12,450"}, {"rank": None, "name": "이가상", "value_text": ""}]}
        r = board.read("순위판 부탁드립니다", ["data:image/png;base64,AAAA"], available={"liner": True}, chat=answering(got))
        d = r["draft"]
        self.assertEqual((r["source"], d["title"], d["unit"], d["duration_seconds"]), ("liner", "득표 순위", "표", 5))
        self.assertEqual(d["rows"][1], {"id": "r2", "rank": None, "name": "이가상", "value_text": ""})     # 빈 값은 빈 채로 온다
        self.assertIn("확인 필요: 3번째 줄의 값이 흐립니다", r["notes"])

    def test_모델이_순위판이_아니라고_하면_넘긴다(self):
        got = {"is_rank_board": False, "title": "", "unit": "", "rows": [], "duration_seconds": None, "unclear": []}
        self.assertIsNone(board.read("순위판 느낌으로 부탁", ["data:image/png;base64,AAAA"], available={"liner": True}, chat=answering(got)))

    def test_모델_호출이_실패해도_글에서_찾은_것으로_이어_간다(self):
        def broken(messages, **kw):
            raise ValueError("모델의 답이 길이 한도(3000토큰)에서 잘렸습니다")
        r = board.read("순위판 부탁드립니다\n" + PASTED, ["data:image/png;base64,AAAA"], available={"liner": True}, chat=broken)
        self.assertEqual((r["source"], len(r["draft"]["rows"])), ("simple", 3))
        self.assertTrue(any("Liner 모델로 표를 읽지 못했습니다" in n for n in r["notes"]))

    def test_모델이_없는데_표가_그림에만_있으면_직접_적으라고_알린다(self):
        r = board.read("순위판 부탁드립니다", ["data:image/png;base64,AAAA"], available={"liner": False})
        self.assertEqual(r["draft"]["rows"], [])
        self.assertTrue(any("LINER_API_KEY" in n for n in r["notes"]))


class NormalizeTest(unittest.TestCase):
    def problems(self, **over):
        with self.assertRaises(request.RequestError) as c:
            board.normalize(raw(**over))
        return c.exception.problems

    def test_빈_값을_0_으로_채우지_않고_돌려보낸다(self):
        p = self.problems(rows=[{"name": "김가상", "value_text": "10"}, {"name": "이가상", "value_text": ""}])
        self.assertTrue(any("이가상" in s and "0 으로 채우지 않습니다" in s for s in p))

    def test_제목과_줄_수(self):
        self.assertIn("제목이 없습니다.", self.problems(title=" "))
        self.assertTrue(any("두 줄 이상" in s for s in self.problems(rows=[{"name": "김가상", "value_text": "1"}])))
        many = [{"name": f"가상{i}", "value_text": str(i)} for i in range(board.MAX_ROWS + 1)]
        self.assertTrue(any(f"{board.MAX_ROWS}줄까지" in s for s in self.problems(rows=many)))

    def test_값으로_정렬하려면_숫자_하나여야_한다(self):
        p = self.problems(order="desc", rows=[{"name": "김가상", "value_text": "3승 2패"}, {"name": "이가상", "value_text": "10"}])
        self.assertTrue(any("김가상" in s and "숫자 하나" in s for s in p))

    def test_통째로_빈_줄은_없는_것으로_본다(self):
        req = board.normalize(raw(rows=[{"name": "김가상", "value_text": "10"}, {"name": "", "value_text": "", "rank": None}, {"name": "이가상", "value_text": "7"}]))
        self.assertEqual([r["name"] for r in req["rows"]], ["김가상", "이가상"])
        self.assertEqual([r["value"] for r in req["rows"]], [10, 7])


class ArrangeTest(unittest.TestCase):
    def arrange(self, **over):
        return board.arrange(board.normalize(raw(**over)))

    def test_적혀_온_순위를_그대로_쓰고_값은_받은_글자_그대로(self):
        rows, notes = self.arrange(unit="점", rows=[{"rank": 2, "name": "이가상", "value_text": "1,180"}, {"rank": 1, "name": "김가상", "value_text": "1,250점"}])
        self.assertEqual([(r["rank"], r["name"], r["show"]) for r in rows], [(1, "김가상", "1,250점"), (2, "이가상", "1,180점")])
        self.assertEqual(notes, [])

    def test_순위가_없으면_적힌_순서대로_매기고_값과_어긋나면_알린다(self):
        rows, notes = self.arrange(rows=[{"name": "김가상", "value_text": "10"}, {"name": "이가상", "value_text": "30"}, {"name": "박가상", "value_text": "20"}])
        self.assertEqual([(r["rank"], r["name"]) for r in rows], [(1, "김가상"), (2, "이가상"), (3, "박가상")])
        self.assertTrue(any("순위와 값의 크기 순서가 서로 다릅니다" in n and "적힌 순서대로" in n for n in notes))

    def test_값으로_매기면_동점은_같은_순위이고_그_사실을_알린다(self):
        rows, notes = self.arrange(order="desc", rows=[{"name": "김가상", "value_text": "70"}, {"name": "이가상", "value_text": "90"},
                                                       {"name": "박가상", "value_text": "70"}, {"name": "최가상", "value_text": "50"}])
        self.assertEqual([(r["rank"], r["name"]) for r in rows], [(1, "이가상"), (2, "김가상"), (2, "박가상"), (4, "최가상")])
        self.assertTrue(any("같은 순위" in n for n in notes))

    def test_적혀_온_순위와_다르게_매겼으면_알린다(self):
        _, notes = self.arrange(order="desc", rows=[{"rank": 1, "name": "김가상", "value_text": "10"}, {"rank": 2, "name": "이가상", "value_text": "30"}])
        self.assertIn("적혀 온 순위 대신 값으로 다시 매겼습니다.", notes)

    def test_이름이_길면_만들기_전에_알린다(self):
        _, notes = self.arrange(rows=[{"name": "아주아주 긴 이름의 가상 해물 칼국수 정식", "value_text": "10"}, {"name": "가상 김밥", "value_text": "7"}])
        self.assertTrue(any("이름이 길어" in n and "칼국수" in n and "가상 김밥" not in n for n in notes))

    def test_숫자_하나가_아닌_값은_막대_없이_글자로만(self):
        rows, notes = self.arrange(rows=[{"name": "김가상", "value_text": "3승 2패"}, {"name": "이가상", "value_text": "2승 3패"}])
        self.assertEqual([r["value"] for r in rows], [None, None])
        self.assertTrue(any("막대 없이" in n for n in notes))


class JobTest(unittest.TestCase):
    def test_작업_폴더에_화면과_기록이_다_들어간다(self):
        with tempfile.TemporaryDirectory() as d:
            job = board.make_job(board.normalize(raw(unit="점")), project_dir=Path(d), out_root=Path(d) / "out", log=lambda s: None)
            folder = job["dir"]
            for name in ("index.html", "job.js", "job.json", *board.FILES):
                self.assertTrue((folder / name).is_file(), name)
            page = json.loads((folder / "job.js").read_text(encoding="utf-8").removeprefix("window.JOB=").rstrip(";"))
            self.assertEqual([(r["rank"], r["name"], r["show"], r["value"]) for r in page["rows"]],
                             [(1, "김가상", "1,250점", 1250), (2, "이가상", "1,180점", 1180), (3, "박가상", "965점", 965)])
            self.assertEqual((page["layout"], page["layout_rect"], page["duration_seconds"]), ("full", [0.0, 0.0, 1.0, 1.0], 8))
            record = json.loads((folder / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(record["request"]["kind"], "rank_board")

    def test_순위판_화면이_부르는_파일은_전부_복사_목록에_있다(self):
        html = (build.TEMPLATES / "rank_board.html").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r'<script src="([^"]+)"', html)) - {"job.js"}, set(board.FILES))


class ReviewCases(unittest.TestCase):
    """코드리뷰(3835479)에서 재현된 사례 — 사용자가 쓰는 입구(보낸 글)에서부터 확인한다."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {"MAPCG_READER": "auto"})
        env.start()
        self.addCleanup(env.stop)

    def test_읽는_쪽을_간이_해석으로_고정하면_Liner_를_부르지_않는다(self):
        calls = []

        def chat(messages, **kw):
            calls.append(1)
            raise AssertionError("Liner 를 불러서는 안 된다")
        with mock.patch.dict(os.environ, {"MAPCG_READER": "simple"}):
            r = board.read("순위판입니다", ["data:image/png;base64,AAAA"], available={"liner": True, "claude": False}, chat=chat)
        self.assertEqual(calls, [])
        self.assertEqual(r["source"], "simple")
        self.assertTrue(any("MAPCG_READER" in n and "직접 적어" in n for n in r["notes"]), r["notes"])

    def test_빈_점수_줄을_버리지_않고_빈_채로_올린다(self):
        cases = ("이름\t점수\n가람\t95점\n나래\t\n다온\t87점",       # 머리글 있는 표
                 "가람\t95점\n나래\t\n다온\t87점",                   # 머리글 없는 표
                 "가람\t95점\n나래\n다온\t87점",                     # 줄 끝의 탭이 지워진 경우
                 "가람\t95점\n다온\t87점\n나래")                     # 빈 값이 마지막 줄(보낼 때 끝의 탭이 지워진다)
        for text in cases:
            with self.subTest(text=text):
                r = board.read(text)
                self.assertEqual({x["name"]: x["value_text"] for x in r["draft"]["rows"]}, {"가람": "95점", "나래": "", "다온": "87점"})
                self.assertTrue(any("나래" in n and "0 으로 채우지 않습니다" in n for n in r["notes"]))
                with self.assertRaises(request.RequestError):      # 채우거나 뺄 때까지 만들지 않는다
                    board.normalize({**r["draft"], "title": "점수"})

    def test_따옴표로_묶은_천_단위_쉼표를_지킨다(self):
        r = board.read('이름,점수\n가람,"1,250"\n나래,980\n다온,965')
        self.assertEqual([(x["name"], x["value_text"]) for x in r["draft"]["rows"]], [("가람", "1,250"), ("나래", "980"), ("다온", "965")])
        rows, _ = board.arrange(board.normalize({**r["draft"], "title": "점수"}))
        self.assertEqual((rows[0]["name"], rows[0]["show"], rows[0]["value"]), ("가람", "1,250", 1250))

    def test_큰_수_단위를_풀어서_견준다(self):
        self.assertEqual(board.number("1만표"), 10000)
        self.assertEqual(board.number("3천만 원"), 30000000)
        self.assertEqual(board.number("2조각"), 2)        # '조각'의 조는 큰 수 단위가 아니다
        rows, _ = board.arrange(board.normalize(raw(order="desc", rows=[{"name": "가", "value_text": "9,000표"}, {"name": "나", "value_text": "1만표"}])))
        self.assertEqual([(r["rank"], r["name"], r["show"]) for r in rows], [(1, "나", "1만표"), (2, "가", "9,000표")])
        self.assertGreater(rows[0]["value"], rows[1]["value"])      # 막대도 더 길다

    def test_단위가_서로_다른_값은_견주지_않는다(self):
        mixed = [{"name": "가", "value_text": "30분"}, {"name": "나", "value_text": "1시간"}]
        with self.assertRaises(request.RequestError) as c:
            board.normalize(raw(order="desc", rows=mixed))
        self.assertTrue(any("단위가 서로 달라" in s for s in c.exception.problems))
        rows, notes = board.arrange(board.normalize(raw(rows=mixed)))        # 적힌 순서대로는 만들되 막대는 그리지 않는다
        self.assertEqual([r["value"] for r in rows], [None, None])
        self.assertTrue(any("단위가 서로 달라" in n and "막대는 그리지 않습니다" in n for n in notes))

    def test_글에_적힌_정렬_방향을_따른다(self):
        r = board.read("값이 큰 순으로 부탁드립니다\nA 10점\nB 30점\nC 50점")
        self.assertEqual(r["draft"]["order"], "desc")
        rows, _ = board.arrange(board.normalize({**r["draft"], "title": "점수"}))
        self.assertEqual([x["name"] for x in rows], ["C", "B", "A"])
        self.assertEqual(board.read("점수가 낮은 순\nA 10점\nB 30점")["draft"]["order"], "asc")

    def test_출발지_도착지를_두_칸으로_적은_지도_의뢰는_넘긴다(self):
        self.assertIsNone(board.read("지도 제작 요청\n출발지\t홍대입구역\n도착지\t여의도역\n길이\t8초"))
        self.assertIsNotNone(board.read("이름\t점수\n가람\t95\n나래\t87"))      # 이름·점수 표는 계속 순위판이다

    def test_객체가_아닌_JSON_과_깨진_JSON_은_안내하고_2_로_끝난다(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as d:
            for body in ("[]", "{ 깨진 글"):
                with self.subTest(body=body):
                    path = Path(d) / "x.json"
                    path.write_text(body, encoding="utf-8")
                    r = subprocess.run([sys.executable, "-m", "mapcg", "make", str(path)], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertNotIn("Traceback", r.stdout + r.stderr)
                    self.assertIn("의뢰 봉투", r.stdout)


if __name__ == "__main__":
    unittest.main()
