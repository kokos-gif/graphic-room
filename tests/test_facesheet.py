"""출연진 시트에서 얼굴 칸 오리기 — 바탕을 지운(투명) 시트는 머리 전체가 동그라미에 들어가게, 바탕은 투명한 채로."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from mapcg import facesheet


def sheet(tmp: Path, transparent: bool) -> Path:
    """2줄 × 3칸, 칸마다 가로 120 · 세로 160 타원 머리. 첫 칸에는 지우다 남은 부스러기도 하나."""
    im = Image.new("RGBA", (600, 400), (0, 0, 0, 0) if transparent else (30, 30, 40, 255))
    d = ImageDraw.Draw(im)
    for r in range(2):
        for c in range(3):
            x, y = c * 200 + 100, r * 200 + 100
            d.ellipse((x - 60, y - 80 + 10, x + 60, y + 80 - 10), fill=(230, 190, 170, 255))
    d.rectangle((10, 10, 14, 14), fill=(255, 255, 255, 255))
    p = tmp / "시트.png"
    im.save(p)
    return p


class Sheet(unittest.TestCase):
    def test_transparent_sheet_keeps_whole_head_and_clear_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = facesheet.crop(sheet(Path(tmp), True), 2, 3, [(1, 1), (2, 2)])
        self.assertEqual(len(out), 2)
        for f in out:
            self.assertEqual((f.mode, f.size), ("RGBA", (facesheet.SIDE, facesheet.SIDE)))
            self.assertEqual(f.getpixel((4, 4))[3], 0)                     # 귀퉁이는 투명 — 바탕 번짐 없음
            a = f.getchannel("A").point(lambda v: 255 if v > 128 else 0)
            x0, y0, x1, y1 = a.getbbox()
            self.assertGreater(y1 - y0, facesheet.SIDE * .9)               # 머리 세로가 거의 한 변을 채우고
            self.assertTrue(y0 > 0 and y1 < facesheet.SIDE)                # 정수리 · 턱이 잘리지 않는다
            self.assertAlmostEqual((x0 + x1) / 2, facesheet.SIDE / 2, delta=8)

    def test_opaque_sheet_still_crops_and_bad_cell_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = sheet(Path(tmp), False)
            out = facesheet.crop(p, 2, 3, [(1, 2)])
            self.assertEqual(out[0].size, (facesheet.SIDE, facesheet.SIDE))
            with self.assertRaises(ValueError):
                facesheet.crop(p, 2, 3, [(3, 1)])


if __name__ == "__main__":
    unittest.main()
