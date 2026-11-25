"""의뢰 메신저 서버 — 대화 흐름과 요청 막기. 지도 서버·Liner 는 부르지 않는다."""
import base64
import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from mapcg import intake, mapdata, server
from mapcg.geo import Geo

TEXT = "홍대에서 여의도로 버스, 8초, 좌하단"


def analyze(text, images=()):
    return intake.analyze(text, images, use_liner=False)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class DeskFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.desk = server.Desk(Path(self.tmp.name), analyze=analyze, liner=False)
        # 뒤에서 도는 작업이 대화.json 을 다 쓴 뒤에 임시 폴더를 지운다 — 카드가 뜬 직후 지우면
        # 쓰는 중인 data/messenger 를 못 지워 가끔 WinError 145 로 끝났다(정리는 거꾸로 돌므로 이것이 먼저 돈다)
        self.addCleanup(wait_for, lambda: self.desk.busy == 0)

    def kinds(self):
        return [m["kind"] for m in self.desk.msgs]

    def test_send_text_makes_a_card(self):
        self.desk.send(TEXT, [])
        self.assertTrue(wait_for(lambda: "card" in self.kinds()))
        card = self.desk.msgs[-1]
        self.assertEqual(card["draft"]["from"]["name"], "홍대")
        self.assertEqual(card["source"], "simple")
        self.assertEqual(self.desk.msgs[1]["state"], "ok")

    def test_empty_send_is_refused(self):
        with self.assertRaises(server.DeskError):
            self.desk.send("  ", [])

    def test_attachment_is_saved_with_safe_name(self):
        png = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()
        self.desk.send("", [{"name": "..\\..\\의뢰서 1.png", "data": png, "images": []}])
        f = self.desk.msgs[0]["files"][0]
        self.assertEqual(f["name"], "의뢰서_1.png")
        saved = list((Path(self.tmp.name) / "data" / "uploads").iterdir())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b"\x89PNG fake")
        self.assertTrue(wait_for(lambda: "card" in self.kinds()))

    def test_failed_reading_is_reported_not_raised(self):
        def boom(text, images=()):
            raise RuntimeError("고장")

        desk = server.Desk(Path(self.tmp.name) / "b", analyze=boom, liner=False)
        desk.send("x", [])

        def failed():       # 저장까지 끝난 뒤의 상태를 본다(임시 폴더 정리와 겹치지 않게)
            with desk.lock:
                return desk.msgs[-1].get("state") == "fail"

        self.assertTrue(wait_for(failed))
        self.assertIn("고장", desk.msgs[-1]["error"])

    def test_conversation_survives_restart_and_reset_archives_it(self):
        self.desk.send(TEXT, [])
        self.assertTrue(wait_for(lambda: "card" in self.kinds()))
        again = server.Desk(Path(self.tmp.name), analyze=analyze, liner=False)
        self.assertEqual([m["kind"] for m in again.msgs], ["text", "progress", "card"])
        self.assertEqual(again.room, self.desk.room)
        again.reset()
        self.assertEqual(again.msgs, [])
        self.assertNotEqual(again.room, self.desk.room)
        kept = list((Path(self.tmp.name) / "data" / "messenger" / "지난대화").iterdir())
        self.assertEqual(len(kept), 1)

    def test_restart_marks_broken_work_and_forces_open_pages_to_refresh(self):
        store = Path(self.tmp.name) / "r" / "data" / "messenger"
        store.mkdir(parents=True)
        (store / "대화.json").write_text(json.dumps({"room": "abcd", "next_id": 2, "rev": 7, "msgs": [
            {"id": 1, "rev": 7, "who": "desk", "kind": "progress", "time": "10:00", "title": "지도 만드는 중",
             "lines": [], "state": "run", "error": ""}]}, ensure_ascii=False), encoding="utf-8")
        desk = server.Desk(Path(self.tmp.name) / "r", analyze=analyze, liner=False)
        self.assertEqual(desk.msgs[0]["state"], "fail")
        self.assertGreater(desk.msgs[0]["rev"], 7)
        # 꺼지기 전의 번호(7)를 들고 있는 화면이 물으면 바뀐 대화를 통째로 다시 준다
        self.assertIn("messages", desk.state(7, "abcd"))

    def test_new_conversation_waits_for_running_work(self):
        gate = threading.Event()

        def slow(text, images=()):
            gate.wait(5)
            return analyze(text, images)

        desk = server.Desk(Path(self.tmp.name) / "s", analyze=slow, liner=False)
        desk.send(TEXT, [])
        with self.assertRaises(server.DeskError) as e:
            desk.reset()
        self.assertEqual(e.exception.status, 409)
        gate.set()

        def idle():
            with desk.lock:
                return desk.busy == 0

        self.assertTrue(wait_for(idle))
        self.assertEqual([m["kind"] for m in desk.msgs], ["text", "progress", "card"])      # 결과는 제 대화에 남는다
        desk.reset()
        self.assertEqual(desk.msgs, [])

    def idle(self, desk):
        def done():
            with desk.lock:
                return desk.busy == 0
        return wait_for(done)

    def test_reset_cannot_slip_between_progress_and_reservation(self):
        """진행 표시를 기록하는 순간에 '새 대화'가 들어와도, 예약(busy)까지 한 임계구역이라 409 로 막힌다."""
        gate, inside, release = threading.Event(), threading.Event(), threading.Event()

        def slow(text, images=()):
            gate.wait(5)
            return analyze(text, images)

        desk = server.Desk(Path(self.tmp.name) / "race", analyze=slow, liner=False)
        real_save = desk._save

        def save():     # 진행 표시를 저장하는 순간(잠금을 쥔 채) 멈춰 서서, 그사이 '새 대화'를 들여보낸다
            real_save()
            if any(m["kind"] == "progress" for m in desk.msgs) and not inside.is_set():
                inside.set()
                release.wait(5)
        desk._save = save
        out = {}

        def do_reset():
            try:
                desk.reset()
                out["ok"] = True
            except server.DeskError as e:
                out["status"] = e.status

        sender = threading.Thread(target=desk.send, args=(TEXT, []))
        sender.start()
        self.assertTrue(inside.wait(5))
        resetter = threading.Thread(target=do_reset)
        resetter.start()
        release.set()
        sender.join(5)
        resetter.join(5)
        self.assertEqual(out, {"status": 409})
        gate.set()
        self.assertTrue(self.idle(desk))
        self.assertEqual([m["kind"] for m in desk.msgs], ["text", "progress", "card"])      # 결과는 제 대화에 남는다
        desk.reset()        # 끝난 뒤에는 새 대화를 열 수 있다
        self.assertEqual(desk.msgs, [])

    def test_requests_from_an_old_room_do_not_touch_the_new_room(self):
        desk = self.desk
        old = desk.room
        desk.send(TEXT, [], room=old)
        self.assertTrue(wait_for(lambda: "card" in self.kinds()))
        self.assertTrue(self.idle(desk))
        desk.reset(room=old)
        # 새 대화에서도 번호가 1부터 붙는다 — 예전 화면의 '1번 내보내기'가 새 대화의 1번 지도를 가리키면 안 된다
        m = desk.add("desk", "result", job="새지도", request={}, video=None, exporting=False, export_lines=[], export_error="")
        self.assertEqual(m["id"], 1)
        tries = {"send": lambda: desk.send(TEXT, [], room=old), "make": lambda: desk.make({"from": {"name": "홍대"}}, room=old),
                 "revise": lambda: desk.revise({"from": {"name": "홍대"}}, "도착을 바꿔", room=old),
                 "export": lambda: desk.export(1, False, room=old), "reset": lambda: desk.reset(room=old)}
        for name, call in tries.items():
            with self.subTest(name), self.assertRaises(server.DeskError) as e:
                call()
            self.assertEqual(e.exception.status, 409, name)
        self.assertEqual([x["kind"] for x in desk.msgs], ["result"])
        self.assertFalse(m["exporting"])
        self.assertEqual(desk.busy, 0)
        self.assertFalse(list((Path(self.tmp.name) / "data" / "uploads").glob("*")))      # 막힌 의뢰의 첨부도 저장하지 않았다

    def test_thread_start_failure_returns_the_reservation(self):
        from unittest import mock
        with mock.patch.object(server.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            with self.assertRaises(server.DeskError) as e:
                self.desk.send(TEXT, [])
        self.assertEqual(e.exception.status, 503)
        self.assertEqual(self.desk.busy, 0)
        self.assertEqual(self.desk.msgs[-1]["kind"], "progress")
        self.assertEqual(self.desk.msgs[-1]["state"], "fail")
        self.desk.reset()       # 예약이 남지 않았으니 새 대화를 열 수 있다

    def test_unexpected_crash_in_work_is_recorded_once(self):
        def crash(p, *args):
            raise KeyError("예상 못 한 고장")
        self.desk._read = crash
        self.desk.send(TEXT, [])
        self.assertTrue(self.idle(self.desk))
        p = self.desk.msgs[-1]
        self.assertEqual((p["kind"], p["state"]), ("progress", "fail"))
        self.assertIn("예상 못 한 고장", p["error"])
        self.assertEqual(self.desk.busy, 0)

    def voice_file(self):
        mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 800
        return mp3, {"name": "의뢰 음성.mp3", "data": "data:audio/mpeg;base64," + base64.b64encode(mp3).decode(), "images": [], "state": "voice"}

    def test_voice_attachment_is_transcribed_and_read_like_text(self):
        from unittest import mock
        mp3, f = self.voice_file()
        said = "홍대에서 여의도로 버스, 8초, 좌하단"
        heard = {"state": "받아씀", "text": said, "request_text": said, "note": "음성을 받아쓴 글입니다", "doubts": [], "seconds": 0.1}
        with mock.patch.object(server.voice, "hear", return_value=heard) as hear:
            self.desk.send("", [f])
            self.assertTrue(wait_for(lambda: "card" in self.kinds()))
        self.assertEqual(hear.call_args.args[0], mp3)       # 받은 원본 그대로 받아쓰기에 넘긴다
        self.assertTrue(self.desk.msgs[0]["files"][0]["voice"])
        card = self.desk.msgs[-1]
        self.assertEqual(card["draft"]["from"]["name"], "홍대")       # 받아쓴 글이 글 의뢰와 같은 길로 읽혔다
        self.assertIn(f"받아쓴 글: “{said}”", card["notes"][0])        # 받아쓴 글을 확인 카드 맨 앞에 그대로 보여 준다
        saved = list((Path(self.tmp.name) / "data" / "uploads").iterdir())
        self.assertEqual([p.read_bytes() for p in saved], [mp3])

    def test_long_transcript_is_quoted_only_as_far_as_it_was_read(self):
        """긴 녹음: 읽기는 MAX_TEXT 자까지 — 확인 카드에 옮기는 받아쓴 글도 그만큼만(전문을 실으면 뒤쪽까지 읽은 것처럼 보인다)."""
        from unittest import mock
        _, f = self.voice_file()
        said = "홍대에서 여의도로 버스, 8초, 좌하단. " + "가" * server.MAX_TEXT
        heard = {"state": "받아씀", "text": said, "request_text": said, "note": "음성을 받아쓴 글입니다", "doubts": [], "seconds": 0.1}
        with mock.patch.object(server.voice, "hear", return_value=heard):
            self.desk.send("", [f])
            self.assertTrue(wait_for(lambda: "card" in self.kinds()))
        note = self.desk.msgs[-1]["notes"][0]
        self.assertIn("한도를 넘어 읽지 않았습니다", note)
        self.assertLess(len(note), server.MAX_TEXT + 200)

    def test_video_or_photo_that_looks_like_audio_is_not_sent_to_transcription(self):
        """MP4 영상 · HEIC 사진은 음성과 같은 그릇이라 머리글자가 비슷하다 — 화면이 음성으로 고르지 않은 첨부는 받아쓰기로 보내지 않는다."""
        from unittest import mock
        mp4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 4000
        heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 4000
        files = [{"name": "참고 영상.mp4", "data": "data:video/mp4;base64," + base64.b64encode(mp4).decode(), "images": [], "state": "keep"},
                 {"name": "의뢰서 사진.heic", "data": "data:image/heic;base64," + base64.b64encode(heic).decode(), "images": [], "state": "failed"},
                 {"name": "사진인데 음성이라고 함.m4a", "data": "data:audio/mp4;base64," + base64.b64encode(heic).decode(), "images": [], "state": "voice"}]
        with mock.patch.object(server.voice, "hear") as hear:
            self.desk.send("홍대에서 여의도로 버스, 8초, 좌하단", files)
            self.assertTrue(wait_for(lambda: "card" in self.kinds()))
        hear.assert_not_called()
        self.assertFalse(any(f.get("voice") for f in self.desk.msgs[0]["files"]))

    def test_voice_that_cannot_be_heard_asks_for_text(self):
        from unittest import mock
        _, f = self.voice_file()
        heard = {"state": "못 받아씀", "text": "", "request_text": "", "note": "말소리를 알아듣지 못했습니다.", "doubts": [], "seconds": 0.1}
        with mock.patch.object(server.voice, "hear", return_value=heard):
            self.desk.send("", [f])
            self.assertTrue(self.idle(self.desk))
        p = self.desk.msgs[-1]
        self.assertEqual((p["kind"], p["state"]), ("progress", "fail"))
        self.assertIn("말소리를 알아듣지 못했습니다", p["error"])
        self.assertIn("글로 적어 주세요", p["error"])
        self.assertNotIn("card", self.kinds())

    def test_state_is_short_when_nothing_changed(self):
        full = self.desk.state()
        self.assertIn("messages", full)
        self.assertNotIn("messages", self.desk.state(full["rev"], full["room"]))


class PickArea(unittest.TestCase):
    def test_area_plan_builds_map_data_without_a_route(self):
        plan = mapdata.plan_area(37.55, 126.92, 2400, 1350)
        self.assertEqual(plan["view"], (-1200, -675, 1200, 675))
        lon, lat = plan["to_lonlat"](100, -50)
        self.assertAlmostEqual((lon - 126.92) * plan["kx"], 100, places=3)       # 화면이 쓰는 환산값과 같은 식
        self.assertAlmostEqual((37.55 - lat) * plan["ky"], -50, places=3)
        osm = {"elements": [{"type": "way", "tags": {"highway": "primary"},
                             "geometry": [{"lat": 37.55, "lon": 126.915}, {"lat": 37.55, "lon": 126.925}]}]}
        data = mapdata.build(osm, plan, "", "", 0.4)
        self.assertEqual((data["route"], data["from"], data["to"], data["total"]), ([], None, None, 0))
        self.assertEqual(len(data["roads"]), 1)


class GeoCache(unittest.TestCase):
    def test_broken_cache_file_is_fetched_again_and_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            geo = Geo(tmp)
            calls = []

            def fetch():
                calls.append(1)
                return {"ok": True}

            self.assertEqual(geo._cached("osm", "x", {"k": 1}, fetch), {"ok": True})
            path = next(Path(tmp).glob("osm_x_*.json"))
            path.write_text('{"ok": tr', encoding="utf-8")       # 쓰다 만 파일
            self.assertEqual(geo._cached("osm", "x", {"k": 1}, fetch), {"ok": True})
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(geo._cached("osm", "x", {"k": 1}, fetch), {"ok": True})
            self.assertEqual(len(calls), 2)                        # 고쳐진 뒤로는 다시 받지 않는다
            self.assertEqual([p.name for p in Path(tmp).iterdir() if p.suffix == ".tmp"], [])


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.desk = server.Desk(Path(cls.tmp.name), analyze=analyze, liner=False)
        cls.httpd = server.make_server(cls.desk, 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_page_and_script_are_served(self):
        status, body = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("방송그래픽 의뢰", body.decode("utf-8"))
        self.assertEqual(self.call("/web/app.js")[0], 200)

    def test_cannot_read_files_outside_web_folder(self):
        for path in ("/web/../server.py", "/web/..%2F..%2Fliner.py", "/jobs/../.env", "/uploads/../../.env"):
            self.assertEqual(self.call(path)[0], 404, path)

    def test_request_from_another_site_is_refused(self):
        status, _ = self.call("/api/send", {"text": TEXT}, {"Origin": "https://example.com"})
        self.assertEqual(status, 403)
        self.assertEqual(self.desk.msgs, [])

    def test_get_from_another_site_is_refused_and_page_cannot_be_framed(self):
        """다른 사이트(같은 컴퓨터의 다른 포트 포함)의 그림 · 끼워 넣기로 온 GET 은 막고, 이 화면은 남의 페이지에 끼워 넣을 수 없다."""
        for site in ("cross-site", "same-site"):
            self.assertEqual(self.call("/api/state", headers={"Sec-Fetch-Site": site})[0], 403, site)
        for site in ("same-origin", "none"):
            self.assertEqual(self.call("/", headers={"Sec-Fetch-Site": site})[0], 200, site)
        with urllib.request.urlopen(self.base + "/web/app.js", timeout=10) as r:
            self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
            self.assertIn("frame-ancestors 'self'", r.headers["Content-Security-Policy"])

    def test_bad_length_and_odd_shaped_values_get_an_answer_instead_of_a_dropped_connection(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.putrequest("POST", "/api/send")
        conn.putheader("Content-Length", "-1")      # 음수 길이를 그대로 읽으면 끝없이 기다린다
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        room = self.desk.state()["room"]
        for odd in ({"requester": "kim", "from": {"name": "홍대"}, "to": {"name": "여의도"}}, {"from": "홍대", "to": "여의도"}):
            log = io.StringIO()
            with contextlib.redirect_stderr(log):
                status, body = self.call("/api/make", {"request": odd, "room": room})
            self.assertEqual(status, 400, odd)
            self.assertIn("error", json.loads(body))
            if odd.get("requester") == "kim":       # AttributeError — 앱 안의 고장일 수도 있어 어디서 났는지 실행 창에 남긴다
                self.assertIn("/api/make 처리 중 AttributeError", log.getvalue())
                self.assertIn("Traceback", log.getvalue())

    def test_request_text_has_a_length_limit(self):
        room = self.desk.state()["room"]
        status, body = self.call("/api/send", {"text": "가" * (server.MAX_TEXT + 1), "room": room})
        self.assertEqual(status, 400)
        self.assertIn("너무 깁니다", json.loads(body)["error"])
        self.assertEqual(self.desk.msgs, [])

    def test_vehicle_picture_must_be_a_file_inside_the_app_folder(self):
        project = Path(self.tmp.name)
        (project / "data").mkdir(exist_ok=True)
        Image.new("RGBA", (8, 8)).save(project / "data" / "boat.png")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        Image.new("RGBA", (8, 8)).save(Path(outside.name) / "secret.png")
        self.assertEqual(server.build.inside("data/boat.png", [project]), project.resolve() / "data" / "boat.png")
        self.assertIsNotNone(server.build.inside(str(project / "data" / "boat.png"), [project]))       # 앱 폴더 안이면 절대 경로도 된다
        for bad in (str(Path(outside.name) / "secret.png"), "../secret.png", "data/../../secret.png", "//example.invalid/share/a.png", r"\\example.invalid\share\a.png", "data/없는그림.png"):
            self.assertIsNone(server.build.inside(bad, [project]), bad)
        room = self.desk.state()["room"]
        req = {"from": {"name": "홍대"}, "to": {"name": "여의도"}, "vehicle_image": str(Path(outside.name) / "secret.png")}
        status, body = self.call("/api/make", {"request": req, "room": room})
        self.assertEqual(status, 400)
        self.assertIn("앱 폴더 안의 파일만", json.loads(body)["error"])

    def test_pdf_reader_module_is_served_as_javascript(self):
        req = urllib.request.Request(self.base + "/web/vendor/pdfjs/pdf.min.mjs")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(r.headers["Content-Type"].startswith("text/javascript"))     # 모듈은 자바스크립트 형식이어야 불러와진다
            self.assertIn(b"4.10.38", r.read())

    def test_request_without_the_room_is_refused(self):
        status, body = self.call("/api/send", {"text": TEXT})
        self.assertEqual(status, 409)
        self.assertIn("대화가 바뀌었습니다", json.loads(body)["error"])
        self.assertEqual(self.desk.msgs, [])

    def test_make_with_missing_fields_lists_problems(self):
        status, body = self.call("/api/make", {"request": {"from": {"name": "홍대"}}, "room": self.desk.room})
        self.assertEqual(status, 400)
        problems = json.loads(body)["problems"]
        self.assertTrue(any("도착지" in p for p in problems))
        self.assertTrue(any("영상 길이" in p for p in problems))

    def test_export_of_unknown_message_is_404(self):
        self.assertEqual(self.call("/api/export", {"id": 999, "room": self.desk.room})[0], 404)


if __name__ == "__main__":
    unittest.main()
