"""모르는 종류 · 못 그리는 요청을 조용히 빼지 않고 알린다(2026-11-21 실측한 세 의뢰를 가짜 응답으로 옮겼다. Liner 를 부르지 않는다)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, intake, plan, quiz, scene, server

BRACKET = "8강 토너먼트 대진표 만들어 주세요. 1경기 가람 대 나래, 2경기 다온 대 라온, 3경기 마루 대 바다, 4경기 사랑 대 아라. 이긴 사람이 위로 올라가는 선으로. 10초"
RUN = ("러닝 크루 5km 코스 지도 부탁해요. 여의도공원에서 출발해 서강대교 남단, 여의나루역을 지나 다시 여의도공원으로 돌아옵니다. "
       "1km마다 거리 표시해 주시고 페이스 6분/km 문구도 넣어 주세요. 10초")


def STOP(name, **over):
    """지도 읽기의 route_stops 한 칸(지나가기만 하는 곳)."""
    return {"name": name, "caption": "", "pass": True, "stop": False, "hold_seconds": None, **over}


def reading(**over):
    """지도 읽기(map_request) 응답 하나. 적히지 않은 칸은 비워 둔다."""
    base = {k: None for k in intake.SCHEMA["properties"]}
    base.update(map_style="unknown", vehicle="unknown", layout="unknown", face_mode="unknown", camera="unknown", separate_moves=False,
                cuts=[], unclear=[], nearest="none", route_stops=[], unsupported=[])
    base.update(over)
    return base


def chat_saying(answer):
    def chat(messages, **options):
        return {"model": "gpt-6-sol", "choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}, "finish_reason": "stop"}]}
    return chat


def analyze(answer, text=""):
    return intake.analyze(text, [], chat=chat_saying(answer), order=["liner"])


class Read(unittest.TestCase):
    def test_지도가_아닌_종류는_이름과_가장_가까운_틀을_돌려준다(self):
        r = analyze(reading(other_kind="토너먼트 대진표", nearest="versus", duration_seconds=10), BRACKET)
        self.assertEqual((r["other_kind"], r["nearest"]), ("토너먼트 대진표", "versus"))
        self.assertEqual((r["draft"]["from"]["name"], r["draft"]["to"]["name"]), ("", ""))

    def test_모르는_대안은_none_이다(self):
        r = analyze(reading(other_kind="꺾은선 그래프", nearest="pie"))
        self.assertEqual(r["nearest"], "none")

    def test_한_화면_지도의_경유지는_그리고_못_그리는_요청만_알린다(self):
        # 11-24: 한 화면 지도도 지나는 곳을 지도에 표시하고 경로가 차례로 지난다(전에는 '컷을 나눠 주세요'로 빠졌다)
        r = analyze(reading(from_name="여의도공원", to_name="여의도공원", duration_seconds=10, vehicle="walk",
                            route_stops=[STOP("서강대교 남단"), STOP("여의나루역")], unsupported=["1km마다 거리 표시", "페이스 6분/km 문구"]), RUN)
        lines = [n for n in r["notes"] if n.startswith("반영하지 못한 요청")]
        self.assertEqual(len(lines), 2)
        self.assertIn("1km마다 거리 표시", lines[0])
        self.assertEqual([s["name"] for s in r["draft"]["stops"]], ["서강대교 남단", "여의나루역"])
        self.assertTrue(any(n.startswith("지나는 곳 2곳을 지도에 표시") for n in r["notes"]))
        self.assertTrue(any("출발지와 도착지가 같은 '여의도공원'" in n for n in r["notes"]))
        self.assertEqual(r["unmade"], ["1km마다 거리 표시", "페이스 6분/km 문구"])
        self.assertEqual(r["other_kind"], "")

    def test_컷을_나눈_의뢰의_경유지는_컷이_그린다(self):
        cuts = [{"who": "", "from_name": "서울역", "to_name": "시청", "duration_seconds": 4, "bridge": False, "closeup": False, "stops": [], "note": ""},
                {"who": "", "from_name": "시청", "to_name": "광화문", "duration_seconds": 4, "bridge": False, "closeup": False, "stops": [], "note": ""}]
        r = analyze(reading(from_name="서울역", to_name="광화문", cuts=cuts, route_stops=[STOP("시청")]))
        self.assertFalse(any(n.startswith("반영하지 못한 요청") for n in r["notes"]))
        self.assertEqual(r["unmade"], [])
        self.assertNotIn("stops", r["draft"])          # 컷을 나눈 의뢰는 컷마다 들르는 곳을 쓴다

    def test_지명이_있는데_다른_종류로도_보이면_카드는_내고_알린다(self):
        r = analyze(reading(from_name="잠실", to_name="고척", other_kind="경기 일정표", nearest="rules"))
        self.assertTrue(any("'경기 일정표'" in n and "설명판" in n for n in r["notes"]))

    def test_모델_없이_읽으면_알림_칸은_비어_있다(self):
        r = intake.analyze("홍대에서 강남까지 8초", [], order=[])
        self.assertEqual((r["other_kind"], r["nearest"], r["unmade"]), ("", "", []))


class Prefer(unittest.TestCase):
    """'틀이 없음' 카드의 단추로 사람이 고른 틀(prefer)로 다시 읽는다.
    글 끝에 'VS 대결판으로 만들어 주세요'를 붙여 보내면 모델이 자료로만 읽고 'none' 이라 답했다(2026-11-21 실측) — 선택은 지시문 쪽으로 넘긴다."""

    def asked(self, answers):
        calls = []

        def chat(messages, **options):
            name = options["response_format"]["json_schema"]["name"]
            calls.append((name, messages[0]["content"]))
            return {"model": "gpt-6-sol", "choices": [{"message": {"content": json.dumps(answers[name], ensure_ascii=False)}, "finish_reason": "stop"}]}
        return chat, calls

    PLAN = {"shape": "none", "title": "8강 대진표", "better": "unknown", "unsupported": ["이긴 사람이 위로 올라가는 선"],
            "pairs": [{"left_name": "가람", "left_value": "", "right_name": "나래", "right_value": ""},
                      {"left_name": "다온", "left_value": "", "right_name": "라온", "right_value": ""}]}

    def test_고른_정보판_모양으로_설계도를_받는다(self):
        chat, calls = self.asked({"info_board_plan": self.PLAN})
        r = board.read(BRACKET, available={"liner": True, "claude": False}, chat=chat, prefer="versus")
        self.assertEqual((r["kind"], r["draft"]["shape"], len(r["draft"]["rows"])), (plan.KIND, "versus", 2))
        self.assertEqual(r["unmade"], ["이긴 사람이 위로 올라가는 선"])
        self.assertEqual([c[0] for c in calls], ["info_board_plan"])         # 순위판인지 다시 묻지 않는다
        self.assertIn("제작진의 선택", calls[0][1])                           # 선택은 지시문 쪽에
        self.assertIn('shape 는 "versus"', calls[0][1])

    def test_고른_것이_없으면_지시문에_선택이_없다(self):
        chat, calls = self.asked({"info_board_plan": {**self.PLAN, "shape": "versus"}})
        plan.read(BRACKET, chat=chat)
        self.assertNotIn("제작진의 선택", calls[0][1])

    def test_순위판으로_고르면_아니라는_답에도_순위판으로_읽는다(self):
        rank = {"is_rank_board": False, "shape": "other", "title": "", "unit": "", "next_title": "", "next_rows": [], "duration_seconds": 10, "unclear": [],
                "rows": [{"rank": None, "name": "가람", "value_text": "3승"}, {"rank": None, "name": "나래", "value_text": "2승"}]}
        chat, calls = self.asked({"rank_board_request": rank})
        r = board.read("가람 3승 나래 2승 그래프", available={"liner": True, "claude": False}, chat=chat, prefer="rank_board")
        self.assertEqual((r["source"], [x["name"] for x in r["draft"]["rows"]]), ("liner", ["가람", "나래"]))
        self.assertIn("is_rank_board 는 true", calls[0][1])

    def test_카드_퀴즈로_고르면_퀴즈로_읽는다(self):
        with mock.patch.object(quiz, "read", return_value={"kind": quiz.KIND, "draft": {}, "source": "liner", "notes": []}) as read:
            r = board.read("그림 맞히기", available={"liner": True}, prefer="card_quiz")
        self.assertEqual(r["kind"], quiz.KIND)
        read.assert_called_once()

    def test_모르는_값은_보통처럼_읽는다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        desk = server.Desk(Path(tmp.name), liner=False)
        with mock.patch.object(desk, "_start") as start:
            desk.send("홍대에서 강남까지", [], room=desk.room, prefer="그래프")
        self.assertEqual(start.call_args[0][-1], "")
        self.assertNotIn("read_as", desk.msgs[-1])


class Messenger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = mock.Mock(return_value={"state": "skipped", "reason": "시험"})

    def send(self, text, answer):
        desk = server.Desk(self.root, liner=True, audit=self.audit,
                           analyze=lambda t, images: intake.analyze(t, images, chat=chat_saying(answer), order=["liner"]))
        # 메인 에이전트(맞춤 그래픽)는 지시서를 못 쓴 것으로 둔다 — 여기서는 '틀 없음' 카드를 본다(맞춤 그래픽 갈래는 test_scene)
        with mock.patch.object(desk, "_spawn", side_effect=lambda fn, *args, **kw: fn(*args)), \
                mock.patch.object(board, "read", return_value=None), \
                mock.patch.object(scene, "plan_request", return_value={"main_ok": False, "problems": ["시험"]}):
            desk.send(text, [], room=desk.room)
        return desk

    def log(self):
        path = self.root / "data" / server.Desk.UNMADE_LOG
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def test_대진표는_빈_지도_카드_대신_틀이_없다고_알린다(self):
        desk = self.send(BRACKET, reading(other_kind="토너먼트 대진표", nearest="versus", duration_seconds=10))
        kinds = [m["kind"] for m in desk.msgs]
        self.assertNotIn("card", kinds)
        m = desk.msgs[-1]
        self.assertEqual((m["kind"], m["what"], m["alt"]), ("unknown", "토너먼트 대진표", "VS 대결판"))
        self.assertEqual((m["retry"], m["nearest"]), (BRACKET, "versus"))       # 같은 글 + 고른 틀(보낼 때 as 로 실린다)
        self.assertIn("아직 틀이 없습니다", desk.msgs[-2]["title"])
        self.audit.assert_not_called()          # 감사할 지도 값이 없다 — 호출을 아낀다
        got = self.log()
        self.assertEqual([(g["what"], g["kind"], g["nearest"]) for g in got], [("모르는 종류", "토너먼트 대진표", "versus")])
        self.assertNotIn("가람", json.dumps(got, ensure_ascii=False))      # 의뢰 글은 기록하지 않는다

    def test_대안이_없으면_다시_보낼_글도_없다(self):
        m = self.send("우리 팀 꺾은선 그래프", reading(other_kind="꺾은선 그래프", nearest="none")).msgs[-1]
        self.assertEqual((m["kind"], m["alt"], m["retry"], m["nearest"]), ("unknown", "", "", "none"))

    def test_러닝_코스는_지도_카드를_내고_빠진_요청을_알리고_기록한다(self):
        desk = self.send(RUN, reading(from_name="여의도공원", to_name="여의도공원", duration_seconds=10, vehicle="walk",
                                      route_stops=[STOP("서강대교 남단"), STOP("여의나루역")], unsupported=["1km마다 거리 표시", "페이스 6분/km 문구"]))
        card = next(m for m in desk.msgs if m["kind"] == "card")
        self.assertEqual(desk.msgs[-1]["kind"], "progress")         # 확인 카드 뒤에 남은 요청을 메인 에이전트가 본다(여기서는 못 쓴 것으로 둔다)
        self.assertIn("위 확인 카드는 그대로", desk.msgs[-1]["error"])
        self.assertEqual(sum(n.startswith("반영하지 못한 요청") for n in card["notes"]), 2)       # 지나는 곳은 이제 지도가 그린다
        self.audit.assert_called_once()
        got = self.log()
        self.assertEqual([(g["what"], g["kind"], len(g["items"])) for g in got], [("반영 못 한 요청", "지도", 2)])

    def test_빠진_요청이_없으면_기록하지_않는다(self):
        self.send("홍대에서 강남까지 8초", reading(from_name="홍대", to_name="강남", duration_seconds=8))
        self.assertEqual(self.log(), [])

    def test_단추로_고른_틀은_읽는_쪽까지_넘어간다(self):
        desk = server.Desk(self.root, liner=True)
        designed = {"kind": plan.KIND, "source": "liner", "notes": [], "unmade": [], "draft": {"kind": plan.KIND, "shape": "versus", "title": "8강", "rows": []}}
        with mock.patch.object(desk, "_spawn", side_effect=lambda fn, *args, **kw: fn(*args)), \
                mock.patch.object(board, "read", return_value=designed) as read:
            desk.send(BRACKET, [], room=desk.room, prefer="versus")
        self.assertEqual(read.call_args.kwargs["prefer"], "versus")
        asked = next(m for m in desk.msgs if m["kind"] == "text")
        self.assertEqual(asked["read_as"], "VS 대결판")
        self.assertEqual(desk.msgs[-1]["kind"], "plan")

    def test_정보판의_못_그리는_요청도_기록한다(self):
        desk = server.Desk(self.root, liner=True)
        designed = {"kind": plan.KIND, "source": "liner", "notes": ["반영하지 못하는 요청: 반짝이 효과 — 우리 부품으로 그릴 수 없어 빼고 만듭니다."],
                    "unmade": ["반짝이 효과"], "draft": {"kind": plan.KIND, "shape": "versus", "title": "", "rows": []}}
        with mock.patch.object(desk, "_spawn", side_effect=lambda fn, *args, **kw: fn(*args)), \
                mock.patch.object(board, "read", return_value=designed), \
                mock.patch.object(scene, "plan_request", return_value={"main_ok": False, "problems": ["시험"]}) as main:
            desk.send("가람 VS 나래", [], room=desk.room)
        self.assertEqual([m["kind"] for m in desk.msgs][-2:], ["plan", "progress"])
        self.assertIn("반짝이 효과", main.call_args.kwargs["focus"])          # 남은 요청만 메인에게 보인다
        self.assertEqual([(g["kind"], g["items"]) for g in self.log()], [("VS 대결판", ["반짝이 효과"])])


if __name__ == "__main__":
    unittest.main()
