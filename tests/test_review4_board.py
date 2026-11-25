"""코드리뷰 a27d633 의 1번(오프라인 재접수) · 8번(이미지 의뢰의 규격) 회귀 시험.

Liner 는 부르지 않는다(liner.chat 자리에 가짜를 끼운다). 대화방은 임시 폴더에 만든다(사용자 대화방 data/messenger 를 쓰지 않는다).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, intake, plan, quiz, server

PICTURE = "data:image/png;base64,AAAA"       # 의뢰서 그림 자리(내용은 보지 않는다)
ATTACHED = [{"name": "의뢰서.png", "data": PICTURE, "images": [PICTURE]}]
REQUEST = "첨부한 의뢰서대로 만들어 주세요."
CHOSEN = ("rules", "versus", "building", "card_quiz")       # '틀이 없음' 카드에서 고르는 종류: 설명판 · VS 대결판 · 건물 안 이동 · 카드 퀴즈
SPEC = {"output_width": 1280, "output_height": 720, "fps": 25}      # 의뢰서 그림에만 적힌 납품 규격
BASIC = {"width": 1920, "height": 1080, "fps": 30}

RANK = {"is_rank_board": True, "shape": "rank", "title": "게임 점수 순위", "unit": "점", "next_title": "", "next_rows": [],
        "rows": [{"rank": 1, "name": "김가상", "value_text": "1,250"}, {"rank": 2, "name": "이가상", "value_text": "1,180"}],
        "duration_seconds": 8, "unclear": []}
PLAN = {"shape": "none", "title": "청백전", "left_label": "", "right_label": "", "left_color": "none", "right_color": "none",
        "pairs": [{"left_name": "가람", "left_value": "40점", "right_name": "나래", "right_value": "60점"}], "better": "high",
        "items": [{"heading": "규칙 1", "body": "공을 떨어뜨리면 탈락"}], "icon_left": "none", "icon_right": "none",
        "floors": [{"label": "B2", "wide": True, "kind": "parking"}, {"label": "1F", "wide": False, "kind": "floor"}],
        "steps": [{"floor": "B2", "how": "start", "caption": "주차"}, {"floor": "1F", "how": "stairs", "caption": ""}],
        "people": 2, "people_names": [], "duration_seconds": 8, "unsupported": [], "unclear": []}
QUIZ = {"title": "카드 퀴즈", "question": "Q1. 세 그림의 공통점은?", "clue_line": "날씨를 떠올려 보세요",
        "cards": [{"kind": "image", "subject": s, "text": "", "syllable": "", "reveal": ""} for s in ("우산", "구름", "장화")],
        "duration_seconds": 9, "first_answer": "", "final_answer": "비", "first_at": None, "final_at": 7.5,
        "output_width": None, "output_height": None, "fps": None, "unclear": []}
ANSWERS = {"rank_board_request": RANK, "info_board_plan": PLAN, "card_quiz_request": QUIZ}


class FakeLiner:
    """liner.chat 자리에 끼우는 가짜. 물음의 이름별로 정해 둔 답을 주고, 불린 이름과 받은 스키마를 적어 둔다."""

    def __init__(self, answers=ANSWERS):
        self.answers, self.calls, self.schemas = answers, [], {}

    def __call__(self, messages, **options):
        fmt = options["response_format"]["json_schema"]
        self.calls.append(fmt["name"])
        self.schemas[fmt["name"]] = fmt["schema"]
        return {"model": "시험", "choices": [{"message": {"content": json.dumps(self.answers[fmt["name"]], ensure_ascii=False)},
                                             "finish_reason": "stop"}]}


class DeskCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"MAPCG_ROOM": "", "MAPCG_READER": "auto"})      # PC 의 녹화방 · 읽는 쪽 고정 설정이 끼어들지 않게
        env.start()
        self.addCleanup(env.stop)

    def desk(self, **how):
        return server.Desk(Path(tempfile.mkdtemp(dir=self.root)), **how)

    def send(self, desk, liner, text, prefer=""):
        """첨부(의뢰서 그림 한 장)가 있는 의뢰를 보낸다. prefer = 사람이 골라 다시 보낸 종류."""
        with mock.patch.object(intake._liner(), "chat", liner), \
                mock.patch.object(desk, "_spawn", side_effect=lambda fn, *args, **kw: fn(*args)):
            desk.send(text, ATTACHED, room=desk.room, prefer=prefer)
        return desk


class OfflineResend(DeskCase):
    """1번: 종류를 직접 골라 다시 보낸 의뢰도 '실제로 쓸 수 있는 판독기' 검사를 거친다."""

    def test_오프라인에서_종류를_골라_다시_보내면_Liner를_부르지_않고_이유를_알린다(self):
        for kind in CHOSEN:
            with self.subTest(kind=kind):
                liner = FakeLiner()
                desk = self.send(self.desk(offline=True), liner, REQUEST, kind)
                self.assertEqual(liner.calls, [])       # 글과 첨부가 밖으로 나가지 않는다
                self.assertEqual([m["kind"] for m in desk.msgs], ["text", "progress"])       # 확인 카드를 내지 않는다
                last = desk.msgs[-1]
                self.assertEqual(last["state"], "fail")
                self.assertIn(intake.NEAREST[kind][0], last["error"])       # 카드에 고른 종류와 까닭이 보인다
                self.assertIn("Liner", last["error"])

    def test_온라인에서는_고른_종류로_접수된다(self):
        for kind in CHOSEN:
            with self.subTest(kind=kind):
                liner = FakeLiner()
                card = self.send(self.desk(liner=True), liner, REQUEST, kind).msgs[-1]
                if kind == "card_quiz":
                    self.assertEqual((card["kind"], liner.calls), (quiz.KIND, ["card_quiz_request"]))
                else:
                    self.assertEqual((card["kind"], card["draft"]["shape"], liner.calls), ("plan", kind, ["info_board_plan"]))

    def test_읽는_쪽_설정이_Liner가_아니면_키가_있어도_부르지_않는다(self):
        for kind in CHOSEN:
            with self.subTest(kind=kind):
                liner = FakeLiner()
                with mock.patch.dict(os.environ, {"MAPCG_READER": "simple"}), self.assertRaises(board.Unsure):
                    board.read(REQUEST, [PICTURE], available={"liner": True, "claude": False}, chat=liner, prefer=kind)
                self.assertEqual(liner.calls, [])


class PictureSpec(DeskCase):
    """8번: 의뢰서 그림에만 적힌 납품 규격(가로 · 세로 · fps)이 순위판 · 정보판 확인 카드까지 간다."""

    def cards(self, text, spec):
        """같은 그림 의뢰를 순위판 · 정보판(VS 대결판)으로 한 번씩 접수해, 두 확인 카드의 초안과 Liner 가 받은 두 스키마를 돌려준다."""
        rank = FakeLiner({"rank_board_request": {**RANK, **spec}})
        versus = FakeLiner({"rank_board_request": {**RANK, "is_rank_board": False, "shape": "versus", "rows": []},
                            "info_board_plan": {**PLAN, "shape": "versus", **spec}})
        b = self.send(self.desk(liner=True), rank, text).msgs[-1]
        p = self.send(self.desk(liner=True), versus, text).msgs[-1]
        self.assertEqual((b["kind"], p["kind"], p["draft"]["shape"]), ("board", "plan", "versus"))
        return b["draft"], p["draft"], (rank.schemas["rank_board_request"], versus.schemas["info_board_plan"])

    def test_그림에만_적힌_규격이_두_확인_카드에_나온다(self):
        b, p, schemas = self.cards("", SPEC)
        want = {"width": 1280, "height": 720, "fps": 25}
        self.assertEqual((b["output"], p["output"]), (want, want))
        for schema in schemas:      # 두 판독 스키마가 규격을 묻는다(strict — 전부 required)
            for field in SPEC:
                self.assertEqual(schema["properties"][field]["type"], ["integer", "null"])
                self.assertIn(field, schema["required"])
        # 확인 카드에서 그대로 확정하면 의뢰 봉투에도 같은 규격이 실린다
        self.assertEqual((board.normalize(b)["output"], plan.normalize(p)["output"]), (want, want))

    def test_별도_글이_fps만_정하면_해상도는_그림대로_fps만_바뀐다(self):
        b, p, _ = self.cards("첨부한 의뢰서대로 부탁드립니다. 50fps 로 내보내 주세요.", SPEC)
        want = {"width": 1280, "height": 720, "fps": 50}
        self.assertEqual((b["output"], p["output"]), (want, want))

    def test_별도_글이_해상도만_정하면_fps는_그림대로다(self):
        b, p, _ = self.cards("첨부한 의뢰서대로 부탁드립니다. 크기는 960*540 입니다.", SPEC)
        want = {"width": 960, "height": 540, "fps": 25}
        self.assertEqual((b["output"], p["output"]), (want, want))

    def test_규격이_없는_의뢰는_종전_기본값이다(self):
        for spec in ({}, dict.fromkeys(SPEC)):      # 예전 모양의 답(칸이 없음) · 칸은 있고 비어 있음(null)
            with self.subTest(spec=spec):
                b, p, _ = self.cards("", spec)
                self.assertEqual((b["output"], p["output"]), (BASIC, BASIC))

    def test_받을_수_없는_규격은_싣지_않고_알린다(self):
        odd = {"output_width": 99999, "output_height": 720, "fps": 120}
        b = board.read("", [PICTURE], available={"liner": True}, chat=FakeLiner({"rank_board_request": {**RANK, **odd}}))
        p = plan.read("", [PICTURE], chat=FakeLiner({"info_board_plan": {**PLAN, "shape": "versus", **odd}}))
        for got in (b, p):
            self.assertEqual(got["draft"]["output"], BASIC)
            self.assertEqual(sum(n.startswith("확인 필요: 의뢰서에서 읽은") for n in got["notes"]), 2)       # 크기 한 줄 · fps 한 줄


if __name__ == "__main__":
    unittest.main()
