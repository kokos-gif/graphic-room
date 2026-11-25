"""Liner 모델이 컷 나눔과 순위 변동까지 읽어 오는 길 — 가짜 응답으로만 본다(실제 Liner 는 부르지 않는다)."""
import json
import tempfile
import unittest
from pathlib import Path

from mapcg import board, cuts, intake, server


def reply(obj):
    return lambda messages, **options: {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


def cut(a, b, sec=4, **kw):
    return {"from_name": a, "to_name": b, "duration_seconds": sec, "bridge": False, "closeup": False, "note": "", **kw}


MAP = {"program": None, "requester_name": "홍길순", "requester_role": "PD", "requester_phone": None, "map_style_text": None,
       "map_style": "route_move", "from_name": "월드컵경기장 앞", "to_name": "노들로", "vehicle": "bus", "duration_seconds": 16,
       "layout": "unknown", "face_count": 4, "face_mode": "ride", "output_width": None, "output_height": None,
       "vehicle_color": "RED", "unclear": [],
       "cuts": [cut("월드컵경기장 앞", "월드컵대교 북단"), cut("월드컵대교 북단", "월드컵대교 남단", bridge=True, note="다리 특유의 디자인"),
                cut("월드컵대교 남단", "노들로 진입"), cut("노들로", "여의도 방향", None, closeup=True)]}

TABLE = {"is_rank_board": True, "title": "1차 미션 출연자 점수", "unit": "", "duration_seconds": None, "unclear": [],
         "rows": [{"rank": 1, "name": "김선수", "value_text": "1230"}, {"rank": 2, "name": "박선수", "value_text": "1120"}],
         "next_title": "2차 미션", "next_rows": [{"rank": 1, "name": "박선수", "value_text": "1300", "out": False},
                                               {"rank": 2, "name": "김선수", "value_text": "기권", "out": True}]}


class MapCuts(unittest.TestCase):
    def read(self, obj):
        return intake.analyze("", ["data:image/jpeg;base64,AAAA"], chat=reply(obj), order=["liner"])

    def test_컷과_색을_초안에_붙인다(self):
        r = self.read(MAP)
        d = r["draft"]
        self.assertEqual(r["source"], "liner")
        self.assertEqual([(c["from"], c["to"], c["duration_seconds"]) for c in d["cuts"]],
                         [("월드컵경기장 앞", "월드컵대교 북단", 4), ("월드컵대교 북단", "월드컵대교 남단", 4),
                          ("월드컵대교 남단", "노들로 진입", 4), ("노들로", "여의도 방향", 4)])       # 길이가 빈 컷은 남는 시간(16 − 12)을 받는다
        self.assertEqual(d["cuts"][1]["bridge"], {"look": "cable"})
        self.assertEqual(d["cuts"][3]["vehicle_scale"], 1.7)
        self.assertEqual((d["duration_seconds"], d["vehicle_color"]), (16, "red"))
        self.assertTrue(any("컷 4개로 나눠" in n for n in r["notes"]))

    def test_읽은_초안이_컷_나눔_검사를_통과한다(self):
        d = self.read(MAP)["draft"]
        spec = cuts.normalize({**d, "faces": []})
        self.assertEqual((len(spec["cuts"]), spec["base"]["duration_seconds"], spec["base"]["vehicle_color"]), (4, 16, "#e8443a"))
        self.assertEqual(spec["cuts"][1]["bridge"], {"look": "cable"})

    def test_컷이_하나뿐이면_컷_나눔이_아니다(self):
        d = self.read({**MAP, "cuts": [cut("가", "나")], "vehicle_color": "무지개"})
        self.assertNotIn("cuts", d["draft"])
        self.assertNotIn("vehicle_color", d["draft"])
        self.assertTrue(any("준비된 색이 아니어서" in n for n in d["notes"]))

    def test_예전_모양의_답도_그대로_읽는다(self):
        old = {k: v for k, v in MAP.items() if k not in ("cuts", "vehicle_color")}
        self.assertNotIn("cuts", self.read(old)["draft"])


class BoardNext(unittest.TestCase):
    def test_뒤_표를_다음_순위로_붙이고_검사를_통과한다(self):
        got = board.read("", ["data:image/jpeg;base64,AAAA"], available={"liner": True, "claude": False}, chat=reply(TABLE))
        d = got["draft"]
        self.assertEqual(got["source"], "liner")
        self.assertEqual(d["next"]["title"], "2차 미션")
        self.assertEqual([(r["name"], r["value_text"], r.get("out", False)) for r in d["next"]["rows"]],
                         [("박선수", "1300", False), ("김선수", "기권", True)])
        self.assertTrue(any("순위 변동판으로 만듭니다" in n for n in got["notes"]))
        req = board.normalize(d)
        self.assertEqual([r["name"] for r in req["next"]["rows"]], ["박선수", "김선수"])

    def test_표가_하나면_다음_순위가_없다(self):
        got = board.read("", ["data:image/jpeg;base64,AAAA"], available={"liner": True, "claude": False},
                         chat=reply({**TABLE, "next_title": "", "next_rows": []}))
        self.assertNotIn("next", got["draft"])


class DeskCuts(unittest.TestCase):
    def test_컷이_든_확인_카드는_컷_나눔으로_만든다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = server.Desk(Path(tmp.name), liner=False, offline=True)
        seen = {}
        d._spawn = lambda fn, *a, **_: seen.update(fn=fn.__name__, args=a)
        draft = intake.analyze("", ["data:image/jpeg;base64,AAAA"], chat=reply(MAP), order=["liner"])["draft"]
        d.make(draft)
        self.assertEqual(seen["fn"], "_make_cuts")
        self.assertEqual(len(seen["args"][1]["cuts"]), 4)
        self.assertIn("4컷", d.msgs[-1]["title"])


class ConnectionCheck(unittest.TestCase):
    """연결 점검 명령 — 키가 들어왔을 때 도는 단계들을 가짜 Liner 로 끝까지 돌려 본다."""

    def run_check(self, chat):
        from unittest import mock
        from mapcg import audit, check
        liner = intake._liner()
        lines = []
        with mock.patch.object(intake, "has_key", lambda: True), mock.patch.object(liner, "chat", chat),                 mock.patch.dict("os.environ", {"MAPCG_AUDIT": "auto", "LINER_MODEL": "", "LINER_AUDIT_MODEL": "", "MAPCG_READER": "auto"}):
            code = check.run(log=lines.append)
        return code, lines, audit

    @staticmethod
    def fake(text_map, table):
        """가짜 Liner: 글로 온 지도 의뢰에는 text_map 을, 그림으로 온 것에는 노들섬 → 여의도를, 순위판에는 table 을 답한다."""
        from mapcg import audit

        def chat(messages, **o):
            name = (o.get("response_format") or {}).get("json_schema", {}).get("name")
            pictured = any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"]) for m in messages)
            obj = (({**MAP, "from_name": "노들섬", "to_name": "여의도", "cuts": []} if pictured else text_map) if name == "map_request"
                   else table if name == "rank_board_request"
                   else {"checks": [{"field": f, "verdict": "ok", "evidence": "", "found": ""} for f in audit.LABELS]} if name == "reading_audit" else None)
            return {"model": "가짜-모델", "choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False) if obj else "연결"}}]}
        return chat

    GOOD_MAP = {**MAP, "from_name": "홍대", "to_name": "여의도", "duration_seconds": 8, "cuts": []}
    GOOD_TABLE = {**TABLE, "rows": [{"rank": 1, "name": "가람", "value_text": "120점"}, {"rank": 2, "name": "나래", "value_text": "95점"}], "next_rows": []}

    def test_다_되면_0을_돌려준다(self):
        code, lines, _ = self.run_check(self.fake(self.GOOD_MAP, self.GOOD_TABLE))
        self.assertEqual(code, 0, lines)
        self.assertEqual(sum("[됨]" in s for s in lines), 5)
        self.assertFalse(any("LINER_API_KEY=" in s and len(s.split("LINER_API_KEY=")[1].strip()) > 20 for s in lines))

    def test_틀은_맞아도_읽은_값이_다르면_안_됨이다(self):
        # 출발·도착이 빈 답, 줄이 없는 표 — 예전에는 '됨'으로 나왔다
        code, lines, _ = self.run_check(self.fake({**self.GOOD_MAP, "from_name": None, "to_name": None}, {**self.GOOD_TABLE, "rows": []}))
        self.assertEqual(code, 1, lines)
        self.assertTrue(any("[안 됨] 지도 의뢰를 정해진 틀로 읽기" in s for s in lines), lines)
        self.assertTrue(any("[안 됨] 순위판 표를 정해진 틀로 읽기" in s and "가람 120" in s for s in lines), lines)
        self.assertTrue(any("[안 됨] 판독 감사" in s and "감사할 판독이 없습니다" in s for s in lines), lines)       # 읽지 못한 것을 간이 초안으로 바꿔 감사하지 않는다

    def test_첫_호출이_안_되면_까닭을_적고_멈춘다(self):
        liner = intake._liner()

        def chat(messages, **o):
            raise liner.LinerError(401, "unauthorized")

        code, lines, _ = self.run_check(chat)
        self.assertEqual(code, 1)
        self.assertTrue(any("키가 맞지 않습니다" in s for s in lines))


class OfficialNames(unittest.TestCase):
    def test_정식_이름과_흔한_이름을_가려_받는다(self):
        got = intake.official_names(["남산한옥마을", "쇼핑센터", "남산한옥마을", ""], chat=reply({"places": [
            {"written": "남산한옥마을", "search_name": "남산골한옥마을", "kind": "place"},
            {"written": "쇼핑센터", "search_name": "쇼핑센터", "kind": "generic"},
            {"written": "받지 않은 이름", "search_name": "엉뚱한 곳", "kind": "place"}]}))
        self.assertEqual(got, {"남산한옥마을": {"search_name": "남산골한옥마을", "kind": "place"},
                               "쇼핑센터": {"search_name": "쇼핑센터", "kind": "generic"}})

    def test_물을_이름이_없으면_부르지_않는다(self):
        self.assertEqual(intake.official_names(["", " "], chat=lambda *a, **k: self.fail("불렀다")), {})


class SearchedName(unittest.TestCase):
    RESULTS = {"results": [{"title": "남산골한옥마을", "url": "https://example.org/a", "description": "서울 중구 퇴계로34길 28"},
                           {"title": "다른 글", "url": "https://example.org/b", "description": ""}]}

    def test_검색_결과를_근거로_정식_이름과_출처를_받는다(self):
        asked = {}

        def search(query, **options):
            asked.update(query=query, **options)
            return self.RESULTS

        def chat(messages, **o):
            asked["body"] = messages[1]["content"][0]["text"]
            return reply({"search_name": "남산골한옥마을", "sources": [1, 9], "sure": True})(messages, **o)

        got = intake.searched_name("남산한옥마을", near="서울역 DDP", chat=chat, search=search)
        self.assertEqual(got, {"search_name": "남산골한옥마을", "sources": ["https://example.org/a"]})
        self.assertIn("남산한옥마을 서울역 DDP", asked["query"])
        self.assertIn("[1] 남산골한옥마을", asked["body"])            # 검색 결과가 모델에게 근거로 넘어간다
        self.assertFalse(asked["include_knowledge"])

    def test_근거가_분명하지_않으면_쓰지_않는다(self):
        unsure = reply({"search_name": "남산한옥마을", "sources": [], "sure": False})
        self.assertIsNone(intake.searched_name("남산한옥마을", chat=unsure, search=lambda q, **o: self.RESULTS))
        self.assertIsNone(intake.searched_name("남산한옥마을", chat=lambda *a, **k: self.fail("결과가 없는데 물었다"), search=lambda q, **o: {"results": []}))


class RouteGuard(unittest.TestCase):
    def test_너무_넓은_지도는_받지_않는다(self):
        from mapcg.geo import Geo, GeoError
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(GeoError) as e:
            Geo(Path(tmp.name), offline=True).osm((34.6, 125.7, 37.6, 127.0))
        self.assertIn("너무 넓습니다", str(e.exception))


class MapPacks(unittest.TestCase):
    """앱에 넣어 둔 지도 묶음 — 그 안의 범위는 지도 서버를 부르지 않고 그린다."""

    def setUp(self):
        from unittest import mock
        from mapcg import packs
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, value in (("PACK_DIR", root / "packs"), ("INDEX", root / "packs" / "index.json")):
            m = mock.patch.object(packs, name, value)
            m.start()
            self.addCleanup(m.stop)
        self.packs, self.cache = packs, root / "geo"
        packs.save("seoul", (37.4, 126.7, 37.7, 127.3), "major", {"elements": [
            {"type": "way", "id": 1, "nodes": [1, 2], "bounds": {}, "tags": {"highway": "primary"},
             "geometry": [{"lat": 37.5000004, "lon": 126.9000004}, {"lat": 37.51, "lon": 126.91}]}]})

    def test_묶음_안의_범위는_서버_없이_그린다(self):
        from mapcg.geo import Geo
        g = Geo(self.cache, offline=True)       # 인터넷 없이
        data = g.osm((37.5, 126.9, 37.52, 126.93), "major")
        self.assertEqual(data["elements"][0]["geometry"][0], {"lat": 37.5, "lon": 126.9})      # 좌표는 반올림해 넣어 둔다
        self.assertNotIn("nodes", data["elements"][0])
        self.assertNotIn("_note", data)

    def test_골목길까지_필요한데_서버가_안_되면_큰길_묶음으로_그리고_알린다(self):
        from unittest import mock
        from mapcg import geo
        g = geo.Geo(self.cache)
        with mock.patch.object(geo.Geo, "_http", staticmethod(lambda *a, **k: (_ for _ in ()).throw(geo.GeoError("막힘")))),                 mock.patch.dict(geo._rested, {}, clear=True):
            data = g.osm((37.5, 126.9, 37.52, 126.93), "minor")
        self.assertIn("지도 묶음으로 그렸습니다", data["_note"])

    def test_묶음_밖이고_서버도_안_되면_바탕_없이_그리고_알린다(self):
        from unittest import mock
        from mapcg import geo
        g = geo.Geo(self.cache)
        with mock.patch.object(geo.Geo, "_http", staticmethod(lambda *a, **k: (_ for _ in ()).throw(geo.GeoError("막힘")))),                 mock.patch.dict(geo._rested, {}, clear=True):
            data = g.osm((35.1, 129.0, 35.2, 129.1), "major")
        self.assertEqual(data["elements"], [])
        self.assertIn("바탕 지도(도로·강) 없이", data["_note"])


if __name__ == "__main__":
    unittest.main()
