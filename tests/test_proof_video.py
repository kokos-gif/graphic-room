"""검수는 완성 MP4의 지정된 프레임을 읽고 작업 폴더에 이미지를 쓰지 않는다."""
import base64
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mapcg import proof


class VideoProof(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg가 필요합니다")
    def test_완성_영상에서_원하는_컷을_읽는다(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "두 컷.mp4"
            cuts = [{"dir": "컷1"}, {"dir": "컷2"}]
            for cut in cuts:
                folder = root / cut["dir"]
                folder.mkdir()
                (folder / "job.json").write_text("{}", encoding="utf-8")
            job = {"kind": "cuts", "cuts": cuts, "request": {"cuts": [{"duration_seconds": 1}] * 2},
                   "delivery": {"mp4": video.name, "frames": 4}}
            (root / "job.json").write_text(json.dumps(job), encoding="utf-8")
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", "16x16", "-r", "2", "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
                           input=(bytes([255, 0, 0]) * 512 + bytes([0, 0, 255]) * 512), check=True)
            items, _ = proof.shots(root)
            self.assertEqual([it["frame"].name for it in items], ["두 컷.mp4 #0", "두 컷.mp4 #2"])
            for item, expected_color in zip(items, (0, 2)):
                url = proof.picture(item["frame"])
                with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
                    pixel = image.getpixel((1, 1))
                    self.assertGreater(pixel[expected_color], 240)
                    self.assertLess(pixel[2 - expected_color], 10)
            self.assertFalse(list(root.rglob("*.png")))
