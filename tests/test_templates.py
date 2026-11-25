"""화면 틀(templates)이 서로 맞물려 있는지 본다 — 같이 쓰는 그리기 코드가 하나라도 빠지면 지도가 빈 화면이 된다."""
import re
import unittest

from mapcg import build

MADE = {"job.js", "map-data.js", "pick.js"}      # 의뢰마다 그때그때 만들어 주는 파일
PAGES = ("route_move.html", "pick.html")         # 지도 화면 · 지점 고르기 화면


class SharedFilesTest(unittest.TestCase):
    def test_같이_쓰는_파일이_다_있다(self):
        for name in build.SHARED:
            self.assertTrue((build.TEMPLATES / name).is_file(), name)

    def test_두_화면이_부르는_파일과_복사_목록이_같다(self):
        # 지도 화면은 무대(재생·내보내기 틀)까지, 지점 고르기 화면은 지도 그리기 코드만 쓴다
        for page, want in zip(PAGES, ({build.STAGE, *build.SHARED}, set(build.SHARED))):
            html = (build.TEMPLATES / page).read_text(encoding="utf-8")
            called = set(re.findall(r'<script src="([^"]+)"', html))
            self.assertEqual(called - MADE, want, f"{page} 가 부르는 파일과 복사 목록이 다릅니다")
        self.assertTrue((build.TEMPLATES / build.STAGE).is_file())


if __name__ == "__main__":
    unittest.main()
