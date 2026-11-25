"""CG Director(접수 에이전트)와 실무 확인 목록 — 가짜 응답만 쓴다(Liner · 브라우저를 부르지 않는다)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import director as D
from mapcg import knowhow

ROOT = Path(__file__).resolve().parent.parent
GAME = ("감독님, 미션추격자들(가칭) 3회 새 게임 코너 CG 부탁드립니다!\n게임 이름은 '눈치 숫자'예요. 게임 방법 설명판 하나 주세요.\n"
        "1. 진행자가 '시작'을 외치면 1부터 차례로 숫자를 외친다\n2. 두 명이 같은 숫자를 동시에 외치면 둘 다 탈락\n"
        "그리고 라운드 시작할 때마다 화면 가운데 큰 글자로 3·2·1 카운트다운 넣어 주시고(3초, 촬영본 위에 얹게 바탕 투명),\n"
        "1라운드 끝나면 하단 자막으로 '1라운드 탈락: 철수 · 영희' 깔아 주세요.")


def call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def reply(*calls, model="claude-sonnet-5.5"):
    return {"model": model, "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": list(calls)}, "finish_reason": "tool_calls"}]}


def script(*answers):
    """차례대로 답하는 가짜 chat. 받은 인자를 seen 에 남긴다."""
    seen, it = [], iter(answers)

    def chat(messages, **kw):
        seen.append({"messages": json.loads(json.dumps(messages, ensure_ascii=False)), **{k: v for k, v in kw.items() if k != "tools"},
                     "tools": [t["function"]["name"] for t in kw.get("tools") or []]})
        got = next(it)
        if isinstance(got, Exception):      # 예외를 넣어 두면 그 차례의 호출이 실패한다
            raise got
        return got
    return chat, seen


HITS = {"results": [{"title": "눈치 게임 규칙", "hostname": "namu.wiki", "url": "https://namu.wiki/w/x", "date": "2025-01-01", "description": "차례로 숫자를 외친다"},
                    {"title": "다른 글", "hostname": "example.com", "url": "https://example.com", "date": "", "description": "…"}]}


class Knowhow(unittest.TestCase):
    def test_every_rule_points_at_code_that_exists(self):
        self.assertEqual(knowhow.missing_code(), [])

    def test_ids_are_unique_and_stages_known(self):
        ids = [r["id"] for r in knowhow.rules()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r["stage"] in knowhow.STAGES for r in knowhow.rules()))
        self.assertGreaterEqual(len(knowhow.for_stages("접수", "확인")), 10)

    def test_markdown_file_matches_the_json(self):
        # 자식은 한글을 콘솔 글자표(한국어 윈도우는 cp949)로 찍는다 — UTF-8 로 찍게 하고, 못 읽는 글자가 있어도 시험이 그 때문에 죽지 않게 한다
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "knowhow_md.py"), "--check"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))

    def test_director_prompt_carries_the_intake_rules(self):
        for r in knowhow.for_stages("접수", "확인"):
            self.assertIn(r["id"], D.SYSTEM)
        self.assertNotIn("K15", D.SYSTEM)      # 화면 · 납품 항목은 그리기 코드와 검수가 지킨다


class Director(unittest.TestCase):
    def test_split_with_search_keeps_only_real_rules_quotes_and_sources(self):
        chat, seen = script(
            reply(call("search_web", {"query": "눈치 게임 규칙", "why": "게임 방법"}, "c1")),
            reply(call("assign", {"specialist": "설명판", "part": "게임 방법 설명판", "rules": ["K10", "K99"]}, "c2"),
                  call("custom", {"name": "카운트다운", "part": "라운드 시작마다 3·2·1 카운트다운(3초, 투명)", "rules": ["k10"]}, "c3"),
                  call("custom", {"name": "탈락 하단 자막", "part": "'1라운드 탈락: 철수 · 영희' 하단 자막", "rules": []}, "c4"),
                  call("finish", {"summary": "설명판은 전문 담당, 카운트다운 · 탈락 자막은 임시 담당에게",
                                  "applied": [{"id": "K10", "quote": "3·2·1 카운트다운 넣어 주시고", "why": "틀 밖이라 따로"},
                                              {"id": "K99", "quote": "게임 방법", "why": "없는 번호"},
                                              {"id": "K02", "quote": "여의도 한강공원 아님", "why": "원문에 없는 구절"}],
                                  "sources": ["s1-1", "s9-9"]}, "c5")))
        got = D.run(GAME, chat=chat, search=lambda q, t: HITS, log=lambda *a: None)
        self.assertTrue(got["ok"])
        self.assertEqual((got["assign"]["specialist"], got["assign"]["prefer"], got["assign"]["rules"]), ("설명판", "rules", ["K10"]))
        self.assertEqual([c["name"] for c in got["custom"]], ["카운트다운", "탈락 하단 자막"])
        self.assertEqual(got["custom"][0]["rules"], ["K10"])                          # 소문자 번호도 받고, 없는 번호는 버린다
        self.assertEqual([a["id"] for a in got["applied"]], ["K10"])                 # 없는 번호 · 원문에 없는 구절은 카드에서 뺀다
        self.assertEqual(got["searches"][0]["cited"], ["s1-1"])                      # 지어낸 출처 번호는 버린다
        self.assertEqual(got["searches"][0]["refs"][0]["hostname"], "namu.wiki")
        self.assertEqual(seen[0]["task"], "director")
        self.assertEqual(seen[0]["model"], D.MODEL)
        self.assertEqual((seen[0]["retries"], seen[0]["fallback"]), (0, False))      # 한도(60초)를 넘겨 붙잡지 않게 다시 보내지 않는다
        self.assertIn("finish", seen[0]["tools"])
        tool_msgs = [m for m in seen[1]["messages"] if m["role"] == "tool"]
        self.assertIn("s1-1", tool_msgs[0]["content"])                               # 검색 결과는 번호가 붙어 돌아간다

    def test_only_one_specialist_and_two_custom_parts(self):
        chat, seen = script(reply(
            call("assign", {"specialist": "지도", "part": "지도", "rules": []}, "c1"),
            call("assign", {"specialist": "순위판", "part": "표", "rules": []}, "c2"),
            call("custom", {"name": "a", "part": "하나", "rules": []}, "c3"),
            call("custom", {"name": "b", "part": "둘", "rules": []}, "c4"),
            call("custom", {"name": "c", "part": "셋", "rules": []}, "c5"),
            call("finish", {"summary": "", "applied": [], "sources": []}, "c6")))
        got = D.run("홍대에서 여의도로", chat=chat, log=lambda *a: None)
        self.assertEqual(got["assign"]["specialist"], "지도")
        self.assertEqual(got["assign"]["prefer"], "")
        self.assertEqual(len(got["custom"]), 2)
        self.assertIn("지도 담당", got["summary"])                                   # 요약이 비면 나눈 일로 만든다

    def test_picture_only_request_can_go_to_temp_workers(self):
        # 11-23 부터 메인 에이전트도 의뢰서 그림을 함께 읽는다 — 글 없이 그림만 온 의뢰도 임시 담당에게 맡길 수 있다
        chat, seen = script(reply(call("custom", {"name": "x", "part": "그림 속 요청", "rules": []}, "c1")),
                            reply(call("finish", {"summary": "", "applied": [], "sources": []}, "c2")))
        got = D.run("", ["data:image/png;base64,AAAA"], chat=chat, log=lambda *a: None)
        self.assertTrue(got["ok"])
        self.assertEqual([c["part"] for c in got["custom"]], ["그림 속 요청"])
        self.assertIsInstance(seen[0]["messages"][1]["content"], list)               # 그림은 그림으로 보낸다

    def test_picture_quotes_are_kept_but_marked_as_from_the_picture(self):
        # 실측(11-22): 손그림 의뢰서는 근거 구절이 그림에만 있어, 글로만 대조하면 적용한 실무 확인 6개가 모두 빠졌다
        text = "영희PD입니다. 1차 의뢰지 찍어서 올려요~ 출연진 동선 지도 부탁드립니다!"
        finish = call("finish", {"summary": "", "sources": [], "applied": [
            {"id": "K05", "quote": "총듀레 컷1 → 8초", "why": "컷마다 길이"}, {"id": "K01", "quote": "출연진 동선 지도", "why": "글에 있는 구절"}]}, "c2")
        chat, _ = script(reply(call("assign", {"specialist": "지도", "part": "2컷 동선", "rules": []}, "c1"), finish))
        got = D.run(text, ["data:image/jpeg;base64,AAAA"], chat=chat, log=lambda *a: None)
        self.assertEqual([(a["id"], a["seen"]) for a in got["applied"]], [("K05", "그림"), ("K01", "글")])
        chat, _ = script(reply(call("assign", {"specialist": "지도", "part": "2컷 동선", "rules": []}, "c1"), finish))
        got = D.run(text, chat=chat, log=lambda *a: None)
        self.assertEqual([a["id"] for a in got["applied"]], ["K01"])                 # 그림이 없으면 글에 없는 구절은 뺀다

    def test_questions_are_asked_not_reported_as_done(self):
        # 실측(11-22): "미션 이동은 걷는 사람으로 그렸습니다"라고 적었는데 지도 카드는 SUV 로 읽었다 — 접수는 그리지 않는다
        ask = next(t for t in D.TOOLS if t["function"]["name"] == "ask")["function"]
        self.assertIn("그렸습니다", ask["parameters"]["properties"]["questions"]["description"])
        self.assertIn("이미 정한 듯 쓰지 않는다", D.SYSTEM)
        # 같은 실측: 손글씨를 읽기 담당과 다르게 읽고('오목' → '윷놀이') 그 글자로 물어 앞뒤 카드가 어긋났다 — 판독은 읽기 · 감사 담당 몫
        self.assertIn("네가 읽은 글자를 적지 않는다", D.SYSTEM)

    def test_no_decision_means_not_ok(self):
        chat, _ = script(reply(), reply(), reply())
        got = D.run("뭔가 만들어 주세요", chat=chat, log=lambda *a: None)
        self.assertFalse(got["ok"])
        self.assertTrue(got["why"])

    def test_search_limit_and_failure_do_not_stop_the_intake(self):
        many = [call("search_web", {"query": f"곳{n}", "why": ""}, f"s{n}") for n in range(6)]
        def search(q, t):
            if q == "곳0":
                raise RuntimeError("연결 실패")
            return HITS
        chat, seen = script(reply(*many), reply(call("assign", {"specialist": "지도", "part": "", "rules": []}, "a"),
                                                call("finish", {"summary": "s", "applied": [], "sources": []}, "f")))
        got = D.run("홍대에서 여의도로", chat=chat, search=search, log=lambda *a: None)
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["searches"]), 3)                                    # 4번까지 보내고 하나는 실패
        outs = [json.loads(m["content"]) for m in seen[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(sum("오류" in o for o in outs), 3)                          # 실패 1 + 한도 넘김 2

    def test_search_links_are_web_only_and_blank_ids_do_not_collide(self):
        bad = {"results": [{"title": "a", "hostname": "x", "url": "javascript:alert(1)", "date": "", "description": ""},
                           {"title": "b", "hostname": "y", "url": "https://y.example/b", "date": "", "description": ""}]}
        chat, seen = script(reply(call("search_web", {"query": "하나", "why": ""}, ""), call("search_web", {"query": "둘", "why": ""}, "")),
                            reply(call("assign", {"specialist": "지도", "part": "", "rules": []}, "a"),
                                  call("finish", {"summary": "s", "applied": [], "sources": []}, "f")))
        got = D.run("홍대에서 여의도로", chat=chat, search=lambda q, t: bad, log=lambda *a: None)
        self.assertEqual([s["query"] for s in got["searches"]], ["하나", "둘"])      # id 가 비어도 두 검색이 섞이지 않는다
        self.assertEqual([r["url"] for r in got["searches"][0]["refs"]], ["", "https://y.example/b"])

    def test_time_limit_stops_and_reports(self):
        now = [0.0]

        def search(q, t):       # 검색이 오래 걸렸다 — 둘째 바퀴는 남은 시간이 2초라 부르지 않는다
            now[0] = 58.0
            return HITS
        chat, seen = script(reply(call("search_web", {"query": "a", "why": ""}, "c1")))
        got = D.run("홍대에서 여의도로", chat=chat, search=search, log=lambda *a: None, clock=lambda: now[0])
        self.assertFalse(got["ok"])
        self.assertIn("시간", got["why"])
        self.assertEqual(len(seen), 1)

    def test_searches_and_fact_checks_wait_only_for_the_time_that_is_left(self):
        # 실측 전 결함(2026-11-22 리뷰): 모델 호출에만 한도가 있고 검색 · 사실 확인은 liner 기본값(120초 · 재시도)으로 기다렸다
        now, waits = [0.0], []
        inner, seen = script(
            reply(call("search_web", {"query": "a", "why": ""}, "c1"), call("check_fact", {"question": "b", "why": ""}, "c2")),
            reply(call("search_web", {"query": "c", "why": ""}, "c3"), call("check_fact", {"question": "d", "why": ""}, "c4"),
                  call("assign", {"specialist": "지도", "part": "", "rules": []}, "c5")))
        ticks = iter([50.0, 57.0])

        def chat(messages, **kw):       # 모델이 답하는 데 50초, 그다음 7초가 걸렸다
            got = inner(messages, **kw)
            now[0] = next(ticks)
            return got
        got = D.run("홍대에서 여의도로", chat=chat, log=lambda *a: None, clock=lambda: now[0],
                    search=lambda q, t: waits.append(("검색", q, t)) or HITS,
                    research=lambda q, t: waits.append(("사실", q, t)) or {"answer": "답", "references": []})
        self.assertEqual(waits, [("검색", "a", 10.0), ("사실", "b", 10.0)])          # 남은 10초만 기다린다 · 3초 남았을 때는 보내지 않는다
        self.assertEqual(seen[1]["timeout"], 10.0)
        self.assertTrue(got["ok"])                                                   # 나눈 일(지도)은 그대로 받는다
        self.assertIn("시간", got["why"])
        self.assertEqual((D._search.__defaults__, D._research.__defaults__), ((D.SEARCH_TIMEOUT,), (D.FACT_TIMEOUT,)))
        self.assertLessEqual(max(D.SEARCH_TIMEOUT, D.FACT_TIMEOUT, D.CALL_TIMEOUT), D.TIME_LIMIT)

    def test_failed_searches_and_fact_checks_count_toward_the_limit(self):
        asked = []

        def down(q, t):
            asked.append(q)
            raise RuntimeError("연결 실패")
        chat, seen = script(
            reply(*[call("search_web", {"query": f"곳{n}", "why": ""}, f"s{n}") for n in range(3)],
                  *[call("check_fact", {"question": f"물음{n}", "why": ""}, f"f{n}") for n in range(3)]),
            reply(*[call("search_web", {"query": f"곳{n}", "why": ""}, f"s{n}") for n in range(3, 6)],
                  call("check_fact", {"question": "물음3", "why": ""}, "f3"),
                  call("assign", {"specialist": "지도", "part": "", "rules": []}, "a"),
                  call("finish", {"summary": "s", "applied": [], "sources": []}, "f")))
        got = D.run("홍대에서 여의도로", chat=chat, search=down, research=down, log=lambda *a: None)
        self.assertEqual(len(asked), D.MAX_SEARCH + D.MAX_FACT)                      # 실패해도 한도 안에서만 보낸다
        self.assertTrue(got["ok"])
        self.assertEqual(got["searches"], [])

    def test_a_failed_later_call_keeps_what_was_already_split(self):
        chat, seen = script(reply(call("search_web", {"query": "여의도공원 문화의마당", "why": "K02"}, "c1"),
                                  call("assign", {"specialist": "지도", "part": "여의도공원 → 노량진역", "rules": ["K02"]}, "c2")),
                            RuntimeError("Liner 의 답을 기다리다 끊겼습니다"))
        got = D.run("여의도공원에서 노량진역으로", chat=chat, search=lambda q, t: HITS, log=lambda *a: None)
        self.assertTrue(got["ok"])                                                   # 예전에는 예외가 그대로 올라가 나눈 일 · 검색 결과를 다 버렸다
        self.assertEqual((got["assign"]["specialist"], len(got["searches"])), ("지도", 1))
        self.assertIn("실패", got["why"])
        self.assertEqual(len(seen), 2)                                               # 기다리다 끊긴 호출은 다시 보내지 않는다(두 번 처리될 수 있다)

    def test_a_refused_call_is_sent_once_more_only_when_it_is_safe(self):
        class Busy(RuntimeError):
            status = 429
        done = reply(call("assign", {"specialist": "지도", "part": "", "rules": []}, "a"), call("finish", {"summary": "s", "applied": [], "sources": []}, "f"))
        naps = []
        chat, seen = script(Busy("잠시 뒤"), done)
        got = D.run("홍대에서 여의도로", chat=chat, log=lambda *a: None, sleep=naps.append)
        self.assertTrue(got["ok"])
        self.assertEqual((len(seen), naps), (2, [D.RETRY_PAUSE]))
        chat, seen = script(Busy("잠시 뒤"), Busy("잠시 뒤"), done)                  # 두 번 다 거절 — 세 번째는 보내지 않는다
        got = D.run("홍대에서 여의도로", chat=chat, log=lambda *a: None, sleep=lambda s: None)
        self.assertFalse(got["ok"])
        self.assertEqual(len(seen), 2)
        now = [0.0]
        chat, seen = script(Busy("잠시 뒤"), done)                                   # 시간이 얼마 안 남았으면 다시 보내지 않는다
        got = D.run("홍대에서 여의도로", chat=lambda m, **kw: (now.__setitem__(0, 45.0), chat(m, **kw))[1], log=lambda *a: None,
                    clock=lambda: now[0], sleep=lambda s: None)
        self.assertFalse(got["ok"])
        self.assertEqual(len(seen), 1)


class Messenger(unittest.TestCase):
    """server.Desk 에 CG Director 를 붙였을 때 — 나눈 대로 지금 코드가 실행되고, 지시서 카드가 겹치지 않는다."""

    def setUp(self):
        from mapcg import board, plan, scene, server
        self.server, self.board, self.plan, self.scene = server, board, plan, scene
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def desk(self, director, analyze=None):
        d = self.server.Desk(self.root, liner=True, audit=mock.Mock(return_value={"state": "skipped"}),
                             analyze=analyze or mock.Mock(side_effect=AssertionError("지도로 읽으면 안 된다")), director=director)
        sync = mock.patch.object(d, "_spawn", side_effect=lambda fn, *args, **k: fn(*args))
        sync.start()
        self.addCleanup(sync.stop)
        return d

    def planned(self):
        return {"main_ok": True, "brief": {"viewer_point": "v", "facts": [], "must_not_assert": [], "open_questions": [], "checks": []},
                "problems": [], "rounds": [], "msgs": [], "unable": [], "calls": [],
                "workers": [{"role": "카운트다운 담당", "goal": "g", "fact_ids": [], "layouts": ["headline"], "checks": ["크게 보이는가"], "max_tries": 3}]}

    def rules_board(self):
        return {"kind": self.plan.KIND, "draft": {"shape": "rules", "title": "눈치 숫자", "rows": []}, "source": "liner", "notes": [],
                "unmade": ["3·2·1 카운트다운", "탈락 하단 자막"]}

    def test_director_card_then_specialist_then_one_brief_for_the_custom_parts(self):
        plan = {"ok": True, "why": "", "summary": "설명판 + 임시 담당", "ask": [], "seconds": 3.0,
                "assign": {"specialist": "설명판", "prefer": "rules", "part": "게임 방법", "rules": ["K10"]},
                "custom": [{"name": "카운트다운", "part": "3·2·1 카운트다운", "rules": []}],
                "applied": [{"id": "K10", "name": "틀 밖의 요청은 나눈다", "quote": "카운트다운", "why": ""}], "searches": []}
        d = self.desk(mock.Mock(return_value=plan))
        with mock.patch.object(self.board, "read", return_value=self.rules_board()) as read, \
                mock.patch.object(self.scene, "plan_request", return_value=self.planned()) as main:
            d.send(GAME, [], room=d.room)
        self.assertEqual(read.call_args.kwargs.get("prefer"), "rules")              # CG Director 가 고른 틀로 읽는다
        kinds = [m["kind"] for m in d.msgs if m["who"] == "desk" and m["kind"] != "progress"]
        self.assertEqual(kinds, ["director", "plan", "scene"])                       # 지시서 카드는 한 장
        main.assert_called_once()
        self.assertIn("3·2·1 카운트다운", main.call_args.kwargs["focus"])           # 메인 에이전트는 CG Director 가 맡긴 부분만 본다
        self.assertEqual(d.msgs[-1]["leftover"], ["3·2·1 카운트다운"])
        card = next(m for m in d.msgs if m["kind"] == "director")
        self.assertEqual(card["assign"]["specialist"], "설명판")

    def test_못_그린_요청은_Director_가_틀만_정했어도_메인에게_넘긴다(self):
        # 11-24: 예전에는 '맡기지 않은 요청' 알림 한 줄만 남기고 버렸다(노들역 의뢰서의 '잠시정차 · 지도표시')
        plan = {"ok": True, "why": "", "summary": "설명판", "ask": [], "seconds": 1.0, "applied": [], "searches": [], "custom": [],
                "assign": {"specialist": "설명판", "prefer": "rules", "part": "게임 방법", "rules": []}}
        d = self.desk(mock.Mock(return_value=plan))
        with mock.patch.object(self.board, "read", return_value=self.rules_board()), \
                mock.patch.object(self.scene, "plan_request", return_value=self.planned()) as main:
            d.send(GAME, [], room=d.room)
        main.assert_called_once()
        focus = main.call_args.kwargs["focus"]
        self.assertIn("- 3·2·1 카운트다운", focus)
        self.assertIn("- 탈락 하단 자막", focus)
        self.assertEqual(d.msgs[-1]["kind"], "scene")
        self.assertEqual(d.msgs[-1]["leftover"], ["3·2·1 카운트다운", "탈락 하단 자막"])
        self.assertFalse(any("맡기지 않은 요청" in (m.get("title") or "") for m in d.msgs))

    def test_그림만_온_의뢰의_못_그린_요청도_그림과_함께_넘긴다(self):
        import base64
        png = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()
        plan = {"ok": True, "why": "", "summary": "설명판", "ask": [], "seconds": 1.0, "applied": [], "searches": [], "custom": [],
                "assign": {"specialist": "설명판", "prefer": "rules", "part": "게임 방법", "rules": []}}
        d = self.desk(mock.Mock(return_value=plan))
        with mock.patch.object(d, "_transcribe", return_value=("", "")), \
                mock.patch.object(self.board, "read", return_value=self.rules_board()), \
                mock.patch.object(self.scene, "plan_request", return_value=self.planned()) as main:
            d.send("", [{"name": "의뢰서.png", "data": png, "images": [png]}], room=d.room)
        main.assert_called_once()
        self.assertEqual(main.call_args.kwargs["images"], [png])                   # 메인이 손그림을 직접 본다
        self.assertEqual(d.msgs[-1]["kind"], "scene")

    def test_map_assignment_skips_the_board_check(self):
        plan = {"ok": True, "why": "", "summary": "지도", "ask": [], "seconds": 1.0, "applied": [], "searches": [], "custom": [],
                "assign": {"specialist": "지도", "prefer": "", "part": "지도", "rules": []}}
        boom = mock.Mock(side_effect=RuntimeError("시험: 지도 읽기에서 멈춤"))
        d = self.desk(mock.Mock(return_value=plan), analyze=boom)
        with mock.patch.object(self.board, "read") as read:
            d.send("홍대에서 여의도로 8초", [], room=d.room)
        read.assert_not_called()
        boom.assert_called_once()

    def test_director_failure_falls_back_to_the_old_order(self):
        d = self.desk(mock.Mock(side_effect=RuntimeError("연결 실패")))
        with mock.patch.object(self.board, "read", return_value=self.rules_board()) as read, \
                mock.patch.object(self.scene, "plan_request", return_value=self.planned()):
            d.send(GAME, [], room=d.room)
        self.assertNotIn("prefer", read.call_args.kwargs)                            # 예전 순서 — 틀을 고르지 않고 판별부터
        kinds = [m["kind"] for m in d.msgs if m["who"] == "desk" and m["kind"] != "progress"]
        self.assertEqual(kinds, ["plan", "scene"])                                   # 예전처럼 남은 요청으로 지시서 카드
        self.assertTrue(any("예전 순서로 읽었습니다" in ln for m in d.msgs for ln in m.get("lines") or []))

    def test_custom_only_goes_to_the_main_agent_for_the_whole_request(self):
        plan = {"ok": True, "why": "", "summary": "틀 없음", "ask": [], "seconds": 1.0, "applied": [], "searches": [], "assign": None,
                "custom": [{"name": "진술 대조판", "part": "세 사람 말 견주기", "rules": []}]}
        d = self.desk(mock.Mock(return_value=plan))
        with mock.patch.object(self.board, "read") as read, mock.patch.object(self.scene, "plan_request", return_value=self.planned()) as main:
            d.send("세 사람 말이 어디서 엇갈리는지 보여 주세요", [], room=d.room)
        read.assert_not_called()
        self.assertNotIn("focus", main.call_args.kwargs)
        self.assertEqual(d.msgs[-1]["what"], "진술 대조판")

    def test_chosen_frame_failure_rereads_in_the_old_order(self):
        plan = {"ok": True, "why": "", "summary": "설명판", "ask": [], "seconds": 1.0, "applied": [], "searches": [], "custom": [],
                "assign": {"specialist": "설명판", "prefer": "rules", "part": "게임 방법", "rules": []}}
        d = self.desk(mock.Mock(return_value=plan))
        answers = [self.board.Unsure("설명판 설계도를 받지 못했습니다(시험)"), self.rules_board()]
        def read(*a, **kw):
            got = answers.pop(0)
            if isinstance(got, Exception):
                raise got
            return got
        with mock.patch.object(self.board, "read", side_effect=read) as reader, \
                mock.patch.object(self.scene, "plan_request", return_value=self.planned()):
            d.send(GAME, [], room=d.room)
        self.assertEqual(reader.call_args_list[0].kwargs.get("prefer"), "rules")
        self.assertNotIn("prefer", reader.call_args_list[1].kwargs)                 # 의뢰를 버리지 않고 예전 순서로 한 번 더
        self.assertIn("plan", [m["kind"] for m in d.msgs])
        self.assertFalse(any(m.get("state") == "fail" for m in d.msgs))

    def test_offline_desk_never_calls_the_director(self):
        director = mock.Mock(side_effect=AssertionError("부르면 안 된다"))
        d = self.server.Desk(self.root / "o", offline=True, director=director)
        with mock.patch.object(d, "_spawn", side_effect=lambda fn, *args, **k: fn(*args)), \
                mock.patch.object(self.board, "read", return_value=self.rules_board()):
            d.send(GAME, [], room=d.room)
        director.assert_not_called()
        self.assertIn("plan", [m["kind"] for m in d.msgs])
        self.assertIsNone(self.server.Desk(self.root / "t", liner=False).director)    # 시험이 만드는 Desk 에는 붙지 않는다

    def test_front_switch_is_read_from_the_env_file(self):
        env = self.root / ".env"
        env.write_text("MAPCG_FRONT=off\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAPCG_FRONT", None)
            self.assertFalse(self.server.director_on(env))                          # 키를 확인하기 전에 .env 를 읽는다
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAPCG_FRONT", None)
            self.assertTrue(self.server.director_on(self.root / "없는.env"))


if __name__ == "__main__":
    unittest.main()
