"""한 화면 지도 · 네비 화면의 지명 찾기: 지도에 없는 이름은 ① 띄어쓰기를 뺀 이름 ② Liner 웹 검색으로 확인한 정식 이름으로 다시 찾는다.

11-23 손그림 의뢰서 시험: '안국역 6번출구'를 한 화면 지도에서 못 찾아 실패했다(Liner 호출 0번 — 검색은 컷 나눔 지도에만 연결돼 있었다).
지도에는 '안국역6번출구'(정류장)는 있고 '안국역 6번출구'는 없다. 지도 서버 · Liner 는 부르지 않는다(가짜로 바꿔 끼운다).
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import build, navi, server
from mapcg.geo import GeoError, GeoNotFound


class Stop(Exception):
    """지명을 다 찾은 뒤 경로 받기에서 멈춘다(지도 도형 · 화면 만들기는 이 시험의 몫이 아니다)."""


SPOTS = {"안국역6번출구": (37.57585, 126.98415), "인사동5길": (37.57323, 126.98694),
         "서강이엔한강LPG충전소": (37.5791, 126.8952), "가양대교": (37.5735, 126.8638)}


class FakeGeo:
    """SPOTS 에 있는 이름만 찾는다(띄어쓰기도 그대로 맞아야 한다). 경로 받기에서 멈추며 받은 두 지점을 남긴다."""

    def __init__(self, down=()):
        self.asked, self.down, self.routed = [], set(down), None

    def geocode_loose(self, name, country="kr", near=None, km=30):
        self.asked.append(name)
        if name in self.down:
            raise GeoError("지명 서버가 응답하지 않습니다")
        if name not in SPOTS:
            raise GeoNotFound(f"'{name}' 을(를) 지도에서 찾지 못했습니다. 더 구체적인 이름이나 좌표를 넣어 주세요.")
        lat, lon = SPOTS[name]
        return {"name": name, "lat": lat, "lon": lon, "display": f"{name}, 종로구, 서울특별시", "candidates": []}

    def route(self, a, b, *args, **kw):
        self.routed = (a, b)
        raise Stop()


def find(geo, place):
    return build._resolve(geo, place)


def searcher(answer=None, error=None):
    """Liner 검색 대역: 받은 것을 남기고 정해 둔 답을 준다."""
    calls = []

    def search(name, near=""):
        calls.append((name, near))
        if error:
            raise error
        return answer
    search.calls = calls
    return search


class 다시_찾기(unittest.TestCase):
    def test_띄어쓰기를_뺀_이름으로_찾고_화면_이름표는_적힌_그대로(self):
        geo, search = FakeGeo(), searcher()
        hit = build.resolve_or_search(geo, {"name": "안국역 6번출구"}, find, search, ("인사동5길",), notes := [])
        self.assertEqual((hit["name"], hit["searched"], hit["lat"]), ("안국역 6번출구", "안국역6번출구", 37.57585))
        self.assertEqual(search.calls, [])          # 돈이 들지 않는 길에서 찾았으면 Liner 를 부르지 않는다
        self.assertEqual(notes, [])                 # 알림은 부르는 쪽의 기존 문구(searched)로 나간다

    def test_Liner_검색으로_확인한_정식_이름으로_찾고_근거를_남긴다(self):
        geo = FakeGeo()
        search = searcher({"search_name": "서강이엔 한강LPG충전소", "sources": ["https://example.invalid/a"]})
        hit = build.resolve_or_search(geo, {"name": "SK엔크린 서강이엔한강LPG충전소"}, find, search, ("DMC역", "", "DMC역"), notes := [])
        self.assertEqual((hit["name"], hit["lat"]), ("SK엔크린 서강이엔한강LPG충전소", 37.5791))
        self.assertNotIn("searched", hit)           # 같은 지점을 두 알림이 겹쳐 말하지 않게
        self.assertEqual(search.calls, [("SK엔크린 서강이엔한강LPG충전소", "DMC역")])     # 같은 의뢰의 다른 지명으로 좁힌다(빈 칸 · 겹침 없이)
        self.assertEqual(geo.asked[-1], "서강이엔한강LPG충전소")       # 정식 이름도 띄어쓰기를 빼서 한 번 더
        self.assertEqual(len(notes), 1)
        self.assertIn("Liner 검색으로 확인한 정식 이름 '서강이엔한강LPG충전소'", notes[0])
        self.assertIn("근거: https://example.invalid/a", notes[0])

    def test_검색이_없거나_안_되면_처음의_찾지_못함을_그대로_알린다(self):
        for search in (None, searcher(None), searcher(error=RuntimeError("402 잔액")),
                       searcher({"search_name": "한강H2 복합충전소", "sources": ["https://example.invalid/b"]}),       # 같은 이름이면 다시 찾지 않는다
                       searcher({"search_name": "어딘가 없는 곳", "sources": ["https://example.invalid/c"]})):         # 정식 이름도 지도에 없다
            with self.subTest(search=search), self.assertRaises(GeoNotFound) as got:
                build.resolve_or_search(FakeGeo(), {"name": "한강H2 복합충전소"}, find, search, (), [])
            self.assertIn("'한강H2 복합충전소' 을(를) 지도에서 찾지 못했습니다", str(got.exception))

    def test_지도_서버가_안_되면_이름을_바꿔_가며_묻지_않는다(self):
        geo, search = FakeGeo(down={"안국역 6번출구"}), searcher({"search_name": "안국역", "sources": ["https://example.invalid"]})
        with self.assertRaises(GeoError) as got:
            build.resolve_or_search(geo, {"name": "안국역 6번출구"}, find, search, (), [])
        self.assertNotIsInstance(got.exception, GeoNotFound)
        self.assertEqual((geo.asked, search.calls), (["안국역 6번출구"], []))

    def test_좌표를_직접_준_지점은_그대로(self):
        geo, search = FakeGeo(), searcher()
        hit = build.resolve_or_search(geo, {"name": "6", "lat": 37.5769, "lon": 126.9862}, find, search, (), [])
        self.assertEqual((hit["lat"], hit["display"], geo.asked, search.calls), (37.5769, "직접 지정한 좌표", [], []))


REQ = {"output": {"width": 1920, "height": 1080, "fps": 30}, "layout": "full", "vehicle": "walk",
       "from": {"name": "안국역 6번출구"}, "to": {"name": "인사동 5길"}, "area": {"name": ""}}


class 한_화면_지도와_네비(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_한_화면_지도가_두_지명을_다시_찾아_경로를_받는다(self):
        geo, search = FakeGeo(), searcher()
        with self.assertRaises(Stop):
            build.make_job(copy.deepcopy(REQ), project_dir=self.root, out_root=self.root / "out", log=lambda s: None, geo=geo, searcher=search)
        a, b = geo.routed
        self.assertEqual(((a["name"], a["lat"]), (b["name"], b["lat"])), (("안국역 6번출구", 37.57585), ("인사동 5길", 37.57323)))

    def test_네비_화면도_같은_길로_찾는다(self):
        geo = FakeGeo()
        search = searcher({"search_name": "가양 대교", "sources": ["https://example.invalid/d"]})
        req = {**copy.deepcopy(REQ), "vehicle": "bus", "to": {"name": "가양대교 북단 쪽 길"}}
        with self.assertRaises(Stop):
            navi.make_job(req, project_dir=self.root, out_root=self.root / "out", log=lambda s: None, geo=geo, searcher=search)
        self.assertEqual(geo.routed[1]["lat"], 37.5735)
        self.assertEqual(search.calls, [("가양대교 북단 쪽 길", "안국역 6번출구")])

    def test_앱은_온라인일_때만_검색을_넘긴다(self):
        draft = {"program": "가상 예능", "requester": {"name": "김가상", "role": "PD", "phone": ""}, "map_style": "route_move",
                 "from": {"name": "안국역 6번출구"}, "to": {"name": "인사동5길"}, "duration_seconds": 8, "layout": "full", "vehicle": "walk",
                 "faces": [], "face_mode": "above", "output": {"width": 1920, "height": 1080, "fps": 30}, "art_style": "v5", "rid": "의뢰-9"}
        for offline, expected in ((False, True), (True, False)):
            with self.subTest(offline=offline):
                d = server.Desk(self.root / f"앱{offline}", liner=True, offline=offline)
                d._spawn = lambda fn, *a, **_: fn(*a)
                got = {}
                with mock.patch.object(build, "make_job", lambda req, **kw: got.update(kw) or (_ for _ in ()).throw(RuntimeError("멈춤"))):
                    d.make(copy.deepcopy(draft))
                self.assertEqual(got["searcher"] is not None, expected)


if __name__ == "__main__":
    unittest.main()
