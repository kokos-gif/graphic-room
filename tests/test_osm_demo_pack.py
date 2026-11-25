"""같이 싣는 시연 지역 지도 묶음(mapcg/packs/osm) — 심사자 컴퓨터에서도 영상과 같은 범위가 공개 지도 서버 없이 그려지는지."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import osmlocal

# 시연 사례에서 앱이 지역 자료에 '실제로 청한' 범위(남, 서, 북, 동) — 2026-11-22 에 Geo._local 이 받은 값을 잰 것이다.
# 손으로 적은 '경로 둘레'로 시험하면 안 된다: 묶음은 청한 범위를 통째로 덮을 때만 쓰이는데(Geo._local), 네비 화면은 경로 둘레
# ±0.02° · ±0.022° 를, 지도 영상은 화면 비율에 맞춰 경로보다 훨씬 넓게 청한다. 처음 시험은 경로 둘레만 봐서, 네비 두 사례의
# 도로가 묶음으로 한 번도 그려지지 않는 것을 잡지 못했다.
ASKED = {
    "영상 서울역 → DDP 지도 영상(걸어서)": (37.5472, 126.9545, 37.5781, 127.0234),
    "영상 김갑PD 네비(여의도공원 → 노량진역) 도로": (37.4926, 126.8903, 37.5467, 126.9645),
    "영상 김갑PD 네비(여의도공원 → 노량진역) 건물": (37.5006, 126.8983, 37.5387, 126.9565),
    "README 네비 예시(노들섬 → 여의도) 도로": (37.4939, 126.9001, 37.5459, 126.9807),
    "README 네비 예시(노들섬 → 여의도) 건물": (37.5019, 126.9081, 37.5378, 126.9727),
}
SLACK = 0.004       # 경로 서버가 길을 조금 다르게 줘도 덮이게(약 400m)


class DemoPack(unittest.TestCase):
    def packs(self):
        return [osmlocal.Local(p) for p in sorted(osmlocal.PACKED.glob("*.sqlite"))]

    def test_every_demo_case_is_covered_for_the_area_the_app_really_asks(self):
        packs = self.packs()
        self.assertGreaterEqual(len(packs), 3)
        for name, (s, w, n, e) in ASKED.items():
            wide = (s - SLACK, w - SLACK, n + SLACK, e + SLACK)
            hit = next((p for p in packs if p.covers(wide)), None)
            self.assertIsNotNone(hit, f"{name}: 청한 범위 {wide} 를 통째로 덮는 묶음이 없다 — tools/osm_demo_pack.py 의 AREAS 를 넓힌다")
            bbox = (s, w, n, e)
            self.assertGreater(len(hit.osm(bbox, "minor")["elements"]), len(hit.osm(bbox, "major")["elements"]), name)     # 골목길까지 있다
            self.assertGreater(len(hit.buildings(bbox)["elements"]), 100, name)

    def test_the_navi_screen_asks_for_more_than_the_route(self):
        """네비 화면이 청하는 여유(navi.make_job 의 pad)가 바뀌면 ASKED 를 다시 재야 한다 — 그 값이 그대로인지 본다."""
        import inspect

        from mapcg import navi
        self.assertIn("pad_lat, pad_lon = 0.02, 0.022", inspect.getsource(navi.make_job))

    def test_packs_are_used_only_for_the_apps_own_cache(self):
        with mock.patch.dict(os.environ, {"MAPCG_OSM_DB": ""}), tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(osmlocal.find(Path(tmp) / "geo"), [])                       # 시험 · 다른 폴더는 그대로
            names = [p.path.name for p in osmlocal.find(osmlocal._APP_CACHE)]
            for p in osmlocal.PACKED.glob("*.sqlite"):
                self.assertIn(p.name, names)
        with mock.patch.dict(os.environ, {"MAPCG_OSM_DB": "0"}):
            self.assertEqual(osmlocal.find(osmlocal._APP_CACHE), [])                     # 끄면 묶음도 쓰지 않는다

    def test_pack_carries_the_odbl_attribution(self):
        for p in self.packs():
            import sqlite3
            from urllib.parse import quote
            db = sqlite3.connect(f"file:{quote(p.path.resolve().as_posix(), safe='/:')}?mode=ro", uri=True)
            meta = dict(db.execute("SELECT key, value FROM meta"))
            db.close()
            self.assertIn("OpenStreetMap", meta["attribution"])


if __name__ == "__main__":
    unittest.main()
