"""Liner 라우터 · 전략 표(liner.ROUTES) — 일마다 정한 라우터와 전략이 실제 요청 본문에 들어가는지 본다(가짜 서버 · 과금 없음)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import intake, scene


class Reply:
    def __init__(self, body: bytes):
        self.buf = io.BytesIO(body)

    def read(self, n=-1):
        return self.buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


ANSWER = {"model": "gpt-6-sol", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}}


class LinerCase(unittest.TestCase):
    def setUp(self):
        self.liner = intake._liner()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "calls.jsonl"
        self.sent: list[tuple[str, dict]] = []
        for p in (mock.patch.object(self.liner, "LOG_PATH", self.log), mock.patch.object(self.liner, "api_key", lambda: "시험용-키"),
                  mock.patch.object(self.liner.time, "sleep", lambda s: None),
                  mock.patch.dict("os.environ", {"LINER_LOG": "on", "LINER_MODEL": "", "LINER_DIRECT": ""}),
                  mock.patch.object(self.liner, "_direct_refused", set()),
                  mock.patch.dict(self.liner._blocked, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def answers(self, *steps):
        """차례로: 예외면 던지고, dict 면 200 으로 답한다(마지막 것은 계속)."""
        steps = list(steps)

        def fake(req, timeout):
            self.sent.append((req.full_url, json.loads(req.data.decode("utf-8"))))
            step = steps.pop(0) if len(steps) > 1 else steps[0]
            if isinstance(step, Exception):
                raise step
            return Reply(json.dumps(step, ensure_ascii=False).encode())
        return mock.patch.object(self.liner, "_open", fake)

    def lines(self):
        return [json.loads(s) for s in self.log.read_text(encoding="utf-8").splitlines()]


class Routes(LinerCase):
    def test_reading_uses_the_table_router_and_strategy(self):
        with self.answers(ANSWER):
            intake.ask_liner("시스템", "홍대에서 강남", [], "map_request", {"type": "object"})
        body = self.sent[0][1]
        self.assertEqual((body["model"], body["routing_strategy"]), ("liner-gpt-router", "balanced"))

    def test_a_given_model_keeps_the_table_strategy_and_an_explicit_strategy_wins(self):
        with self.answers(ANSWER):
            intake.ask_liner("시스템", "글", [], "reading_audit", {"type": "object"}, model="liner-gemini-router")
            self.liner.chat([{"role": "user", "content": "x"}], task="scene_judge", routing_strategy="cost")
        self.assertEqual((self.sent[0][1]["model"], self.sent[0][1]["routing_strategy"]), ("liner-gemini-router", "balanced"))
        self.assertEqual((self.sent[1][1]["model"], self.sent[1][1]["routing_strategy"]), ("liner-gpt-router", "cost"))

    def test_scene_tool_calls_go_to_the_claude_router_with_quality_and_are_logged_by_task(self):
        with self.answers({**ANSWER, "model": "claude-opus-5.5"}), self.liner.request("의뢰-장면"):
            scene._call([{"role": "user", "content": "x"}], model=scene.MAIN_MODEL, tools=scene.MAIN_TOOLS)
        body = self.sent[0][1]
        self.assertEqual((body["model"], body["routing_strategy"]), ("liner-claude-router", "quality"))
        self.assertNotIn("task", body)                                   # 일 이름은 기록용 — 보내지 않는다
        self.assertEqual(self.lines()[0]["task"], "scene_agent")

    def test_liner_model_env_still_overrides_the_default_router(self):
        with mock.patch.dict("os.environ", {"LINER_MODEL": "liner-claude-router"}), self.answers(ANSWER):
            intake.ask_liner("시스템", "글", [], "map_request", {"type": "object"})
        self.assertEqual((self.sent[0][1]["model"], self.sent[0][1]["routing_strategy"]), ("liner-claude-router", "balanced"))

    def test_direct_model_ids_get_no_strategy(self):
        with self.answers(ANSWER):
            self.liner.chat([{"role": "user", "content": "x"}], model="openai/gpt-transcribe", task="map_request")
        self.assertNotIn("routing_strategy", self.sent[0][1])


class Fallback(LinerCase):
    """라우터가 실패하면 다른 회사 라우터로 한 번 더 — 보내지 못했거나 5xx 일 때만."""

    def by_model(self, failing: dict):
        """요청 본문의 모델마다 답을 정한다: failing[모델] = 던질 예외(없으면 200)."""
        def fake(req, timeout):
            body = json.loads(req.data.decode("utf-8"))
            self.sent.append((req.full_url, body))
            err = failing.get(body["model"])
            if err is not None:
                raise err
            return Reply(json.dumps({**ANSWER, "model": body["model"] + "-답"}, ensure_ascii=False).encode())
        return mock.patch.object(self.liner, "_open", fake)

    @staticmethod
    def http(code):
        import urllib.error
        return urllib.error.HTTPError("http://x", code, "오류", {}, io.BytesIO(b'{"error":"x"}'))

    def models(self):
        return [b["model"] for _, b in self.sent]

    def test_server_error_goes_to_the_other_company_once(self):
        with self.by_model({"liner-claude-router": self.http(502)}), self.liner.request("의뢰-대체"):
            out = self.liner.chat([{"role": "user", "content": "x"}], model="liner-claude-router", retries=1)
        self.assertEqual(out["model"], "liner-gpt-router-답")
        self.assertEqual(self.models(), ["liner-claude-router", "liner-claude-router", "liner-gpt-router"])     # 같은 라우터 재시도 뒤 한 번
        self.assertTrue(all("fallback" not in b for _, b in self.sent))
        self.assertIn("다른 회사 라우터로 다시", self.lines()[-1]["note"])

    def test_unsent_connection_failure_also_goes_to_the_other_company(self):
        import urllib.error
        with self.by_model({"liner-gpt-router": urllib.error.URLError("이름 풀이 실패")}):
            out = self.liner.chat([{"role": "user", "content": "x"}], retries=0, task="map_request")
        self.assertEqual(out["model"], "liner-claude-router-답")

    def test_no_second_send_when_it_may_have_been_received_or_will_fail_anyway(self):
        for err in (TimeoutError("끊김"), self.http(402), self.http(400)):
            self.sent.clear()
            with self.subTest(err=type(err).__name__ + str(getattr(err, "code", ""))), self.by_model({"liner-gpt-router": err}), \
                    mock.patch.dict(self.liner._blocked, {}, clear=True):
                with self.assertRaises(self.liner.LinerError):
                    self.liner.chat([{"role": "user", "content": "x"}], retries=0)
                self.assertEqual(self.models(), ["liner-gpt-router"])

    def test_caller_can_name_the_third_company_or_turn_it_off(self):
        with self.by_model({"liner-gpt-router": self.http(503)}):
            out = self.liner.chat([{"role": "user", "content": "x"}], retries=0, fallback="liner-gemini-router")
            self.assertEqual(out["model"], "liner-gemini-router-답")
            with self.assertRaises(self.liner.LinerError):
                self.liner.chat([{"role": "user", "content": "x"}], retries=0, fallback=False)

    def test_audit_and_judge_fall_back_to_the_third_company(self):
        from mapcg import audit
        plan, _ = audit.pick("liner", {"liner": True, "claude": False})
        self.assertEqual((plan["model"], plan["fallback"]), ("liner-claude-router", "liner-gemini-router"))     # 읽기 gpt · 감사 claude · 남는 gemini
        seen = {}
        with mock.patch.object(scene, "picture", lambda p: {"type": "image_url", "image_url": {"url": "data:,"}}):
            scene.judge(Path("시안.png"), ["보이는가"], chat=lambda m, **kw: seen.update(kw) or {
                "model": "x", "choices": [{"message": {"content": json.dumps({"checks": [], "text_seen": [], "readability": []})}}]})
        self.assertEqual(seen["fallback"], "liner-gemini-router")


class Summary(LinerCase):
    """의뢰 하나가 쓴 Liner — 호출 수 · 시간 · 어림 비용 · 일마다 답한 모델(카드의 한 줄)."""

    def write(self, *rows):
        self.log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    def test_counts_prices_and_models(self):
        u = lambda p, c: {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}      # noqa: E731
        self.write(
            {"request": "의뢰-1", "path": "/chat/completions", "task": "map_request", "answered_model": "gpt-6-sol", "status": 200, "final": True, "seconds": 2.5, "usage": u(10000, 1000)},
            {"request": "의뢰-1", "path": "/chat/completions", "task": "reading_audit", "answered_model": "claude-opus-5.5", "status": 200, "final": True, "seconds": 6.0, "usage": u(10000, 1000)},
            {"request": "의뢰-1", "path": "/tools/search/web", "task": "web_search", "status": 200, "final": True, "seconds": 1.0},
            {"request": "의뢰-1", "path": "/images/generations", "asked_model": "openai/gpt-image-2", "status": 200, "final": True, "seconds": 9.0, "usage": {"cost": 0.014}},
            {"request": "의뢰-1", "path": "/chat/completions", "task": "scene_judge", "answered_model": "gpt-6.2-sol", "status": 502, "final": False, "seconds": 3.0},
            {"request": "의뢰-1", "path": "/chat/completions", "task": "scene_judge", "answered_model": "gpt-6.2-sol", "status": 200, "final": True, "seconds": 4.0, "usage": u(1000, 0)},
            {"request": "다른-의뢰", "path": "/chat/completions", "task": "map_request", "answered_model": "gpt-6-sol", "status": 200, "final": True, "seconds": 99, "usage": u(9, 9)})
        s = self.liner.summary("의뢰-1")
        self.assertEqual((s["calls"], s["failed"], s["seconds"]), (5, 0, 25.5))                  # 다시 보낸 시도의 시간도 기다린 시간이다
        # gpt-6-sol 2/10 · opus 4/20 · 검색 0.002 · 그림 0.014 · 표에 없는 gpt-6.2-sol 은 sol 계열 값
        self.assertAlmostEqual(s["usd"], 0.03 + 0.06 + 0.002 + 0.014 + 0.002, places=4)
        self.assertFalse(s["guess"])
        self.assertEqual(s["models"][:2], [("map_request", "gpt-6-sol"), ("reading_audit", "claude-opus-5.5")])

    def test_card_line_names_each_job_and_its_model(self):
        from mapcg import server
        fake = {"calls": 2, "failed": 0, "seconds": 8.4, "usd": 0.0123, "guess": False,
                "models": [("map_request", "gpt-6-sol"), ("reading_audit", "claude-sonnet-5.5")]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(self.liner, "summary", return_value=fake) as asked:
            d = server.Desk(Path(tmp), liner=False)
            m = d.add("desk", "card", draft={"rid": "의뢰-9"})
            plain = d.add("desk", "text", text="안녕")
        asked.assert_called_once_with("의뢰-9")
        self.assertEqual(m["liner"], "Liner 2번 · 8초 · 약 $0.012 — 읽기 gpt-6-sol · 감사 claude-sonnet-5.5")
        self.assertNotIn("liner", plain)                                       # 글 카드에는 싣지 않는다
        self.assertEqual(server.Desk._liner_line(d, ""), "")                    # 번호 없는 호출은 세지 않는다


if __name__ == "__main__":
    unittest.main()


class Direct(Fallback):
    """모델 직접 지정(liner.DIRECT, LINER_DIRECT=on 일 때만) — 메인 · 담당 · CG Director. 안 되면 원래 라우터로 이어 간다."""
    ASK = [{"role": "user", "content": "x"}]
    OPUS = "anthropic/claude-opus-5.5"

    def setUp(self):
        super().setUp()
        p = mock.patch.dict("os.environ", {"LINER_DIRECT": "on"})
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def no_such_model():
        import urllib.error
        return urllib.error.HTTPError("http://x", 400, "오류", {}, io.BytesIO(b'{"error":{"message":"The model does not exist."}}'))

    def test_director_and_scene_agent_are_sent_to_the_direct_model(self):
        with self.by_model({}):
            for task in ("director", "scene_agent"):
                out = self.liner.chat(self.ASK, model="liner-claude-router", task=task, retries=0, fallback=False)
                self.assertEqual(out["model"], self.OPUS + "-답")
        self.assertEqual(self.models(), [self.OPUS, self.OPUS])

    def test_other_tasks_and_other_models_are_left_alone(self):
        with self.by_model({}):
            self.liner.chat(self.ASK, model="liner-claude-router", task="reading_audit", retries=0)      # 표에 없는 일
            self.liner.chat(self.ASK, model="liner-gpt-router", task="scene_agent", retries=0)           # 부른 쪽이 다른 모델을 골랐다
        self.assertEqual(self.models(), ["liner-claude-router", "liner-gpt-router"])

    def test_it_is_off_unless_switched_on(self):
        with mock.patch.dict("os.environ", {"LINER_DIRECT": ""}), self.by_model({}):
            self.liner.chat(self.ASK, model="liner-claude-router", task="scene_agent", retries=0)
        self.assertEqual((self.sent[0][1]["model"], self.sent[0][1]["routing_strategy"]), ("liner-claude-router", "quality"))

    def test_refused_model_falls_back_to_the_router_even_without_fallback_and_is_not_tried_again(self):
        with self.by_model({self.OPUS: self.no_such_model()}):
            out = self.liner.chat(self.ASK, model="liner-claude-router", task="director", retries=0, fallback=False)
            self.liner.chat(self.ASK, model="liner-claude-router", task="scene_agent", retries=0)
        self.assertEqual(out["model"], "liner-claude-router-답")
        self.assertEqual(self.models(), [self.OPUS, "liner-claude-router", "liner-claude-router"])       # 두 번째 호출은 바로 라우터로
        self.assertEqual(self.sent[1][1]["routing_strategy"], "balanced")                                # 그 일의 전략 그대로
        self.assertIn("직접 지정", self.lines()[1]["note"])

    def test_server_error_goes_to_the_router_only_when_fallback_is_on(self):
        with self.by_model({self.OPUS: self.http(502)}):
            out = self.liner.chat(self.ASK, model="liner-claude-router", task="scene_agent", retries=0)
            self.assertEqual(out["model"], "liner-claude-router-답")
            with self.assertRaises(self.liner.LinerError):       # CG Director 는 제 시간 한도 안에서 스스로 다시 보낸다
                self.liner.chat(self.ASK, model="liner-claude-router", task="director", retries=0, fallback=False)
        self.assertEqual(self.models(), [self.OPUS, "liner-claude-router", self.OPUS])                   # 5xx 는 거절이 아니다 — 다음에도 직접 보낸다

    def test_a_bad_request_is_not_sent_twice(self):
        with self.by_model({self.OPUS: self.http(400)}):          # 모델이 아니라 요청 내용이 틀린 400
            with self.assertRaises(self.liner.LinerError):
                self.liner.chat(self.ASK, model="liner-claude-router", task="scene_agent", retries=0)
        self.assertEqual(self.models(), [self.OPUS])
