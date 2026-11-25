"""사용자 시험(11-23) — 손그림 의뢰서 'DMC역 → 수색역 / 수색역 → 서부운전면허 / 가양대교 북단 → 일산으로 빠지는 길(각각 5~8초)'.

1 컷 2의 끝('서부운전면허')이 도착지('서부운전면허시험장')와 같은 좌표로 잡혀 경유지로 또 들어가 길이 0 인 구간이 생겼다
2 그 0m 구간 때문에 멀쩡한 경계(수색역)까지 버리고 경로를 똑같이 3등분해 '완성'했다 — 이름표와 자리가 다 어긋났다
3 '각각 5~8초'를 못 옮겨 4초씩 넣었다(의뢰서보다 짧다)
전부 가짜 응답·임시 폴더로 본다(지도 서버·Liner 를 실제로 부르지 않는다).
"""
import unittest

from mapcg import intake, request

from tests.test_review3 import BASE, CutsBase, FakeGeo, A, B, C, cut


class SameAsGoal(CutsBase):
    def setUp(self):
        # '서부'는 도착지 '나'와 같은 곳(10m 안)
        FakeGeo.places = {"가": A, "다": B, "나": C, "서부": {**C, "name": "서부", "lon": C["lon"] - 0.0001}, "가양": {**B, "lat": 37.49}}

    def test_도착지와_같은_곳은_경유지로_또_넣지_않는다(self):
        job, _ = self.make({**BASE, "cuts": [cut("가", "다"), cut("다", "서부")]})
        self.assertEqual(job["state"], "done")
        self.assertEqual(len(FakeGeo.last.calls["route"][0]), 3)        # 가 · 다 · 나 — '서부'를 넣지 않는다
        self.assertFalse(any("똑같이" in w for w in job["warnings"]), job["warnings"])
        # 경계는 '다'(경로의 한가운데) 그대로다
        self.assertAlmostEqual(job["cuts"][0]["to_m"] / job["route"]["distance_m"], 0.5, places=2)

    def test_끊어진_컷이_들어갈_자리가_없으면_똑같이_나누지_않고_멈춘다(self):
        raw = {**BASE, "cuts": [cut("가", "다"), cut("다", "서부"), cut("가양", "나")]}
        with self.assertRaises(request.RequestError) as got:
            self.make(raw)
        text = " ".join(got.exception.problems if hasattr(got.exception, "problems") else [str(got.exception)])
        self.assertIn("컷 3", text)
        self.assertIn("이어지지 않는", text)
        self.assertIn("만들지 않았습니다", text)


class EachRange(unittest.TestCase):
    RAW = [{"from_name": "가", "to_name": "다", "duration_seconds": None}, {"from_name": "다", "to_name": "나", "duration_seconds": None}]

    def test_각각_5에서_8초면_큰_값으로_넣고_알린다(self):
        notes = []
        got = intake._cuts(self.RAW, None, notes, "확인 필요: ‘각각 5~8초’, ‘3종 순서대로 나오게’")
        self.assertEqual([c["duration_seconds"] for c in got], [8.0, 8.0])
        self.assertTrue(any("8초" in n and "5~8" in n for n in notes), notes)

    def test_범위가_없으면_예전처럼_4초(self):
        got = intake._cuts(self.RAW, None, [], "")
        self.assertEqual([c["duration_seconds"] for c in got], [4.0, 4.0])

    def test_적힌_길이는_범위보다_먼저다(self):
        raw = [{**self.RAW[0], "duration_seconds": 6}, self.RAW[1]]
        got = intake._cuts(raw, None, [], "각각 5~8초")
        self.assertEqual([c["duration_seconds"] for c in got], [6.0, 8.0])


if __name__ == "__main__":
    unittest.main()
