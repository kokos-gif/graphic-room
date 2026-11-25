"""내려받아 둔 지역 지도 자료(mapcg/osmlocal.py)와 geo.Geo 의 연결 — 공개 지도 서버를 부르지 않고 그린다."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from mapcg import geo, osmlocal  # noqa: E402
import osm_local  # noqa: E402

BOX = (37.50, 126.90, 37.56, 126.98)        # 이 자료가 덮는 범위(남, 서, 북, 동)


def way(i, tags, pts):
    return {"type": "way", "id": i, "tags": tags, "geometry": [{"lat": a, "lon": o} for a, o in pts]}


def make(folder: Path) -> Path:
    wr = osmlocal.Writer(folder / "시험.sqlite", BOX, "시험.osm.pbf", "2026-11-22")
    wr.add("base", 0, way(1, {"highway": "primary", "name": "큰길"}, [(37.52, 126.92), (37.53, 126.93)]), (37.52, 126.92, 37.53, 126.93))
    wr.add("base", 1, way(2, {"highway": "residential"}, [(37.52, 126.92), (37.521, 126.921)]), (37.52, 126.92, 37.521, 126.921))
    wr.add("base", 2, way(3, {"highway": "footway"}, [(37.52, 126.92), (37.5205, 126.9205)]), (37.52, 126.92, 37.5205, 126.9205))
    wr.add("base", 0, way(4, {"leisure": "park", "name": "먼 공원"}, [(37.55, 126.97), (37.555, 126.975)]), (37.55, 126.97, 37.555, 126.975))
    wr.add("building", 0, way(5, {"building": "yes"}, [(37.52, 126.92), (37.5201, 126.9201)]), (37.52, 126.92, 37.5201, 126.9201))
    wr.add("feature", 0, {"name": "", "tags": {"leisure": "playground"}, "lat": 37.5202, "lon": 126.9202}, (37.5202, 126.9202, 37.5202, 126.9202))
    return wr.close()


class LocalStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.loc = osmlocal.Local(make(Path(self.tmp.name)))

    def tearDown(self):
        self.loc.db.close()
        self.tmp.cleanup()

    def test_자세함까지만_도로가_나온다(self):
        near = (37.519, 126.919, 37.522, 126.922)
        ids = lambda d: sorted(x["id"] for x in self.loc.osm(near, d)["elements"])
        self.assertEqual(ids("major"), [1])
        self.assertEqual(ids("minor"), [1, 2])
        self.assertEqual(ids("foot"), [1, 2, 3])

    def test_범위_밖은_나오지_않는다(self):
        self.assertEqual([x["id"] for x in self.loc.osm((37.549, 126.969, 37.556, 126.976), "foot")["elements"]], [4])
        self.assertEqual(self.loc.buildings((37.54, 126.95, 37.55, 126.96))["elements"], [])

    def test_건물과_시설(self):
        near = (37.519, 126.919, 37.522, 126.922)
        self.assertEqual([x["id"] for x in self.loc.buildings(near)["elements"]], [5])
        self.assertEqual(self.loc.features(near)[0]["tags"], {"leisure": "playground"})

    def test_빈칸_샵_한글이_든_경로(self):
        odd = Path(self.tmp.name) / "지도 자료 #1 100%"
        odd.mkdir()
        loc = osmlocal.Local(make(odd))
        try:
            self.assertEqual(loc.bbox, BOX)
        finally:
            loc.db.close()

    def test_덮는_범위(self):
        self.assertTrue(self.loc.covers((37.51, 126.91, 37.52, 126.92)))
        self.assertFalse(self.loc.covers((37.49, 126.91, 37.52, 126.92)))


class GeoUsesLocal(unittest.TestCase):
    """지역 자료가 덮으면 지도 도형 · 건물 · 시설을 서버에 묻지 않는다(서버를 부르면 시험이 실패한다)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "osm").mkdir()
        make(base / "osm")
        self.geo = geo.Geo(base / "geo")
        self.no_net = mock.patch.object(geo.Geo, "_http", side_effect=AssertionError("서버를 불렀습니다"))
        self.no_net.start()
        self.env = mock.patch.dict(os.environ, {"MAPCG_OSM_DB": ""})
        self.env.start()
        osmlocal._opened.clear()

    def tearDown(self):
        self.no_net.stop()
        self.env.stop()
        for loc in osmlocal._opened.values():
            if loc:
                loc.db.close()
        osmlocal._opened.clear()
        self.tmp.cleanup()

    def test_지도_도형은_지역_자료에서(self):
        got = self.geo.osm((37.519, 126.919, 37.522, 126.922), "foot")
        self.assertEqual(got["_source"], "local")
        self.assertEqual(sorted(x["id"] for x in got["elements"]), [1, 2, 3])

    def test_넓은_범위의_골목길은_큰길만(self):
        got = self.geo.osm((37.50, 126.90, 37.56, 126.98), "foot")       # 0.0048도² — 걷는 길 한도(0.004)를 넘는다
        self.assertEqual(got["_source"], "local_major")
        self.assertIn("큰길만", got["_note"])
        self.assertTrue(all(x["tags"].get("highway") in (None, "primary") for x in got["elements"]))

    def test_건물과_둘레_시설도_지역_자료에서(self):
        self.assertEqual([x["id"] for x in self.geo.buildings((37.519, 126.919, 37.522, 126.922))["elements"]], [5])
        got = self.geo.features(37.5202, 126.9202, radius_m=200)
        self.assertEqual(got[0]["tags"], {"leisure": "playground"})

    def test_덮지_못하면_예전처럼_서버로(self):
        with self.assertRaises(AssertionError):     # 범위 밖 — 서버를 부르려 한다(여기서는 막아 둔 것이 터진다)
            self.geo.buildings((37.60, 127.00, 37.601, 127.001))

    def test_끄면_쓰지_않는다(self):
        with mock.patch.dict(os.environ, {"MAPCG_OSM_DB": "0"}):
            g = geo.Geo(Path(self.tmp.name) / "geo")
            self.assertIsNone(g._local((37.519, 126.919, 37.522, 126.922)))
        g = geo.Geo(Path(self.tmp.name) / "geo")
        g.use_local = False
        self.assertIsNone(g._local((37.519, 126.919, 37.522, 126.922)))

    def test_다시_만들면_새로_연다(self):
        folder = Path(self.tmp.name) / "osm"
        first = osmlocal.find(Path(self.tmp.name) / "geo")
        broken = folder / "나중.sqlite"
        broken.write_bytes(b"not sqlite")
        self.assertEqual(len(osmlocal.find(Path(self.tmp.name) / "geo")), len(first))        # 깨진 것은 건너뛴다
        wr = osmlocal.Writer(folder / "나중.sqlite.새것", BOX, "x", "2026-11-22")
        wr.close()
        import os as _os
        _os.replace(folder / "나중.sqlite.새것", broken)        # 같은 이름으로 다시 만들었다
        self.assertEqual(len(osmlocal.find(Path(self.tmp.name) / "geo")), len(first) + 1)

    def test_깨진_파일은_건너뛴다(self):
        (Path(self.tmp.name) / "osm" / "깨짐.sqlite").write_bytes(b"not sqlite")
        osmlocal._opened.clear()
        got = osmlocal.find(Path(self.tmp.name) / "geo")
        self.assertEqual([p.path.name for p in got], ["시험.sqlite"])


class BuilderRules(unittest.TestCase):
    """만들기 도구의 판별 규칙이 geo.py 의 Overpass 질의와 같다."""

    def test_도로_등급(self):
        self.assertEqual(osm_local.base_rank({"highway": "primary"}, "w"), 0)
        self.assertEqual(osm_local.base_rank({"highway": "residential"}, "w"), 1)
        self.assertEqual(osm_local.base_rank({"highway": "footway"}, "w"), 2)
        self.assertIsNone(osm_local.base_rank({"highway": "bus_stop"}, "w"))

    def test_도로가_아닌_바탕(self):
        self.assertEqual(osm_local.base_rank({"railway": "rail"}, "w"), 0)
        self.assertIsNone(osm_local.base_rank({"railway": "rail", "tunnel": "yes"}, "w"))
        self.assertEqual(osm_local.base_rank({"natural": "water"}, "r"), 0)
        self.assertEqual(osm_local.base_rank({"place": "quarter", "name": "여의동"}, "n"), 0)
        self.assertIsNone(osm_local.base_rank({"amenity": "cafe"}, "n"))

    def test_시설(self):
        self.assertTrue(osm_local.is_feature({"name": "문화의마당"}))
        self.assertFalse(osm_local.is_feature({"name": "여의대로", "highway": "primary"}))
        self.assertTrue(osm_local.is_feature({"name": "여의도광장", "highway": "pedestrian"}))
        self.assertFalse(osm_local.is_feature({"name": "가게", "shop": "convenience"}))
        self.assertTrue(osm_local.is_feature({"man_made": "flagpole"}))
        self.assertTrue(osm_local.is_feature({"entrance": "main"}))

    def test_태그_줄이기(self):
        got = osmlocal.slim_tags({"name": "여의도공원", "name:ko": "여의도공원", "name:ja": "汝矣島公園", "source": "x", "leisure": "park"})
        self.assertEqual(got, {"name": "여의도공원", "name:ko": "여의도공원", "leisure": "park"})


TINY = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="시험">
 <node id="1" lat="37.520" lon="126.920" version="1"/>
 <node id="2" lat="37.530" lon="126.930" version="1"/>
 <node id="3" lat="37.521" lon="126.921" version="1"><tag k="leisure" v="playground"/></node>
 <node id="4" lat="37.525" lon="126.925" version="1"><tag k="place" v="quarter"/><tag k="name" v="여의동"/><tag k="name:ja" v="汝矣洞"/></node>
 <node id="5" lat="37.522" lon="126.922" version="1"/>
 <node id="6" lat="37.523" lon="126.923" version="1"/>
 <node id="7" lat="37.900" lon="127.900" version="1"/>
 <node id="8" lat="37.901" lon="127.901" version="1"/>
 <way id="10" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/><tag k="name" v="여의대로"/></way>
 <way id="11" version="1"><nd ref="1"/><nd ref="5"/><nd ref="6"/><nd ref="1"/><tag k="building" v="yes"/></way>
 <way id="12" version="1"><nd ref="5"/><nd ref="6"/><nd ref="2"/><nd ref="5"/></way>
 <way id="13" version="1"><nd ref="7"/><nd ref="8"/><tag k="highway" v="primary"/></way>
 <relation id="20" version="1"><member type="way" ref="12" role="outer"/><tag k="natural" v="water"/><tag k="type" v="multipolygon"/></relation>
</osm>
"""


@unittest.skipUnless(__import__("importlib").util.find_spec("osmium"), "pyosmium 이 없다(만들기 도구만 쓴다)")
class BuilderEndToEnd(unittest.TestCase):
    """작은 OSM 파일로 만들기 도구 전체 — 범위 밖 길은 빠지고, 물 관계는 구성 길의 좌표까지 담긴다."""

    def test_작은_파일(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "tiny.osm"       # libosmium 은 한글 경로를 못 연다(도구는 짧은 경로로 바꿔 열지만 시험 폴더는 8.3 이름이 없을 수 있다)
            src.write_text(TINY, encoding="utf-8")
            with mock.patch.dict(osm_local.packs.AREAS, {"시험": {"label": "시험", "bbox": (37.50, 126.90, 37.56, 126.98), "detail": "major"}}):
                path = osm_local.build(src, "시험", 0.0, Path(d) / "시험.sqlite", log=lambda *_: None)
            loc = osmlocal.Local(path)
            try:
                els = loc.osm((37.50, 126.90, 37.56, 126.98), "foot")["elements"]
                kinds = sorted((x["type"], x["id"]) for x in els)
                self.assertEqual(kinds, [("node", 4), ("relation", 20), ("way", 10)])        # 범위 밖 길(13)은 없다
                rel = next(x for x in els if x["type"] == "relation")
                self.assertEqual(len(rel["members"][0]["geometry"]), 4)
                node = next(x for x in els if x["type"] == "node")
                self.assertNotIn("name:ja", node["tags"])
                self.assertEqual([x["id"] for x in loc.buildings((37.50, 126.90, 37.56, 126.98))["elements"]], [11])
                # Overpass 의 시설 질의와 같다: 종류로 찾는 놀이터 · 이름 붙은 동네 · 물(관계도) — 시설은 태그를 줄이지 않는다
                feats = sorted(loc.features((37.50, 126.90, 37.56, 126.98)), key=lambda f: sorted(f["tags"]))
                self.assertEqual([f["tags"] for f in feats], [{"leisure": "playground"}, {"name": "여의동", "name:ja": "汝矣洞", "place": "quarter"},
                                                              {"natural": "water", "type": "multipolygon"}])
                self.assertIn("bounds", feats[2])
            finally:
                loc.db.close()


if __name__ == "__main__":
    unittest.main()
