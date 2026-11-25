"""의뢰 해석 — 간이 해석 규칙과 Liner 모델 경로(가짜 응답으로)."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from mapcg import intake

EXAMPLE = ("〈오늘도 출발〉 김가상 조연출입니다(010-0000-0000). 출연진 4명이 홍대에서 여의도로 버스로 이동하는 장면 "
           "지도 부탁드립니다. 총 8초, 화면 좌하단 1/4 크기로 띄울 예정입니다.")


def reply(obj) -> dict:
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


LINER_ANSWER = {
    "program": "오늘도 출발", "requester_name": "김가상", "requester_role": "조연출", "requester_phone": "010-0000-0000",
    "map_style_text": "경로 이동", "map_style": "route_move", "from_name": "홍대", "to_name": "여의도", "vehicle": "bus",
    "duration_seconds": 8, "layout": "bottom_left_quarter", "face_count": 4, "unclear": [],
}


class SimpleParse(unittest.TestCase):
    def test_example_sentence(self):
        d, missing = intake.simple_parse(EXAMPLE)
        self.assertEqual((d["from"]["name"], d["to"]["name"]), ("홍대", "여의도"))
        self.assertEqual(d["duration_seconds"], 8)
        self.assertEqual(d["layout"], "bottom_left_quarter")
        self.assertEqual(d["vehicle"], "bus")
        self.assertEqual(d["face_count"], 4)
        self.assertEqual(d["program"], "오늘도 출발")
        self.assertEqual(d["requester"], {"name": "김가상", "role": "조연출", "phone": "010-0000-0000"})
        self.assertEqual(missing, [])

    def test_arrow_form(self):
        d, missing = intake.simple_parse("홍대입구역 → 여의도역 10초 전체화면 승용차")
        self.assertEqual((d["from"]["name"], d["to"]["name"]), ("홍대입구역", "여의도역"))
        self.assertEqual((d["duration_seconds"], d["layout"], d["vehicle"]), (10, "full", "car"))
        self.assertIn("program", missing)

    def test_place_name_ending_with_ro(self):
        d, _ = intake.simple_parse("시청에서 종로로 이동, 5초")
        self.assertEqual((d["from"]["name"], d["to"]["name"]), ("시청", "종로"))

    def test_until_form(self):
        d, _ = intake.simple_parse("서울역부터 강남역까지 우하단에 6초")
        self.assertEqual((d["from"]["name"], d["to"]["name"], d["layout"]), ("서울역", "강남역", "bottom_right_quarter"))

    def test_nothing_found_keeps_defaults_and_reports_missing(self):
        d, missing = intake.simple_parse("안녕하세요, 지도 하나 부탁드려요")
        self.assertEqual((d["from"]["name"], d["to"]["name"], d["duration_seconds"]), ("", "", None))
        self.assertEqual((d["layout"], d["vehicle"], d["face_count"]), ("full", "bus", 0))
        for key in ("from", "to", "duration_seconds", "layout", "vehicle"):
            self.assertIn(key, missing)

    def test_out_of_range_duration_is_missing(self):
        d, missing = intake.simple_parse("홍대에서 여의도로 300초")
        self.assertIsNone(d["duration_seconds"])
        self.assertIn("duration_seconds", missing)


class LinerPath(unittest.TestCase):
    def test_reads_with_model_and_passes_images(self):
        calls = []

        def chat(messages, **options):
            calls.append((messages, options))
            return reply(LINER_ANSWER)

        r = intake.analyze("의뢰서 보냅니다", ["data:image/jpeg;base64,AAAA"], chat=chat, use_liner=True)
        self.assertEqual(r["source"], "liner")
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["draft"]["to"]["name"], "여의도")
        self.assertEqual(r["draft"]["requester"]["phone"], "010-0000-0000")
        content = calls[0][0][1]["content"]
        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}})
        self.assertEqual(calls[0][1]["response_format"]["type"], "json_schema")
        self.assertNotIn("reasoning_effort", calls[0][1])      # 그림과 같이 "none" 을 주면 400 이다

    def test_unknown_values_become_missing_and_unclear_becomes_note(self):
        answer = {**LINER_ANSWER, "layout": "unknown", "vehicle": "unknown", "duration_seconds": None,
                  "map_style": "unknown", "map_style_text": "입체 지구본", "unclear": ["전화번호 끝자리가 흐립니다"]}
        r = intake.analyze("x", chat=lambda m, **o: reply(answer), use_liner=True)
        for key in ("layout", "vehicle", "duration_seconds", "map_style"):
            self.assertIn(key, r["missing"])
        self.assertEqual(r["draft"]["layout"], "full")
        self.assertTrue(any("전화번호" in n for n in r["notes"]))
        self.assertTrue(any("입체 지구본" in n for n in r["notes"]))

    def test_retries_with_json_object_when_schema_is_rejected(self):
        liner = intake._liner()
        formats = []

        def chat(messages, **options):
            formats.append(options["response_format"]["type"])
            if len(formats) == 1:
                raise liner.LinerError(400, "response_format not supported")
            return reply(LINER_ANSWER)

        r = intake.analyze("x", chat=chat, use_liner=True)
        self.assertEqual(formats, ["json_schema", "json_object"])
        self.assertEqual(r["source"], "liner")

    def test_falls_back_to_simple_parse_when_call_fails(self):
        liner = intake._liner()

        def chat(messages, **options):
            raise liner.LinerError(402, "잔액 부족")

        r = intake.analyze(EXAMPLE, ["data:image/jpeg;base64,AAAA"], chat=chat, use_liner=True)
        self.assertEqual(r["source"], "simple")
        self.assertEqual(r["draft"]["from"]["name"], "홍대")
        self.assertTrue(any("실패" in n for n in r["notes"]))
        self.assertTrue(any("그림" in n for n in r["notes"]))

    def test_broken_answer_falls_back(self):
        r = intake.analyze(EXAMPLE, chat=lambda m, **o: {"choices": [{"message": {"content": "죄송합니다"}}]}, use_liner=True)
        self.assertEqual(r["source"], "simple")

    def test_without_key_never_calls_model(self):
        def chat(messages, **options):
            raise AssertionError("키가 없으면 부르면 안 된다")

        r = intake.analyze(EXAMPLE, chat=chat, use_liner=False)
        self.assertEqual((r["source"], r["notes"]), ("simple", []))


class NewOptions(unittest.TestCase):
    def test_nav_bus_ride_and_size_from_text(self):
        d, missing = intake.simple_parse("출연진 4명이 버스를 타고 노들섬에서 여의도로 이동. 네비게이션처럼 남은 거리가 보이는 지도, 960*540 전체화면, 8초")
        self.assertEqual((d["from"]["name"], d["to"]["name"]), ("노들섬", "여의도"))
        self.assertEqual((d["map_style"], d["face_mode"], d["face_count"]), ("nav", "ride", 4))
        self.assertEqual(d["output"], {"width": 960, "height": 540, "fps": 30})
        self.assertNotIn("map_style", missing)

    def test_글에_적힌_초당_프레임을_지킨다(self):
        d, _ = intake.simple_parse("홍대에서 여의도로 이동. 8초·25fps")
        self.assertEqual((d["duration_seconds"], d["output"]["fps"]), (8, 25))
        for text, want in (("홍대에서 여의도로, 8초", 30), ("총 240프레임", 30), ("29.97fps", 30), ("24FPS 로", 24), ("프레임 수 30", 30)):
            with self.subTest(text=text):
                self.assertEqual(intake.simple_parse(text)[0]["output"]["fps"], want)

    def test_dash_form_and_defaults(self):
        d, _ = intake.simple_parse("노들섬 - 여의도 8초")
        self.assertEqual((d["from"]["name"], d["to"]["name"]), ("노들섬", "여의도"))
        self.assertEqual((d["face_mode"], d["output"]["width"]), ("above", 1920))

    def test_phone_number_is_not_read_as_a_route_or_size(self):
        d, _ = intake.simple_parse("김가상 조연출 010-0000-0000 입니다")
        self.assertEqual((d["from"]["name"], d["output"]["width"]), ("", 1920))


class ClaudePath(unittest.TestCase):
    def setUp(self):
        # claude 명령줄 도구가 있는 것처럼: 실제로 있는 아무 파일이나 가리키게 한다(실행은 가짜가 대신한다)
        p = mock.patch.dict(os.environ, {"MAPCG_CLAUDE": sys.executable, "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x"})
        p.start()
        self.addCleanup(p.stop)
        intake._down.clear()
        self.addCleanup(intake._down.clear)

    @staticmethod
    def done(stdout: str):
        return types.SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    def test_reads_with_opus_medium_and_hands_images_as_files(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(cmd=cmd, kw=kw, files=sorted(p.name for p in Path(kw["cwd"]).iterdir()))
            return self.done(json.dumps({"is_error": False, "result": "```json\n" + json.dumps(LINER_ANSWER, ensure_ascii=False) + "\n```"}))

        r = intake.analyze("의뢰서 보냅니다", ["data:image/jpeg;base64,AAAA"], order=["claude"], claude_run=run)
        self.assertEqual((r["source"], r["missing"]), ("claude", []))
        self.assertEqual(r["draft"]["to"]["name"], "여의도")
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read")
        self.assertIn("--restricted", cmd)
        self.assertEqual(seen["files"], ["page1.jpg"])
        self.assertIn("page1.jpg", seen["kw"]["input"])
        self.assertFalse([k for k in seen["kw"]["env"] if k.upper().startswith("CLAUDE")])     # 세션 전용 설정은 안 물려준다

    def test_no_tools_when_there_is_no_image(self):
        seen = {}

        def run(cmd, **kw):
            seen["cmd"] = cmd
            return self.done(json.dumps({"is_error": False, "result": json.dumps(LINER_ANSWER)}))

        intake.analyze("홍대에서 여의도로", order=["claude"], claude_run=run)
        self.assertEqual(seen["cmd"][seen["cmd"].index("--tools") + 1], "")

    def test_expired_login_falls_back_with_a_korean_hint(self):
        def run(cmd, **kw):
            return self.done(json.dumps({"is_error": True, "result": "Failed to authenticate: run `claude auth login`"}))

        r = intake.analyze(EXAMPLE, order=["claude"], claude_run=run)
        self.assertEqual(r["source"], "simple")
        self.assertEqual(r["draft"]["from"]["name"], "홍대")
        self.assertTrue(any("claude auth login" in n and "만료" in n for n in r["notes"]))
        # 한 번 겪은 로그인 오류는 기억해서, 다음부터는 Claude 를 건너뛴다(보낼 때마다 헛부르지 않는다)
        self.assertFalse(intake.readers()["claude"])
        self.assertEqual(intake.reader_notes(), {"claude": "로그인 필요"})

    def test_runner_stops_a_command_that_does_not_finish(self):
        import subprocess
        import time
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                intake._run([sys.executable, "-c", "import time; time.sleep(60)"], input="", cwd=tmp,
                            env=dict(os.environ), timeout=0.5)
            self.assertLess(time.monotonic() - t0, 20)

    def test_runner_returns_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = intake._run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], input="abc", cwd=tmp,
                            env=dict(os.environ), timeout=30)
        self.assertEqual((r.stdout.strip(), r.returncode), ("ABC", 0))

    def test_liner_failure_moves_on_to_claude(self):
        liner = intake._liner()

        def chat(messages, **options):
            raise liner.LinerError(500, "고장")

        def run(cmd, **kw):
            return self.done(json.dumps({"is_error": False, "result": json.dumps(LINER_ANSWER)}))

        r = intake.analyze("x", chat=chat, order=["liner", "claude"], claude_run=run)
        self.assertEqual(r["source"], "claude")
        self.assertTrue(any("Liner" in n and "실패" in n for n in r["notes"]))


class Route(unittest.TestCase):
    def test_default_order_and_forced_reader(self):
        with mock.patch.dict(os.environ, {"MAPCG_READER": "auto"}):
            self.assertEqual(intake.route({"liner": True, "claude": True}), ["liner", "claude"])
            self.assertEqual(intake.route({"liner": False, "claude": True}), ["claude"])
            self.assertEqual(intake.route({"liner": False, "claude": False}), [])
        with mock.patch.dict(os.environ, {"MAPCG_READER": "claude"}):
            self.assertEqual(intake.route({"liner": True, "claude": True}), ["claude"])
        with mock.patch.dict(os.environ, {"MAPCG_READER": "simple"}):
            self.assertEqual(intake.route({"liner": True, "claude": True}), [])


class FaceFiles(unittest.TestCase):
    def test_picks_existing_extension_and_defaults_to_tga(self):
        with tempfile.TemporaryDirectory() as tmp:
            faces = Path(tmp) / "자산" / "얼굴"
            faces.mkdir(parents=True)
            (faces / "02.png").write_bytes(b"x")
            self.assertEqual(intake.face_files(3, Path(tmp)),
                             ["자산/얼굴/01.tga", "자산/얼굴/02.png", "자산/얼굴/03.tga"])
            self.assertEqual(intake.face_files(0, Path(tmp)), [])
            self.assertEqual(len(intake.face_files(99, Path(tmp))), 8)


if __name__ == "__main__":
    unittest.main()
