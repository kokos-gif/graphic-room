"""맞춤 그래픽(mapcg/scene.py, 에이전트) — 메인의 도구 호출 검사 · 장면 설계 검사 · 화면 글자 대조 · 고칠 점 모으기 · 도구로 일하는 담당 · 메인의 다시 시키기와 납품(가짜 응답으로 본다. Liner 도 브라우저도 부르지 않는다)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import scene as A

REQ = '세 사람 말이 어디서 엇갈리는지 보여 주세요.\n가람 "8시 10분에는 나래랑 대기실에 있었어요."\n나래 "8시 10분에 저는 소품실에서 열쇠를 챙겼어요."'
BRIEF = {"viewer_point": "8시 10분 나래의 위치를 두고 말이 엇갈린다",
         "facts": [{"id": "f1", "fact": "가람: 8시 10분 나래와 대기실", "quote": "8시 10분에는 나래랑 대기실에 있었어요"},
                   {"id": "f2", "fact": "나래: 8시 10분 소품실", "quote": "8시 10분에 저는 소품실에서 열쇠를 챙겼어요"}],
         "must_not_assert": ["누가 거짓말을 했는지"], "open_questions": [], "checks": ["두 진술이 견주어지는가"]}
WORKER = {"role": "진술 대조 담당", "goal": "엇갈림을 보인다", "fact_ids": ["f1", "f2"], "layouts": ["lanes", "columns"], "checks": ["두 진술이 견주어지는가"], "max_tries": 3}


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def scene(**over):
    s = A._sc("lanes", "8시 10분", [A._it("i1", "g1", "대기실", "나래랑 대기실에 있었어요", time_="8:10"),
                                   A._it("i2", "g2", "소품실", "소품실에서 열쇠를 챙겼어요", time_="8:10")],
              [A._g("g1", "가람", "blue"), A._g("g2", "나래", "pink")], marks=[{"target": "8:10", "label": "엇갈림", "fact_ids": []}])
    s["items"][0]["fact_ids"], s["items"][1]["fact_ids"] = ["f1"], ["f2"]
    s.update(over)
    return s


def reply(message, model="m"):
    return {"model": model, "choices": [{"message": message, "finish_reason": "stop"}]}


class Main(unittest.TestCase):
    def test_brief_and_worker_pass(self):
        calls = A.parse_calls({"tool_calls": [call("write_brief", BRIEF), call("spawn_worker", WORKER, "c2")]})
        got, problems = A.check_main(calls, REQ)
        self.assertEqual(problems, [])
        self.assertEqual(got["workers"][0]["role"], "진술 대조 담당")

    def test_ids_given_as_numbers_are_read_as_text(self):
        brief = {**BRIEF, "facts": [{**BRIEF["facts"][0], "id": 1}, {**BRIEF["facts"][1], "id": 2}]}
        main = scripted([("write_brief", brief), ("spawn_worker", {**WORKER, "fact_ids": [1, 2]})])
        planned = A.plan_request(REQ, log=lambda *a: None, chat=main)          # 앱과 같은 길(지시서.json 에 담기는 값까지)
        self.assertEqual((planned["main_ok"], planned["problems"]), (True, []))
        self.assertEqual(planned["brief"]["facts"][0]["id"], "1")
        self.assertEqual(planned["workers"][0]["fact_ids"], ["1", "2"])
        self.assertEqual(planned["calls"][1][2]["fact_ids"], ["1", "2"])       # 제자리에서 바꿨다 — 저장되는 호출 기록도 같은 값
        with tempfile.TemporaryDirectory() as tmp:
            w = A.Worker("w1", planned["workers"][0], planned["brief"], Path(tmp), Path(tmp) / "p", log=lambda *a: None)
            self.assertEqual(len(w.facts), 2)                                   # 담당이 맡은 사실이 0개가 되지 않는다
        mixed = {**BRIEF, "facts": [{**BRIEF["facts"][0], "id": 1}, {**BRIEF["facts"][1], "id": 2}]}       # 사실은 숫자 · 담당은 글자(고치기 전 TypeError)
        _, problems = A.check_main(A.parse_calls({"tool_calls": [call("write_brief", mixed), call("spawn_worker", {**WORKER, "fact_ids": ["1", "2"]}, "c2")]}), REQ)
        self.assertEqual(problems, [])

    def test_empty_quote_is_not_a_source(self):
        brief = {**BRIEF, "facts": [{**BRIEF["facts"][0], "quote": ""}, BRIEF["facts"][1]]}
        _, problems = A.check_main(A.parse_calls({"tool_calls": [call("write_brief", brief), call("spawn_worker", WORKER, "c2")]}), REQ)
        self.assertTrue(any(p.startswith("f1 의 근거(quote)가 의뢰 원문에 없다") for p in problems))

    def test_quote_must_come_from_the_request(self):
        bad = {**BRIEF, "facts": [{"id": "f1", "fact": "지어낸 것", "quote": "9시에 옥상에서 만났어요"}]}
        _, problems = A.check_main(A.parse_calls({"tool_calls": [call("write_brief", bad), call("spawn_worker", {**WORKER, "fact_ids": ["f1"]}, "c2")]}), REQ)
        self.assertTrue(any("원문에 없다" in p for p in problems))

    def test_missing_route_and_unknown_fact_and_layout(self):
        _, p1 = A.check_main(A.parse_calls({"tool_calls": [call("write_brief", BRIEF)]}), REQ)
        self.assertTrue(any("spawn_worker 나 report_unable" in p for p in p1))
        w = {**WORKER, "fact_ids": ["f1", "f9"], "layouts": ["photo_zoom"]}
        _, p2 = A.check_main(A.parse_calls({"tool_calls": [call("write_brief", BRIEF), call("spawn_worker", w, "c2")]}), REQ)
        self.assertTrue(any("없는 사실 id" in p for p in p2))
        self.assertTrue(any("배치를 주지 않았다" in p for p in p2))
        self.assertTrue(any("맡은 담당이 없는 사실: f2" in p for p in p2))

    def test_unable_alone_is_a_valid_answer(self):
        u = {"route": "사람 지정 필요", "existing": "없음", "reason": "사진 속 위치", "needed": ["달력 위치"]}
        got, problems = A.check_main(A.parse_calls({"tool_calls": [call("write_brief", BRIEF), call("report_unable", u, "c2")]}), REQ)
        self.assertEqual(problems, [])
        self.assertEqual(got["unable"][0]["route"], "사람 지정 필요")

    def test_deliver_before_any_worker_is_a_problem(self):
        _, problems = A.check_main(A.parse_calls({"tool_calls": [call("write_brief", BRIEF), call("spawn_worker", WORKER, "c2"),
                                                                 call("deliver", {"accept": ["w1"], "message": ""}, "c3")]}), REQ)
        self.assertTrue(any("담당 결과를 받기 전에는 deliver" in p for p in problems))



class Limits(unittest.TestCase):
    """메인 에이전트에게 주는 글이 한도를 제대로 알리는가 — 2026-11-24 에 시험 의뢰를 돌리다 걸린 것들."""

    def test_한도를_넘는_양은_처음부터_담당을_나누라고_적혀_있다(self):
        # 막대 16개(네 팀 × 4)를 담당 한 명에게 맡기고, 마무리 때 넷으로 다시 띄우려다 거부돼 시안이 0장이었다
        self.assertIn("담당 한 명은 시안 한 장만 낸다", A.MAIN_SYSTEM)
        self.assertIn("담당들이 일을 마친 뒤에는 새 담당을 띄울 수 없다", A.MAIN_SYSTEM)
        self.assertNotIn("spawn_worker", A.LAST_TOOLS)      # 위 글이 사실이어야 한다

    def test_못_하는_것에_항목_그림을_넣지_않는다(self):
        # 항목 이름에 맞는 작은 그림은 코드가 붙인다(attach_pictures) — '아이콘 목록에 없는 새 그림'을 못 한다고 적어 두면 메인이 되는 일을 거절한다
        self.assertNotIn("아이콘 목록에 없는 새 그림", A.CANNOT)
        self.assertIn("항목 이름에 맞는 작은 그림은 코드가 항목마다 붙인다", A.CANNOT)

    def test_차례로_바뀌는_큰_글자는_보통_큰_글자_크기를_기준으로_줄임을_잰다(self):
        # 숫자 한 자에 맞춘 300px 을 기준으로 재면 낱말이 늘 '줄여야 들어감'으로 걸렸다
        html = (Path(A.__file__).parent / "templates" / "scene.html").read_text(encoding="utf-8")
        self.assertIn("fit(s, want, BOX.w - 160, '큰 글자', Math.min(want, 150, BOX.h * 0.42))", html)
        self.assertIn("judgeSize(s, p, base || px, where)", html)


class Scene(unittest.TestCase):
    def test_good_scene_has_no_problems(self):
        self.assertEqual(A.check_scene(scene(), WORKER), [])

    def test_layout_refs_and_coverage(self):
        s = scene(layout="ring")
        s["items"][0]["group"] = "g9"
        s["items"][1]["fact_ids"] = ["f7"]
        s["links"] = [A._ln("i1", "zz")]
        s["marks"] = [{"target": "9:00", "label": "x", "fact_ids": []}]
        p = A.check_scene(s, WORKER)
        for want in ("허락받지 않은 배치", "group(g9)", "화살표의 끝", "target(9:00)", "맡은 사실이 화면에 없다", "맡지 않은 사실 id"):
            self.assertTrue(any(want in x for x in p), want)

    def test_schema_never_names_a_field_thinking(self):
        # Claude 라우터가 strict 틀의 'thinking' 칸을 내용 거름으로 막는다(빈 답). 다시 쓰지 않게 지킨다
        self.assertNotIn("thinking", json.dumps(A.SCENE_SCHEMA))


class TextMatch(unittest.TestCase):
    def test_short_needs_exact_long_tolerates_line_breaks(self):
        hay = A._norm("8:10 대기실 나래랑 대기실에 소품실 있었어요 소품실에서 열쇠를 챙겼어요")
        self.assertTrue(A._seen("8:10", hay))
        self.assertTrue(A._seen("나래랑 대기실에 있었어요", hay))
        self.assertFalse(A._seen("8:15", hay))
        self.assertTrue(A._seen("18,500원", A._norm("합계 18500원")))

    def test_wanted_texts_skip_unused_groups_and_blanks(self):
        s = scene()
        s["groups"].append(A._g("g3", "다온", "green"))
        got = A.wanted_texts(s)
        self.assertIn("가람", got)
        self.assertNotIn("다온", got)
        self.assertNotIn("", got)
        self.assertIn("8:10", got)


class WantedByLayout(unittest.TestCase):
    """읽혀야 할 글자는 그 배치가 실제로 그리는 것만 — 안 그리는 글을 요구하면 담당이 고칠 수 없는 실패가 난다(01 하단 띠, 11-21)."""

    def test_layout_rules(self):
        items = [A._it("a", "g", "이번 미션", "가람 다온 한 팀"), A._it("b", None, "오늘만", "끝나면 원래 팀")]
        groups = [A._g("g", "파랑 팀", "blue")]
        banner = A.wanted_texts(A._sc("banner", "", items, groups))
        self.assertTrue({"파랑 팀", "이번 미션", "오늘만"} <= set(banner))           # 띠는 묶음 이름표와 이름을 둘 다 단다
        ring = A.wanted_texts(A._sc("ring", "", items, groups))
        self.assertNotIn("파랑 팀", ring)                                             # 둘러앉기의 묶음은 색으로만
        chat = A.wanted_texts(A._sc("chat", "", items, groups))
        self.assertIn("파랑 팀", chat)
        self.assertNotIn("이번 미션", chat)                                           # 대화: 묶음이 있으면 묶음 이름이 보낸 사람
        self.assertIn("오늘만", chat)
        bars = A.wanted_texts(A._sc("bars", "", [A._it("a", "g", "", value=2, value_text="2개"), A._it("b", "g", "나래", value=1)], groups))
        self.assertIn("파랑 팀", bars)                                                # 이름이 빈 막대는 묶음 이름을 쓴다


class Failures(unittest.TestCase):
    def test_collects_code_audit_verdict_text_and_readability(self):
        verdict = {"checks": [{"n": 1, "ok": True, "why": ""}, {"n": 2, "ok": False, "why": "단정함"}],
                   "text_seen": ["8시 10분", "가람", "나래", "대기실", "나래랑 대기실에 있었어요", "소품실", "소품실에서 열쇠를 챙겼어요", "8:10"],
                   "readability": ["배지가 제목을 가림"]}
        out = A.failures(["코드 문제"], ["글자가 작음"], verdict, ["견주어지는가", "단정하지 않는가"], scene())
        self.assertIn("[코드 검사] 코드 문제", out)
        self.assertIn("[자동 검사] 글자가 작음", out)
        self.assertTrue(any("확인할 점 2: 아니오" in x for x in out))
        self.assertTrue(any("읽히지 않음: 엇갈림" in x for x in out))
        self.assertTrue(any("읽기 어려움" in x for x in out))
        self.assertFalse(any("확인할 점 1" in x for x in out))


def scripted(*steps):
    """차례대로 답하는 가짜 Liner: 단계마다 (도구 호출 목록) 또는 문자열(그냥 글) 하나. 받은 대화는 그때 모습 그대로 적어 둔다."""
    it, sent = iter(steps), []

    def chat(messages, **kw):
        sent.append([dict(m) for m in messages])
        step = next(it)
        if isinstance(step, str):
            return reply({"role": "assistant", "content": step})
        return reply({"role": "assistant", "content": "", "tool_calls": [call(n, a, f"c{len(sent)}_{k}") for k, (n, a) in enumerate(step)]})
    chat.sent = sent
    return chat


def verdict(ok=True):
    return {"checks": [{"n": i, "ok": ok or i > 1, "why": ""} for i in range(1, 3)],
            "text_seen": ["8시 10분 가람 나래 대기실 나래랑 대기실에 있었어요 소품실 소품실에서 열쇠를 챙겼어요 8:10 엇갈림"], "readability": []}


def judging(*oks):
    it = iter(oks)
    return lambda messages, **kw: reply({"role": "assistant", "content": json.dumps(verdict(next(it)), ensure_ascii=False)})


class Fake(unittest.TestCase):
    """그리기 · 찍기 · 그림 붙이기는 가짜로 바꾼다(브라우저를 띄우지 않는다)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        patches = [mock.patch.object(A, "install", lambda job_dir, sc: Path(job_dir).mkdir(parents=True, exist_ok=True)),
                   mock.patch.object(A, "shoot", lambda job_dir, profile: (Path(job_dir) / "시안.png", [], {"big": 0.2})),
                   mock.patch.object(A, "picture", lambda p: {"type": "image_url", "image_url": {"url": "data:,"}})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def worker(self, chat, judge_chat, tries=4):
        return A.Worker("w1", WORKER, BRIEF, self.work, self.work / "p", tries, log=lambda *a: None, chat=chat, judge_chat=judge_chat)


class WorkerTools(Fake):
    def test_worker_picks_its_own_order_and_passes(self):
        chat = scripted([("draw_draft", {"scene": scene()})], [("request_review", {"note": ""})],
                        [("draw_draft", {"scene": scene()}), ("request_review", {"note": ""})], [("submit", {"summary": "끝", "unresolved": []})])
        w = self.worker(chat, judging(False, True))
        got = w.run()
        self.assertEqual(got["result"], "합격")
        self.assertEqual(got["order"], ["draw_draft", "request_review", "draw_draft", "request_review", "submit"])
        self.assertEqual((got["draws"], got["reviews"]), (2, 2))
        self.assertTrue(any(isinstance(m.get("content"), list) for m in chat.sent[1]))        # 그린 뒤에는 시안 그림을 보여 준다
        self.assertIn("걸린 점 있음", chat.sent[2][-2]["content"] if chat.sent[2][-1]["role"] == "user" else chat.sent[2][-1]["content"])

    def test_submit_without_review_is_not_a_pass(self):
        w = self.worker(scripted([("draw_draft", {"scene": scene()}), ("submit", {"summary": "", "unresolved": []})]), judging())
        got = w.run()
        self.assertEqual(got["result"], "검수 없이 제출")
        self.assertIn("마지막 시안이 검수를 받지 않았다", got["unresolved"])

    def test_limits_are_kept_by_code(self):
        steps = [[("draw_draft", {"scene": scene()})]] * 10
        w = self.worker(scripted(*steps), judging(), tries=1)          # 검수 1번 · 그리기 3번 · 대화 1+3+3 번까지
        got = w.run()
        self.assertEqual(got["draws"], 3)
        self.assertEqual(got["result"], "한도")
        self.assertTrue(any("그리기 한도" in m["content"] for m in w.msgs if m["role"] == "tool"))

    def test_redo_opens_only_the_turns_that_are_left(self):
        steps = [[("draw_draft", {"scene": scene()})]] * 7 + [[("없는_도구", {})]] * 12
        w = self.worker(scripted(*steps), judging(), tries=1)          # 첫 일: 그리기 3 + 한도 오류 4 → 7턴에서 한도
        self.assertEqual((w.run()["result"], w.turns), ("한도", 7))
        got = w.run("다시")                                              # 남은 그리기 2 + 남은 검수 2 + 3 = 7턴만 더(고치기 전에는 전체 한도로 10턴)
        self.assertEqual((got["result"], w.turns), ("한도", 14))

    def test_give_up_and_plain_text_answer(self):
        w = self.worker(scripted("생각 중입니다", [("give_up", {"reason": "사진 위치가 필요", "needed": ["좌표"]})]), judging())
        got = w.run()
        self.assertEqual((got["result"], got["needed"]), ("포기", ["좌표"]))
        self.assertTrue(got["order"][0].startswith("(도구 없이 답함"))

    def test_a_broken_draw_is_told_to_the_worker_and_counts_against_the_limit(self):
        boom = mock.patch.object(A, "shoot", side_effect=RuntimeError("캡처 시간 초과"))
        boom.start()
        self.addCleanup(boom.stop)
        w = self.worker(scripted(*[[("draw_draft", {"scene": scene()})]] * 10), judging(), tries=1)      # 그리기 3번까지
        got = w.run()
        self.assertEqual((got["result"], got["draws"], w.broken["그리기"]), ("한도", 0, 3))             # 고장도 한도를 쓴다 — 끝없이 돌지 않는다
        tools = [m["content"] for m in w.msgs if m["role"] == "tool"]
        self.assertTrue(any("그리기 고장" in t for t in tools) and any("그리기 한도" in t for t in tools))
        self.assertEqual(got["broken"], {"그리기": 3, "검수": 0})

    def test_a_broken_review_counts_and_the_worker_goes_on(self):
        def judge_chat(messages, **kw):
            raise RuntimeError("검수 답이 끊김")
        chat = scripted([("draw_draft", {"scene": scene()}), ("request_review", {"note": ""})], [("request_review", {"note": ""})],
                        [("request_review", {"note": ""})], [("submit", {"summary": "", "unresolved": []})])
        w = self.worker(chat, judge_chat, tries=2)                                                       # 검수 2번까지
        got = w.run()
        self.assertEqual((got["result"], got["draws"], got["reviews"], w.broken["검수"]), ("검수 없이 제출", 1, 0, 2))
        self.assertTrue(any("검수 한도" in m["content"] for m in w.msgs if m["role"] == "tool"))

    def test_a_worker_that_cannot_be_called_ends_alone_as_broken(self):
        def chat(messages, **kw):
            raise RuntimeError("시간 초과")
        got = self.worker(chat, judging()).run()
        self.assertEqual(got["result"], "고장")
        self.assertIn("담당을 부르지 못했다", got["summary"])

    def test_normalize_fills_defaults_and_drops_bad_values(self):
        s = A.normalize_scene({"layout": "rows", "groups": [{"id": "g", "name": "가", "color": "gold"}],
                               "items": [{"id": "a", "label": "가", "value": "1,200", "icon": "Z99"}, "깨진 항목"], "grid_total": "100"})
        self.assertEqual(s["groups"][0]["color"], "blue")
        self.assertEqual(len(s["items"]), 1)
        it = s["items"][0]
        self.assertEqual((it["value"], it["icon"], it["text"], it["fact_ids"], it["empty"]), (1200.0, None, "", [], False))
        self.assertEqual((s["grid_total"], s["links"], s["memo"]), (100, [], ""))


class MainLoop(Fake):
    def test_main_spawns_reads_results_redoes_and_delivers(self):
        main = scripted([("write_brief", BRIEF), ("spawn_worker", WORKER)],
                        [("redo_worker", {"worker": "w1", "instruction": "제목을 더 짧게"})],
                        [("deliver", {"accept": ["w1"], "message": "진술 대조판 납품"})])
        work = scripted([("draw_draft", {"scene": scene()}), ("request_review", {"note": ""})], [("submit", {"summary": "1차", "unresolved": []})],
                        [("draw_draft", {"scene": scene(title="8:10")}), ("request_review", {"note": ""})], [("submit", {"summary": "2차", "unresolved": []})])
        got = A.run_request(REQ, self.work, self.work / "p", log=lambda *a: None, chat=main, worker_chat=work, judge_chat=judging(True, True))
        self.assertTrue(got["main_ok"])
        self.assertEqual(got["delivery"], {"accept": ["w1"], "message": "진술 대조판 납품", "by": "메인"})
        w = got["workers"][0]
        self.assertEqual((w["result"], w["redo"], w["draws"]), ("합격", "제목을 더 짧게", 2))
        second = main.sent[1]
        spawned = [m for m in second if m["role"] == "tool" and "w1" in m["content"]]
        self.assertTrue(spawned and '"result": "합격"' in spawned[0]["content"])          # 담당의 결과가 spawn_worker 의 도구 결과로 돌아간다
        self.assertTrue(isinstance(second[-1]["content"], list))                             # 마지막 시안 그림도 메인에게 보인다
        self.assertTrue(any("메인의 다시 하기 지시: 제목을 더 짧게" in str(m.get("content")) for m in work.sent[2]))

    def test_main_asks_again_once_when_tools_are_wrong(self):
        main = scripted([("write_brief", BRIEF)], [("write_brief", BRIEF), ("report_unable", {"route": "사람 지정 필요", "existing": "없음", "reason": "사진", "needed": []})])
        got = A.run_request(REQ, self.work, self.work / "p", log=lambda *a: None, chat=main, worker_chat=scripted(), judge_chat=judging())
        self.assertTrue(got["main_ok"])
        self.assertEqual(got["delivery"]["by"], "담당 없음 — 못 함으로 끝")
        self.assertEqual(len(got["rounds"]), 2)
        self.assertIn("spawn_worker 나 report_unable", main.sent[1][-1]["content"])
        self.assertEqual(sum(m["role"] == "tool" for m in main.sent[1]), 1)          # 앞 호출마다 도구 결과를 돌려준 뒤 다시 묻는다

    def test_main_failing_twice_stops_without_workers(self):
        main = scripted([("write_brief", BRIEF)], [("write_brief", BRIEF)])
        got = A.run_request(REQ, self.work, self.work / "p", log=lambda *a: None, chat=main, worker_chat=scripted(), judge_chat=judging())
        self.assertFalse(got["main_ok"])
        self.assertEqual(got["workers"], [])

    def test_a_broken_worker_or_a_broken_main_keeps_what_the_others_drew(self):
        """담당 하나나 메인의 마무리 호출이 고장 나도 의뢰 전체를 버리지 않는다 — 다른 담당이 낸 시안은 그대로 돌아온다."""
        planned = {"brief": BRIEF, "unable": [], "msgs": [],
                   "calls": [["c1", "write_brief", BRIEF], ["c2", "spawn_worker", WORKER], ["c3", "spawn_worker", WORKER]]}
        inner = scripted([("draw_draft", {"scene": scene()}), ("request_review", {"note": ""})], [("submit", {"summary": "끝", "unresolved": []})])

        def work(messages, **kw):       # 첫 담당은 끝까지 일하고, 둘째 담당은 부르자마자 끊긴다
            if len(inner.sent) >= 2:
                raise RuntimeError("연결 끊김")
            return inner(messages, **kw)

        def main(messages, **kw):
            raise RuntimeError("시간 초과")

        got = A.make_request(planned, self.work, self.work / "p", log=lambda *a: None, chat=main, worker_chat=work, judge_chat=judging(True))
        self.assertEqual([w["result"] for w in got["workers"]], ["합격", "고장"])
        self.assertTrue(got["workers"][0]["final_shot"])
        self.assertIsNone(got["workers"][1]["final_shot"])
        self.assertEqual((got["delivery"]["by"], got["delivery"]["accept"]), ("메인 호출 실패", []))
        self.assertIn("시안을 그대로", got["delivery"]["message"])

    def last_round(self, third):
        """담당 셋 · 메인은 바퀴마다 w1 · w2 를 다시 시키고 셋째 바퀴에 third 를 부른다."""
        spawn = [("spawn_worker", {**WORKER, "role": f"담당{n}"}) for n in (1, 2, 3)]
        main = scripted([("write_brief", BRIEF), *spawn], [("redo_worker", {"worker": "w1", "instruction": "고쳐"})],
                        [("redo_worker", {"worker": "w2", "instruction": "고쳐"})], [third])
        once = [[("draw_draft", {"scene": scene()}), ("request_review", {"note": ""})], [("submit", {"summary": "", "unresolved": []})]]
        work = scripted(*(once * 6))
        got = A.run_request(REQ, self.work, self.work / "p", log=lambda *a: None, chat=main, worker_chat=work, judge_chat=judging(*[True] * 6))
        return main, got

    def test_last_round_asks_for_delivery_and_takes_no_more_redo(self):
        main, got = self.last_round(("redo_worker", {"worker": "w3", "instruction": "고쳐"}))
        third = main.sent[3]                                                    # 0 지시서 · 1~3 마무리 바퀴
        self.assertTrue(any(A.LAST_CALL in json.dumps(m.get("content"), ensure_ascii=False) for m in third if m["role"] == "user"))
        self.assertFalse(any(A.LAST_CALL in json.dumps(m.get("content"), ensure_ascii=False) for m in main.sent[2]))     # 앞 바퀴에는 없다
        self.assertFalse(any(a["role"] == b["role"] == "user" for a, b in zip(third, third[1:])))       # user 가 연달아 놓이지 않는다
        by = {w["worker"]: w for w in got["workers"]}
        self.assertEqual([by[w]["redo"] for w in ("w1", "w2", "w3")], ["고쳐", "고쳐", None])           # w3 은 돌지 않았다
        self.assertEqual([by[w]["result"] for w in ("w1", "w2", "w3")], ["합격"] * 3)
        self.assertEqual(got["delivery"]["by"], "메인이 deliver 를 부르지 않음")
        self.assertEqual(len(main.sent), 4)

    def test_last_round_deliver_is_taken(self):
        main, got = self.last_round(("deliver", {"accept": ["w1", "w3"], "message": "납품합니다"}))
        self.assertEqual(got["delivery"], {"accept": ["w1", "w3"], "message": "납품합니다", "by": "메인"})
        self.assertEqual([w["result"] for w in got["workers"]], ["합격"] * 3)


class Motion(unittest.TestCase):
    """움직임 · 길이 · 얹기(11-21 밤): 모델은 방식만 고르고 때는 코드가 정한다. 납품 길이와 찍는 시각이 장면에서 나온다."""

    def test_normalize_motion_duration_over_video(self):
        s = A.normalize_scene({"layout": "headline", "motion": {"in": "spin", "out": "fade", "order": "one_by_one"}, "duration_seconds": "40", "over_video": 1})
        self.assertEqual(s["motion"], {"in": "none", "out": "fade", "order": "one_by_one"})
        self.assertEqual((s["duration_seconds"], s["over_video"]), (15.0, True))
        s = A.normalize_scene({"layout": "rows"})
        self.assertEqual((s["motion"]["in"], s["duration_seconds"], s["over_video"]), ("none", None, False))

    def test_duration_and_shot_times(self):
        still = A._sc("rows", "x", [A._it("a", label="가")])
        opening = A._sc("headline", "", [A._it("a", None, "코너", "제목")], motion={"in": "pop"}, duration=3)
        wipe = A._sc("wipe", "", [A._it("a", text="다음")])
        self.assertEqual([A.scene_duration(x) for x in (still, opening, wipe)], [2.0, 3.0, 1.5])
        self.assertFalse(A.moving(still))
        self.assertTrue(A.moving(opening) and A.moving(wipe))
        t, hold = A.shot_times({**opening, "duration_seconds": 3})
        self.assertEqual((len(t), hold), (4, 2))
        self.assertLessEqual(max(t), (90 - 1) / 30)                 # 3초 = 90장, 마지막 장의 시각을 넘지 않는다
        self.assertEqual(len(A.shot_times(still)[0]), 1)

    def test_swap_countdown(self):
        """차례로 바꿔 보이기(11-23): 큰 글자 판에서 항목이 같은 자리에서 하나씩 바뀐다 — 길이 · 찍는 순간 · 코드 검사가 따라온다."""
        cd = A.SAMPLES["countdown"]
        self.assertEqual(A.normalize_scene({"layout": "headline", "motion": {"order": "swap"}})["motion"]["order"], "swap")
        self.assertEqual(A.normalize_scene({"layout": "headline", "motion": {"order": "뒤집기"}})["motion"]["order"], "together")
        self.assertTrue(A.moving(cd))
        self.assertEqual([i["value_text"] for i in cd["items"]], ["5", "4", "3", "2", "1"])     # 보기는 5부터 빠르게(3초 = 하나에 0.6초)
        self.assertEqual(A.swap_count(cd), 5)
        self.assertEqual(A.scene_duration({**cd, "duration_seconds": None}), 5.0)       # 길이를 안 적으면 항목마다 1초
        t, hold = A.shot_times({**cd, "duration_seconds": 3})
        self.assertEqual((len(t), hold), (5, 0))                                         # 항목마다 한 순간
        for k, x in enumerate(t):       # 구간(0.6초)의 72 ~ 85% 사이 — 등장 효과가 걷힌 뒤 · 퇴장 전, 글자만 또렷한 순간(templates/scene.html 의 박자)
            self.assertTrue(0.6 * (k + 0.72) < x < 0.6 * (k + 0.85), (k, x))
        self.assertLessEqual(t[-1], (90 - 1) / 30)
        t3, _ = A.shot_times({**cd, "items": cd["items"][:3], "duration_seconds": 3})    # 3개 · 3초(하나에 1초)여도 같은 비율
        self.assertTrue(0.72 < t3[0] < 0.85 and 2.72 < t3[2] < 2.85)
        w = {"layouts": ["headline", "rows"], "fact_ids": []}
        self.assertEqual(A.check_scene(cd, w), [])
        self.assertTrue(any("큰 글자 판" in p for p in A.check_scene({**cd, "layout": "rows"}, w)))
        self.assertTrue(any("2~6개" in p for p in A.check_scene({**cd, "items": cd["items"][:1]}, w)))
        self.assertEqual(A.swap_count(A.SAMPLES["opening"]), 0)                         # 차례로 쌓이는 장면은 그대로

    def test_swap_effects_are_not_review_criteria(self):
        """카운트다운의 등장 효과(고리 · 색 조각 · 평행 띠)는 코드가 붙이는 장식이다 — 지시문이 '글자만'이라고 하지 않고, 검수자도 그 사정을 받는다."""
        text = A.LAYOUTS["headline"]
        self.assertNotIn("판 없이 글자만", text)                  # 이 말을 읽고 '글자만 보이는가'를 합격 기준으로 쓰면 효과가 보이는 시안이 떨어진다
        for part in ("바탕판은 없고", "등장 효과", "확인할 점에 넣지 않는다"):
            self.assertIn(part, text)
        self.assertEqual(A.LAYOUT_LABELS["headline"], "큰 글자 판")
        seen = []

        def chat(messages, **kw):
            seen.append(messages)
            return reply({"role": "assistant", "content": json.dumps({"checks": [], "text_seen": [], "readability": []})})
        info = {"big": 0.3, "sheet": Path("움직임.png"), "times": [0.468, 1.068], "seconds": 3.0, "frames": 90, "clear": True}
        with mock.patch.object(A, "picture", lambda p: {"type": "image_url", "image_url": {"url": str(p)}}):
            A.judge(Path("시안.png"), ["숫자가 크게 보이는가"], chat=chat, info={**info, "swap": 5})
            A.judge(Path("시안.png"), ["숫자가 크게 보이는가"], chat=chat, info=info)
        self.assertIn("항목 5개가 저마다 자리 잡은 순간", seen[0][1]["content"][0]["text"])
        self.assertIn("보이지 않는 것이 정상", seen[0][1]["content"][0]["text"])
        self.assertNotIn("등장 효과", seen[1][1]["content"][0]["text"])        # 차례로 바뀌는 장면이 아니면 붙이지 않는다

    def test_wanted_texts_for_wipe_and_banner_subtitle(self):
        self.assertEqual(A.wanted_texts(A._sc("wipe", "제목", [A._it("a", None, "머리", "다음 라운드"), A._it("b", text="둘째")])), ["다음 라운드"])
        self.assertNotIn("부제", A.wanted_texts(A._sc("banner", "", [A._it("a", text="한 줄")], subtitle="부제")))

    def test_judge_gets_size_measure_and_motion_sheet(self):
        seen = []

        def chat(messages, **kw):
            seen.append(messages)
            return reply({"role": "assistant", "content": json.dumps({"checks": [], "text_seen": [], "readability": []})})
        with mock.patch.object(A, "picture", lambda p: {"type": "image_url", "image_url": {"url": str(p)}}):
            A.judge(Path("시안.png"), ["크게 보이는가"], chat=chat, info={"big": 0.055, "sheet": Path("움직임.png"), "times": [0.3, 2.8], "seconds": 3.0, "frames": 90, "clear": True})
        content = seen[0][1]["content"]
        self.assertIn("화면 세로의 6%", content[0]["text"])
        self.assertIn("코드 실측 — 길이 3초(90장 · 30fps) · 바탕: 투명", content[0]["text"])      # 길이 · 투명은 그림이 아니라 실측으로 판정
        self.assertEqual([c["image_url"]["url"] for c in content[1:]], ["시안.png", "움직임.png"])
        self.assertIn("12% 미만이면", seen[0][0]["content"])           # '크게'를 너그럽게 넘기지 않는 기준

    def test_arrows_that_come_one_by_one_are_shot_as_they_add_up(self):
        """화살표가 차례로 나오는 장면(11-24): 고정된 네 순간은 화살표가 '없음'에서 '다 나옴'으로 건너뛴다 — 화면이 알려 준
        '하나씩 더해지는 순간'으로 검수판을 읽고, 검수자에게 그 사정을 알린다."""
        self.assertEqual(A._steps({"times": [4.27, 4.62, 4.97, 5.43], "steps": [1, 2, 3, 4]}),
                         {"times": [4.27, 4.62, 4.97, 5.43], "shown": [1, 2, 3, 4], "kind": "links", "hold": 3, "leave": False})
        # 차례로 나오는 항목(화살표 없음) + 나가는 움직임: 더해지는 순간 뒤에 나가는 순간 한 장 — 다 나온 순간은 그 앞 장이다
        self.assertEqual(A._steps({"times": [0.42, 0.77, 1.5, 6.6], "steps": [1, 2, 3], "step_kind": "items", "hold": 2, "leave": True}),
                         {"times": [0.42, 0.77, 1.5, 6.6], "shown": [1, 2, 3], "kind": "items", "hold": 2, "leave": True})
        for bad in ({}, {"times": [1.0, 2.0]}, {"times": [1.0], "steps": [1]}, {"times": [1.0, 2.0], "steps": [1]},
                    {"times": [1.0, 2.0], "steps": [1, 2], "leave": True},
                    {"times": [1.0, "둘"], "steps": [1, 2]}, {"times": [1.0, float("nan")], "steps": [1, 2]},
                    {"times": list(range(9)), "steps": list(range(9))}, {"times": "1,2", "steps": "1,2"}):
            self.assertIsNone(A._steps(bad), bad)

        from PIL import Image
        told = {"issues": [], "big": 0.05, "times": [4.27, 4.62, 4.97, 5.43], "steps": [1, 2, 3, 4]}

        def capture(job_dir, view, frames, *a, **kw):     # 네 칸(2 × 2)을 저마다 다른 색으로 — 어느 칸이 시안으로 오려지는지 본다
            frames.mkdir(parents=True, exist_ok=True)
            im = Image.new("RGB", (3840, 2160 + 60))
            for k, col in enumerate(["#ff0000", "#00ff00", "#0000ff", "#ffffff"]):
                im.paste(col, ((k % 2) * 1920, (k // 2) * 1080, (k % 2 + 1) * 1920, (k // 2 + 1) * 1080))
            im.save(frames / "00000.png")
            return 1, told
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(A.export, "_capture", capture):
            job = Path(tmp) / "시안1"
            job.mkdir()
            (job / "job.json").write_text(json.dumps({"layout": "columns", "duration_seconds": 12.0, "moving": True, "swap": 0}), encoding="utf-8")
            shot, _, info = A.shoot(job, Path(tmp) / "프로필")
            self.assertEqual((info["times"], info["steps"]), ([4.27, 4.62, 4.97, 5.43], [1, 2, 3, 4]))
            with Image.open(shot) as im:
                self.assertEqual(im.getpixel((10, 10)), (255, 255, 255))       # 시안 = 마지막 칸(다 나온 순간)
            told.update(times=[0.42, 0.77, 1.5, 6.6], steps=[1, 2, 3], step_kind="items", leave=True)     # 맨 끝은 나가는 순간 — 시안은 셋째 칸
            shot, _, info = A.shoot(job, Path(tmp) / "프로필")
            self.assertEqual((info["steps"], info["step_kind"], info["leave"]), ([1, 2, 3], "items", True))
            with Image.open(shot) as im:
                self.assertEqual(im.getpixel((10, 10)), (0, 0, 255))
            told.pop("times")                                                   # 화면이 바꾸지 않았으면 보낸 네 순간 · 셋째 칸 그대로
            shot, _, info = A.shoot(job, Path(tmp) / "프로필")
            self.assertEqual((len(info["times"]), info["steps"]), (4, []))
            with Image.open(shot) as im:
                self.assertEqual(im.getpixel((10, 10)), (0, 0, 255))

        seen = []

        def chat(messages, **kw):
            seen.append(messages)
            return reply({"role": "assistant", "content": json.dumps({"checks": [], "text_seen": [], "readability": []})})
        info = {"big": 0.05, "sheet": Path("움직임.png"), "times": [4.27, 4.62, 4.97, 5.43], "seconds": 12.0, "frames": 360, "clear": False}
        with mock.patch.object(A, "picture", lambda p: {"type": "image_url", "image_url": {"url": str(p)}}):
            A.judge(Path("시안.png"), ["화살표가 차례로 나오는가"], chat=chat, info={**info, "steps": [1, 2, 3, 4]})
            A.judge(Path("시안.png"), ["화살표가 차례로 나오는가"], chat=chat, info=info)
        self.assertIn("화살표가 하나씩 더해지는 순간 4장", seen[0][1]["content"][0]["text"])
        self.assertIn("화살표 1 · 2 · 3 · 4개째까지", seen[0][1]["content"][0]["text"])
        self.assertNotIn("하나씩 더해지는", seen[1][1]["content"][0]["text"])
        with mock.patch.object(A, "picture", lambda p: {"type": "image_url", "image_url": {"url": str(p)}}):
            A.judge(Path("시안.png"), ["일정이 하나씩 나오는가"], chat=chat, info={**info, "steps": [1, 2, 4, 5, 6], "step_kind": "items", "leave": True})
        said = seen[2][1]["content"][0]["text"]
        for part in ("항목이 차례로 나오는 장면", "화면에 하나씩 더해지는 순간 5장", "차례 1 · 2 · 4 · 5 · 6번째까지", "맨 끝 한 장은 나가는 순간", "건너뛴 장 사이"):
            self.assertIn(part, said)

    def test_the_worker_is_told_what_the_judge_decided_not_only_what_failed(self):
        """걸린 것이 자동 검사뿐이어도 검수자의 판정은 담당에게 간다(11-24: 모두 '예'였는데 담당이 '판정을 내지 않았다'고 적었다)."""
        checks = ["이름이 맞는가", "마지막이 당첨인가", "다른 것이 당첨으로 안 보이는가"]
        all_yes = {"checks": [{"n": n, "ok": True, "why": "보인다"} for n in (1, 2, 3)]}
        self.assertIn("확인할 점 3개 — 예 3", A.judged(all_yes, checks))
        self.assertIn("모두 '예'로 판정했다", A.judged(all_yes, checks))
        mixed = {"checks": [{"n": 1, "ok": True}, {"n": 2, "ok": False, "why": "안 보인다"}]}
        self.assertEqual(A.judged(mixed, checks), "확인할 점 3개 — 예 1 · 아니오 1 · 판정 없음 1")
        self.assertEqual(A.judged(None, checks), "확인할 점 3개 — 예 0 · 판정 없음 3")
        self.assertEqual(A.judged({"checks": "엉뚱한 답"}, []), "확인할 점 0개 — 예 0")

    def test_the_page_times_the_arrow_steps_and_the_stage_lets_it(self):
        """화면 틀 쪽 약속(글자로 본다 — 실제 그림은 시안을 찍어 눈으로 확인했다): 화살표마다 박자를 적고, 다음 화살표 직전을 찍고, 무대가 그 시각을 받는다."""
        page = (A.build.TEMPLATES / "scene.html").read_text(encoding="utf-8")
        stage = (A.build.TEMPLATES / "stage.js").read_text(encoding="utf-8")
        for part in ("LINKB.push(BEAT)", "MOT.order !== 'one_by_one'", "startOf(beats[i + 1]) - 0.01", "steps: STEPS.shown", "step_kind: STEPS.kind",
                     "LINKB.length >= 2 ? 'links' : 'items'", "GAP < 0.12", "beats = kind === 'links' ? LINKB : drawnBeats",
                     "[...new Set(OPS.map(o => o.b))].filter(b => b > 0)", "leave ? [...times, +Math.min(LASTT, OUT_AT + OUT_LEN * 0.6).toFixed(3)] : times",
                     "ST.run(content, sent, AUDIT, STEPS ? () => STEPS.times : null)"):
            self.assertIn(part, page)
        self.assertIn("function run(content, wait, audit, retime)", stage)
        self.assertIn("ASKED.length && retime ? (retime(ASKED) || ASKED) : ASKED", stage)       # 안 주면 받은 시각 그대로(지도 · 순위판은 달라지지 않는다)



class TwoSteps(Fake):
    """앱은 두 단계로 쓴다: 읽을 때 지시서(plan_request) → 사람이 지시서 카드에서 만들기(make_request). 지시서는 JSON 으로 저장했다가 이어 간다."""

    def test_plan_is_saved_then_make_resumes_with_drop_and_note(self):
        main = scripted([("write_brief", BRIEF), ("spawn_worker", WORKER), ("spawn_worker", {**WORKER, "role": "둘째 담당"})],
                        [("deliver", {"accept": ["w1"], "message": "w1 납품"})])
        planned = A.plan_request(REQ, log=lambda *a: None, chat=main)
        self.assertTrue(planned["main_ok"])
        self.assertEqual([w["role"] for w in planned["workers"]], ["진술 대조 담당", "둘째 담당"])
        self.assertNotEqual(planned["msgs"][0]["role"], "system")          # 긴 시스템 지시는 저장하지 않는다(이어 갈 때 다시 붙인다)
        saved = json.loads(json.dumps(planned, ensure_ascii=False))
        work = scripted([("draw_draft", {"scene": scene()}), ("request_review", {"note": ""})], [("submit", {"summary": "끝", "unresolved": []})])
        got = A.make_request(saved, self.work, self.work / "p", log=lambda *a: None, chat=main, worker_chat=work, judge_chat=judging(True),
                             drop=["w2"], note="제목은 짧게")
        self.assertEqual([w["worker"] for w in got["workers"]], ["w1"])
        self.assertEqual(got["delivery"]["accept"], ["w1"])
        w = got["workers"][0]
        self.assertEqual([v["ok"] for v in w["verdict"]], [True, True])          # 마지막 시안의 판정이 시안 카드로 간다
        self.assertTrue(w["final_shot"].endswith("시안1/시안.png"))
        self.assertIn("제작진이 덧붙인 말(따른다): 제목은 짧게", work.sent[0][1]["content"])
        second = main.sent[1]
        self.assertEqual(second[0]["content"], A.MAIN_SYSTEM)
        self.assertTrue(any(m["role"] == "tool" and "제작진이 뺌" in m["content"] for m in second))      # 뺀 담당도 메인에게 알린다
        self.assertEqual(sum(m["role"] == "tool" for m in second), 3)          # 첫 답의 도구 호출 셋 모두에 결과를 돌려준다


def other(kind="방 탈출 단서 보관함"):
    from mapcg import intake
    base = {k: None for k in intake.SCHEMA["properties"]}
    base.update(map_style="unknown", vehicle="unknown", layout="unknown", face_mode="unknown", camera="unknown", separate_moves=False,
                cuts=[], unclear=[], nearest="none", route_stops=[], unsupported=[], other_kind=kind)
    return base


class Messenger(unittest.TestCase):
    """메신저(server.Desk)의 맞춤 그래픽 갈래 — 틀 없는 종류 → 지시서 카드 → 만들기 → 시안 카드. Liner · 브라우저는 부르지 않는다."""

    def setUp(self):
        from mapcg import board, intake, server
        self.server, self.board = server, board
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.answer = other()
        chat = lambda messages, **kw: reply({"role": "assistant", "content": json.dumps(self.answer, ensure_ascii=False)})      # noqa: E731
        self.analyze = lambda t, images: intake.analyze(t, images, chat=chat, order=["liner"])      # noqa: E731

    def desk(self, **kw):
        d = self.server.Desk(self.root, liner=True, audit=mock.Mock(), analyze=self.analyze, **kw)
        sync = mock.patch.object(d, "_spawn", side_effect=lambda fn, *args, **k: fn(*args))
        sync.start()
        self.addCleanup(sync.stop)
        return d

    def send(self, d, planned):
        with mock.patch.object(self.board, "read", return_value=None), mock.patch.object(A, "plan_request", return_value=planned) as plan:
            d.send(REQ, [], room=d.room)
        return plan

    def planned(self, workers=1, ok=True):
        return {"main_ok": ok, "brief": BRIEF, "problems": [] if ok else ["확인할 점이 없다"], "rounds": [], "msgs": [],
                "workers": [{**WORKER, "role": f"담당{n}"} for n in range(1, workers + 1)] if ok else [],
                "unable": [{"route": "사람 지정 필요", "existing": "없음", "reason": "사진 속 위치", "needed": ["상자 좌표"]}],
                "calls": [["c1", "write_brief", BRIEF]] + [[f"c{n + 1}", "spawn_worker", WORKER] for n in range(1, workers + 1)]}

    def test_brief_card_carries_the_found_sources(self):
        d = self.desk()
        planned = {**self.planned(1), "research": [{"id": "r1", "question": "우승 횟수", "answer": "브라질 5회",
                                                    "references": [{"n": "r1-1", "title": "기록", "hostname": "fifa.com", "date": "", "url": "https://fifa.com/x", "text": "…"}]}]}
        self.send(d, planned)
        card = d.msgs[-1]
        self.assertEqual(card["research"][0]["refs"][0]["hostname"], "fifa.com")
        self.assertEqual(card["research"][0]["question"], "우승 횟수")

    def test_unknown_kind_gets_a_brief_card_and_the_plan_is_saved(self):
        d = self.desk()
        self.send(d, self.planned(2))
        m = d.msgs[-1]
        self.assertEqual((m["kind"], m["what"]), ("scene", "방 탈출 단서 보관함"))
        self.assertEqual([w["wid"] for w in m["workers"]], ["w1", "w2"])
        self.assertEqual(m["unable"][0]["route"], "사람 지정 필요")
        self.assertTrue((d.out / m["plan"] / "지시서.json").is_file())
        self.assertIn("지시서를 썼습니다", d.msgs[-2]["title"])

    def test_offline_or_failed_main_falls_back_to_unknown_card(self):
        d = self.desk()
        self.send(d, self.planned(ok=False))
        self.assertEqual(d.msgs[-1]["kind"], "unknown")
        self.assertTrue(any("지시서를 쓰지 못했습니다" in n for n in d.msgs[-1]["notes"]))
        off = self.server.Desk(self.root / "off", offline=True, analyze=self.analyze)
        with mock.patch.object(off, "_spawn", side_effect=lambda fn, *args, **k: fn(*args)):
            plan = self.send(off, self.planned())
        plan.assert_not_called()                                      # 오프라인이면 메인을 부르지 않는다
        self.assertEqual(off.msgs[-1]["kind"], "unknown")

    def test_make_checks_the_request(self):
        d = self.desk()
        self.send(d, self.planned(2))
        plan = d.msgs[-1]["plan"]
        bad = [{"plan": "../밖"}, {"plan": "없는_지시서"}, {"plan": plan, "drop": ["w1", "w2"]}, {"plan": plan, "note": "가" * 301}]
        for raw in bad:
            with self.assertRaises(self.server.DeskError):
                d.make({"kind": "scene", **raw}, room=d.room)
        with self.assertRaises(self.server.DeskError):
            d.revise({"kind": "scene", "title": "x"}, "고쳐 주세요", room=d.room)

    def test_make_runs_workers_and_gives_draft_cards_delivered_first(self):
        d = self.desk()
        self.send(d, self.planned(2))
        card = d.msgs[-1]

        def made(planned, work, profile, **kw):
            self.assertEqual((kw["drop"], kw["note"]), ([], "3초로"))
            for wid in ("w1", "w2"):
                j = Path(work) / f"{wid}_담당" / "시안2"
                j.mkdir(parents=True)
                (j / "job.json").write_text(json.dumps({"kind": "scene", "layout": "headline", "duration_seconds": 3.0, "moving": True}), encoding="utf-8")

            def w(wid, res):
                return {"worker": wid, "role": f"{wid} 담당", "result": res, "summary": "", "unresolved": [], "order": ["draw_draft"],
                        "draws": 2, "reviews": 1, "redo": None, "layout": "headline", "limits": {}, "goal": "",
                        "verdict": [{"check": "크게 보이는가", "ok": True, "why": ""}], "final_shot": f"{wid}_담당/시안2/시안.png"}
            return {"main_ok": True, "brief": BRIEF, "unable": [], "rounds": [],
                    "workers": [w("w1", "미해결 제출"), w("w2", "합격"), {**w("w3", "포기"), "final_shot": None, "summary": "사진이 필요"}],
                    "delivery": {"accept": ["w2"], "message": "w2 를 납품합니다", "by": "메인"}}

        with mock.patch.object(A, "make_request", side_effect=made):
            d.make({"kind": "scene", "plan": card["plan"], "rid": card["rid"], "what": card["what"], "note": "3초로"}, room=d.room)
        results = [m for m in d.msgs if m["kind"] == "result"]
        self.assertEqual([r["scene"]["worker"] for r in results], ["w2", "w1"])          # 메인이 납품한 것 먼저
        self.assertEqual([r["scene"]["accepted"] for r in results], [True, False])
        r = results[0]
        self.assertEqual((r["request"]["kind"], r["request"]["duration_seconds"], r["request"]["moving"]), ("scene", 3.0, True))
        self.assertTrue((d.out / r["job"] / "job.json").is_file())
        said = [m["text"] for m in d.msgs if m["kind"] == "text" and m["who"] == "desk"]
        self.assertTrue(said and "w2 를 납품합니다" in said[0] and "사진이 필요" in said[0])


    def test_leftover_requests_of_a_map_get_their_own_brief_card(self):
        self.answer = {**other(), "other_kind": None, "from_name": "여의도공원", "to_name": "여의나루역", "duration_seconds": 10,
                       "unsupported": ["페이스 6분/km 문구"],
                       "route_stops": [{"name": "서강대교 남단", "caption": "", "pass": True, "stop": False, "hold_seconds": None}]}
        d = self.desk()
        with mock.patch.object(self.board, "read", return_value=None), \
                mock.patch.object(A, "plan_request", return_value=self.planned(1)) as main:
            d.send("여의도공원에서 서강대교 남단 지나 여의나루역까지 10초, 페이스 6분/km 문구도", [], room=d.room)
        kinds = [m["kind"] for m in d.msgs]
        self.assertLess(kinds.index("card"), kinds.index("scene"))          # 지도 확인 카드가 먼저, 남은 요청의 지시서 카드가 뒤에
        focus = main.call_args.kwargs["focus"]
        self.assertIn("- 페이스 6분/km 문구", focus)
        self.assertNotIn("지나는 곳", focus)                                  # 컷을 나누면 되는 것은 넘기지 않는다
        self.assertIn("지도 영상(여의도공원 → 여의나루역", focus)
        sc = d.msgs[-1]
        self.assertEqual((sc["what"], sc["leftover"]), ("지도의 남은 요청", ["페이스 6분/km 문구"]))
        self.assertIn("담당 1", d.msgs[-2]["title"])

    def test_leftover_that_cannot_be_made_only_says_why(self):
        self.answer = {**other(), "other_kind": None, "from_name": "홍대", "to_name": "강남", "unsupported": ["1km마다 거리 표시"]}
        d = self.desk()
        only_unable = {**self.planned(1), "workers": []}
        with mock.patch.object(self.board, "read", return_value=None), mock.patch.object(A, "plan_request", return_value=only_unable):
            d.send("홍대에서 강남까지, 1km마다 거리 표시", [], room=d.room)
        self.assertNotIn("scene", [m["kind"] for m in d.msgs])
        self.assertIn("따로 만들 수 없습니다", d.msgs[-1]["title"])
        self.assertIn("사람 지정 필요: 사진 속 위치", d.msgs[-1]["lines"][-1])

    def test_focus_is_shown_to_main_but_quotes_must_come_from_the_request(self):
        focus = A.leftover_focus("지도 영상", ["페이스 6분/km 문구"])
        brief = {**BRIEF, "facts": [{"id": "f1", "fact": "페이스", "quote": "페이스 6분/km 문구"}]}
        main = scripted([("write_brief", brief), ("spawn_worker", {**WORKER, "fact_ids": ["f1"]})],
                        [("write_brief", brief), ("spawn_worker", {**WORKER, "fact_ids": ["f1"]})])
        got = A.plan_request("홍대에서 강남까지", log=lambda *a: None, chat=main, focus=focus)
        self.assertIn("[그 담당이 못 그린 요청 — 이것만 본다]", main.sent[0][1]["content"])
        self.assertFalse(got["main_ok"])          # 덧붙임에만 있는 말은 원문 근거가 아니다


    def test_all_broken_request_still_ends_with_a_reason_and_a_record(self):
        """담당 둘 다 고장 · 메인 마무리도 실패 — 메신저가 예외 없이 끝까지 가고, '완성'이라 쓰지 않으며, 까닭 글과 결과.json 을 남긴다."""
        d = self.desk()
        self.send(d, self.planned(2))
        card = d.msgs[-1]
        with mock.patch.object(A, "_call", side_effect=RuntimeError("시간 초과")):
            d.make({"kind": "scene", "plan": card["plan"], "rid": card["rid"], "what": card["what"]}, room=d.room)      # 예외가 올라오지 않는다
        self.assertNotIn("result", [m["kind"] for m in d.msgs])
        prog = [m for m in d.msgs if m["kind"] == "progress"][-1]
        self.assertEqual(prog["state"], "fail")
        self.assertIn("시안을 한 장도", prog["error"])
        said = [m["text"] for m in d.msgs if m["kind"] == "text" and m["who"] == "desk"]
        self.assertTrue(said and "마무리 판단을 하지 못했습니다" in said[-1] and "고장" in said[-1])
        found = list((d.out / card["plan"]).glob("제작_*/결과.json"))
        self.assertEqual(len(found), 1)
        rec = json.loads(found[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["delivery"]["by"], "메인 호출 실패")
        self.assertEqual([w["result"] for w in rec["workers"]], ["고장", "고장"])


    def test_draft_card_can_be_redrawn_in_another_style_without_liner(self):
        d = self.desk()
        self.send(d, self.planned(1))
        card = d.msgs[-1]

        def made(planned, work, profile, **kw):
            j = Path(work) / "w1_담당" / "시안1"
            A.install(j, A.SAMPLES["rows"])
            (j / "scene.json").write_text(json.dumps(A.SAMPLES["rows"], ensure_ascii=False), encoding="utf-8")
            return {"main_ok": True, "brief": BRIEF, "unable": [], "rounds": [], "delivery": {"accept": ["w1"], "message": "", "by": "메인"},
                    "workers": [{"worker": "w1", "role": "줄 담당", "result": "합격", "summary": "", "unresolved": [], "order": [], "draws": 1, "reviews": 1,
                                 "redo": None, "layout": "rows", "limits": {}, "goal": "", "verdict": [{"check": "보이는가", "ok": True, "why": ""}],
                                 "final_shot": "w1_담당/시안1/시안.png"}]}

        with mock.patch.object(A, "make_request", side_effect=made):
            d.make({"kind": "scene", "plan": card["plan"], "rid": card["rid"], "what": card["what"]}, room=d.room)
        first = d.msgs[-1]
        self.assertEqual(first["request"]["art_style"], A.request.HOUSE_STYLE)
        shots = []
        with mock.patch.object(A, "shoot", side_effect=lambda job_dir, profile: shots.append(job_dir) or (Path(job_dir) / "시안.png", ["글자가 작음"], {})), \
                mock.patch.object(A, "_call", side_effect=AssertionError("Liner 를 부르면 안 된다")):
            d.make({**first["request"], "art_style": "v4", "restyle_from": first["job"]}, room=d.room)
        again = d.msgs[-1]
        self.assertEqual((again["kind"], again["request"]["art_style"], again["scene"]["worker"]), ("result", "v4", "w1"))
        self.assertNotEqual(again["job"], first["job"])
        self.assertEqual(Path(again["job"]).parent, Path(first["job"]).parent)          # 원래 시안 옆 폴더
        self.assertTrue((d.out / again["job"] / "style_v4.js").is_file())
        self.assertIn("화풍만 바꿔 다시 그렸습니다", again["warnings"][0])
        self.assertIn("자동 검사: 글자가 작음", again["warnings"])
        self.assertEqual(len(shots), 1)
        with self.assertRaises(self.server.DeskError):              # 지금 대화에 없는 시안은 받지 않는다
            d.make({**first["request"], "art_style": "v4", "restyle_from": "../밖/시안"}, room=d.room)
        with self.assertRaises(A.request.RequestError):
            d.make({**first["request"], "art_style": "v99", "restyle_from": first["job"]}, room=d.room)



CUP = "축구 특집에 월드컵 최다 우승국 막대그래프 넣어 주세요. 상위 다섯 나라와 우승 횟수, 출처도 화면 아래에."
FOUND = {"answer": "최다 우승국은 브라질(5회)이고 독일 · 이탈리아가 4회입니다.",
         "references": [{"title": "[기록] 월드컵 최다 우승국", "url": "https://www.fifa.com/ko/x", "hostname": "fifa.com",
                         "description": "브라질 5회 우승, 독일 4회, 이탈리아 4회, 아르헨티나 3회", "date": "Jul 19, 2026"}]}


def cup_brief(quote="브라질 5회 우승", source="r1-1"):
    return {"viewer_point": "월드컵 최다 우승국", "must_not_assert": [], "open_questions": [], "checks": ["막대가 보이는가"],
            "facts": [{"id": "f1", "fact": "브라질 5회", "quote": quote, "source": source},
                      {"id": "f2", "fact": "막대그래프 요청", "quote": "월드컵 최다 우승국 막대그래프", "source": "의뢰"}]}


CUP_WORKER = {"role": "우승 막대 담당", "goal": "막대", "fact_ids": ["f1", "f2"], "layouts": ["bars"], "checks": ["막대가 보이는가"], "max_tries": 2}


class Research(unittest.TestCase):
    """메인이 의뢰 글에 없는 공개 사실을 Liner 에이전트로 찾고(research), 출처 원문을 근거로만 사실을 적는다."""

    def plan(self, *steps, found=FOUND):
        asked = []

        def research(q):
            asked.append(q)
            if isinstance(found, Exception):
                raise found
            return found
        main = scripted(*steps)
        return A.plan_request(CUP, log=lambda *a: None, chat=main, research=research), main, asked

    def test_main_researches_then_briefs_with_the_source(self):
        got, main, asked = self.plan([("research", {"question": "월드컵 우승 횟수 상위 5개국", "why": "막대"})],
                                     [("write_brief", cup_brief()), ("spawn_worker", CUP_WORKER)])
        self.assertTrue(got["main_ok"], got["problems"])
        self.assertEqual(asked, ["월드컵 우승 횟수 상위 5개국"])
        self.assertEqual([r["id"] for r in got["research"]], ["r1"])
        self.assertEqual(got["research"][0]["references"][0]["hostname"], "fifa.com")
        f1 = got["brief"]["facts"][0]
        self.assertEqual((f1["source_name"], f1["source_url"]), ("fifa.com", "https://www.fifa.com/ko/x"))
        self.assertEqual(got["workers"][0]["fact_ids"], ["f1", "f2"])
        told = [m for m in main.sent[1] if m["role"] == "tool"][0]["content"]
        self.assertIn("r1-1", told)                                             # 출처 번호와 원문이 메인에게 돌아간다
        planned = json.loads(json.dumps(got, ensure_ascii=False))                 # 지시서.json 으로 저장된다
        self.assertEqual(planned["research"][0]["question"], "월드컵 우승 횟수 상위 5개국")

    def test_a_quote_found_nowhere_is_not_accepted(self):
        bad = cup_brief(quote="브라질 7회 우승")                                # 출처에도 의뢰 글에도 없는 수치
        got, _, _ = self.plan([("research", {"question": "q", "why": "w"})], [("write_brief", bad), ("spawn_worker", CUP_WORKER)],
                              [("write_brief", bad), ("spawn_worker", CUP_WORKER)])
        self.assertFalse(got["main_ok"])
        self.assertTrue(any("받은 자료에 없다" in p for p in got["problems"]))

    def test_source_written_as_request_is_corrected_when_only_the_found_text_has_it(self):
        got, _, _ = self.plan([("research", {"question": "q", "why": "w"})],
                              [("write_brief", cup_brief(source="의뢰")), ("spawn_worker", CUP_WORKER)])
        self.assertTrue(got["main_ok"], got["problems"])
        self.assertEqual((got["brief"]["facts"][0]["source"], got["brief"]["facts"][0]["source_name"]), ("자료", "fifa.com"))

    def test_research_failure_is_told_and_the_main_can_still_report_unable(self):
        unable = {"route": "자료 필요", "existing": "없음", "reason": "자료를 못 찾음", "needed": ["우승 기록"]}
        got, main, _ = self.plan([("research", {"question": "q", "why": "w"})],
                                 [("write_brief", {**cup_brief(quote="월드컵 최다 우승국 막대그래프", source="의뢰"),
                                                   "facts": [cup_brief()["facts"][1]]}), ("report_unable", unable)],
                                 found=RuntimeError("연결 실패"))
        self.assertTrue(got["main_ok"], got["problems"])
        self.assertEqual(got["research"], [])
        self.assertIn("자료를 찾지 못했다", [m for m in main.sent[1] if m["role"] == "tool"][0]["content"])
        self.assertEqual(got["unable"][0]["route"], "자료 필요")

    def test_research_is_capped(self):
        many = [("research", {"question": f"q{k}", "why": "w"}) for k in range(3)]
        got, main, asked = self.plan(many, [("research", {"question": "더", "why": "w"})],
                                     [("write_brief", cup_brief()), ("spawn_worker", CUP_WORKER)])
        self.assertEqual(len(asked), A.MAX_RESEARCH)                              # 한도 넘는 것은 보내지 않는다
        self.assertTrue(got["main_ok"], got["problems"])
        self.assertTrue(any("2번까지" in m["content"] for m in main.sent[-1] if m["role"] == "tool"))

    def test_research_tool_and_fact_source_are_in_the_main_tools(self):
        names = [t["function"]["name"] for t in A.MAIN_TOOLS]
        self.assertEqual(names[0], "research")
        brief = next(t for t in A.MAIN_TOOLS if t["function"]["name"] == "write_brief")["function"]["parameters"]
        self.assertIn("source", brief["properties"]["facts"]["items"]["required"])
        self.assertIn("research", A.MAIN_SYSTEM)


if __name__ == "__main__":
    unittest.main()
