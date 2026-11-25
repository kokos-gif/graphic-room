"""첨부 가운데 읽지 않은 쪽을 조용히 넘기지 않는다."""
import unittest

from mapcg import server


class CoverageTest(unittest.TestCase):
    def test_다_읽었으면_아무_말도_하지_않는다(self):
        self.assertEqual(server.coverage([{"name": "의뢰서.pdf", "sent": 3, "total": 3}]), "")
        self.assertEqual(server.coverage([]), "")

    def test_그림으로_바꾸지_못한_첨부는_다_읽은_것처럼_넘기지_않는다(self):
        # 화면이 PDF 를 그림으로 바꾸지 못하면 쪽수도 모른다(sent 0 · total 0) — 그래도 '못 읽었다'고 적는다
        note = server.coverage([{"name": "의뢰서.pdf", "sent": 3, "total": 3}, {"name": "깨진.pdf", "sent": 0, "total": 0, "failed": True}])
        self.assertIn("그림으로 바꾸지 못해 읽지 못한 첨부: 깨진.pdf", note)
        self.assertEqual(server.coverage([{"name": "보관.zip", "sent": 0, "total": 0, "failed": False}]), "")     # 보관만 하는 첨부는 실패가 아니다

    def test_세_쪽짜리_둘이면_둘째_파일의_뒤쪽을_못_읽었다고_적는다(self):
        note = server.coverage([{"name": "가.pdf", "sent": 3, "total": 3}, {"name": "나.pdf", "sent": 3, "total": 3}])
        self.assertIn("읽은 쪽: 가.pdf 1~3쪽, 나.pdf 1쪽", note)
        self.assertIn("읽지 않은 쪽: 나.pdf 2~3쪽", note)
        self.assertIn("판독 감사도 읽은 쪽만", note)

    def test_그림으로_바꾸지_않은_뒤쪽도_적는다(self):
        self.assertIn("읽지 않은 쪽: 긴.pdf 4~12쪽", server.coverage([{"name": "긴.pdf", "sent": 3, "total": 12}]))


if __name__ == "__main__":
    unittest.main()
