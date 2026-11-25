"""판독 감사 — 감사 모델 고르기, 판정 다듬기, 호출 경로(가짜 응답으로). 실제 모델은 부르지 않는다."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from mapcg import audit, intake, server

TEXT = "〈오늘도 출발〉 김가상 조연출입니다. 출연진 4명이 홍대에서 여의도로 버스로 이동, 총 10초, 좌하단, 960*540"
BOTH = {"liner": True, "claude": True}


def reply(obj) -> dict:
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


def reading(source="liner", **over) -> dict:
    """읽는 쪽이 낸 결과(길이를 8초로 잘못 읽은 것으로 둔다)."""
    draft, missing = intake._draft(program="오늘도 출발", name="김가상", role="조연출", style="route_move", src="홍대",
                                   dst="여의도", vehicle="bus", duration=8, layout="bottom_left_quarter", face_count=4,
                                   width=960, height=540, **over)
    return {"draft": draft, "missing": missing, "source": source, "notes": []}


def ok(field, evidence):
    return {"field": field, "verdict": "ok", "evidence": evidence, "found": ""}


GOOD = [ok("program", "오늘도 출발"), ok("requester_name", "김가상"), ok("requester_role", "조연출"), ok("requester_phone", ""),
        ok("map_style", "홍대에서 여의도로"), ok("from_name", "홍대에서"), ok("to_name", "여의도로"), ok("vehicle", "버스로 이동"),
        {"field": "duration_seconds", "verdict": "mismatch", "evidence": "총 10초", "found": "10초"},
        ok("layout", "좌하단"), ok("face_count", "출연진 4명"), ok("face_mode", ""), ok("output_size", "960*540"), ok("fps", ""),
        ok("vehicle_color", ""), ok("cuts", ""), ok("area_name", ""), ok("camera", ""), ok("separate_moves", "")]


def by_field(result) -> dict:
    return {c["field"]: c for c in result["checks"]}


class Pick(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"MAPCG_AUDIT": "auto", "LINER_MODEL": "", "LINER_AUDIT_MODEL": ""})
        p.start()
        self.addCleanup(p.stop)

    def test_liner_auditor_is_a_different_model_family_from_the_reader(self):
        plan, _ = audit.pick("liner", BOTH)
        self.assertEqual((plan["by"], plan["model"], plan["same"]), ("liner", "liner-claude-router", False))
        with mock.patch.dict(os.environ, {"LINER_MODEL": "liner-claude-router"}):
            self.assertEqual(audit.pick("liner", BOTH)[0]["model"], "liner-gemini-router")

    def test_same_model_is_admitted_not_hidden(self):
        with mock.patch.dict(os.environ, {"LINER_AUDIT_MODEL": "liner-gpt-router"}):
            self.assertTrue(audit.pick("liner", BOTH)[0]["same"])
        plan, _ = audit.pick("claude", {"liner": False, "claude": True})
        self.assertEqual((plan["by"], plan["same"]), ("claude", True))

    def test_empty_or_unknown_setting_means_on(self):
        # .env.example 을 그대로 복사하면 MAPCG_AUDIT 가 빈 값으로 들어온다 — 그때 꺼지면 안 된다
        for value in ("", "  ", "on", "true", "auto"):
            with mock.patch.dict(os.environ, {"MAPCG_AUDIT": value}):
                self.assertIsNotNone(audit.pick("liner", BOTH)[0], repr(value))
        with mock.patch.dict(os.environ):
            del os.environ["MAPCG_AUDIT"]
            self.assertIsNotNone(audit.pick("liner", BOTH)[0])

    def test_claude_reader_is_not_audited_by_the_claude_family_on_liner(self):
        plan, _ = audit.pick("claude", BOTH)
        self.assertEqual((plan["model"], plan["same"]), ("liner-gemini-router", False))
        with mock.patch.dict(os.environ, {"LINER_AUDIT_MODEL": "liner-claude-router"}):
            self.assertTrue(audit.pick("claude", BOTH)[0]["same"])       # 일부러 같은 계열을 고르면 그렇다고 적는다

    def test_reader_that_just_failed_is_not_called_again(self):
        only_liner = {"liner": True, "claude": False}
        r = audit.check(TEXT, [], reading("simple") | {"failed": ["liner"]}, available=only_liner,
                        chat=lambda *a, **k: self.fail("실패한 쪽을 다시 불렀다"))
        self.assertEqual(r["state"], "skipped")
        self.assertIn("방금", r["reason"])
        self.assertEqual(audit.pick("simple", {"liner": False, "claude": True})[0]["by"], "claude")

    def test_every_item_the_reader_reads_is_audited(self):
        # 읽는 항목을 늘리고 감사 쪽을 잊으면 그 항목이 감사 없이 '통과'로 보인다 — 여기서 잡는다
        # NOTICE_KEYS(지도가 아닌 종류 · 못 그리는 요청)는 읽은 값이 아니라 알림이라 감사하지 않는다
        read = set(intake.SCHEMA["properties"]) - {"map_style_text", "unclear", "output_width", "output_height", *intake.NOTICE_KEYS} | {"output_size"}
        self.assertEqual(read, set(audit.LABELS))
        self.assertEqual(list(audit.read_values(reading()["draft"], [])), list(audit.LABELS))

    def test_no_model_or_switched_off_means_skipped_with_a_reason(self):
        plan, why = audit.pick("simple", {"liner": False, "claude": False})
        self.assertIsNone(plan)
        self.assertIn("모델이 없습니다", why)
        with mock.patch.dict(os.environ, {"MAPCG_AUDIT": "off"}):
            self.assertIsNone(audit.pick("liner", BOTH)[0])
        self.assertEqual(audit.check(TEXT, [], reading(), available={"liner": False, "claude": False})["state"], "skipped")


class Check(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"MAPCG_AUDIT": "auto", "LINER_MODEL": "", "LINER_AUDIT_MODEL": ""})
        p.start()
        self.addCleanup(p.stop)

    def run_check(self, checks, text=TEXT, images=(), result=None):
        self.calls = []

        def chat(messages, **options):
            self.calls.append((messages, options))
            return reply({"checks": checks})

        return audit.check(text, images, result or reading(), available={"liner": True, "claude": False}, chat=chat)

    def test_mismatch_is_flagged_with_the_quote_and_the_value_on_the_form(self):
        r = self.run_check(GOOD)
        self.assertEqual((r["state"], r["flags"], r["unverified"]), ("done", 1, 0))
        c = by_field(r)["duration_seconds"]
        self.assertEqual((c["verdict"], c["value"], c["found"], c["evidence"]), ("mismatch", "8초", "10초", "총 10초"))
        self.assertTrue(c["flag"])
        self.assertEqual(by_field(r)["layout"]["value"], audit.LAYOUT_LABELS["bottom_left_quarter"])    # 코드 값이 아니라 화면 말로

    def test_auditor_sees_the_original_and_the_reading_and_uses_another_model(self):
        self.run_check(GOOD, images=["data:image/jpeg;base64,AAAA"])
        messages, options = self.calls[0]
        self.assertEqual(options["model"], "liner-claude-router")
        self.assertNotIn("reasoning_effort", options)
        content = messages[1]["content"]
        self.assertIn("[판독 결과]", content[0]["text"])
        self.assertIn("- duration_seconds: 8.0", content[0]["text"])
        self.assertIn("- requester_phone: 못 읽음", content[0]["text"])
        self.assertIn(TEXT, content[0]["text"])
        self.assertEqual(content[1]["image_url"]["url"], "data:image/jpeg;base64,AAAA")

    def test_quote_that_is_not_in_the_text_is_not_trusted(self):
        checks = [ok("from_name", "신촌에서 출발") if c["field"] == "from_name" else c for c in GOOD]
        c = by_field(self.run_check(checks))["from_name"]
        self.assertEqual(c["verdict"], "unverified")
        self.assertIn("근거 구절", c["why"])
        # 의뢰서 그림이 같이 왔으면 구절이 그림에서 나왔을 수 있으니 그대로 둔다
        c = by_field(self.run_check(checks, images=["data:image/jpeg;base64,AAAA"]))["from_name"]
        self.assertEqual(c["verdict"], "ok")

    def test_value_that_is_literally_in_the_text_cannot_be_called_unsupported(self):
        checks = [{"field": "to_name", "verdict": "unsupported", "evidence": "", "found": ""} if c["field"] == "to_name" else c
                  for c in GOOD]
        self.assertEqual(by_field(self.run_check(checks))["to_name"]["verdict"], "ok")

    def test_unsupported_that_quotes_the_value_goes_to_the_human(self):
        # 11-20 실측: 그림 의뢰서에서 SUV 를 '의뢰서에 없음'이라 하면서 "suv 출연진 4명"을 근거로 댔다 — 앞뒤가 안 맞는 판정은 '확인 못 함'으로
        checks = [{"field": "vehicle", "verdict": "unsupported", "evidence": "주차장에 버스 출연진 4명", "found": ""} if c["field"] == "vehicle" else c
                  for c in GOOD]       # 판독 값은 bus(화면 말 '버스') — 근거 구절에 '버스'가 들어 있다
        c = by_field(self.run_check(checks, images=["data:image/jpeg;base64,AAAA"]))["vehicle"]
        self.assertEqual((c["verdict"], c["flag"]), ("unverified", False))
        self.assertIn("서로 맞지 않습니다", c["why"])

    def test_made_up_value_is_flagged(self):
        checks = [{"field": "to_name", "verdict": "unsupported", "evidence": "", "found": ""} if c["field"] == "to_name" else c
                  for c in GOOD]
        r = self.run_check(checks, result=reading(), text=TEXT.replace("여의도", "한강공원"))
        self.assertEqual(by_field(r)["to_name"]["verdict"], "unsupported")
        self.assertTrue(by_field(r)["to_name"]["flag"])

    def test_default_value_without_support_is_fine(self):
        checks = [{"field": "face_mode", "verdict": "unsupported", "evidence": "", "found": ""} if c["field"] == "face_mode" else c
                  for c in GOOD]
        self.assertEqual(by_field(self.run_check(checks))["face_mode"]["verdict"], "ok")

    def test_missed_value_is_flagged(self):
        checks = [{"field": "requester_phone", "verdict": "missed", "evidence": "010-0000-0000", "found": "010-0000-0000"}
                  if c["field"] == "requester_phone" else c for c in GOOD]
        c = by_field(self.run_check(checks, text=TEXT + " 010-0000-0000"))["requester_phone"]
        self.assertEqual((c["verdict"], c["value"], c["found"]), ("missed", "", "010-0000-0000"))

    def test_mismatch_that_names_the_same_value_is_not_a_mismatch(self):
        checks = [{"field": "duration_seconds", "verdict": "mismatch", "evidence": "총 10초", "found": "10초"}
                  if c["field"] == "duration_seconds" else c for c in GOOD]
        r = self.run_check(checks, result=reading() | {"draft": {**reading()["draft"], "duration_seconds": 10.0}})
        self.assertEqual((by_field(r)["duration_seconds"]["verdict"], r["flags"]), ("ok", 0))

    def test_same_choice_written_in_words_is_not_a_mismatch(self):
        swap = {"vehicle": "버스", "layout": "좌하단", "map_style": "경로 이동 지도"}
        checks = [{"field": c["field"], "verdict": "mismatch", "evidence": c["evidence"], "found": swap[c["field"]]}
                  if c["field"] in swap else c for c in GOOD]
        r = self.run_check(checks)
        self.assertEqual([by_field(r)[f]["verdict"] for f in swap], ["ok", "ok", "ok"])
        # 정말 다른 값이면 말로 적어도 코드 값으로 알아보고 화면 말로 보여 준다
        checks = [{"field": "layout", "verdict": "mismatch", "evidence": "좌하단", "found": "우하단"} if c["field"] == "layout" else c
                  for c in GOOD]
        c = by_field(self.run_check(checks))["layout"]
        self.assertEqual((c["verdict"], c["found"]), ("mismatch", audit.LAYOUT_LABELS["bottom_right_quarter"]))

    def test_verdict_that_contradicts_the_reading_is_not_passed(self):
        checks = [{"field": "requester_phone", "verdict": "mismatch", "evidence": "", "found": ""}
                  if c["field"] == "requester_phone" else c for c in GOOD]
        c = by_field(self.run_check(checks))["requester_phone"]
        self.assertEqual(c["verdict"], "unverified")
        self.assertIn("맞지 않습니다", c["why"])

    def test_auditor_that_answers_nothing_is_a_failure_not_a_pass(self):
        r = self.run_check([])
        self.assertEqual(r["state"], "failed")
        self.assertIn("하나도", r["reason"])

    def test_cut_off_answer_is_reported_in_korean(self):
        def chat(messages, **options):
            return {"choices": [{"finish_reason": "length", "message": {"content": '{"checks": [{"field": "pro'}}]}

        r = audit.check(TEXT, [], reading(), available={"liner": True, "claude": False}, chat=chat)
        self.assertEqual(r["state"], "failed")
        self.assertIn("잘렸습니다", r["reason"])
        self.assertNotIn("Expecting", r["reason"])

    def test_audit_waits_less_than_reading(self):
        self.run_check(GOOD)
        options = self.calls[0][1]
        self.assertEqual((options["timeout"], options["retries"]), (audit.LINER_TIMEOUT, audit.LINER_RETRIES))
        self.assertLess(audit.CLAUDE_TIMEOUT, intake.CLAUDE_TIMEOUT)

    def test_fields_the_auditor_skipped_are_marked_not_passed(self):
        r = self.run_check(GOOD[:3])
        self.assertEqual(r["unverified"], len(audit.LABELS) - 3)
        self.assertEqual(by_field(r)["to_name"]["verdict"], "unverified")

    def test_failed_call_is_reported_not_raised(self):
        liner = intake._liner()

        def chat(messages, **options):
            raise liner.LinerError(500, "서버 오류")

        r = audit.check(TEXT, [], reading(), available={"liner": True, "claude": False}, chat=chat)
        self.assertEqual(r["state"], "failed")
        self.assertIn("Liner 서버 오류(응답 500)", r["reason"])      # 영어 오류문을 그대로 내보내지 않는다

    def test_claude_auditor_gets_the_reading_in_the_prompt(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(cmd=cmd, kw=kw)
            return types.SimpleNamespace(stdout=json.dumps({"is_error": False, "result": json.dumps({"checks": GOOD}, ensure_ascii=False)}),
                                         stderr="", returncode=0)

        with mock.patch.dict(os.environ, {"MAPCG_CLAUDE": sys.executable}):
            r = audit.check(TEXT, [], reading("simple"), available={"liner": False, "claude": True}, claude_run=run)
        self.assertEqual((r["state"], r["by"], r["same"], r["flags"]), ("done", "claude", False, 1))
        self.assertIn("[판독 결과]", seen["kw"]["input"])
        self.assertIn("판독 감사 담당", seen["kw"]["input"])


def wait_for(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class DeskAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def desk(self, audit_fn):
        return server.Desk(Path(self.tmp.name), analyze=lambda t, i=(): intake.analyze(t, i, use_liner=False),
                           liner=False, audit=audit_fn)

    def card(self, desk):
        def ready():
            with desk.lock:
                return desk.busy == 0 and desk.msgs and desk.msgs[-1]["kind"] == "card"

        self.assertTrue(wait_for(ready))
        return desk.msgs[-1]

    def test_audit_runs_between_reading_and_the_card(self):
        seen = {}

        def fake(text, images, result, *, available):
            seen.update(text=text, source=result["source"], kinds=[m["kind"] for m in desk.msgs])
            return {"state": "done", "label": "가짜", "same": False, "checks": [], "flags": 2, "unverified": 0}

        desk = self.desk(fake)
        desk.send("홍대에서 여의도로 8초", [])
        card = self.card(desk)
        self.assertEqual(seen["kinds"], ["text", "progress"])       # 감사할 때는 아직 카드가 없다
        self.assertEqual((seen["source"], card["audit"]["flags"]), ("simple", 2))
        self.assertIn("감사에서 확인할 곳 2", desk.msgs[1]["title"])

    def test_unsettled_items_are_not_announced_as_a_pass(self):
        def fake(text, images, result, *, available):
            return {"state": "done", "label": "가짜", "same": False, "checks": [], "flags": 0, "unverified": 3}

        desk = self.desk(fake)
        desk.send("홍대에서 여의도로 8초", [])
        self.card(desk)
        self.assertIn("감사가 가리지 못한 곳 3", desk.msgs[1]["title"])
        self.assertNotIn("통과", desk.msgs[1]["title"])

    def test_reading_reports_which_side_failed_to_answer(self):
        liner = intake._liner()

        def down(messages, **options):
            raise liner.LinerError(0, "연결 실패: 시험")

        r = intake.analyze("홍대에서 여의도로 8초", chat=down, order=["liner"])
        self.assertEqual((r["source"], r["failed"]), ("simple", ["liner"]))
        r = intake.analyze("홍대에서 여의도로 8초", chat=lambda m, **o: reply(["엉뚱한 답"]), order=["liner"])
        self.assertEqual(r["failed"], [])       # 답이 엉뚱했던 것은 호출 실패가 아니다

    def test_broken_audit_still_gives_the_card(self):
        def boom(text, images, result, *, available):
            raise RuntimeError("감사 고장")

        desk = self.desk(boom)
        desk.send("홍대에서 여의도로 8초", [])
        card = self.card(desk)
        self.assertEqual(card["audit"]["state"], "failed")
        self.assertIn("감사 고장", card["audit"]["reason"])
        self.assertEqual(card["draft"]["from"]["name"], "홍대")

    def test_without_a_model_the_card_says_audit_was_skipped(self):
        desk = self.desk(None)
        desk.send("홍대에서 여의도로 8초", [])
        self.assertEqual(self.card(desk)["audit"]["state"], "skipped")
        self.assertIn("감사 안 함", desk.msgs[1]["title"])


if __name__ == "__main__":
    unittest.main()
