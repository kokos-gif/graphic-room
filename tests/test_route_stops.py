"""한 화면 지도의 들르는 곳 · 정차(11-24): 의뢰서의 '신용산 잠시정차 · 지도표시'를 지도에 점과 이름표로 놓고, 경로가 차례로 지나며 그 자리에서 멈춘다.

11-23 손그림 의뢰서(노들역 → 한강대교 → 신용산(잠시정차 · 지도표시) → 삼각지역(잠시정차 · 지도표시) → 서울역, 10초)에서
한 화면 지도가 출발 · 도착만 그리고 나머지를 '반영하지 못한 요청'으로 뺐다. 지도 서버 · Liner 는 부르지 않는다.
"""
import copy
import unittest

from mapcg import build, cuts, intake, request
from mapcg.geo import GeoError, GeoNotFound


def stop(name, **over):
    return {"name": name, "caption": "", "pass": False, "stop": False, "hold_seconds": None, **over}


class 읽기(unittest.TestCase):
    def test_잠시정차는_기본_1초로_넣고_알린다(self):
        notes = []
        got = intake.route_stops([stop("한강대교", **{"pass": True}), stop("신용산", stop=True), stop("삼각지역", stop=True, hold_seconds=2)], 10, notes)
        self.assertEqual(got, [{"name": "한강대교", "caption": "", "pass": True},
                               {"name": "신용산", "caption": "", "pass": False, "hold": 1.0},
                               {"name": "삼각지역", "caption": "", "pass": False, "hold": 2.0}])
        self.assertIn("지나는 곳 3곳을 지도에 표시", notes[0])
        self.assertIn("신용산 · 1초 정차", notes[0])
        self.assertIn("신용산의 정차 시간이 적혀 있지 않아 1초로", notes[1])

    def test_지나가기만_하는_곳은_멈추지_않고_정차가_너무_길면_줄인다(self):
        notes = []
        got = intake.route_stops([stop("가", **{"pass": True}, stop=True, hold_seconds=3), stop("나", stop=True, hold_seconds=8)], 10, notes)
        self.assertNotIn("hold", got[0])
        self.assertEqual(got[1]["hold"], 5.0)           # 영상(10초)의 절반까지
        self.assertTrue(any("모두 5초로 줄였습니다" in n for n in notes))

    def test_이름_없는_칸과_이상한_답은_버린다(self):
        self.assertEqual(intake.route_stops([{"name": " "}, "신용산", None], 10, []), [])
        self.assertEqual(intake.route_stops(None, 10, []), [])

    def test_정차_지도표시는_못_그리는_요청으로_빠지지_않는다(self):
        self.assertIn("잠시 멈추는 정차", intake.KIND_ITEMS)
        self.assertNotIn("via_names", intake.SCHEMA["properties"])


class 의뢰_봉투(unittest.TestCase):
    BASE = {"from": {"name": "노들역"}, "to": {"name": "서울역"}, "duration_seconds": 10, "vehicle": "suv"}

    def test_들르는_곳과_정차가_봉투에_남는다(self):
        req = request.normalize({**self.BASE, "stops": [{"name": "신용산", "hold": 1}, {"name": "한강대교", "pass": True, "hold": 2},
                                                        {"name": "삼각지역", "caption": "환승", "lat": 37.5347, "lon": 126.9731}]})
        self.assertEqual(req["stops"], [{"name": "신용산", "caption": "", "pass": False, "hold": 1.0},
                                        {"name": "한강대교", "caption": "", "pass": True},        # 지나가기만 하는 곳은 멈추지 않는다
                                        {"name": "삼각지역", "caption": "환승", "pass": False, "lat": 37.5347, "lon": 126.9731}])

    def test_들르는_곳이_없으면_봉투에_칸을_만들지_않는다(self):
        self.assertNotIn("stops", request.normalize({**self.BASE, "stops": []}))

    def test_고칠_곳을_알린다(self):
        for stops, said in (([{"name": ""}], "1번의 이름"), ([{"name": "신용산", "hold": 30}], "0~10 초"),
                            ([{"name": "신용산", "hold": 5}, {"name": "삼각지역", "hold": 4}], "움직일 시간이 2초도"),
                            ([{"name": "신용산", "lat": "북쪽", "lon": 1}], "숫자가 아닙니다"), ("신용산", "목록이어야")):
            with self.subTest(stops=stops), self.assertRaises(request.RequestError) as got:
                request.normalize({**self.BASE, "stops": stops})
            self.assertTrue(any(said in p for p in got.exception.problems), got.exception.problems)

    def test_컷_나눔은_한_화면의_들르는_곳을_쓰지_않는다(self):
        spec = cuts.normalize({**self.BASE, "stops": [{"name": "신용산", "hold": 1}],
                               "cuts": [{"from": "노들역", "to": "신용산", "duration_seconds": 5}, {"from": "신용산", "to": "서울역", "duration_seconds": 5}]})
        self.assertNotIn("stops", spec["base"])


# 노들역(남) → 서울역(북) 사이에 신용산 · 삼각지역. 경도는 같게 두고 위도만 다르게 — 길이를 셈하기 쉽게
SPOTS = {"노들역": (37.5129, 126.9700), "신용산": (37.5292, 126.9700), "삼각지역": (37.5347, 126.9700), "서울역": (37.5529, 126.9700)}


class FakeGeo:
    def __init__(self, down=()):
        self.asked, self.via, self.down = [], None, set(down)

    def geocode_loose(self, name, country="kr", near=None, km=30):
        self.asked.append((name, near is not None))
        if name in self.down:
            raise GeoError("지명 서버가 응답하지 않습니다")
        if name not in SPOTS:
            raise GeoNotFound(name)
        lat, lon = SPOTS[name]
        return {"name": name, "lat": lat, "lon": lon, "display": name, "candidates": []}

    def route_via(self, points, foot=False):
        self.via = [p["name"] for p in points]
        return osrm(points)


def osrm(points):
    """지점들을 곧게 잇는 경로(구간마다 길이 legs)."""
    coords = [[p["lon"], p["lat"]] for p in points]
    legs = [{"distance": abs(q[1] - p[1]) * 110540} for p, q in zip(coords, coords[1:])]
    return {"code": "Ok", "routes": [{"distance": sum(x["distance"] for x in legs), "duration": 600, "geometry": {"coordinates": coords}, "legs": legs}]}


class 찾기와_자리(unittest.TestCase):
    REQ = {"from": {"name": "노들역"}, "to": {"name": "서울역"},
           "stops": [{"name": "신용산", "caption": "", "pass": False, "hold": 1.0}, {"name": "삼각지역", "caption": "", "pass": False, "hold": 1.0}]}

    def ends(self, geo):
        return geo.geocode_loose("노들역"), geo.geocode_loose("서울역")

    def test_들르는_곳을_차례로_지나는_경로와_자리(self):
        geo = FakeGeo()
        a, b = self.ends(geo)
        items, notes = build.find_stops(geo, self.REQ, a, b)
        via = build.stop_waypoints(items, a, b)
        self.assertEqual([v["name"] for v in via], ["신용산", "삼각지역"])
        self.assertTrue(all(near for name, near in geo.asked if name in ("신용산", "삼각지역")))     # 출발 · 도착 둘레에서만 찾는다
        marks = build.stop_marks(items, osrm([a, *via, b]), notes)
        self.assertEqual([(m["name"], m["hold"]) for m in marks], [("신용산", 1.0), ("삼각지역", 1.0)])
        self.assertAlmostEqual(marks[0]["at"], (37.5292 - 37.5129) / (37.5529 - 37.5129), places=2)
        self.assertAlmostEqual(marks[1]["at"], (37.5347 - 37.5129) / (37.5529 - 37.5129), places=2)
        self.assertEqual(notes, [])

    def test_못_찾은_곳은_표시하지_않고_알린다(self):
        geo = FakeGeo(down={"삼각지역"})
        a, b = self.ends(geo)
        req = copy.deepcopy(self.REQ)
        req["stops"].insert(0, {"name": "어딘가 없는 곳", "caption": "", "pass": False, "hold": 2.0})
        items, notes = build.find_stops(geo, req, a, b)
        self.assertEqual([("place" in it) for it in items], [False, True, False])
        self.assertIn("'어딘가 없는 곳' 을(를) 이 지역의 지도에서 찾지 못해 표시하지 못했습니다 — 그곳의 정차도 빠집니다", notes[0])
        self.assertIn("'삼각지역' 을(를) 지금 찾아볼 수 없어", notes[1])
        marks = build.stop_marks(items, osrm([a, *build.stop_waypoints(items, a, b), b]), notes)
        self.assertEqual([m["name"] for m in marks], ["신용산"])

    def test_지도에서_찍은_지점은_찾지_않고_출발지_곁은_경유지로_넣지_않는다(self):
        geo = FakeGeo()
        a, b = self.ends(geo)
        req = {**self.REQ, "stops": [{"name": "노들역 앞", "caption": "", "pass": False, "lat": 37.51292, "lon": 126.97001},
                                     {"name": "신용산", "caption": "", "pass": False, "lat": 37.5292, "lon": 126.9700, "hold": 1.0}]}
        items, _ = build.find_stops(geo, req, a, b)
        self.assertEqual(geo.asked, [("노들역", False), ("서울역", False)])
        via = build.stop_waypoints(items, a, b)
        self.assertEqual([v["name"] for v in via], ["신용산"])
        marks = build.stop_marks(items, osrm([a, *via, b]), [])
        self.assertLess(marks[0]["at"], 0.01)          # 출발지 곁 — 경로 위의 가장 가까운 자리

    def test_한_화면_지도가_들르는_곳으로_경로를_받는다(self):
        class Stop(Exception):
            pass

        geo = FakeGeo()
        geo.route_via = lambda points, foot=False: (setattr(geo, "via", [p["name"] for p in points]), (_ for _ in ()).throw(Stop()))[1]
        req = {"output": {"width": 1920, "height": 1080, "fps": 30}, "layout": "full", "vehicle": "suv", **copy.deepcopy(self.REQ)}
        with self.assertRaises(Stop):
            build.make_job(req, project_dir=None, out_root=None, log=lambda s: None, geo=geo)
        self.assertEqual(geo.via, ["노들역", "신용산", "삼각지역", "서울역"])


class 리뷰에서_고친_것(unittest.TestCase):
    """11-24 코드리뷰 5건: 자리는 그려지는 선 기준 · 동떨어진 곳은 뺀다 · 7번째부터 알린다 · 정차 한도는 만들기 검사와 같게 · 수정 요청이 들르는 곳을 고친다."""
    REQ = 찾기와_자리.REQ

    def test_자리는_꼬인_고리를_잘라_낸_뒤의_선에서_잰다(self):
        from mapcg import mapdata
        lon = 126.97        # 출발 직후 300m 를 갔다 되돌아오는 고리(잘린다) → 곧장 북으로 4.4km, 그 한가운데에 들르는 곳
        coords = [[lon, 37.5000], [lon + 0.0001, 37.5027], [lon, 37.5001], [lon, 37.5200], [lon, 37.5400]]
        raw = {"code": "Ok", "routes": [{"geometry": {"coordinates": coords}, "legs": [{"distance": 2800.0}, {"distance": 2211.0}]}]}
        plan = mapdata.plan_view(raw, 16 / 9)
        self.assertLess(plan["total"], 4500)                                # 고리(약 600m)가 잘렸다
        items = [{"name": "가운데", "caption": "", "pass": False, "hold": 1.0, "wp": 0, "place": {"lat": 37.5200, "lon": lon}}]
        self.assertAlmostEqual(build.stop_marks(items, raw, [], plan)[0]["at"], 0.5, delta=0.01)     # 그려지는 선의 한가운데
        self.assertGreater(build.stop_marks(items, raw, [])[0]["at"], 0.54)                          # 원본 길이로 내면 뒤로 밀린다(예전 값)

    def test_동떨어진_곳은_경유지로_넣지_않고_알린다_찍은_지점은_둔다(self):
        geo = FakeGeo()
        a, b = geo.geocode_loose("노들역"), geo.geocode_loose("서울역")
        # 동쪽으로 약 7km — 들르는 곳을 찾는 반경(8km) 안에서 실제로 잡힐 수 있는 거리다. 컷 나눔의 한도(30km)로는 걸리지 않았다(11-24 정밀 리뷰)
        far = {"lat": 37.5300, "lon": 127.0500, "display": "다른 동의 같은 이름"}
        items = [{"name": "주민센터", "caption": "", "pass": False, "hold": 1.0, "place": dict(far)},
                 {"name": "신용산", "caption": "", "pass": False, "place": {"lat": 37.5292, "lon": 126.9700}},
                 {"name": "시장", "caption": "", "pass": False, "place": {"lat": 37.5300, "lon": 126.9870}},      # 길에서 1.5km 벗어난 곳 — 들를 만한 거리
                 {"name": "촬영지", "caption": "", "pass": False, "lat": far["lat"], "lon": far["lon"], "place": dict(far)}]
        notes = []
        via = build.stop_waypoints(items, a, b, notes)
        self.assertEqual([v["name"] for v in via], ["신용산", "시장", "촬영지"])        # 사람이 지도에서 찍은 곳은 멀어도 그대로
        self.assertNotIn("place", items[0])
        self.assertEqual(len(notes), 1)
        self.assertIn("'주민센터' 이(가) 출발 · 도착에서 동떨어진 곳으로 잡혀", notes[0])
        self.assertIn("그곳의 정차도 빠집니다", notes[0])

    def test_차례가_어긋난_곳은_앞_곳에_겹치지_않고_제자리에_찍힌다(self):
        from mapcg import mapdata
        lon = 126.97
        raw = {"code": "Ok", "routes": [{"geometry": {"coordinates": [[lon, 37.50], [lon, 37.52], [lon, 37.54]]}, "legs": [{"distance": 2211.0}, {"distance": 2211.0}]}]}
        plan = mapdata.plan_view(raw, 16 / 9)
        items = [{"name": "가운데", "caption": "", "pass": False, "wp": 0, "place": {"lat": 37.52, "lon": lon}},
                 {"name": "출발지 곁", "caption": "", "pass": False, "place": {"lat": 37.5003, "lon": lon}},      # 뒤에 적혔지만 자리는 맨 앞
                 {"name": "도착지 곁", "caption": "", "pass": False, "place": {"lat": 37.5397, "lon": lon}}]
        notes = []
        at = {m["name"]: m["at"] for m in build.stop_marks(items, raw, notes, plan)}
        self.assertAlmostEqual(at["가운데"], 0.5, delta=0.01)
        self.assertLess(at["출발지 곁"], 0.02)                      # 예전에는 앞 곳(0.5) 위에 겹쳐 찍혔다
        self.assertGreater(at["도착지 곁"], 0.98)                   # 어긋난 곳 다음의 곳은 다시 차례대로
        self.assertEqual(sum("차례와 맞지 않는" in n for n in notes), 1)

    def test_일곱째부터는_뺐다고_알린다(self):
        notes = []
        got = intake.route_stops([stop(f"곳{n}") for n in range(1, 9)], 20, notes)
        self.assertEqual(len(got), 6)
        self.assertTrue(any("6곳까지" in n and "곳7, 곳8" in n for n in notes), notes)

    def test_읽은_정차는_만들기_검사를_통과한다(self):
        for total, stops in ((3, [stop("가", stop=True), stop("나", stop=True)]), (2.5, [stop("가", stop=True)]), (2, [stop("가", stop=True, hold_seconds=1)]),
                             (10, [stop("가", stop=True, hold_seconds=9)]),
                             # 반올림하면 합이 한도를 넘던 것들(0.17 × 6 = 1.02 · 0.5 + 0.17 × 3 = 1.01 · 0.57 × 3 = 1.71) — 내림으로 맞춘다
                             (3, [stop(f"곳{n}", stop=True) for n in range(6)]),
                             (3, [stop("가", stop=True, hold_seconds=3), stop("나", stop=True), stop("다", stop=True), stop("라", stop=True)]),
                             (3.7, [stop("가", stop=True), stop("나", stop=True), stop("다", stop=True)])):
            with self.subTest(total=total, n=len(stops)):
                notes = []
                got = intake.route_stops(stops, total, notes)
                request.normalize({**의뢰_봉투.BASE, "duration_seconds": total, "stops": got})      # 거절되면 RequestError 가 난다
                self.assertLessEqual(sum(s.get("hold", 0) for s in got), max(0, total - request.MOVE_MIN) + 1e-9)
        self.assertNotIn("hold", intake.route_stops([stop("가", stop=True)], 2, notes := [])[0])     # 2초 영상에는 멈출 시간이 없다 — 정차를 뺀다
        self.assertTrue(any("정차를 뺐습니다" in n for n in notes))

    def test_수정_요청으로_정차를_고치고_찍은_지점은_지킨다(self):
        from mapcg import revise
        req = {"from": {"name": "노들역"}, "to": {"name": "서울역"}, "duration_seconds": 10, "vehicle": "suv", "layout": "full",
               "output": {"width": 1920, "height": 1080, "fps": 30},
               "stops": [{"name": "신용산", "caption": "", "pass": False, "hold": 1.0, "lat": 37.5292, "lon": 126.97},
                         {"name": "삼각지역", "caption": "", "pass": False, "hold": 1.0}]}
        self.assertEqual(revise.current_map(req)["route_stops"][0], {"name": "신용산", "label": "", "caption": "", "pass": False, "stop": True, "hold_seconds": 1.0})
        self.assertIn("route_stops", revise.MAP_SCHEMA["required"])
        d, changed, _ = revise.apply_map(req, {"route_stops_changed": True, "route_stops": [stop("신용산", stop=True, hold_seconds=3)]})
        self.assertEqual(d["stops"], [{"lat": 37.5292, "lon": 126.97, "name": "신용산", "caption": "", "pass": False, "hold": 3.0}])
        self.assertEqual((changed[0]["label"], changed[0]["before"], changed[0]["after"]),
                         ("지나는 곳", "신용산 · 1초 정차 → 삼각지역 · 1초 정차", "신용산 · 3초 정차"))
        d, changed, _ = revise.apply_map(req, {"route_stops_changed": True, "route_stops": []})          # 다 빼기
        self.assertNotIn("stops", d)
        self.assertIn("없음", changed[0]["after"])
        d, changed, _ = revise.apply_map(req, {"route_stops_changed": False, "route_stops": []})         # 안 바뀌면 그대로
        self.assertEqual((d["stops"], changed), (req["stops"], []))

    def test_수정으로_길이만_줄이면_정차를_새_길이에_맞춘다(self):
        from mapcg import revise
        req = {"from": {"name": "노들역"}, "to": {"name": "서울역"}, "duration_seconds": 10, "vehicle": "suv", "layout": "full",
               "output": {"width": 1920, "height": 1080, "fps": 30},
               "stops": [{"name": "신용산", "caption": "", "pass": False, "hold": 2.0, "lat": 37.5292, "lon": 126.97},
                         {"name": "삼각지역", "caption": "", "pass": False, "hold": 1.5}]}
        d, changed, notes = revise.apply_map(req, {"duration_seconds": 4, "route_stops_changed": False, "route_stops": []})
        self.assertEqual(d["duration_seconds"], 4)
        self.assertEqual([s["hold"] for s in d["stops"]], [1.14, 0.85])                 # 합 1.99초 — 4초에서 움직일 2초가 남는다
        self.assertEqual((d["stops"][0]["lat"], d["stops"][0]["lon"]), (37.5292, 126.97))       # 찍어 둔 지점은 그대로
        request.normalize({k: v for k, v in d.items() if k != "face_count"})            # 거절되면 RequestError 가 난다
        self.assertIn(("지나는 곳", "신용산 · 2초 정차 → 삼각지역 · 1.5초 정차"), [(c["label"], c["before"]) for c in changed])
        self.assertTrue(any("움직일 시간이 남게" in n for n in notes))
        self.assertEqual(req["stops"][0]["hold"], 2.0)                                   # 받은 의뢰는 건드리지 않는다
        d, changed, notes = revise.apply_map(req, {"duration_seconds": 12, "route_stops_changed": False, "route_stops": []})     # 늘리면 그대로
        self.assertEqual(([s["hold"] for s in d["stops"]], notes), ([2.0, 1.5], []))

    def test_수정으로_컷을_나누면_한_화면의_들르는_곳은_뺐다고_알린다(self):
        from mapcg import revise
        req = {"from": {"name": "노들역"}, "to": {"name": "서울역"}, "duration_seconds": 10, "vehicle": "suv", "layout": "full",
               "output": {"width": 1920, "height": 1080, "fps": 30}, "stops": [{"name": "신용산", "caption": "", "pass": False, "hold": 1.0}]}
        cut = {"who": None, "from_name": "노들역", "to_name": "신용산", "duration_seconds": 5, "bridge": False, "closeup": False, "stops": [], "note": ""}
        d, _, notes = revise.apply_map(req, {"cuts_changed": True, "cuts": [cut, {**cut, "from_name": "신용산", "to_name": "서울역"}]})
        self.assertEqual(len(d["cuts"]), 2)
        self.assertNotIn("stops", d)
        self.assertTrue(any("신용산 · 1초 정차" in n and "쓰지 않습니다" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
