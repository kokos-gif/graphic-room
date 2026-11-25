import unittest

from mapcg import request


def base(**over):
    raw = {"from": {"name": "홍대"}, "to": "여의도", "duration_seconds": 8}
    raw.update(over)
    return raw


class NormalizeTest(unittest.TestCase):
    def test_기본값을_채운다(self):
        req = request.normalize(base())
        self.assertEqual(req["layout"], "full")
        self.assertEqual(req["vehicle"], "bus")
        self.assertEqual(req["map_style"], "route_move")
        self.assertEqual(req["output"], {"width": 1920, "height": 1080, "fps": 30})
        self.assertEqual(req["to"], {"name": "여의도"})          # 글자만 줘도 받는다
        self.assertEqual(req["faces"], [])

    def test_네비_형식은_받고_없앤_하프톤_형식은_돌려보낸다(self):
        self.assertEqual(request.normalize(base(map_style="nav"))["map_style"], "nav")
        with self.assertRaises(request.RequestError):
            request.normalize(base(map_style="halftone_nav"))

    def test_좌표를_주면_그대로_쓴다(self):
        req = request.normalize(base(**{"from": {"name": "홍대 정문", "lat": 37.5512, "lon": 126.9255}}))
        self.assertEqual(req["from"]["lat"], 37.5512)

    def test_좌표가_반쪽이면_거절한다(self):
        with self.assertRaises(request.RequestError) as cm:
            request.normalize(base(**{"from": {"name": "홍대", "lat": 37.55}}))
        self.assertIn("lat·lon", cm.exception.problems[0])

    def test_틀린_곳을_한번에_모아_알린다(self):
        with self.assertRaises(request.RequestError) as cm:
            request.normalize({"to": {"name": ""}, "layout": "top", "vehicle": "배", "duration_seconds": 0})
        self.assertEqual(len(cm.exception.problems), 5)   # 출발지·도착지·배치·아이콘·길이

    def test_길이가_없으면_거절한다(self):
        raw = base()
        del raw["duration_seconds"]
        with self.assertRaises(request.RequestError):
            request.normalize(raw)

    def test_원본을_고치지_않는다(self):
        raw = base()
        request.normalize(raw)
        self.assertEqual(raw, base())


class PanelRectTest(unittest.TestCase):
    def test_배치_네_가지(self):
        self.assertEqual(request.panel_rect("full", 1920, 1080), (0, 0, 1920, 1080))
        self.assertEqual(request.panel_rect("bottom_half", 1920, 1080), (0, 540, 1920, 540))
        self.assertEqual(request.panel_rect("bottom_left_quarter", 1920, 1080), (0, 540, 960, 540))
        self.assertEqual(request.panel_rect("bottom_right_quarter", 1920, 1080), (960, 540, 960, 540))

    def test_이름표가_배치마다_있다(self):
        self.assertEqual(set(request.LAYOUTS), set(request.LAYOUT_LABELS))


if __name__ == "__main__":
    unittest.main()
