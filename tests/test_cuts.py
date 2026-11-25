"""컷 나눔 지도 — 의뢰 검사와 경로 자르기. 지도 서버는 부르지 않는다."""
import unittest

from mapcg import cuts, request

LINE = [[126.90, 37.50], [126.91, 37.50], [126.92, 37.50]]      # 동쪽으로 곧게 뻗은 줄(한 칸이 약 883m)
BASE = {"from": {"name": "가"}, "to": {"name": "나"}, "output": {"width": 960, "height": 540, "fps": 30}}


def cut(**kw):
    return {"from": "ㄱ", "to": "ㄴ", "duration_seconds": 4, **kw}


class Normalize(unittest.TestCase):
    def test_길이는_컷_길이의_합이다(self):
        spec = cuts.normalize({**BASE, "cuts": [cut(), cut(), cut(), cut()]})
        self.assertEqual(spec["base"]["duration_seconds"], 16)
        self.assertEqual(len(spec["cuts"]), 4)

    def test_고칠_곳을_모아서_알린다(self):
        with self.assertRaises(request.RequestError) as e:
            cuts.normalize({**BASE, "cuts": [cut(from_m=0), {"from": "", "to": "ㄴ", "duration_seconds": 4},
                                               cut(bridge={"look": "arch"}), cut(from_m=0, to_m=10)]})
        text = " / ".join(e.exception.problems)
        for word in ("둘 다 주거나", "구간 이름", "다리 모양", "너무 짧습니다"):
            self.assertIn(word, text)

    def test_컷은_둘_이상이어야_한다(self):
        with self.assertRaises(request.RequestError):
            cuts.normalize({**BASE, "cuts": [cut()]})

    def test_합이_60초를_넘으면_돌려보낸다(self):
        with self.assertRaises(request.RequestError):
            cuts.normalize({**BASE, "cuts": [cut(duration_seconds=40), cut(duration_seconds=30)]})


class SliceRoute(unittest.TestCase):
    def test_자른_구간의_길이가_맞다(self):
        total = cuts.route_length(LINE)
        part = cuts.slice_route(LINE, 400, 1300)
        self.assertAlmostEqual(cuts.route_length(part), 900, delta=1)
        self.assertEqual(len(part), 3)                                  # 양 끝 두 점 + 그 사이의 원래 점 하나
        self.assertAlmostEqual(cuts.route_length(cuts.slice_route(LINE, -50, total + 50)), total, delta=1)

    def test_끝점은_선_위에_있다(self):
        a, *_, b = cuts.slice_route(LINE, 400, 1300)
        self.assertTrue(LINE[0][0] < a[0] < LINE[1][0] < b[0] < LINE[2][0])
        self.assertAlmostEqual(a[1], 37.50)


class VehicleLook(unittest.TestCase):
    def test_색은_이름이나_16진수로_받는다(self):
        raw = {**BASE, "duration_seconds": 8}
        self.assertEqual(request.normalize({**raw, "vehicle_color": "빨강"})["vehicle_color"], "#e8443a")
        self.assertEqual(request.normalize({**raw, "vehicle_color": "#12ABef"})["vehicle_color"], "#12abef")
        self.assertNotIn("vehicle_color", request.normalize(raw))
        with self.assertRaises(request.RequestError):
            request.normalize({**raw, "vehicle_color": "무지개"})
        with self.assertRaises(request.RequestError):
            request.normalize({**raw, "vehicle_scale": 9})


class StagedBoard(unittest.TestCase):
    """순위 변동판 — 같은 줄들이 다음 순위의 자리로 옮겨 간다."""

    def raw(self, next_rows):
        return {"kind": "rank_board", "title": "1차", "duration_seconds": 12, "output": BASE["output"],
                "rows": [{"name": "가", "value_text": "10"}, {"name": "나", "value_text": "9"}],
                "next": {"title": "2차", "rows": next_rows}}

    def test_다음_순위의_줄은_처음_줄의_이름표를_이어받는다(self):
        from mapcg import board
        req = board.normalize(self.raw([{"name": "나", "value_text": "12"}, {"name": "가", "value_text": "기권", "out": True}]))
        first = {r["name"]: r["id"] for r in req["rows"]}
        self.assertEqual([(r["name"], r["id"], r.get("out", False)) for r in req["next"]["rows"]],
                         [("나", first["나"], False), ("가", first["가"], True)])
        rows, _ = board.arrange({**req, "rows": req["next"]["rows"]})
        self.assertEqual([(r["rank"], r["name"], r["show"]) for r in rows], [(1, "나", "12"), (2, "가", "기권")])

    def test_이름이_다르면_돌려보낸다(self):
        from mapcg import board
        with self.assertRaises(request.RequestError) as e:
            board.normalize(self.raw([{"name": "나", "value_text": "12"}, {"name": "다", "value_text": "3"}]))
        self.assertIn("이름과 같아야", " ".join(e.exception.problems))


if __name__ == "__main__":
    unittest.main()
