"""9-3 남은 결함 표의 나머지(2026-11-21 밤): 가짜 응답 · Liner 를 부르지 않는다."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, geo, intake, plan, request


class Minus(unittest.TestCase):
    """붙여 넣은 빼기 기호(−12 · △5 · ▼3)를 음수로 읽는다 — 양수로 정렬되면 순위가 뒤집힌다."""

    def test_빼기_기호(self):
        for text, want in (("−12점", -12), ("－3", -3), ("△5", -5), ("▼2", -2), ("-1.5", -1.5), ("+7", 7), ("▲4", 4), ("12,450표", 12450)):
            with self.subTest(text=text):
                self.assertEqual(board.number(text), want)

    def test_줄글_한_줄(self):
        self.assertEqual(board._LINE.match("2위 가람 −12점").group("value"), "−12점")

    def test_범위는_숫자가_아니다(self):
        self.assertIsNone(board.number("10–20"))


class BuildingPeople(unittest.TestCase):
    """정보판 봉투에 사람 수만 적어도('people': 2) 멈추지 않는다."""

    BASE = {"kind": "plan", "shape": "building", "title": "", "duration_seconds": 8, "layout": "full",
            "output": {"width": 960, "height": 540, "fps": 30},
            "floors": [{"label": "B2", "kind": "parking"}, {"label": "8F", "kind": "goal"}],
            "steps": [{"floor": "B2", "how": "start", "caption": ""}, {"floor": "8F", "how": "elevator", "caption": ""}]}

    def test_사람_수(self):
        self.assertEqual(plan.normalize({**self.BASE, "people": 2})["people"], [{"name": ""}, {"name": ""}])

    def test_이상한_값(self):
        for bad in ("두 명", None, True, [None, "가람"]):
            with self.subTest(bad=bad):
                self.assertEqual(plan.normalize({**self.BASE, "people": bad})["people"], [{"name": ""}])


class Private(unittest.TestCase):
    """작업 기록(job.json)에는 전화번호를 가려서 남긴다 — 작업 폴더는 납품하며 넘겨진다."""

    def test_가리기(self):
        req = {"requester": {"name": "이가상", "role": "조연출", "phone": "010-1234-5678"},
               "text": "연락처 01098765432 · 사무실 02 123 4567", "duration_seconds": 8, "rows": [{"value_text": "12,450표"}]}
        got = request.private(req)
        self.assertEqual(got["requester"], {"name": "이가상", "role": "조연출", "phone": "010-****-5678"})
        self.assertEqual(got["text"], "연락처 010-****-5432 · 사무실 02-****-4567")
        self.assertEqual(got["rows"], req["rows"])
        self.assertEqual(req["requester"]["phone"], "010-1234-5678")      # 받은 값은 그대로

    def test_정보판_작업_기록(self):
        req = plan.normalize({**BuildingPeople.BASE, "requester": {"name": "이가상", "phone": "010-1234-5678"}, "text": "전화 010-1234-5678"})
        with tempfile.TemporaryDirectory() as d:
            job = plan.make_job(req, project_dir=Path(d), out_root=Path(d) / "out", log=lambda _: None)
            saved = json.loads((job["dir"] / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["request"]["requester"]["phone"], "010-****-5678")
        self.assertNotIn("1234", saved["request"]["text"])


class Reason(unittest.TestCase):
    """Liner 오류는 영어 원문 · 응답 본문 대신 우리말 한 줄로 화면 · 작업 기록에 적는다."""

    def test_liner_오류(self):
        err = intake._liner().LinerError
        self.assertEqual(intake.reason(err(402, '{"error": "Insufficient balance", "detail": "..."}')), "Liner 잔액이 부족합니다(응답 402)")
        self.assertEqual(intake.reason(err(503, "<html>Service Unavailable</html>")), "Liner 서버 오류(응답 503)")
        self.assertNotIn("Insufficient", intake.reason(err(402, "Insufficient balance"), typed=False))

    def test_다른_오류는_그대로(self):
        self.assertEqual(intake.reason(ValueError("값이 이상합니다")), "ValueError: 값이 이상합니다")
        self.assertEqual(intake.reason(ValueError("값이 이상합니다"), typed=False), "값이 이상합니다")


class Buildings(unittest.TestCase):
    """네비의 건물 받기도 지도 도형과 같이 쉬는 서버는 건너뛰고, 이 의뢰의 몫을 다 썼으면 부르지 않는다."""

    BBOX = (37.52, 126.92, 37.53, 126.93)

    def test_쉬는_서버는_부르지_않는다(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(geo._rested, {u: 9e18 for u in geo.OVERPASS}):
            g = geo.Geo(d)
            with mock.patch.object(g, "_http", side_effect=AssertionError("쉬는 서버를 불렀다")), self.assertRaises(geo.GeoError):
                g.buildings(self.BBOX)

    def test_몫을_다_썼으면_부르지_않는다(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(geo._rested, {}, clear=True):
            g = geo.Geo(d)
            g.budget.tried = g.budget.tries
            with mock.patch.object(g, "_http", side_effect=AssertionError("몫을 다 쓰고 불렀다")), self.assertRaises(geo.GeoError) as e:
                g.buildings(self.BBOX)
        self.assertIn("더 부르지 않습니다", str(e.exception))

    def test_받은_양을_몫에_센다(self):
        def fake(url, **k):
            geo._received.n = 1234
            return {"elements": []}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(geo._rested, {}, clear=True):
            g = geo.Geo(d)
            with mock.patch.object(g, "_http", side_effect=fake):
                g.buildings(self.BBOX)
            self.assertEqual((g.budget.got, g.budget.tried), (1234, 0))
            self.assertGreater(g._usage(), 0)       # 건물 도형도 쌓아 둔 양에 센다


class WaterLegs(unittest.TestCase):
    """여러 지점을 잇는 뱃길도 구간마다의 길이(legs)를 낸다 — 없으면 컷 경계가 균등 어림이 된다."""

    def test_구간_길이(self):
        def leg(a, b):
            d = {"가": 1000.0, "나": 3000.0}[a["name"]]
            return {"routes": [{"geometry": {"coordinates": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]]}, "distance": d}]}
        pts = [{"name": n, "lon": 127 + i * .01, "lat": 37.5} for i, n in enumerate("가나다")]
        with tempfile.TemporaryDirectory() as d:
            g = geo.Geo(d, offline=True)
            with mock.patch.object(g, "water_route", side_effect=leg):
                got = g.water_route_via(pts)["routes"][0]
        self.assertEqual([x["distance"] for x in got["legs"]], [1000.0, 3000.0])
        self.assertEqual(got["distance"], 4000.0)
        self.assertEqual(len(got["geometry"]["coordinates"]), 3)


if __name__ == "__main__":
    unittest.main()
