import unittest

from mapcg import mapdata


class ProjectorTest(unittest.TestCase):
    def test_왕복하면_제자리(self):
        to_xy, to_lonlat = mapdata.projector(37.54, 126.92)
        lon, lat = to_lonlat(*to_xy(126.93, 37.55))
        self.assertAlmostEqual(lon, 126.93, places=9)
        self.assertAlmostEqual(lat, 37.55, places=9)

    def test_북쪽은_y가_작다(self):
        to_xy, _ = mapdata.projector(37.54, 126.92)
        self.assertLess(to_xy(126.92, 37.55)[1], 0)
        self.assertGreater(to_xy(126.93, 37.54)[0], 0)
        self.assertAlmostEqual(to_xy(126.92, 37.55)[1], -1105.4, places=1)   # 위도 0.01도 ≈ 1.1km


class RouteTest(unittest.TestCase):
    def test_되돌아오는_가지를_잘라낸다(self):
        pts = [(0, 0), (100, 0), (100, 60), (100, 0), (200, 0)]
        self.assertAlmostEqual(mapdata.length(pts), 320)
        clean = mapdata.clean_route(pts)
        self.assertLess(mapdata.length(clean), 215)
        self.assertEqual(clean[0], (0, 0))
        self.assertEqual(clean[-1], (200, 0))

    def test_출발과_도착이_가까워도_경로를_지우지_않는다(self):
        pts = [(0, 0), (0, 1000), (1000, 1000), (1000, 0), (10, 0)]     # 거의 한 바퀴
        self.assertGreater(mapdata.length(mapdata.clean_route(pts)), 3900)

    def test_출발_직후_꼬인_고리를_잘라낸다(self):
        hook = [(0, 0), (0, -300), (60, -300), (60, 100), (3000, 100)]     # 북쪽으로 갔다가 돌아와 동쪽으로
        clean = mapdata.clean_route(hook)
        cut = mapdata.trim_hook(clean)
        self.assertLess(mapdata.length(cut), mapdata.length(clean) - 600)
        self.assertLess(abs(cut[0][0] - 60) + abs(cut[0][1]), 10)          # 출발점에 가장 가까이 돌아온 곳
        self.assertEqual(cut[-1], (3000, 100))

    def test_곧게_가는_길은_그대로_둔다(self):
        straight = mapdata.clean_route([(0, 0), (500, 20), (3000, 100)])
        self.assertEqual(mapdata.trim_hook(straight), straight)
        self.assertEqual(mapdata.trim_hook(straight[::-1]), straight[::-1])

    def test_곧은_선의_가운데_점을_뺀다(self):
        self.assertEqual(mapdata.simplify([(0, 0), (5, 0.1), (10, 0)], 1), [(0, 0), (10, 0)])
        self.assertEqual(len(mapdata.simplify([(0, 0), (5, 3), (10, 0)], 1)), 3)

    def test_닫힌_고리도_단순화한다(self):
        ring = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        self.assertEqual(len(mapdata.simplify(ring, 0.5)), 5)


class ViewTest(unittest.TestCase):
    def test_비율이_맞고_경로가_다_들어온다(self):
        pts = [(0, 0), (300, 4000)]                         # 거의 남쪽으로 4km
        for aspect in (16 / 9, 32 / 9):
            x0, y0, x1, y1 = mapdata.fit_view(pts, aspect)
            self.assertAlmostEqual((x1 - x0) / (y1 - y0), aspect, places=6)
            self.assertLess(x0, 0)
            self.assertGreater(x1, 300)
            self.assertLess(y0, 0)
            self.assertGreater(y1, 4000)
            self.assertGreater(0 - y0, y1 - 4000)            # 위쪽 여유가 더 크다(얼굴 자리)

    def test_가로로_긴_경로(self):
        x0, y0, x1, y1 = mapdata.fit_view([(0, 0), (8000, 500)], 16 / 9)
        self.assertAlmostEqual((x1 - x0) / (y1 - y0), 16 / 9, places=6)
        self.assertLess(x0, 0)
        self.assertGreater(x1, 8000)

    def test_아주_짧은_경로는_너무_당기지_않는다(self):
        x0, _, x1, _ = mapdata.fit_view([(0, 0), (50, 20)], 16 / 9)
        self.assertGreaterEqual(x1 - x0, 1500)


class ShapeTest(unittest.TestCase):
    def test_끝점이_같은_선을_고리로_잇는다(self):
        a, b, c = (0, 0), (1, 0), (1, 1)
        rings = mapdata.stitch([[a, b], [c, b], [c, a]])     # 가운데 선은 방향이 반대
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0][0], rings[0][-1])
        self.assertEqual(len(rings[0]), 4)

    def test_사각형으로_잘라낸다(self):
        ring = [(-10, -10), (10, -10), (10, 10), (-10, 10)]
        cut = mapdata.clip_ring(ring, (0, 0, 5, 5))
        self.assertEqual(sorted(cut), [(0, 0), (0, 5), (5, 0), (5, 5)])

    def test_밖에_있는_도형은_사라진다(self):
        self.assertEqual(mapdata.clip_ring([(20, 20), (30, 20), (30, 30)], (0, 0, 5, 5)), [])


class BuildTest(unittest.TestCase):
    def setUp(self):
        osrm = {"routes": [{"geometry": {"coordinates": [[126.924, 37.557], [126.924, 37.521]]}}]}
        self.plan = mapdata.plan_view(osrm, 16 / 9)
        self.ppm = 960 / (self.plan["view"][2] - self.plan["view"][0])

    def osm(self):
        def way(tags, pts):
            return {"type": "way", "tags": tags, "geometry": [{"lon": lon, "lat": lat} for lon, lat in pts]}
        river = [(126.90, 37.535), (126.95, 37.535), (126.95, 37.530), (126.90, 37.530), (126.90, 37.535)]
        return {"elements": [
            way({"highway": "primary", "name": "양화로"}, [(126.92, 37.556), (126.93, 37.556)]),
            way({"highway": "primary", "bridge": "yes", "name": "가상로", "bridge:name": "가상대교 (Gasang)"},
                [(126.924, 37.536), (126.924, 37.529)]),
            way({"highway": "primary", "bridge": "yes", "name": "이름없는고가로"}, [(126.93, 37.55), (126.93, 37.549)]),
            way({"highway": "primary", "tunnel": "yes"}, [(126.92, 37.55), (126.93, 37.55)]),
            way({"highway": "footway"}, [(126.92, 37.54), (126.93, 37.54)]),
            way({"highway": "primary"}, [(127.5, 37.9), (127.6, 37.9)]),                 # 화면 밖
            way({"natural": "water", "name": "가상강"}, river),
            {"type": "node", "lon": 126.93, "lat": 37.54, "tags": {"place": "neighbourhood", "name": "가상동3가"}},
            way({"leisure": "park"}, [(126.921, 37.545), (126.923, 37.545), (126.923, 37.543), (126.921, 37.543), (126.921, 37.545)]),
            {"type": "node", "lon": 126.9237, "lat": 37.5572, "tags": {"railway": "station", "name": "가상입구 (2호선)"}},
            {"type": "node", "lon": 126.925, "lat": 37.525, "tags": {"place": "suburb", "name": "가상동"}},
            {"type": "node", "lon": 127.9, "lat": 37.9, "tags": {"place": "suburb", "name": "먼동"}},
        ]}

    def test_종류별로_나눠_담는다(self):
        d = mapdata.build(self.osm(), self.plan, "홍대", "여의도", self.ppm)
        self.assertEqual(len(d["roads"]), 2)          # 터널·화면 밖은 빠진다(보행로는 걸어서 가는 지도에서만 받아 오는 가장 가는 길 — 받아 오면 그린다)
        self.assertEqual(sorted(r["c"] for r in d["roads"]), [1, 5])
        self.assertEqual(len(d["bridges"]), 2)
        self.assertEqual(len(d["water"]), 1)
        self.assertEqual(len(d["green"]), 1)
        names = {l["n"]: l["k"] for l in d["labels"]}      # 다리 이름은 bridge:name 에서, 괄호·'○가'는 뺀다
        self.assertEqual(names, {"가상입구역": "st", "가상동": "pl", "가상대교": "br", "가상강": "wt"})

    def test_강_이름은_물_위에_경로를_피해_둔다(self):
        d = mapdata.build(self.osm(), self.plan, "홍대", "여의도", self.ppm)
        x, y = next(l["p"] for l in d["labels"] if l["n"] == "가상강")
        to_xy = self.plan["to_xy"]
        self.assertTrue(to_xy(0, 37.535)[1] < y < to_xy(0, 37.530)[1])      # 강 안쪽
        self.assertGreater(abs(x - to_xy(126.924, 0)[0]), 500)              # 경로(가운데 세로선)에서 떨어져 있다

    def test_출발과_도착은_경로의_양끝이다(self):
        d = mapdata.build(self.osm(), self.plan, "홍대", "여의도", self.ppm)
        self.assertEqual(d["from"], {"n": "홍대", "p": d["route"][0]})
        self.assertEqual(d["to"], {"n": "여의도", "p": d["route"][-1]})
        self.assertAlmostEqual(d["total"], 3980, delta=10)   # 위도 0.036도 ≈ 3.98km
        x0, y0, x1, y1 = d["view"]
        for x, y in d["route"]:
            self.assertTrue(x0 < x < x1 and y0 < y < y1)


if __name__ == "__main__":
    unittest.main()
