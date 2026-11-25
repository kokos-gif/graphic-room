"""지도 서버에서 받기 — 끊긴 응답 · 횟수 한도 · 대체 서버의 답 저장(가짜 응답으로 본다. 지도 서버를 부르지 않는다)."""
import http.client
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import geo


class Cut:
    """보내다가 끊기는 가짜 응답."""
    headers = {}

    def read(self, n=-1):
        raise http.client.IncompleteRead(b"{\"elements\":[")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def boxes(n):
    """서울 지도 묶음 밖(부산 쪽)의 서로 다른 범위 n 개 — 컷 n 개가 저마다 바탕 지도를 받는 상황."""
    return [(35.1 + i * 0.2, 129.0, 35.2 + i * 0.2, 129.1) for i in range(n)]


class Fetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.geo = geo.Geo(Path(self.tmp.name))
        rest = mock.patch.dict(geo._rested, {}, clear=True)
        rest.start()
        self.addCleanup(rest.stop)

    def test_중간에_끊긴_응답은_못_받은_것으로_돌려_대비책으로_간다(self):
        with mock.patch.object(geo, "_open", lambda req, timeout: Cut()):
            with self.assertRaises(geo.GeoError) as e:
                geo.Geo._http("https://example.invalid/api?x=1")
            self.assertIn("중간에 끊겼습니다", str(e.exception))
            got = self.geo.osm(boxes(1)[0], "major")        # 제작 전체가 죽지 않고 바탕 없는 지도로 간다
        self.assertEqual(got["_source"], "blank")

    def test_잘_받은_호출은_횟수_한도에_들지_않는다(self):
        self.geo.budget = geo.Budget(tries=2)
        calls = []

        def fine(url, data=None, timeout=60, limit=None, **k):
            calls.append(url)
            return {"elements": []}
        with mock.patch.object(geo.Geo, "_http", staticmethod(fine)):
            got = [self.geo.osm(b, "major") for b in boxes(8)]      # 여덟 컷 — 한도(2번)보다 많다
        self.assertEqual([d["_source"] for d in got], ["server"] * 8)
        self.assertEqual((len(calls), self.geo.budget.tried), (8, 0))

    def test_실패한_시도는_횟수_한도에_든다(self):
        self.geo.budget = geo.Budget(tries=2)
        calls = []

        def broken(url, data=None, timeout=60, limit=None, **k):
            calls.append(url)
            raise geo.GeoError("막힘")
        with mock.patch.object(geo.Geo, "_http", staticmethod(broken)):
            got = [self.geo.osm(b, "major") for b in boxes(3)]
        self.assertEqual(len(calls), 2)                              # 두 번 실패한 뒤로는 부르지 않는다
        self.assertTrue(all(d["_source"] == "blank" for d in got))

    def test_다_못_보낸_답도_실패로_센다(self):
        self.geo.budget = geo.Budget(tries=2)
        calls = []

        def partial(url, data=None, timeout=60, limit=None, **k):
            calls.append(url)
            return {"elements": [], "remark": "runtime error: Query timed out"}
        with mock.patch.object(geo.Geo, "_http", staticmethod(partial)):
            self.geo.osm(boxes(1)[0], "major")
        self.assertEqual((len(calls), self.geo.budget.tried), (2, 2))


class PlaceCache(unittest.TestCase):
    """지명 서버가 잠깐 안 될 때 대체 서버에서 받은 답(특히 '없음')을 기한 없이 저장하면, 서버가 돌아와도 그 지명은 계속 못 찾는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.geo = geo.Geo(Path(self.tmp.name))
        self.down, self.calls = True, []
        for p in (mock.patch.object(geo.Geo, "_http", staticmethod(self.http)), mock.patch.object(geo.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)

    def http(self, url, data=None, timeout=60, limit=None, **k):
        main = url.startswith(geo.NOMINATIM)
        self.calls.append("지명 서버" if main else "대체 서버")
        if main:
            if self.down:
                raise geo.GeoError("지명 서버 장애")
            return [{"display_name": "노들섬, 이촌2동, 용산구, 서울특별시", "lat": "37.5176", "lon": "126.9586", "category": "place", "type": "islet"}]
        return {"features": []}         # 대체 서버는 그런 이름을 모른다

    def rest_is_over(self):
        geo._place_state[str(self.geo.dir)]["until"] = 0.0       # 쉬는 시간이 지났다

    def test_대체_서버의_없음은_저장하지_않고_서버가_돌아오면_다시_묻는다(self):
        with self.assertRaises(geo.GeoNotFound):
            self.geo.geocode("노들섬")
        self.assertEqual(self.calls, ["지명 서버", "대체 서버"])
        self.assertEqual(list(self.geo.dir.glob("place_*")), [])       # 디스크에는 남기지 않는다
        self.down = False
        self.rest_is_over()
        hit = self.geo.geocode("노들섬")                      # 저장된 '없음'을 쓰지 않고 지명 서버에 다시 묻는다
        self.assertAlmostEqual(hit["lat"], 37.5176)
        self.assertEqual(self.calls[2:], ["지명 서버"])

    def test_지명_서버가_안_되는_동안에는_지명마다_다시_기다리지_않는다(self):
        """장애 중에 지명 네 개를 찾으면 전에는 지명마다 지명 서버의 시간 초과(30초)를 기다렸다. 한 번 안 되면 잠깐 쉬게 두고 대체 서버로 바로 간다."""
        for name in ("노들섬", "여의도", "노들섬", "서강대교"):
            with self.assertRaises(geo.GeoNotFound):
                self.geo.geocode(name)
        # 지명 서버는 처음 한 번만, 같은 지명(노들섬)은 대체 서버에도 다시 묻지 않는다
        self.assertEqual(self.calls, ["지명 서버", "대체 서버", "대체 서버", "대체 서버"])
        other = geo.Geo(Path(self.tmp.name) / "다른보관")       # 보관 폴더가 다르면 서로의 기억을 쓰지 않는다
        with self.assertRaises(geo.GeoNotFound):
            other.geocode("노들섬")
        self.assertEqual(self.calls[4:], ["지명 서버", "대체 서버"])

    def test_지명_서버의_답은_저장해_다시_묻지_않는다(self):
        self.down = False
        self.geo.geocode("노들섬")
        self.geo.geocode("노들섬")
        self.assertEqual(self.calls, ["지명 서버"])


if __name__ == "__main__":
    unittest.main()
