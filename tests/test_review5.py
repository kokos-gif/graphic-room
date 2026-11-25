"""전체 코드 통독(11-23)에서 찾은 것 — 모델 답의 틀린 모양 · 맥의 한글 파일 이름 · 경로 서버의 400."""
import unicodedata
import unittest
from unittest import mock

from mapcg import build, geo, scene, server


class 첨부_이름(unittest.TestCase):
    def test_맥이_자모로_풀어_보낸_한글_이름도_그대로_남는다(self):
        name = unicodedata.normalize("NFD", "출연진 시트.png")
        self.assertNotEqual(name, "출연진 시트.png")       # 풀어 쓴 글자다
        self.assertEqual(server._safe_name(name), "출연진_시트.png")

    def test_긴_한글_이름은_파일_이름_한도_안으로_줄이고_확장자는_남긴다(self):
        got = server._safe_name("가" * 120 + ".pdf")
        self.assertTrue(got.endswith(".pdf"))
        self.assertLessEqual(len(got.encode("utf-8")), 180)

    def test_빈_이름은_file(self):
        self.assertEqual(server._safe_name("..."), "file")


class 장면_설계의_틀린_값(unittest.TestCase):
    def test_글자_숫자와_무한대와_목록_아이콘이_와도_예외_없이_정리한다(self):
        got = scene.normalize_scene({"layout": "rows", "grid_total": "12.0", "grid_cols": "1,000", "duration_seconds": 1e999,
                                     "items": [{"id": "a", "icon": ["A01"], "value": float("inf")}, {"id": "b", "icon": "A01", "value": "3"}]})
        self.assertEqual((got["grid_total"], got["grid_cols"], got["duration_seconds"]), (12, 1000, None))
        self.assertEqual([(i["icon"], i["value"]) for i in got["items"]], [(None, None), ("A01", 3.0)])
        self.assertIsNone(scene.normalize_scene({"grid_total": 1e999})["grid_total"])

    def test_글자_목록_자리에_글자_하나가_와도_목록으로_바꾼다(self):
        self.assertEqual(scene._strs("한 줄 기준"), ["한 줄 기준"])
        self.assertEqual(scene._strs("없음"), [])
        self.assertEqual(scene._strs(None), [])
        self.assertEqual(scene._strs(["가", "", 3, {"x": 1}, None]), ["가", "3"])

    def test_지시서와_담당의_목록_칸은_검사_뒤에_언제나_목록이다(self):
        brief = {"viewer_point": "열쇠 현황", "facts": [{"id": "f1", "fact": "가람 2개", "quote": "가람 2개"}],
                 "checks": "가람 2개가 보이는가", "must_not_assert": "없음", "open_questions": None}
        worker = {"role": "열쇠 현황판", "goal": "개수", "fact_ids": ["f1"], "layouts": "rows", "checks": "글자가 읽히는가"}
        unable = {"route": "자료 필요", "reason": "사진 없음", "needed": "사진"}
        got, _ = scene.check_main([("c1", "write_brief", brief, ""), ("c2", "spawn_worker", worker, ""), ("c3", "report_unable", unable, "")],
                                  "가람 2개, 나래 1개")
        self.assertEqual((got["brief"]["checks"], got["brief"]["must_not_assert"], got["brief"]["open_questions"]), (["가람 2개가 보이는가"], [], []))
        self.assertEqual((got["workers"][0]["layouts"], got["workers"][0]["checks"]), (["rows"], ["글자가 읽히는가"]))
        self.assertEqual(got["unable"][0]["needed"], ["사진"])


class 경로_서버의_400(unittest.TestCase):
    def test_길이_없다는_답은_서버_고장으로_치지_않는다(self):
        err = geo.GeoError("지도 서버 응답 400: x")
        err.status = 400
        me = mock.Mock()
        me._http.side_effect = err
        with mock.patch.dict(geo._rested, {}, clear=True):
            with self.assertRaisesRegex(geo.GeoError, "경로를 찾지 못했습니다"):
                geo.Geo._route(me, "1,2;3,4")
            self.assertEqual(geo._rested, {})             # 쉬게 한 서버가 없다
            self.assertEqual(me._http.call_count, 1)      # 다른 서버에 같은 것을 또 묻지 않는다

    def test_서버가_안_되면_예전처럼_다음_서버로_넘어간다(self):
        err = geo.GeoError("지도 서버 응답 503: x")
        err.status = 503
        me = mock.Mock()
        me._http.side_effect = err
        with mock.patch.dict(geo._rested, {}, clear=True):
            with self.assertRaisesRegex(geo.GeoError, "503"):
                geo.Geo._route(me, "1,2;3,4")
            self.assertEqual(me._http.call_count, len(geo.ROUTERS))


class 그리지_않는_칸(unittest.TestCase):
    def test_줄_목록의_시각은_읽혀야_할_글자로_세지_않고_코드_검사가_알린다(self):
        sc = scene.normalize_scene({"layout": "rows", "items": [{"id": "a", "label": "집합", "time": "7시", "fact_ids": ["f1"]}]})
        self.assertEqual(scene.wanted_texts(sc), ["집합"])
        notes = scene.check_scene(sc, {"layouts": ["rows"], "fact_ids": ["f1"]})
        self.assertEqual(len(notes), 1)
        self.assertIn("그리지 않는 칸", notes[0])

    def test_시간표의_시각과_대화의_시각은_그대로_센다(self):
        lanes = scene.normalize_scene({"layout": "lanes", "groups": [{"id": "g", "name": "가람"}],
                                       "items": [{"id": "a", "group": "g", "text": "대기실", "time": "8시 10분", "value_text": "3점"}]})
        self.assertEqual(scene.wanted_texts(lanes), ["가람", "대기실", "8시 10분"])       # 값(value_text)은 시간표가 그리지 않는다
        chat = scene.normalize_scene({"layout": "chat", "items": [{"id": "a", "label": "가람", "text": "3시에 만나요", "time": "오후 2:01"}]})
        self.assertEqual(scene.wanted_texts(chat), ["가람", "3시에 만나요", "오후 2:01"])

    def test_차례로_바뀌는_글은_항목마다_큰_글자_하나만_센다(self):
        sc = scene.normalize_scene({"layout": "headline", "motion": {"order": "swap"}, "duration_seconds": 3, "over_video": True,
                                    "items": [{"id": "a", "value_text": "5", "text": "초"}, {"id": "b", "value_text": "4"}]})
        self.assertEqual(scene.wanted_texts(sc), ["5", "4"])
        self.assertEqual([k for _, k in scene.undrawn(sc)], ["text"])


class 거절된_호출(unittest.TestCase):
    def post(self, code: int, rid: str):
        import io
        import urllib.error

        import liner

        def refuse(*a, **k):
            raise urllib.error.HTTPError("https://x", code, "거절", {}, io.BytesIO(b"{}"))

        with mock.patch.dict("os.environ", {"LINER_API_KEY": "k", "LINER_LOG": "off"}), mock.patch.object(liner, "_open", side_effect=refuse):
            with liner.request(rid):
                with self.assertRaises(liner.LinerError):
                    liner._post("/x", {}, bearer=True, retries=0)
        return liner

    def test_403_은_그_호출만_실패하고_401은_의뢰째_막는다(self):
        import liner
        with mock.patch.dict(liner._blocked, {}, clear=True):
            self.post(403, "r-권한")
            self.assertNotIn("r-권한", liner._blocked)      # 검색 · 그림 권한이 없다고 읽기 · 제작까지 막지 않는다
            self.post(401, "r-키")
            self.assertEqual(liner._blocked["r-키"][0], 401)


class 같은_지시서를_두_번_만들지_않는다(unittest.TestCase):
    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict("os.environ", {"MAPCG_ROOM": ""})
        env.start()
        self.addCleanup(env.stop)
        self.desk = server.Desk(Path(self.tmp.name) / "앱", offline=True)
        self.desk.offline = False
        self.desk.readers["liner"] = True
        plan = self.desk.out / "지시서_시험"
        plan.mkdir(parents=True)
        (plan / "지시서.json").write_text(json.dumps({"workers": [{"role": "가"}]}), encoding="utf-8")
        self.raw = {"kind": "scene", "plan": "지시서_시험", "what": "시험"}

    def test_만드는_중에_또_누르면_받지_않고_끝나면_다시_받는다(self):
        with mock.patch.object(self.desk, "_start") as start:
            self.desk.make(dict(self.raw), room=self.desk.room)
            with self.assertRaises(server.DeskError) as e:
                self.desk.make(dict(self.raw), room=self.desk.room)
            self.assertEqual(e.exception.status, 409)
            self.assertEqual(start.call_count, 1)
            req = start.call_args.args[-1]
            with mock.patch.object(self.desk, "_make_scene", side_effect=RuntimeError("고장")):
                with self.assertRaises(RuntimeError):
                    self.desk._make_scene_once({}, req)       # 고장으로 끝나도 표시는 풀린다
            self.assertEqual(self.desk.making, set())
            self.desk.make(dict(self.raw), room=self.desk.room)
            self.assertEqual(start.call_count, 2)

    def test_시작하지_못하면_표시를_남기지_않는다(self):
        with mock.patch.object(self.desk, "_start", side_effect=server.DeskError("대화가 바뀌었습니다", 409)):
            with self.assertRaises(server.DeskError):
                self.desk.make(dict(self.raw), room=self.desk.room)
        self.assertEqual(self.desk.making, set())


class 수정_요청에는_앞서_보낸_의뢰서가_따라간다(unittest.TestCase):
    """11-23 사용자 실측: 손그림 의뢰서로 시안을 받은 뒤 "이 느낌이 아니다 · 의뢰서를 다시 볼 것"이라고 보내자
    접수가 세 번 내리 "의뢰서 그림이 첨부되지 않았다"고 되물었다(뒤에 온 글이 새 의뢰로 읽혀 그림이 따라가지 않았다)."""
    PIC = "data:image/jpeg;base64,/9j/2Q=="

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict("os.environ", {"MAPCG_ROOM": ""})
        env.start()
        self.addCleanup(env.stop)
        self.desk = server.Desk(Path(self.tmp.name) / "앱", offline=True)
        start = mock.patch.object(self.desk, "_start")
        self.start = start.start()
        self.addCleanup(start.stop)

    def read(self):
        """마지막으로 읽기에 넘긴 (진행 제목, 글, 그림들)."""
        a = self.start.call_args.args
        return a[1], a[3], a[4]

    def sheet(self):
        self.desk.send("배경 핑크, 구매리스트", [{"name": "의뢰서.jpg", "data": self.PIC, "images": [self.PIC], "state": "read"}], room=self.desk.room)

    def test_첨부_없이_온_수정_요청은_앞_의뢰서_그림과_함께_읽는다(self):
        self.sheet()
        self.desk.send("이 느낌이 아니다. 의뢰서를 다시 볼 것", [], room=self.desk.room)
        title, text, images = self.read()
        self.assertEqual(images, [self.PIC])
        self.assertIn("앞서 보낸 의뢰서와 함께", title)
        self.assertIn("배경 핑크, 구매리스트", text)
        self.assertTrue(text.endswith("[이어서 온 글]\n이 느낌이 아니다. 의뢰서를 다시 볼 것"))
        self.desk.send("화살표가 빠졌다", [], room=self.desk.room)        # 수정 요청이 이어져도 그림은 계속 따라가고, 앞의 수정 요청도 남는다
        _, text, images = self.read()
        self.assertEqual(images, [self.PIC])
        self.assertEqual(text.count("[앞서 보낸 의뢰"), 1)
        self.assertIn("이 느낌이 아니다", text)
        self.assertTrue(text.endswith("화살표가 빠졌다"))

    def test_새_의뢰로_보이는_글과_새_첨부에는_붙이지_않는다(self):
        self.sheet()
        self.desk.send("홍대에서 여의도로 버스 이동 지도 8초", [], room=self.desk.room)
        self.assertEqual(self.read()[1:], ("홍대에서 여의도로 버스 이동 지도 8초", []))
        self.desk.send("이 느낌이 아니다", [], room=self.desk.room)          # 바로 앞 의뢰(홍대 지도)에는 그림이 없었다
        _, text, images = self.read()
        self.assertEqual(images, [])
        self.assertIn("홍대에서 여의도로", text)

    def test_새_대화에서는_지난_대화의_의뢰서를_붙이지_않는다(self):
        self.sheet()
        self.desk.reset(self.desk.room)
        self.desk.send("이 느낌이 아니다. 다시", [], room=self.desk.room)
        self.assertEqual(self.read()[1:], ("이 느낌이 아니다. 다시", []))


class 메인_에이전트는_의뢰서_그림도_읽는다(unittest.TestCase):
    """11-23 사용자 실측: 손그림 의뢰서에 엇갈린 화살표 넷이 그려져 있었는데 시안에는 화살표가 없었다 —
    메인 에이전트가 의뢰 글만 받아, 그림에만 있는 짝(1만원 → 기차 …)을 알 수 없었다."""
    PIC = "data:image/jpeg;base64,/9j/2Q=="
    BRIEF = {"viewer_point": "금액과 아이템이 엇갈려 이어진다", "checks": ["화살표가 1만원과 기차를 잇는가"], "must_not_assert": [], "open_questions": [],
             "facts": [{"id": "f1", "fact": "1만원에서 기차로 가는 화살표", "quote": "① 10000 에서 ② 기차로 가는 화살표", "source": "의뢰서 그림"}]}
    WORKER = {"role": "화살표 담당", "goal": "links 로 그린다", "fact_ids": ["f1"], "layouts": ["columns"], "checks": ["화살표가 1만원과 기차를 잇는가"]}

    def calls(self):
        import copy
        return [("c1", "write_brief", copy.deepcopy(self.BRIEF), ""), ("c2", "spawn_worker", copy.deepcopy(self.WORKER), "")]

    def test_그림에서_읽은_사실은_그림이_같이_왔을_때만_받는다(self):
        _, problems = scene.check_main(self.calls(), "의뢰서 그림대로 만들어 주세요", pictured=True)
        self.assertEqual(problems, [])
        _, problems = scene.check_main(self.calls(), "의뢰서 그림대로 만들어 주세요")          # 그림 없이 '그림에서 읽었다'는 사실은 받지 않는다
        self.assertTrue(any("근거(quote)" in p for p in problems))

    def test_지시서를_쓸_때_의뢰서_그림을_함께_보낸다(self):
        import json
        seen = []

        def chat(*a, **k):
            seen.append(a[0] if a else k["messages"])
            tool = lambda n, args, cid: {"id": cid, "type": "function", "function": {"name": n, "arguments": json.dumps(args, ensure_ascii=False)}}      # noqa: E731
            return {"model": "시험", "choices": [{"message": {"role": "assistant", "content": "",
                                                            "tool_calls": [tool("write_brief", self.BRIEF, "c1"), tool("spawn_worker", self.WORKER, "c2")]}}]}

        got = scene.plan_request("의뢰서 그림대로", log=lambda *a: None, chat=chat, images=[self.PIC])
        self.assertTrue(got["main_ok"], got["problems"])
        content = seen[0][1]["content"]
        self.assertEqual([c["type"] for c in content], ["text", "image_url"])
        self.assertIn("의뢰서 그림 1장", content[0]["text"])
        self.assertIsInstance(scene.plan_request("글만", log=lambda *a: None, chat=chat)["msgs"][0]["content"], str)      # 그림이 없으면 예전처럼 글 하나

    def test_화살표가_안_나왔다는_말도_수정_요청으로_본다(self):
        for said in ("지금 화살표가 안나왔는데", "화살표가 안 보여요", "제목이 없는데요", "왜 두 칸뿐이죠"):
            self.assertTrue(server.FOLLOW_UP.search(said), said)
        self.assertFalse(server.FOLLOW_UP.search("홍대에서 여의도로 버스 이동 지도 8초"))


class 지명은_서울과_장소_둘레에서_먼저_찾는다(unittest.TestCase):
    """11-23 사용자 실측: 서울시청 의뢰의 '시청' → '시청광장'이 대전 시청 → 세종 시청광장으로 잡혔다(지명을 전국에서 찾고 '장소' 칸을 쓰지 않았다)."""
    SEOUL = {"display": "시청, 세종대로, 중구, 서울특별시", "lat": 37.5657, "lon": 126.9769}
    SEJONG = {"display": "시청광장, 보람동, 세종특별자치시", "lat": 36.48, "lon": 127.289}

    def geo(self, answer):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        g = geo.Geo(Path(tmp.name))
        self.asked = []

        def fake(name, country="kr", near=None, km=30):
            self.asked.append((name, near, km))
            hits = answer(name, near)
            if not hits:
                raise geo.GeoNotFound(name)
            return {"name": name, **hits[0], "candidates": hits}
        g.geocode = fake
        return g

    def test_둘레를_주지_않은_지명은_서울_안에서_먼저_찾는다(self):
        g = self.geo(lambda name, near: [self.SEOUL] if near else [self.SEJONG])
        hit = g.geocode_loose("시청")
        self.assertEqual((hit["display"], hit.get("outside")), (self.SEOUL["display"], None))
        self.assertEqual(self.asked, [("시청", geo.HOME, geo.HOME_KM)])

    def test_서울에_없는_이름만_전국에서_찾고_표시를_남긴다(self):
        g = self.geo(lambda name, near: [] if near else [self.SEJONG])
        hit = g.geocode_loose("시청광장")
        self.assertTrue(hit["outside"])
        self.assertIn("서울 안에서 찾지 못해", build.outside_note(hit))
        with mock.patch.dict("os.environ", {"MAPCG_REGION": "all"}):      # 서울 밖 의뢰를 다룰 때: 처음부터 전국
            self.asked.clear()
            self.assertNotIn("outside", g.geocode_loose("시청광장"))
            self.assertEqual(self.asked, [("시청광장", None, 30)])

    def test_의뢰의_장소가_있으면_그_둘레에서_먼저_찾고_가까운_쌍으로_다시_고르지_않는다(self):
        plaza = {"display": "서울광장, 중구, 서울특별시", "lat": 37.5656, "lon": 126.978}
        shop = {"display": "도시락집, 시청광장 지하도상가, 을지로", "lat": 37.566, "lon": 126.99}
        g = self.geo(lambda name, near: [plaza] if name == "서울광장" else [self.SEOUL] if name == "시청" else [shop] if near else [self.SEJONG])
        near, note = build.area_anchor(g, {"area": {"name": "서울광장"}})
        self.assertEqual((near, note), ((plaza["lat"], plaza["lon"]), None))
        a, b = build._resolve(g, {"name": "시청"}, near), build._resolve(g, {"name": "지하상가"}, near)
        self.assertEqual(a["display"], self.SEOUL["display"])
        self.assertNotIn("near_note", a)                     # 이름 그대로 실린 곳
        self.assertEqual(b["display"], shop["display"])      # 세종이 아니라 장소 둘레
        self.assertIn("지점이 맞는지", b["near_note"])        # 이름이 그대로 실린 곳이 아니면 알린다
        # '시청광장'은 지도에 '서울광장'으로 실려 있다 — 실린 이름으로 찾고 무엇으로 찾았는지 남긴다
        c = build._resolve(g, {"name": "시청 광장"}, near)
        self.assertEqual((c["display"], c["name"], c["searched"]), (plaza["display"], "시청 광장", "서울광장"))

    def test_장소를_못_찾으면_알리고_예전처럼_찾는다(self):
        g = self.geo(lambda name, near: [] if "없는" in name else [self.SEOUL])
        near, note = build.area_anchor(g, {"area": {"name": "없는 장소"}})
        self.assertIsNone(near)
        self.assertIn("좁히지 못했습니다", note)
        self.assertEqual(build.area_anchor(g, {}), (None, None))


if __name__ == "__main__":
    unittest.main()
