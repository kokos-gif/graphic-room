"""네비 화면(map_style 'navi_screen') — 휴대폰 길안내 화면 모양. 경로 다듬기 · 출발 고르기 · 지도 도형 바꾸기 · 작업 만들기 · 앱 연결(지도 서버를 부르지 않는다)."""
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import intake, navi, request, server
from mapcg.geo import GeoError

LAT0, LON0 = 37.52, 126.93
K = 111195 * 0.7931       # 위도 37.52 에서 경도 1도의 m(대략)


def pt(east_m, north_m):
    return [round(LON0 + east_m / K, 7), round(LAT0 + north_m / 111195, 7)]


def osrm(points, steps, distance=None, duration=300):
    """steps: [(거리 m, type, modifier, name)]"""
    coords = [pt(*p) for p in points]
    total = distance or sum(s[0] for s in steps)
    return {"code": "Ok", "routes": [{"distance": total, "duration": duration, "geometry": {"coordinates": coords},
                                      "legs": [{"steps": [{"distance": d, "duration": 0, "name": n, "maneuver": {"type": t, "modifier": m}}
                                                          for d, t, m, n in steps]}]}]}


# 동쪽으로 2km 가는 길. 앞 30m 는 북쪽으로 갔다 꺾여 들고(갈고리), 끝 40m 는 U턴
HOOKED = osrm([(0, 0), (0, 30), (30, 30), (1000, 30), (2000, 30), (2000, 50), (1980, 50)],
              [(30, "depart", "left", "여의대로"), (1970, "turn", "right", "노량진로"), (40, "continue", "uturn", "노량진로"), (0, "arrive", "", "노량진로")])


class Tidy(unittest.TestCase):
    def test_start_hook_and_last_short_uturn_are_cut(self):
        coords, steps, dist, secs, head, notes = navi.tidy(HOOKED)
        self.assertGreater(head, 25)
        self.assertLess(coords[0][1], pt(0, 31)[1] + 1e-6)          # 북쪽 갈고리 끝(꺾이는 곳)에서 출발
        self.assertAlmostEqual(coords[-1][0], pt(2000, 30)[0], places=4)     # U턴 앞에서 끝난다
        self.assertEqual([s["type"] for s in steps], ["depart", "arrive"])
        self.assertEqual(steps[0]["name"], "노량진로")
        self.assertLess(dist, 2040 - 60)
        self.assertEqual(len(notes), 2)

    def test_clean_route_is_untouched(self):
        clean = osrm([(0, 0), (1000, 0), (2000, 0)], [(2000, "depart", "", "강변로"), (0, "arrive", "", "강변로")])
        coords, steps, dist, _, head, notes = navi.tidy(clean)
        self.assertEqual((head, notes, dist, len(coords)), (0.0, [], 2000, 3))

    def test_cost_prefers_no_uturn_and_no_tunnel_start(self):
        plain = osrm([(0, 0), (3000, 0)], [(3000, "depart", "", "여의대로"), (0, "arrive", "", "")])
        uturn = osrm([(0, 0), (2600, 0)], [(2000, "depart", "", "여의대로"), (600, "continue", "uturn", "여의대방로"), (0, "arrive", "", "")])
        tunnel = osrm([(0, 0), (2500, 0)], [(2500, "depart", "", "여의도지하차도"), (0, "arrive", "", "")])
        self.assertLess(navi.route_cost(plain), navi.route_cost(uturn))
        self.assertLess(navi.route_cost(plain), navi.route_cost(tunnel))


class FakeGeo:
    """출발 지점의 위도에 따라 다른 경로를 준다: 정확히 그 자리면 U턴 경로, 남쪽 150m 면 곧은 경로."""

    def __init__(self):
        self.asked = []

    def geocode_loose(self, name):
        spots = {"여의도공원": (LAT0, LON0), "노량진역": (LAT0 - 0.01, LON0 + 0.02)}
        if name not in spots:
            raise GeoError(name)
        return {"name": name, "lat": spots[name][0], "lon": spots[name][1], "candidates": [],
                "display": f"{name}, 노량진로, 노량진1동, 동작구, 서울특별시, 06907, 대한민국"}

    def route(self, a, b, foot=False, steps=False):
        self.asked.append((round(a["lat"], 5), round(a["lon"], 5)))
        straight = abs(a["lat"] - (LAT0 - 150 / 111195)) < 1e-6 and abs(a["lon"] - LON0) < 1e-6
        if straight:
            return osrm([(0, -150), (1000, -600), (1800, -1100)], [(1500, "depart", "", "의사당대로"), (0, "arrive", "", "노량진로")])
        return osrm([(0, 0), (0, 400), (0, 0), (1800, -1100)],
                    [(400, "depart", "", "여의도지하차도"), (400, "continue", "uturn", "의사당대로"), (1700, "turn", "right", "노량진로"), (0, "arrive", "", "")])

    def osm(self, bbox, detail="major"):
        road = {"type": "way", "tags": {"highway": "primary", "name": "의사당대로"}, "geometry": [{"lat": LAT0, "lon": LON0}, {"lat": LAT0 - .01, "lon": LON0 + .02}]}
        foot = {"type": "way", "tags": {"highway": "footway"}, "geometry": [{"lat": LAT0, "lon": LON0}, {"lat": LAT0 + .001, "lon": LON0}]}
        ring = [(0, 0), (0, 400), (400, 400), (400, 0), (0, 0)]
        park = {"type": "way", "tags": {"leisure": "park", "name": "여의도공원"},
                "geometry": [{"lat": LAT0 + n / 111195, "lon": LON0 + e / K} for e, n in ring]}
        half1 = [{"lat": LAT0 - .005, "lon": LON0}, {"lat": LAT0 - .005, "lon": LON0 + .01}, {"lat": LAT0 - .006, "lon": LON0 + .01}]
        half2 = [{"lat": LAT0 - .006, "lon": LON0 + .01}, {"lat": LAT0 - .006, "lon": LON0}, {"lat": LAT0 - .005, "lon": LON0}]
        river = {"type": "relation", "tags": {"natural": "water", "name": "샛강"},
                 "members": [{"type": "way", "role": "outer", "geometry": half1}, {"type": "way", "role": "outer", "geometry": half2},
                             {"type": "way", "role": "outer", "geometry": [{"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}]}]}       # 닫히지 않는 조각은 버린다
        nodes = [{"type": "node", "lat": LAT0 - .009, "lon": LON0 + .019, "tags": {"railway": "station", "name": "노량진"}},
                 {"type": "node", "lat": LAT0 - .002, "lon": LON0 + .01, "tags": {"place": "quarter", "name": "여의도동"}},
                 {"type": "node", "lat": LAT0 - .003, "lon": LON0 + .01, "tags": {"place": "quarter", "name": "영등포동1가"}}]
        return {"elements": [road, foot, park, river, *nodes], "_source": "server"}

    def buildings(self, bbox):
        sq = [(500, -300), (500, -260), (540, -260), (540, -300), (500, -300)]
        return {"elements": [{"type": "way", "tags": {"building": "yes"}, "geometry": [{"lat": LAT0 + n / 111195, "lon": LON0 + e / K} for e, n in sq]}]}


class Shapes(unittest.TestCase):
    def test_osm_becomes_navi_shapes_and_labels(self):
        geo = FakeGeo()
        data = navi.shapes(geo.osm(None), geo.buildings(None), navi.projector(LAT0, LON0))
        self.assertEqual((len(data["roads"]), len(data["parks"]), len(data["water"]), len(data["buildings"])), (1, 1, 1, 1))
        self.assertEqual(len(data["water"][0]["outer"]), 1)          # 두 조각을 이어 닫은 고리 하나(닫히지 않는 조각은 버림)
        names = [l[0] for l in data["labels"]]
        self.assertEqual(names, ["여의도공원", "노량진역", "여의도동"])      # 역 이름에 '역'을 붙이고, 번호 동은 빼다

    def test_destination_texts(self):
        place = {"display": "노량진역, 노량진로, 노량진1동, 동작구, 서울특별시, 06907, 대한민국"}
        self.assertEqual(navi.address(place, "노량진역"), "서울특별시 동작구 노량진역")
        self.assertEqual(navi.address({"display": ""}, "어딘가"), "어딘가")
        self.assertEqual((navi.short_name("노량진역"), navi.short_name("여의도공원")), ("노량진", "여의도공원"))


class Job(unittest.TestCase):
    def test_make_job_picks_the_natural_start_and_writes_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = request.normalize({"from": {"name": "여의도공원"}, "to": {"name": "노량진역"}, "duration_seconds": 2,
                                     "map_style": "navi_screen", "vehicle": "car", "output": {"width": 640, "height": 360, "fps": 5}})
            geo = FakeGeo()
            job = navi.make_job(req, project_dir=Path(tmp), out_root=Path(tmp), log=lambda s: None, geo=geo)
            self.assertEqual(len(geo.asked), len(navi.START_NUDGE))         # 지명으로 찾은 출발: 둘레 8곳도 본다
            self.assertTrue(any("150m 옮겼습니다" in w for w in job["warnings"]), job["warnings"])
            n = json.loads((job["dir"] / "navi.json").read_text(encoding="utf-8"))
            self.assertEqual(n["dest"], {"name": "노량진역", "address": "서울특별시 동작구 노량진역", "short": "노량진"})
            self.assertEqual(n["steps"][0]["name"], "의사당대로")
            self.assertEqual(len(list((job["dir"] / "preview").glob("*.jpg"))), 5)       # 0.5초마다(0 ~ 2초)
            self.assertIn("preview/", (job["dir"] / "index.html").read_text(encoding="utf-8"))
            self.assertEqual(job["map"]["counts"]["buildings"], 1)
            frame = navi.Scene(n).broadcast(1.0)
            self.assertEqual(frame.size, (640, 360))
            self.assertEqual(frame.getpixel((5, 5)), (0, 0, 0))            # 세로 화면 바깥은 검은 바탕

            # 지점을 직접 정한 출발은 옮기지 않는다
            geo2 = FakeGeo()
            pinned = {**req, "from": {"name": "여의도공원", "lat": LAT0, "lon": LON0}}
            navi.make_job(pinned, project_dir=Path(tmp), out_root=Path(tmp) / "b", log=lambda s: None, geo=geo2)
            self.assertEqual(len(geo2.asked), 1)

    def test_clean_start_is_not_moved_just_because_a_nearer_start_is_shorter(self):
        """어색하지 않은 출발은 옮기지 않는다 — 도착지 쪽으로 옮기면 길이 짧아진다는 이유만으로는."""
        class Shorter(FakeGeo):
            def route(self, a, b, foot=False, steps=False):
                self.asked.append((round(a["lat"], 5), round(a["lon"], 5)))
                d = round(math.hypot((a["lat"] - b["lat"]) * 111195, (a["lon"] - b["lon"]) * K))     # 출발이 도착에 가까울수록 짧은 길
                return osrm([(0, 0), (1800, -1100)], [(d, "depart", "", "의사당대로"), (0, "arrive", "", "노량진로")], distance=d)
        geo = Shorter()
        a, b = {"name": "여의도공원", "lat": LAT0, "lon": LON0}, {"name": "노량진역", "lat": LAT0 - 0.01, "lon": LON0 + 0.02}
        route, start, moved = navi.best_route(geo, a, b, False)
        self.assertEqual((moved, start, len(geo.asked)), (0.0, a, 1))        # 옮기지 않고, 둘레도 묻지 않는다(경로 서버 1번)

    def test_awkward_start_moves_only_when_it_gets_less_awkward(self):
        class Stuck(FakeGeo):       # 어디서 출발해도 똑같이 지하차도에서 나와 되돌아온다
            def route(self, a, b, foot=False, steps=False):
                self.asked.append(1)
                return osrm([(0, 0), (0, 400), (0, 0), (1800, -1100)], [(400, "depart", "", "여의도지하차도"), (400, "continue", "uturn", "의사당대로"), (0, "arrive", "", "")])
        geo = Stuck()
        a, b = {"name": "여의도공원", "lat": LAT0, "lon": LON0}, {"name": "노량진역", "lat": LAT0 - 0.01, "lon": LON0 + 0.02}
        self.assertEqual(navi.best_route(geo, a, b, False)[2], 0.0)          # 옮겨도 나아지지 않으면 제자리
        self.assertEqual(len(geo.asked), len(navi.START_NUDGE))

    def test_uturn_near_arrival_does_not_ask_around_the_start(self):
        """도착 쪽 U턴은 출발을 옮겨도 그대로다 — 둘레 8곳의 경로를 더 묻지 않는다(경로 서버 1번)."""
        class Late(FakeGeo):
            def route(self, a, b, foot=False, steps=False):
                self.asked.append(1)
                return osrm([(0, 0), (2500, 0), (2200, 0)], [(2500, "depart", "", "여의대로"), (300, "continue", "uturn", "노량진로"), (0, "arrive", "", "")])
        geo = Late()
        a, b = {"name": "여의도공원", "lat": LAT0, "lon": LON0}, {"name": "노량진역", "lat": LAT0 - 0.01, "lon": LON0 + 0.02}
        self.assertEqual(navi.best_route(geo, a, b, False)[2], 0.0)
        self.assertEqual(len(geo.asked), 1)

    def test_export_closes_the_log_when_ffmpeg_cannot_start(self):
        """ffmpeg 실행 자체가 실패하면 기록 파일을 열린 채 두지 않고 지운다."""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(navi, "Scene") as scene, \
                mock.patch.object(navi.subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg")), \
                mock.patch.object(navi.video, "find_ffmpeg", return_value="ffmpeg-없음"):
            scene.return_value.duration = 2
            job = Path(tmp)
            (job / "navi.json").write_text(json.dumps({"output": {"width": 640, "height": 360, "fps": 30}}), encoding="utf-8")
            with self.assertRaises(navi.video.ExportError) as e:
                navi.export(job, log=lambda s: None)
            self.assertIn("ffmpeg 를 실행하지 못했습니다", str(e.exception))
            self.assertEqual(sorted(p.name for p in job.iterdir()), ["navi.json"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 없음")
    def test_failed_export_leaves_no_half_video_and_keeps_the_earlier_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = request.normalize({"from": {"name": "여의도공원"}, "to": {"name": "노량진역"}, "duration_seconds": 1,
                                     "map_style": "navi_screen", "output": {"width": 640, "height": 360, "fps": 5}})
            job = navi.make_job(req, project_dir=Path(tmp), out_root=Path(tmp), log=lambda s: None, geo=FakeGeo())
            (job["dir"] / "네비.mp4").write_bytes(b"earlier video")
            real, count = navi.Scene.broadcast, [0]

            def breaks(scene, t):
                count[0] += 1
                if count[0] == 3:
                    raise RuntimeError("그리다 실패")
                return real(scene, t)
            with mock.patch.object(navi.Scene, "broadcast", breaks), self.assertRaises(RuntimeError):
                navi.export(job["dir"], log=lambda s: None)
            self.assertEqual((job["dir"] / "네비.mp4").read_bytes(), b"earlier video")       # 앞서 만든 영상을 덮지 않는다
            self.assertEqual([p.name for p in job["dir"].iterdir() if "쓰는중" in p.name or p.suffix == ".log"], [])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 없음")
    def test_export_counts_frames_like_the_browser_and_accepts_odd_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = request.normalize({"from": {"name": "여의도공원"}, "to": {"name": "노량진역"}, "duration_seconds": 2.5,
                                     "map_style": "navi_screen", "output": {"width": 321, "height": 181, "fps": 5}})
            job = navi.make_job(req, project_dir=Path(tmp), out_root=Path(tmp), log=lambda s: None, geo=FakeGeo())
            r = navi.export(job["dir"], log=lambda s: None)
            self.assertTrue(r["mp4"].is_file())
            self.assertEqual(r["frames"], 13)           # 2.5초 × 5fps = 12.5 → 반은 올린다(파이썬 round 는 12)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 없음")
    def test_export_makes_a_video_of_the_requested_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = request.normalize({"from": {"name": "여의도공원"}, "to": {"name": "노량진역"}, "duration_seconds": 1,
                                     "map_style": "navi_screen", "output": {"width": 640, "height": 360, "fps": 5}})
            job = navi.make_job(req, project_dir=Path(tmp), out_root=Path(tmp), log=lambda s: None, geo=FakeGeo())
            r = navi.export(job["dir"], log=lambda s: None)
            self.assertTrue(r["mp4"].is_file())
            self.assertEqual(r["frames"], 5)
            if r["spec"]["state"] != "unknown":
                self.assertEqual(r["spec"]["state"], "ok")
            self.assertIn("delivery", json.loads((job["dir"] / "job.json").read_text(encoding="utf-8")))


class App(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.desk = server.Desk(Path(tmp.name), liner=False)
        self.desk._spawn = lambda fn, *a, **_: fn(*a)
        self.base = {"from": {"name": "여의도공원"}, "to": {"name": "노량진역"}, "duration_seconds": 12, "map_style": "navi_screen", "face_count": 2}

    def test_make_goes_to_navi_and_export_too(self):
        out = Path(self.desk.out) / "j1"
        out.mkdir(parents=True)
        (out / "index.html").write_text("x", encoding="utf-8")
        job = {"dir": out, "places": {k: {"lat": 1, "lon": 2, "display": "d"} for k in ("from", "to")}, "route": {"distance_m": 4000},
               "warnings": [], "timing": {"build_seconds": 1.0}}
        with mock.patch.object(navi, "make_job", return_value=job) as made, \
                mock.patch.object(server.build, "make_job", side_effect=AssertionError("지도 화면으로 갔다")):
            self.desk.make(dict(self.base))
        self.assertEqual(made.call_args[0][0]["map_style"], "navi_screen")
        res = self.desk.msgs[-1]
        self.assertEqual((res["kind"], res["job"]), ("result", "j1"))
        fake = {"mp4": out / "네비.mp4", "frames": 360, "spec": None, "seconds": 3.0}
        with mock.patch.object(navi, "export", return_value=fake) as ex, \
                mock.patch.object(server.export, "export", side_effect=AssertionError("브라우저 내보내기로 갔다")):
            self.desk.export(res["id"], False)
        ex.assert_called_once()
        self.assertEqual(self.desk.msgs[-1]["video"]["url"], "/jobs/j1/네비.mp4")

    def test_cuts_are_refused_for_now(self):
        with self.assertRaises(request.RequestError):
            self.desk.make({**self.base, "cuts": [{"from": "여의도공원", "to": "대방역", "duration_seconds": 6},
                                                  {"from": "대방역", "to": "노량진역", "duration_seconds": 6}]})

    def test_reading_rules(self):
        self.assertEqual(intake.simple_parse("CG의뢰 김갑PD 네비 form 총 12초 여의도공원에서 노량진역까지")[0]["map_style"], "navi_screen")
        self.assertEqual(intake.simple_parse("여의도공원에서 노량진역까지 네비처럼 남은 거리")[0]["map_style"], "nav")
        self.assertIn("navi_screen", request.STYLES)


if __name__ == "__main__":
    unittest.main()
