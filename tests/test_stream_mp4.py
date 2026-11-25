import io
import json
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from PIL import Image

from mapcg import export


@unittest.skipUnless(shutil.which("ffmpeg") and export.find_ffprobe(), "FFmpeg와 ffprobe 필요")
class StreamMp4Test(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.job = Path(temp.name)
        self.movie = self.job / "카드 퀴즈.mp4"
        pixels = io.BytesIO()
        Image.new("RGB", (32, 16), "#883300").save(pixels, format="PNG")
        self.png = pixels.getvalue()

    def capture(self, attempts):
        """실제 수신 서버와 FFmpeg에 페이지가 보내는 계획·프레임·완료 요청을 재현한다."""
        real_popen = subprocess.Popen
        attempts = iter(attempts)

        def popen(cmd, **kwargs):
            if cmd[0] != "test-browser":
                return real_popen(cmd, **kwargs)
            origin = cmd[-1].partition("/index.html")[0]
            for path, body in next(attempts):
                try:
                    with urllib.request.urlopen(origin + path, data=body, timeout=5):
                        pass
                except urllib.error.HTTPError as error:
                    error.close()
                    break
            return mock.Mock(**{"poll.return_value": 1})

        with mock.patch.object(export, "find_browser", return_value="test-browser"), \
             mock.patch.object(subprocess, "Popen", popen), mock.patch.dict(export._GPU_STATE, broken=False), \
             mock.patch.dict("os.environ", MAPCG_GPU="1"):
            return export.capture_mp4(self.job, self.movie, 2, 2, self.job / "profile")

    def steps(self, indexes, *, done=True):
        result = [("/plan", json.dumps({"frames": 2, "width": 32, "height": 16, "fps": 2}).encode())]
        result.extend((f"/frame/{i}", self.png) for i in indexes)
        if done:
            result.append(("/done", b""))
        return result

    def test_retried_post_feeds_encoder_only_once(self):
        self.assertEqual(self.capture([self.steps([0, 0, 1])]), 2)
        self.assertEqual(export.probe(self.movie), {"width": 32, "height": 16, "fps": 2, "frames": 2})
        self.assertFalse(list(self.job.rglob("*.png")))

    def test_gpu_restart_discards_partial_encoded_prefix(self):
        self.capture([self.steps([0], done=False), self.steps([0, 1])])
        self.assertEqual(export.probe(self.movie)["frames"], 2)

    def test_legacy_scene_uses_its_announced_count_and_can_omit_plan(self):
        steps = self.steps([0, 1, 2])
        steps[0] = ("/plan", b'{"frames":3}')
        self.assertEqual(self.capture([steps]), 3)
        self.assertEqual(export.probe(self.movie)["frames"], 3)
        self.assertEqual(self.capture([self.steps([0, 1])[1:]]), 2)

    def test_incomplete_or_invalid_stream_keeps_previous_export(self):
        self.movie.write_bytes(b"previous successful take")
        corrupt = self.steps([0, 1])
        corrupt[2] = ("/frame/1", b"broken")
        undecodable = self.steps([0, 1])
        undecodable[2] = ("/frame/1", self.png[:8] + self.png[-12:])
        for steps in (self.steps([1, 0]), self.steps([0]), corrupt, undecodable):
            with self.subTest(steps=[step[0] for step in steps]), self.assertRaises(export.ExportError):
                self.capture([steps])
            self.assertEqual(self.movie.read_bytes(), b"previous successful take")
            self.assertFalse(list(self.job.glob(".render-*.mp4")))
