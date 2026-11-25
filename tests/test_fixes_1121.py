"""남은 결함 위 세 줄(2026-11-21): 명령줄 · 시험 도구의 종류 갈래, 카드 퀴즈 정답 덮어쓰기, 건물 안 이동 단계 검사(가짜 응답 · Liner 를 부르지 않는다)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import __main__ as cli
from mapcg import board, build, export, intake, navi, plan, quiz, quiz_build, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import trial  # noqa: E402

NAVI = {"map_style": "navi_screen", "from": {"name": "노들섬"}, "to": {"name": "여의도"}, "vehicle": "car",
        "duration_seconds": 8, "layout": "full", "output": {"width": 960, "height": 540, "fps": 30}}
VERSUS = {"kind": "plan", "shape": "versus", "title": "2차 대결", "better": "high", "duration_seconds": 8, "layout": "full",
          "output": {"width": 960, "height": 540, "fps": 30},
          "rows": [{"left": {"name": "가람", "value_text": "40"}, "right": {"name": "나래", "value_text": "60"}}]}


class Cli(unittest.TestCase):
    """명령줄은 메신저(server.Desk.make)와 같은 갈래로 만든다 — 모르는 종류는 거절하고, 네비 봉투를 일반 지도로 만들지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_cli(self, envelope, *extra):
        path = self.root / "의뢰.json"
        path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sys.stdout, "reconfigure", create=True):
            code = cli.main(["make", str(path), "--out", str(self.root / "out"), *extra])
        return code, out.getvalue()

    def fake_job(self, **more):
        folder = self.root / "out" / "작업"
        folder.mkdir(parents=True, exist_ok=True)
        return {"dir": folder, "warnings": [], "timing": {"build_seconds": 1}, **more}

    def test_모르는_종류는_거절한다(self):
        with mock.patch.object(build, "make_job", side_effect=AssertionError("지도로 만들면 안 된다")):
            code, said = self.run_cli({**NAVI, "kind": "bracket"})
        self.assertEqual(code, 2)
        self.assertIn("모르는 종류(kind: bracket)", said)

    def test_네비_봉투는_네비로_만들고_네비로_내보낸다(self):
        job = self.fake_job(places={"from": {"display": "노들섬"}, "to": {"display": "여의도"}}, route={"distance_m": 4400}, attribution="© OSM")
        with mock.patch.object(navi, "make_job", return_value=job) as make, \
                mock.patch.object(navi, "export", return_value={"mp4": job["dir"] / "네비.mp4", "frames": 240}) as video, \
                mock.patch.object(build, "make_job", side_effect=AssertionError("일반 지도로 만들면 안 된다")), \
                mock.patch.object(export, "export", side_effect=AssertionError("브라우저 내보내기를 쓰면 안 된다")):
            code, said = self.run_cli(NAVI)
        self.assertEqual(code, 0)
        make.assert_called_once()
        video.assert_called_once_with(job["dir"])
        self.assertIn("네비 화면: 노들섬 → 여의도", said)

    def test_네비는_컷_나눔을_거절한다(self):
        code, said = self.run_cli({**NAVI, "cuts": [{"from": "노들섬", "to": "한강대교", "duration_seconds": 4}]})
        self.assertEqual(code, 2)
        self.assertIn("네비 화면은 아직 컷 나눔을 하지 않습니다", said)

    def test_정보판은_설계도로_만들고_프레임을_남긴다(self):
        job = self.fake_job()
        with mock.patch.object(plan, "make_job", return_value=job) as make, \
                mock.patch.object(export, "export", return_value={"mp4": job["dir"] / "정보판.mp4", "frames": 240, "seconds": 3}) as video:
            code, said = self.run_cli(VERSUS)
        self.assertEqual(code, 0)
        make.assert_called_once()
        self.assertEqual((video.call_args.kwargs["name"], video.call_args.kwargs["keep_panel_frames"]), (plan.KIND_LABEL, True))

    def test_카드_퀴즈는_의뢰_번호_안에서_만든다(self):
        job, seen = self.fake_job(), []
        req = {"kind": quiz.KIND, "title": "장소 퀴즈", "duration_seconds": 15, "layout": "full", "art_style": "v5", "output": {"width": 1920, "height": 1080, "fps": 30}}
        with mock.patch.object(quiz, "normalize", return_value=req), \
                mock.patch.object(quiz_build, "make_job", side_effect=lambda *a, **k: seen.append(intake._liner()._ctx.request) or job), \
                mock.patch.object(export, "export", return_value={"mp4": job["dir"] / "카드 퀴즈.mp4", "frames": 450}) as video:
            code, _ = self.run_cli({"kind": quiz.KIND})
        self.assertEqual(code, 0)
        self.assertTrue(seen[0].startswith("명령줄-"))
        self.assertEqual((video.call_args.kwargs["name"], video.call_args.kwargs["keep_panel_frames"]), (quiz.KIND_LABEL, False))


class Trial(unittest.TestCase):
    """시험 도구도 같은 갈래 — 카드 퀴즈를 순위판으로, 네비 봉투를 일반 지도로 넘기지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "01.txt").write_text("의뢰", encoding="utf-8")
        self.case = {"name": "01", "text": self.root / "01.txt"}

    def test_카드_퀴즈는_카드_퀴즈로_읽는다(self):
        got = {"kind": quiz.KIND, "source": "liner", "notes": [], "draft": {"kind": quiz.KIND, "title": "장소 퀴즈", "question": "어디일까요?",
                                                                            "cards": [{}, {}], "final_answer": "강원"}}
        with mock.patch.object(board, "read", return_value=got):
            row = trial.run(self.case, make=False, sheet=False, offline=True, out=self.root)
        self.assertEqual(row["kind"], "카드 퀴즈")
        self.assertIn("정답 강원", row["read"])

    def test_네비_봉투는_네비로_만든다(self):
        draft = {**NAVI, "from": {"name": "노들섬"}, "to": {"name": "여의도"}}
        read = {"draft": draft, "missing": [], "source": "simple", "notes": []}
        job = {"dir": self.root / "네비", "warnings": [], "places": {"from": {"display": "노들섬"}, "to": {"display": "여의도"}}, "route": {"distance_m": 4400}}
        with mock.patch.object(board, "read", return_value=None), mock.patch.object(intake, "analyze", return_value=read), \
                mock.patch.object(trial.audit, "check", return_value={"state": "skipped"}), mock.patch.object(request, "normalize", side_effect=lambda r: r), \
                mock.patch.object(navi, "make_job", return_value=job) as make, \
                mock.patch.object(build, "make_job", side_effect=AssertionError("일반 지도로 만들면 안 된다")):
            row = trial.run(self.case, make=True, sheet=False, offline=True, out=self.root)
        self.assertEqual(row["kind"], navi.LABEL)
        make.assert_called_once()
        self.assertNotIn("make_error", row)
        self.assertEqual(row["route_km"], 4.4)


class QuizAnswer(unittest.TestCase):
    """제작진 글의 '최종 답은 …' 뒤 구절을 통째로 정답으로 덮어쓰지 않는다."""

    def test_지시_말투는_정답이_아니다(self):
        for text in ("최종 답은 사진대로 해주세요", "최종 답은 그림처럼 해 주세요", "최종 정답은 의뢰서 참고", "최종 답: 위와 같이"):
            with self.subTest(text=text):
                self.assertNotIn("final_answer", quiz._operator_fields(text))

    def test_짧은_답은_받고_끝말은_뗀다(self):
        self.assertEqual(quiz._operator_fields("최종 답은 강원입니다.")["final_answer"], "강원")
        self.assertEqual(quiz._operator_fields("최종 답: 세종대로")["final_answer"], "세종대로")       # 답 안의 '대로'는 그대로
        self.assertEqual(quiz._operator_fields("최종 답은 '사진대로'")["final_answer"], "사진대로")     # 따옴표로 묶은 답은 적힌 그대로
        self.assertEqual(quiz._operator_fields("1차 답은 서울, 최종 답 부산은 맨 마지막에 공개"), {"first_answer": "서울", "final_answer": "부산"})


class BuildingSteps(unittest.TestCase):
    """건물 안 이동: 그릴 수 없는 단계를 거른다."""

    FLOORS = [{"label": "B2", "kind": "parking"}, {"label": "1F"}, {"label": "8F", "kind": "goal"}]

    def steps(self, *rows):
        return [{"floor": f, "how": h, "caption": ""} for f, h in rows]

    def test_바른_이동은_통과한다(self):
        ok = self.steps(("B2", "start"), ("1F", "stairs"), ("1F", "walk"), ("8F", "elevator"))
        self.assertEqual(plan.step_faults(ok), [])
        problems = []
        plan._check_building({"floors": self.FLOORS, "steps": ok}, problems)
        self.assertEqual(problems, [])

    def test_걸어서_다른_층으로_중간_출발_같은_층_엘리베이터를_잡는다(self):
        bad = self.steps(("B2", "start"), ("1F", "walk"), ("1F", "start"), ("1F", "elevator"))
        got = plan.step_faults(bad)
        self.assertEqual(len(got), 3)
        self.assertIn("'B2' 에서 '1F' 로는 걸어서 갈 수 없습니다", got[0])
        self.assertIn("'출발'은 첫 단계에만", got[1])
        self.assertIn("이미 '1F' 에 있습니다", got[2])
        problems = []
        plan._check_building({"floors": self.FLOORS, "steps": bad}, problems)
        self.assertEqual(len(problems), 3)

    def test_읽을_때는_확인_필요로_알린다(self):
        notes = []
        plan._building({"floors": self.FLOORS, "steps": self.steps(("B2", "start"), ("8F", "walk")), "people": 2}, notes)
        self.assertTrue(any(n.startswith("확인 필요: 2번째 단계") for n in notes))


if __name__ == "__main__":
    unittest.main()
