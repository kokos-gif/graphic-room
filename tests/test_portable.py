"""심사자 컴퓨터가 Windows 가 아니어도(맥 · 리눅스) 크롬 · ffmpeg · 네비 글꼴을 찾는다 — 그 컴퓨터를 흉내 내어 본다(아무것도 실행하지 않는다)."""
import importlib
import unittest
from pathlib import Path
from unittest import mock

from PIL import ImageFont

from mapcg import export, navi, navi_ui

MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Portable(unittest.TestCase):
    def test_finds_chrome_inside_the_mac_app_bundle(self):
        only = {MAC_CHROME}
        with mock.patch.dict("os.environ", {"MAPCG_BROWSER": ""}), mock.patch.object(export.shutil, "which", return_value=None), \
                mock.patch.object(Path, "is_file", lambda self: str(self).replace("\\", "/") in only):
            self.assertEqual(export.find_browser(), MAC_CHROME)

    def test_says_where_to_look_when_no_browser(self):
        with mock.patch.dict("os.environ", {"MAPCG_BROWSER": ""}), mock.patch.object(export.shutil, "which", return_value=None), \
                mock.patch.object(Path, "is_file", lambda self: False):
            with self.assertRaisesRegex(export.ExportError, "MAPCG_BROWSER"):
                export.find_browser()

    def test_finds_homebrew_ffmpeg_outside_path(self):
        with mock.patch.dict("os.environ", {"MAPCG_FFMPEG": ""}), mock.patch.object(export.shutil, "which", return_value=None), \
                mock.patch.object(Path, "is_file", lambda self: str(self).replace("\\", "/") == "/opt/homebrew/bin/ffmpeg"):
            self.assertEqual(export.find_ffmpeg().replace("\\", "/"), "/opt/homebrew/bin/ffmpeg")

    def test_navi_falls_back_to_the_bundled_font_without_malgun(self):
        real = Path.is_file
        try:
            with mock.patch.object(Path, "is_file", lambda self: False if "Windows" in str(self) else real(self)):
                importlib.reload(navi_ui)
                importlib.reload(navi)
                for path in (navi.FONT_BOLD, navi.FONT_REG, navi_ui.FONT_REGULAR, navi_ui.FONT_BOLD, navi_ui.FONT_LATIN):
                    self.assertTrue(path.endswith("Pretendard-Bold.otf"), path)
                    ImageFont.truetype(path, 20)          # 실제로 열린다
        finally:
            importlib.reload(navi_ui)                    # 이 컴퓨터의 값으로 되돌린다
            importlib.reload(navi)


if __name__ == "__main__":
    unittest.main()
