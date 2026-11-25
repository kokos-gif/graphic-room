"""설계도 정보판 — Liner 가 구조(설계도)만 답하고 우리 화풍 코드가 그린다. 가짜 응답으로 본다(Liner 를 실제로 부르지 않는다)."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, intake, plan, request, server

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ["data:image/jpeg;base64,AAAA"]
PAIRS = [("A선수", "40", "D선수", "60"), ("B선수", "50", "E선수", "50"), ("C선수", "삼십", "F선수", "45")]


def designed(**over):
    """Liner 가 낸 설계도(가짜)."""
    return {"shape": "versus", "title": "2차 카드 게임 점수", "left_label": "", "right_label": "", "left_color": "blue", "right_color": "red",
            "pairs": [{"left_name": a, "left_value": b, "right_name": c, "right_value": d} for a, b, c, d in PAIRS], "items": [],
            "icon_left": "A15", "icon_right": "none", "duration_seconds": None, "unsupported": ["제목 옆 트럼프 카드 그림"], "unclear": [], **over}


def chat_by_task(answers: dict, calls: list):
    """물음(틀 이름)마다 정해 둔 답을 돌려주는 가짜 Liner."""
    def chat(messages, **options):
        name = options["response_format"]["json_schema"]["name"]
        calls.append(name)
        return {"choices": [{"message": {"content": json.dumps(answers[name], ensure_ascii=False)}}]}
    return chat


def draft():
    return plan.read("", IMAGE, chat=chat_by_task({"info_board_plan": designed()}, []))["draft"]


class Read(unittest.TestCase):
    def test_설계도를_초안으로_바꾸고_못_그리는_요청을_알린다(self):
        r = plan.read("", IMAGE, chat=chat_by_task({"info_board_plan": designed()}, []))
        d = r["draft"]
        self.assertEqual((d["kind"], d["shape"], d["title"], d["icon_left"], d["icon_right"], d["art_style"]), ("plan", "versus", "2차 카드 게임 점수", "A15", "", "v5"))
        self.assertEqual(d["rows"][0], {"left": {"name": "A선수", "value_text": "40"}, "right": {"name": "D선수", "value_text": "60"}})
        self.assertTrue(any("반영하지 못하는 요청: 제목 옆 트럼프 카드 그림" in n for n in r["notes"]), r["notes"])

    def test_정보판이_아니라고_하면_넘긴다(self):
        self.assertIsNone(plan.read("", IMAGE, chat=chat_by_task({"info_board_plan": designed(shape="none", pairs=[])}, [])))


class Normalize(unittest.TestCase):
    def test_이긴_쪽은_모델이_아니라_숫자로_가린다(self):
        req = plan.normalize(draft())
        self.assertEqual([r["winner"] for r in req["rows"]], ["right", "tie", "none"])      # 숫자가 아닌 값('삼십')이 든 줄은 가리지 않는다
        self.assertEqual(req["rows"][0]["left"]["value"], 40.0)

    def test_빈_값은_0_으로_채우지_않고_돌려보낸다(self):
        d = draft()
        d["rows"][1]["right"]["value_text"] = ""
        with self.assertRaises(request.RequestError) as e:
            plan.normalize(d)
        self.assertIn("값이 비어 있습니다", " ".join(e.exception.problems))

    def test_줄_수와_색과_소품을_검사한다(self):
        d = draft()
        d["rows"] = d["rows"] * 3
        d["left_color"], d["icon_right"] = "rainbow", "Z99"
        with self.assertRaises(request.RequestError) as e:
            plan.normalize(d)
        said = " ".join(e.exception.problems)
        self.assertIn("8줄까지", said)
        self.assertIn("편의 색", said)
        self.assertIn("제목 옆 소품", said)

    def test_설명판(self):
        req = plan.normalize({"kind": "plan", "shape": "rules", "title": "게임 규칙", "duration_seconds": 8,
                              "rows": [{"heading": "제한 시간", "body": "60초"}, {"heading": "", "body": ""}]})
        self.assertEqual(req["rows"], [{"heading": "제한 시간", "body": "60초"}])


class Routing(unittest.TestCase):
    """그림만 온 의뢰: 순위판인지 묻는 답이 'VS 대결'이면 설계도를 받는다(호출 2번). 글의 'VS' 는 바로 설계도로(1번)."""

    def test_그림_속_VS_대결은_순위판으로_눌리지_않고_설계도로(self):
        calls = []
        rank = {"is_rank_board": True, "shape": "versus", "title": "2차 카드 게임 점수", "unit": "", "duration_seconds": None, "unclear": [],
                "rows": [{"rank": None, "name": "A선수", "value_text": "40"}, {"rank": None, "name": "D선수", "value_text": "60"}], "next_title": "", "next_rows": []}
        with mock.patch.dict("os.environ", {"MAPCG_READER": "auto"}):
            r = board.read("", IMAGE, available={"liner": True, "claude": False}, chat=chat_by_task({"rank_board_request": rank, "info_board_plan": designed()}, calls))
        self.assertEqual((r["kind"], r["draft"]["shape"], calls), ("plan", "versus", ["rank_board_request", "info_board_plan"]))

    def test_순위판이라고_하면_그대로_순위판(self):
        calls = []
        rank = {"is_rank_board": True, "shape": "rank", "title": "게임 점수", "unit": "점", "duration_seconds": None, "unclear": [],
                "rows": [{"rank": 1, "name": "가람", "value_text": "120"}, {"rank": 2, "name": "나래", "value_text": "95"}], "next_title": "", "next_rows": []}
        with mock.patch.dict("os.environ", {"MAPCG_READER": "auto"}):
            r = board.read("", IMAGE, available={"liner": True, "claude": False}, chat=chat_by_task({"rank_board_request": rank}, calls))
        self.assertEqual(("kind" in r, calls), (False, ["rank_board_request"]))

    def test_글에_VS_가_있으면_설계도부터(self):
        calls = []
        with mock.patch.dict("os.environ", {"MAPCG_READER": "auto"}):
            r = board.read("2차 카드게임 점수 부탁드립니다\nA선수 40 VS D선수 60\nB선수 50 VS E선수 50", available={"liner": True, "claude": False},
                           chat=chat_by_task({"info_board_plan": designed()}, calls))
        self.assertEqual((r["kind"], calls), ("plan", ["info_board_plan"]))

    def test_Liner_가_없으면_글의_VS_도_설계도를_묻지_않는다(self):
        self.assertFalse(plan.hinted("홍대에서 여의도로") or False)
        r = board.read("A선수 40 VS D선수 60", available={"liner": False})
        self.assertTrue(r is None or "kind" not in r)


class Winner(unittest.TestCase):
    """이긴 쪽: 어느 쪽이 이기는 대결인지는 설계도가 읽고 사람이 확정한다. 숫자 비교만 앱이 한다."""

    def card(self, *rows, **over):
        d = draft()
        d["rows"] = [{"left": {"name": a, "value_text": b}, "right": {"name": c, "value_text": e}} for a, b, c, e in rows]
        d.update(over)
        return d

    def test_작은_값이_이기는_대결은_작은_쪽에_표시한다(self):
        req = plan.normalize(self.card(("가람", "12.5초", "나래", "13.1초"), ("다온", "50", "라온", "50"), better="low"))
        self.assertEqual((req["better"], [r["winner"] for r in req["rows"]]), ("low", ["left", "tie"]))
        self.assertEqual(plan.normalize(self.card(("가람", "12.5초", "나래", "13.1초")))["rows"][0]["winner"], "right")      # 정하지 않으면 큰 값

    def test_단위가_서로_다른_줄은_가리지_않고_알린다(self):
        req = plan.normalize(self.card(("가람", "40kg", "나래", "900g"), ("다온", "3개", "라온", "5개")))
        self.assertEqual([r["winner"] for r in req["rows"]], ["none", "right"])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        job = plan.make_job(req, project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None)
        self.assertEqual(job["warnings"], ["두 값의 단위가 서로 다른 줄은 이긴 쪽을 표시하지 않았습니다."])

    def test_표시하지_않기로_하면_아무_줄도_표시하지_않는다(self):
        req = plan.normalize(self.card(("가람", "40", "나래", "60"), better="none"))
        self.assertEqual(req["rows"][0]["winner"], "none")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(plan.make_job(req, project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None)["warnings"], [])

    def test_모르는_기준은_돌려보낸다(self):
        with self.assertRaises(request.RequestError) as e:
            plan.normalize(self.card(("가람", "40", "나래", "60"), better="fast"))
        self.assertIn("이기는 쪽", " ".join(e.exception.problems))

    def test_설계도가_읽은_기준을_초안에_싣고_확인하라고_알린다(self):
        timed = [{"left_name": "가람", "left_value": "12.5초", "right_name": "나래", "right_value": "13.1초"}]
        read = lambda **over: plan.read("", IMAGE, chat=chat_by_task({"info_board_plan": designed(**over)}, []))      # noqa: E731
        low = read(pairs=timed, better="low")
        self.assertEqual(low["draft"]["better"], "low")
        self.assertTrue(any("작은 쪽이 이기는 대결로 읽었습니다" in n for n in low["notes"]))
        unknown = read(pairs=timed, better="unknown")
        self.assertEqual(unknown["draft"]["better"], "none")          # 시간 기록인데 모르겠다 — 앱이 정하지 않는다
        self.assertTrue(any("시간 기록" in n for n in unknown["notes"]))
        long_wins = read(pairs=timed, better="high")                                            # 버티기처럼 긴 쪽이 이기는 시간 대결
        self.assertEqual(long_wins["draft"]["better"], "high")
        self.assertTrue(any("시간 기록인데 값이 큰(긴) 쪽이 이기는 대결로 읽었습니다" in n for n in long_wins["notes"]))     # 모델이 틀렸을 수 있으니 조용히 넘기지 않는다
        self.assertFalse(any("이기는 쪽" in n for n in read(better="high")["notes"]))            # 점수 대결은 알릴 것이 없다
        self.assertEqual(read(better="unknown")["draft"]["better"], "high")                  # 점수 대결은 큰 값
        self.assertEqual(draft()["better"], "high")                                           # 옛 설계도(칸 없음)도 큰 값


class Job(unittest.TestCase):
    def test_우리_화풍_화면과_기록이_다_들어간다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        job = plan.make_job(plan.normalize(draft()), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None)
        d = job["dir"]
        for name in ("index.html", "job.js", "job.json", "stage.js", "brush.js", "boardskin.js"):
            self.assertTrue((d / name).is_file(), name)
        html = (d / "index.html").read_text(encoding="utf-8")
        self.assertIn("SK.team", html)
        self.assertIn("style_v5.js", html)      # 기본 화풍 V5 — 순위판과 같은 색·글꼴·종이 띠
        js = json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", (d / "job.js").read_text(encoding="utf-8"), re.S).group(1))
        self.assertEqual([r["winner"] for r in js["rows"]], ["right", "tie", "none"])
        self.assertTrue(any("이긴 쪽을 표시하지 않았습니다" in w for w in job["warnings"]))

    def test_V5_화풍이_편의_색을_준다(self):
        self.assertIn("team:", (ROOT / "mapcg" / "templates" / "style_v5.js").read_text(encoding="utf-8"))


class Desk(unittest.TestCase):
    def test_메신저가_설계도_카드를_내고_확정하면_정보판을_만든다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = server.Desk(Path(tmp.name), liner=True, analyze=lambda *a, **k: self.fail("지도 읽기로 넘어갔다"))
        d._spawn = lambda fn, *a, **_: fn(*a)
        # 11-24 부터 그림만 온 의뢰의 못 그린 요청도 메인 에이전트에게 넘긴다 — 시험에서는 메인을 부르지 않게 막는다(실제 Liner 로 나가지 않게)
        with mock.patch.object(board, "read", lambda text, images, available=None: {**plan.read("", IMAGE, chat=chat_by_task({"info_board_plan": designed()}, [])), "kind": plan.KIND}), \
                mock.patch.object(server.scene, "plan_request", return_value={"main_ok": False, "problems": ["시험: 메인 부르지 않음"]}):
            d.send("", [{"name": "의뢰지.jpg", "data": "data:image/jpeg;base64,/9j/AAAA", "images": ["data:image/jpeg;base64,/9j/AAAA"]}])
        card = next(m for m in d.msgs if m["kind"] == "plan")
        self.assertEqual((card["kind"], card["draft"]["shape"]), ("plan", "versus"))
        self.assertTrue(card["draft"]["rid"].startswith("의뢰-"))
        d.make(card["draft"])
        res = d.msgs[-1]
        self.assertEqual((res["kind"], res["request"]["kind"], res["request"]["rid"]), ("result", "plan", card["draft"]["rid"]))
        self.assertTrue((Path(tmp.name) / "output" / res["job"] / "index.html").is_file())
        d.revise(res["request"], "제목을 바꿔 줘")        # 설계도 정보판은 아직 수정 지시를 받지 않는다 — 지금 설계도로 카드를 낸다
        self.assertEqual((d.msgs[-2]["state"], d.msgs[-1]["kind"]), ("fail", "plan"))


def building(**over):
    """손그림 의뢰지 — B2 주차장에서 계단으로 1층, 엘리베이터까지 걸어서, 엘리베이터로 목표층(가짜 설계도)."""
    floors = [{"label": "B2", "wide": True, "kind": "parking"}, {"label": "B1", "wide": True, "kind": "floor"}]
    floors += [{"label": f"{i}F", "wide": i <= 4, "kind": "floor"} for i in range(1, 9)] + [{"label": "목표층", "wide": False, "kind": "floor"}]
    return designed(shape="building", title="", pairs=[], icon_left="none", unsupported=[], duration_seconds=8, floors=floors,
                    steps=[{"floor": "B2", "how": "start", "caption": "주차"}, {"floor": "1F", "how": "stairs", "caption": "이동"},
                           {"floor": "1F", "how": "walk", "caption": "엘베"}, {"floor": "목표층", "how": "elevator", "caption": ""}],
                    people=2, people_names=[], **over)


class Building(unittest.TestCase):
    TEXT = "건물 내부 이동CG의뢰드립니다. 8초고 주차장에서 부터 엘베타고 올라가면됩니다."

    def test_글의_엘베_건물_내부는_설계도로_바로(self):
        calls = []
        with mock.patch.dict("os.environ", {"MAPCG_READER": "auto"}):
            r = board.read(self.TEXT, IMAGE, available={"liner": True, "claude": False}, chat=chat_by_task({"info_board_plan": building()}, calls))
        self.assertEqual((r["kind"], r["draft"]["shape"], calls), ("plan", "building", ["info_board_plan"]))

    def test_그림만_온_건물_단면도도_순위판으로_눌리지_않는다(self):
        calls = []
        rank = {"is_rank_board": False, "shape": "building", "title": "", "unit": "", "duration_seconds": None, "unclear": [], "rows": [], "next_title": "", "next_rows": []}
        with mock.patch.dict("os.environ", {"MAPCG_READER": "auto"}):
            r = board.read("", IMAGE, available={"liner": True, "claude": False}, chat=chat_by_task({"rank_board_request": rank, "info_board_plan": building()}, calls))
        self.assertEqual((r["kind"], calls), ("plan", ["rank_board_request", "info_board_plan"]))

    def test_마지막_단계의_층이_목표층이고_출연진_수대로(self):
        d = plan.read(self.TEXT, IMAGE, chat=chat_by_task({"info_board_plan": building()}, []))["draft"]
        self.assertEqual([f["label"] for f in d["floors"] if f["kind"] == "goal"], ["목표층"])
        self.assertEqual((len(d["people"]), d["duration_seconds"], d["rows"]), (2, 8, []))
        req = plan.normalize(d)         # 제목이 없어도 된다(제목판 없이)
        self.assertEqual([s["how"] for s in req["steps"]], ["start", "stairs", "walk", "elevator"])

    def test_층_목록에_없는_층으로_가거나_출발이_아니면_돌려보낸다(self):
        d = plan.read(self.TEXT, IMAGE, chat=chat_by_task({"info_board_plan": building()}, []))["draft"]
        d["steps"][-1]["floor"] = "옥상"
        d["steps"][0]["how"] = "walk"
        with self.assertRaises(request.RequestError) as e:
            plan.normalize(d)
        said = " ".join(e.exception.problems)
        self.assertIn("'옥상' 이(가) 층 목록에 없습니다", said)
        self.assertIn("첫 단계는 '출발'", said)

    def test_건물_화면으로_만든다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = plan.read(self.TEXT, IMAGE, chat=chat_by_task({"info_board_plan": building()}, []))["draft"]
        job = plan.make_job(plan.normalize(d), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None)
        html = (job["dir"] / "index.html").read_text(encoding="utf-8")
        self.assertIn("건물 안 이동", html)
        js = json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", (job["dir"] / "job.js").read_text(encoding="utf-8"), re.S).group(1))
        self.assertEqual((len(js["floors"]), len(js["steps"]), len(js["people"])), (11, 4, 2))


if __name__ == "__main__":
    unittest.main()
