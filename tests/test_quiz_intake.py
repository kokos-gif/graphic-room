"""카드 퀴즈의 그림 분류, 제작진 정정, 공개 시각 검사. API는 호출하지 않는다."""
import json
import unittest
from unittest import mock

from mapcg import board, intake, quiz, request


def brief():
    cards = [{"kind": kind, "subject": subject, "text": text, "syllable": syllable, "reveal": ""}
             for kind, subject, text, syllable in [
                 ("image", "빛나는 여의주", "", "여"), ("image", "의자", "", "의"), ("image", "도자기", "", "도"),
                 ("text", "", "ㄱㄴㄷ", "한"), ("missing", "", "?", "?"), ("image", "농구공", "", "공"),
                 ("missing", "", "?", "?")]]
    return {"title": "카드 퀴즈", "question": "다음 카드가 뜻하는 것을 맞춰 보세요", "clue_line": "여의도 한?공?",
            "cards": cards, "duration_seconds": 15, "first_answer": "", "final_answer": "강원",
            "first_at": None, "final_at": None, "unclear": []}


class QuizIntake(unittest.TestCase):
    def read(self, text="", got=None):
        calls = []

        def chat(messages, **options):
            fmt = options["response_format"]["json_schema"]
            name = fmt["name"]
            calls.append(name)
            self.assertTrue(any(p.get("type") == "image_url" for p in messages[-1]["content"]))
            if name == "card_quiz_request":
                self.assertTrue(fmt["strict"])
                for field in ("output_width", "output_height", "fps"):
                    self.assertIn(field, fmt["schema"]["required"])
                    self.assertEqual(fmt["schema"]["properties"][field]["type"], ["integer", "null"])
            reply = ({"shape": "card_quiz", "is_rank_board": False} if name == "rank_board_request"
                     else brief() if got is None else got)
            return {"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]}

        with mock.patch.object(intake, "route", return_value=["liner"]):
            result = board.read(text, ["data:image/jpeg;base64,AAAA"], available={"liner": True}, chat=chat)
        self.assertEqual(result["kind"], quiz.KIND)
        self.assertEqual(result["source"], "liner")
        return result["draft"], calls

    def test_image_only_classifies_before_reading_and_supports_one_answer(self):
        draft, calls = self.read()
        self.assertEqual(calls, ["rank_board_request", "card_quiz_request"])
        self.assertEqual((draft["first_answer"], draft["first_at"]), ("", None))
        self.assertEqual(draft["clue_line"], "여의도 한?공?")
        self.assertEqual(quiz.normalize(draft)["final_answer"], "강원")
        self.assertEqual(draft["output"], {"width": 1920, "height": 1080, "fps": 30})
        with self.assertRaisesRegex(request.RequestError, "최종 답"):
            quiz.normalize(dict(draft, final_answer=""))

    def test_weather_brief_keeps_final_only_reveal_and_printed_delivery_spec(self):
        got = {"title": "비 오는 날", "question": "Q3. 세 그림의 공통점은?", "clue_line": "날씨를 떠올려 보세요",
               "first_answer": "", "final_answer": "비", "first_at": None, "final_at": 7.5, "duration_seconds": 9,
               "cards": [{"kind": "image", "subject": name} for name in ("우산", "구름", "장화")],
               "output_width": 1280, "output_height": 720, "fps": 24}
        draft, calls = self.read(got=got)
        self.assertEqual(calls, ["rank_board_request", "card_quiz_request"])
        req = quiz.normalize(draft)
        self.assertEqual(req["output"], {"width": 1280, "height": 720, "fps": 24})
        self.assertEqual((req["first_at"], req["final_at"], req["final_answer"]), (None, 7.5, "비"))
        self.assertEqual([c["subject"] for c in req["cards"]], ["우산", "구름", "장화"])
        self.assertEqual(req["clue_line"], got["clue_line"])
        for text, expected in [("1920×1080로 수정", (1920, 1080, 24)), ("30fps로 수정", (1280, 720, 30))]:
            with self.subTest(text=text):
                changed, _ = self.read(text, got)
                self.assertEqual(changed["output"], dict(zip(("width", "height", "fps"), expected)))
        invalid, _ = self.read(got=dict(got, fps=120))
        with self.assertRaisesRegex(request.RequestError, "출력 규격"):
            quiz.normalize(invalid)

    def test_operator_can_omit_preliminary_answer_and_its_old_timing(self):
        draft, _ = self.read("카드 퀴즈. 1차 답 없음. 최종 답: 서울",
                             dict(brief(), first_answer="한강공원", first_at=4))
        self.assertEqual((draft["first_answer"], draft["first_at"], draft["final_answer"]), ("", None, "서울"))
        self.assertIsNone(quiz.normalize(dict(draft, first_at=4))["first_at"])
        with self.assertRaisesRegex(request.RequestError, "공개 시각"):
            quiz.normalize(dict(draft, final_at=14.999))

    def test_operator_text_overrides_printed_answers_without_a_second_classification(self):
        got = dict(brief(), first_answer="여의도 한강공원", final_answer="부산")
        draft, calls = self.read("카드 퀴즈. 1차 답은 한강공원, 최종 답 강원은 맨 마지막에 공개.", got)
        req = quiz.normalize(draft)
        self.assertEqual(calls, ["card_quiz_request"])
        self.assertEqual((req["first_answer"], req["final_answer"]), ("한강공원", "강원"))
        self.assertEqual((req["first_at"], req["final_at"], req["duration_seconds"]), (10.3, 13.5, 15))
        self.assertEqual([c["reveal"] for c in req["cards"] if c["kind"] == "missing"], ["강", "원"])
        self.assertEqual([c["subject"] for c in req["cards"] if c["kind"] == "image"], ["빛나는 여의주", "의자", "도자기", "농구공"])
        self.assertEqual(req["cards"][3]["text"], "ㄱㄴㄷ")

    def test_confirmation_preserves_another_quiz_and_checks_actual_frames(self):
        got = dict(brief(), first_answer="빨간 종이", final_answer="종",
                   cards=[{"kind": "image", "subject": "종이접기", "syllable": "이"}, {"kind": "missing", "reveal": "종"}])
        draft, _ = self.read("카드 퀴즈\n1차 답 공개 시각: 5초\n최종 답 공개 시각: 8.5초", got)
        self.assertEqual((draft["first_at"], draft["final_at"], draft["first_answer"]), (5, 8.5, "빨간 종이"))
        self.assertEqual(quiz.normalize(draft)["cards"][0]["subject"], "종이접기")
        # 30fps의 249번째 프레임은 JS에서 정확히 8.3초다. 8.31초는 다음 프레임에서 공개된다.
        self.assertEqual(quiz.normalize(dict(draft, first_at=8.3, final_at=8.31))["first_at"], 8.3)
        for first, final in ((9, 9), (9, 8), (9, 15), (9.01, 9.02), (9, 14.999), (float("nan"), 12)):
            with self.subTest(first=first, final=final), self.assertRaisesRegex(request.RequestError, "공개 시각"):
                quiz.normalize(dict(draft, first_at=first, final_at=final))

    def test_unreadable_slot_is_present_for_correction_and_rejects_unknown_kind(self):
        draft, _ = self.read("카드 퀴즈\n1차 답: 한강공원")
        draft["cards"][1]["subject"] = ""
        draft["cards"][3]["kind"] = "url"
        with self.assertRaises(request.RequestError) as exc:
            quiz.normalize(draft)
        self.assertIn("2번", str(exc.exception))
        self.assertIn("4번", str(exc.exception))

    def test_quiz_game_ranking_table_still_uses_local_reader(self):
        result = board.read("카드 퀴즈 점수표\n이름\t점수\n파랑팀\t30\n노랑팀\t20")
        self.assertEqual(result["source"], "simple")
        self.assertEqual([row["name"] for row in result["draft"]["rows"]], ["파랑팀", "노랑팀"])

    def test_printed_card_reveals_can_differ_from_final_answer(self):
        got = dict(brief(), first_answer="한강공원", final_answer="서울")
        got["cards"][4]["reveal"] = "강"
        got["cards"][6]["reveal"] = "원"
        draft, _ = self.read("카드 퀴즈", got)
        req = quiz.normalize(draft)
        self.assertEqual(req["final_answer"], "서울")
        self.assertEqual([c["reveal"] for c in req["cards"] if c["kind"] == "missing"], ["강", "원"])

    def test_quiz_reader_failure_stops_dispatch(self):
        with mock.patch.object(intake, "route", return_value=["liner"]), \
             mock.patch.object(intake, "ask_liner", side_effect=ValueError("접수 불명")) as call:
            with self.assertRaisesRegex(board.Unsure, "카드 퀴즈"):
                board.read("카드 퀴즈", ["image"], available={"liner": True})
        self.assertEqual(call.call_count, 1)
