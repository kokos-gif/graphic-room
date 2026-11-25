"""앱 코드리뷰(a27d633) 06 · 10 · 11 회귀 시험 — 출처 번호와 근거의 짝 검증, 자료 찾기 시도 한도, 상대 출력 경로의 미리보기 열기
(가짜 응답만 쓴다. Liner 도 검색도 실제 브라우저도 부르지 않는다)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import __main__ as cli
from mapcg import scene as A

ROOT = Path(__file__).resolve().parent.parent

CUP = "축구 특집에 월드컵 최다 우승국 막대그래프 넣어 주세요. 상위 다섯 나라와 우승 횟수, 출처도 화면 아래에."
# 축구 기록은 1번 자료(fifa.com)에만 있고, 2번 자료는 날씨 사이트다
FOUND = {"answer": "역대 기록과 이번 주 날씨를 찾았습니다.",
         "references": [{"title": "[기록] 월드컵 최다 우승국", "url": "https://www.fifa.com/ko/x", "hostname": "fifa.com",
                         "description": "브라질 5회 우승, 독일 4회, 이탈리아 4회, 아르헨티나 3회", "date": "Jul 19, 2026"},
                        {"title": "주간 날씨 예보", "url": "https://weather.example.com/week", "hostname": "weather.example.com",
                         "description": "이번 주말은 전국이 대체로 맑고 낮 기온은 18도 안팎입니다", "date": "Nov 20, 2026"}]}
ASK = [("research", {"question": "월드컵 우승 횟수 상위 5개국", "why": "막대"})]
WORKER = {"role": "우승 막대 담당", "goal": "막대", "fact_ids": ["f1", "f2"], "layouts": ["bars"], "checks": ["막대가 보이는가"], "max_tries": 2}
UNABLE = {"route": "자료 필요", "existing": "없음", "reason": "자료를 못 찾음", "needed": ["우승 기록"]}


def brief(source="r1-1", request_source="의뢰"):
    """f1 = 자료에서 온 축구 기록(출처 번호를 바꿔 본다), f2 = 의뢰 글에 있는 요청."""
    return {"viewer_point": "월드컵 최다 우승국", "must_not_assert": [], "open_questions": [], "checks": ["막대가 보이는가"],
            "facts": [{"id": "f1", "fact": "브라질 5회", "quote": "브라질 5회 우승", "source": source},
                      {"id": "f2", "fact": "막대그래프 요청", "quote": "월드컵 최다 우승국 막대그래프", "source": request_source}]}


def request_only_brief():
    b = brief()
    return {**b, "facts": [b["facts"][1]]}


def call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def scripted(*steps):
    """차례대로 답하는 가짜 메인: 단계마다 도구 호출 목록 하나. 받은 대화는 그때 모습 그대로 적어 둔다."""
    it, sent = iter(steps), []

    def chat(messages, **kw):
        sent.append([dict(m) for m in messages])
        step = next(it)
        return {"model": "m", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "", "tool_calls": [call(n, a, f"c{len(sent)}_{k}") for k, (n, a) in enumerate(step)]}}]}
    chat.sent = sent
    return chat


def plan(*steps, research):
    main = scripted(*steps)
    return A.plan_request(CUP, log=lambda *a: None, chat=main, research=research), main


class 출처_번호와_근거(unittest.TestCase):
    """06 — 근거(quote)는 모델이 지목한 출처 번호의 원문에 있어야 하고, 화면용 사이트 이름 · 주소는 그렇게 확인한 번호로만 채운다."""

    def plan(self, *steps):
        return plan(ASK, *steps, research=lambda q: FOUND)

    def test_기록이_1번_자료에만_있는데_2번_날씨_사이트를_고르면_승인되지_않는다(self):
        wrong = [("write_brief", brief("r1-2")), ("spawn_worker", WORKER)]
        got, _ = self.plan(wrong, wrong)
        self.assertFalse(got["main_ok"])
        self.assertTrue(any("r1-2" in p for p in got["problems"]), got["problems"])
        self.assertEqual(got["workers"], [])
        f1 = got["brief"]["facts"][0]
        self.assertNotIn("weather", f1.get("source_name", ""))                  # 확인되지 않은 번호의 사이트는 붙이지 않는다
        self.assertNotIn("source_url", f1)

    def test_1번을_고르면_지시서에도_1번_사이트가_표시된다(self):
        got, _ = self.plan([("write_brief", brief("r1-1")), ("spawn_worker", WORKER)])
        self.assertTrue(got["main_ok"], got["problems"])
        f1 = got["brief"]["facts"][0]
        self.assertEqual((f1["source"], f1["source_name"], f1["source_url"]), ("r1-1", "fifa.com", "https://www.fifa.com/ko/x"))

    def test_걸린_점을_듣고_번호를_1번으로_바로잡으면_승인된다(self):
        got, main = self.plan([("write_brief", brief("r1-2")), ("spawn_worker", WORKER)], [("write_brief", brief("r1-1")), ("spawn_worker", WORKER)])
        self.assertTrue(got["main_ok"], got["problems"])
        self.assertIn("r1-2", main.sent[2][-1]["content"])                      # 어느 출처 번호가 걸렸는지 메인에게 알린다
        self.assertEqual(got["brief"]["facts"][0]["source_name"], "fifa.com")

    def test_check_main_은_지목한_출처의_원문만_근거로_본다(self):
        sources = {"r1-1": "[기록] 월드컵 최다 우승국 브라질 5회 우승, 독일 4회", "r1-2": "주간 날씨 예보 이번 주말은 전국이 대체로 맑고"}

        def problems(source):
            calls = A.parse_calls({"tool_calls": [call("write_brief", brief(source), "c1"), call("spawn_worker", WORKER, "c2")]})
            return A.check_main(calls, CUP, " ".join(sources.values()), sources)[1]
        self.assertEqual(problems("r1-1"), [])
        self.assertTrue(any("r1-2" in p for p in problems("r1-2")))

    def test_의뢰_글에_있는_근거에_출처_번호를_적으면_의뢰로_바로잡고_사이트를_붙이지_않는다(self):
        got, _ = self.plan([("write_brief", brief("r1-1", request_source="r1-2")), ("spawn_worker", WORKER)])
        self.assertTrue(got["main_ok"], got["problems"])
        f2 = got["brief"]["facts"][1]
        self.assertEqual(f2["source"], "의뢰")
        self.assertNotIn("source_name", f2)

    def test_번호_없이_자료로_바로잡힌_사실에는_근거가_있는_사이트만_붙는다(self):
        got, _ = self.plan([("write_brief", brief("의뢰")), ("spawn_worker", WORKER)])
        self.assertTrue(got["main_ok"], got["problems"])
        f1 = got["brief"]["facts"][0]
        self.assertEqual((f1["source"], f1["source_name"]), ("자료", "fifa.com"))    # 날씨 사이트는 빠진다


class 자료_찾기_한도(unittest.TestCase):
    """10 — 의뢰마다 실제로 보낸 자료 찾기를 센다(실패도 한도에 든다)."""

    def setUp(self):
        self.asked = []

    def failing(self, q):
        self.asked.append(q)
        raise RuntimeError("연결 실패")

    def asks(self, n, tag="q"):
        return [("research", {"question": f"{tag}{k}", "why": "w"}) for k in range(n)]

    def test_한_응답에_검색_5개를_넣고_전부_실패해도_호출은_2번까지다(self):
        got, main = plan(self.asks(5), [("write_brief", request_only_brief()), ("report_unable", UNABLE)], research=self.failing)
        self.assertEqual(self.asked, ["q0", "q1"])
        self.assertEqual(len(self.asked), A.MAX_RESEARCH)
        told = [m["content"] for m in main.sent[1] if m["role"] == "tool"]
        self.assertEqual(len(told), 5)                                           # 도구 호출마다 결과는 돌려준다
        self.assertEqual(sum("자료를 찾지 못했다" in t for t in told), 2)
        self.assertEqual(sum("2번까지" in t for t in told), 3)
        self.assertTrue(got["main_ok"], got["problems"])
        self.assertEqual(got["research"], [])

    def test_여러_응답에_걸쳐_요청해도_같은_의뢰의_남은_횟수를_쓴다(self):
        got, main = plan(self.asks(1, "a"), self.asks(3, "b"), [("write_brief", request_only_brief()), ("report_unable", UNABLE)], research=self.failing)
        self.assertEqual(self.asked, ["a0", "b0"])                               # 첫 응답에서 1번 썼으니 다음 응답은 1번만
        self.assertTrue(any("2번까지" in m["content"] for m in main.sent[2] if m["role"] == "tool"))
        self.assertTrue(got["main_ok"], got["problems"])

    def test_성공한_뒤_실패한_검색도_같은_한도에서_빠진다(self):
        def research(q):
            self.asked.append(q)
            if len(self.asked) > 1:
                raise RuntimeError("연결 실패")
            return FOUND
        got, _ = plan(self.asks(1, "a"), self.asks(2, "b"), [("write_brief", brief("r1-1")), ("spawn_worker", WORKER)], research=research)
        self.assertEqual(self.asked, ["a0", "b0"])
        self.assertTrue(got["main_ok"], got["problems"])
        self.assertEqual([r["id"] for r in got["research"]], ["r1"])             # 받은 자료는 그대로 쓴다
        self.assertEqual(got["brief"]["facts"][0]["source_name"], "fifa.com")


class 상대_경로_미리보기(unittest.TestCase):
    """11 — 결과 폴더를 상대 경로로 주고 --open 을 붙여도, 만든 HTML 의 절대 경로로 파일 주소를 만들어 연다."""

    def test_동봉된_순위판을_상대_출력_경로로_만들고_열면_만든_파일의_주소를_한_번_받는다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())          # 나중에 건 것부터 돈다 — 먼저 원래 폴더로 돌아간 뒤 임시 폴더를 치운다
        os.chdir(tmp.name)                               # 임시 폴더를 현재 폴더로 — '--out preview' 가 상대 경로가 된다
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sys.stdout, "reconfigure", create=True), \
                mock.patch.object(cli.webbrowser, "open") as opened:       # 실제 브라우저는 열지 않는다
            code = cli.main(["make", str(ROOT / "examples" / "순위판_게임점수.json"), "--no-video", "--offline", "--out", "preview", "--open"])
        self.assertEqual(code, 0, out.getvalue())
        made = list((Path(tmp.name) / "preview").glob("*/index.html"))
        self.assertEqual(len(made), 1)                                           # HTML 은 한 벌
        opened.assert_called_once_with(made[0].resolve().as_uri())
        self.assertTrue(opened.call_args.args[0].startswith("file:///"))


if __name__ == "__main__":
    unittest.main()
