"""배 이동 — 강 중심선을 이은 뱃길 · '한강버스'는 배 · 선착장 먼저 찾기(지도 서버를 부르지 않는다)."""
import unittest

from mapcg import build, geo, intake, request
from mapcg.geo import GeoNotFound, meters


def line(name, pts):
    return {"name": name, "pts": [list(p) for p in pts]}


# 동서로 흐르다 북쪽으로 크게 굽이치는 강(위도 37.52 → 37.55 → 37.52) 과, 그 강에 북쪽에서 들어오는 지류
RIVER = line("한강", [(37.52, 126.90), (37.52, 126.93), (37.55, 126.95), (37.55, 126.99), (37.52, 127.01), (37.52, 127.06)])
STREAM = line("중랑천", [(37.52, 127.04), (37.54, 127.04), (37.57, 127.045)])


class WaterPath(unittest.TestCase):
    def test_follows_the_river_bend_instead_of_crossing_land(self):
        a, b = {"lat": 37.521, "lon": 126.905}, {"lat": 37.521, "lon": 127.03}
        r = geo.water_path([RIVER, STREAM], a, b)
        coords = r["routes"][0]["geometry"]["coordinates"]
        self.assertEqual(coords[0], [126.905, 37.521])          # 출발 지점에서 시작해
        self.assertEqual(coords[-1], [127.03, 37.521])          # 도착 지점에서 끝난다
        self.assertIn([126.95, 37.55], coords)                   # 굽이를 따라 북쪽으로 돌아간다
        straight = meters(coords[0], coords[-1])
        self.assertGreater(r["routes"][0]["distance"], straight * 1.1)
        self.assertEqual(r["water"], "한강")
        self.assertAlmostEqual(r["routes"][0]["duration"], r["routes"][0]["distance"] / geo.BOAT_MS)

    def test_prefers_the_river_both_ends_are_near(self):
        # 도착은 지류(중랑천)가 더 가깝지만, 출발은 한강에만 가깝다 — 두 끝이 함께 가까운 한강으로 간다
        a, b = {"lat": 37.521, "lon": 126.905}, {"lat": 37.524, "lon": 127.043}
        r = geo.water_path([RIVER, STREAM], a, b)
        self.assertEqual(r["water"], "한강")
        inner = r["routes"][0]["geometry"]["coordinates"][1:-1]
        self.assertTrue(all(abs(lat - 37.52) < 1e-6 or lat >= 37.52 for _, lat in inner))
        self.assertNotIn([127.045, 37.57], inner)               # 지류 위쪽으로 올라가지 않는다

    def test_far_from_any_river_is_refused(self):
        with self.assertRaises(GeoNotFound):
            geo.water_path([RIVER], {"lat": 37.60, "lon": 126.95}, {"lat": 37.521, "lon": 127.03})
        with self.assertRaises(GeoNotFound):
            geo.water_path([], {"lat": 37.52, "lon": 126.95}, {"lat": 37.52, "lon": 127.0})

    def test_same_segment_goes_straight(self):
        r = geo.water_path([RIVER], {"lat": 37.519, "lon": 127.02}, {"lat": 37.521, "lon": 127.05})
        coords = r["routes"][0]["geometry"]["coordinates"]
        self.assertEqual(coords, [[127.02, 37.519], [127.05, 37.521]])     # 같은 조각 위 — 중심선까지 갔다 오는 꺾임 없이 곧장

    def test_no_hook_when_the_nearest_centerline_point_is_ahead(self):
        # 중심선 점이 성길 때: 선착장에서 가장 가까운 점이 비스듬히 앞(북동)에 있고, 강은 거기서 남동으로 꺾인다
        sparse = line("한강", [(37.50, 126.90), (37.53, 126.94), (37.52, 126.95), (37.52, 127.00)])
        r = geo.water_path([sparse], {"lat": 37.5245, "lon": 126.935}, {"lat": 37.521, "lon": 126.99})
        coords = r["routes"][0]["geometry"]["coordinates"]
        self.assertNotIn([126.94, 37.53], coords)      # 북동의 점까지 갔다가 되돌아오지 않는다
        self.assertEqual(coords[1], [126.95, 37.52])


class Reading(unittest.TestCase):
    def test_hangang_bus_is_a_boat(self):
        for text in ("여의도 선착장에서 뚝섬까지 오는 한강버스에 출연진 4명", "수상택시로 이동", "유람선 타고 반포까지", "배를 타고 건너갑니다"):
            self.assertEqual(intake._vehicle(text), "boat", text)
        self.assertEqual(intake._vehicle("홍대에서 여의도로 버스"), "bus")
        self.assertIsNone(intake._vehicle("화면 배치는 좌하단, 배경은 밝게"))       # '배치'·'배경'은 배가 아니다
        self.assertIn("boat", request.VEHICLES)


class FakeGeo:
    def __init__(self, known):
        self.known, self.asked = known, []

    def geocode(self, name):
        self.asked.append(name)
        if name not in self.known:
            raise GeoNotFound(name)
        return {"name": name, "lat": self.known[name][0], "lon": self.known[name][1], "display": name, "candidates": []}

    def geocode_loose(self, name):
        return self.geocode(name)


class Pier(unittest.TestCase):
    def test_town_name_tries_the_pier_first(self):
        g = FakeGeo({"뚝섬 선착장": (37.529, 127.069), "뚝섬": (37.547, 127.047)})
        got = build._resolve_pier(g, {"name": "뚝섬"})
        self.assertEqual((got["lat"], got["searched"]), (37.529, "뚝섬 선착장"))

    def test_spacing_variants_are_tried(self):
        # 지도에는 붙여 쓴 '여의도선착장'만 있다 — 띄어 쓴 의뢰도 찾는다(이름이 같으니 '다른 이름으로 찾음' 알림은 붙이지 않는다)
        g = FakeGeo({"여의도선착장": (37.5248, 126.9353)})
        got = build._resolve_pier(g, {"name": "여의도 선착장"})
        self.assertEqual((got["lat"], "searched" in got), (37.5248, False))
        self.assertEqual(g.asked, ["여의도 선착장", "여의도선착장"])
        g = FakeGeo({"한강버스 망원": (37.55, 126.89)})
        self.assertEqual(build._resolve_pier(g, {"name": "망원"})["searched"], "한강버스 망원")

    def test_pier_name_and_missing_pier_use_the_name_as_written(self):
        g = FakeGeo({"여의도 선착장": (37.527, 126.932), "반포": (37.51, 126.99)})
        self.assertEqual(build._resolve_pier(g, {"name": "여의도 선착장"})["lat"], 37.527)
        self.assertEqual(g.asked, ["여의도 선착장"])            # 이미 선착장이면 '선착장'을 또 붙이지 않는다
        self.assertEqual(build._resolve_pier(g, {"name": "반포"})["lat"], 37.51)     # '반포 선착장'이 없으면 적힌 이름으로
        pinned = build._resolve_pier(g, {"name": "어디", "lat": 37.5, "lon": 127.0})
        self.assertEqual(pinned["display"], "직접 지정한 좌표")


class PictureVehicle(unittest.TestCase):
    """그림 이동 아이콘(vehicle_image — Liner 그림 모델로 그린 배)과 얼굴 크기 배율(face_scale)."""

    def base(self, **extra):
        return {"from": {"name": "여의도 선착장"}, "to": {"name": "뚝섬"}, "duration_seconds": 10, "vehicle": "boat", **extra}

    def test_request_fields(self):
        req = request.normalize(self.base(face_scale=1.2, vehicle_image="data/icons/배.png"))
        self.assertEqual((req["face_scale"], req["vehicle_image"]), (1.2, "data/icons/배.png"))
        self.assertNotIn("face_scale", request.normalize(self.base(face_scale=1)))      # 기본값은 봉투에 적지 않는다
        for bad in ({"face_scale": 3}, {"face_scale": "크게"}, {"vehicle_image": "배.jpg"}):
            with self.assertRaises(request.RequestError):
                request.normalize(self.base(**bad))

    def test_picture_is_trimmed_into_the_job_folder(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            im = Image.new("RGBA", (320, 320), (255, 255, 255, 0))
            im.paste(Image.new("RGBA", (200, 100), (255, 255, 255, 255)), (60, 110))        # 투명한 여백 가운데의 배
            im.save(tmp / "배.png")
            warnings = []
            self.assertEqual(build._vehicle_image("배.png", [tmp], tmp, warnings), "vehicle.png")
            self.assertEqual(Image.open(tmp / "vehicle.png").size, (200, 100))       # 여백을 잘라 낸 크기
            self.assertIsNone(build._vehicle_image("없는배.png", [tmp], tmp, warnings))
            self.assertIn("코드로 그린 아이콘으로", warnings[0])


if __name__ == "__main__":
    unittest.main()
