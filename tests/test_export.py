"""납품 규격 확인과 납품 기록 — 브라우저·ffmpeg 없이 볼 수 있는 부분만."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mapcg import export

WANT = {"width": 960, "height": 540, "fps": 30, "frames": 240}


class SpecTest(unittest.TestCase):
    def test_같으면_ok_이고_잰_값을_한_줄로_보여_준다(self):
        s = export.check_spec(WANT, dict(WANT))
        self.assertEqual((s["state"], s["diffs"]), ("ok", []))
        self.assertEqual(export.spec_line(s), "규격 확인: 960×540 · 30fps · 240프레임(8.00초) — 의뢰와 같음")

    def test_다르면_무엇이_다른지_적는다(self):
        s = export.check_spec(WANT, {**WANT, "width": 962, "frames": 239})
        self.assertEqual(s["state"], "differs")
        self.assertEqual(s["diffs"], ["가로 960 → 962", "프레임 수 240 → 239"])
        self.assertIn("규격이 의뢰와 다릅니다", export.spec_line(s))

    def test_재지_못한_것을_통과로_적지_않는다(self):
        s = export.check_spec(WANT, None)
        self.assertEqual(s["state"], "unknown")
        self.assertIn("재지 못했습니다", export.spec_line(s))
        self.assertNotIn("같음", export.spec_line(s))


class ProbeTest(unittest.TestCase):
    @staticmethod
    def answering(stdout):
        return lambda cmd, **kw: SimpleNamespace(stdout=stdout, returncode=0)

    def test_ffprobe_의_답을_숫자로_바꾼다(self):
        out = json.dumps({"streams": [{"width": 960, "height": 540, "r_frame_rate": "30/1", "nb_read_frames": "240"}]})
        self.assertEqual(export.probe(Path("x.mp4"), exe="ffprobe", run=self.answering(out)), WANT)

    def test_읽지_못한_답은_None(self):
        for out in ("", "{}", json.dumps({"streams": []}), json.dumps({"streams": [{"width": 960}]})):
            self.assertIsNone(export.probe(Path("x.mp4"), exe="ffprobe", run=self.answering(out)), out)


class RecordTest(unittest.TestCase):
    def test_납품_기록을_작업_기록에_남기고_원래_기록은_둔다(self):
        with tempfile.TemporaryDirectory() as d:
            job = Path(d)
            (job / "job.json").write_text(json.dumps({"request": {"layout": "full"}}), encoding="utf-8")
            export._record(job, {"mp4": job / "지도.mp4", "frames": 240, "seconds": 12.3, "spec": export.check_spec(WANT, dict(WANT))})
            rec = json.loads((job / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["request"], {"layout": "full"})
            got = rec["delivery"]
            self.assertEqual((got["mp4"], got["frames"], got["seconds"], got["spec"]["state"]), ("지도.mp4", 240, 12.3, "ok"))

    def test_작업_기록이_없으면_건너뛴다(self):
        with tempfile.TemporaryDirectory() as d:
            export._record(Path(d), {"mp4": Path(d) / "지도.mp4", "frames": 1, "seconds": 0.1})
            self.assertFalse((Path(d) / "job.json").exists())


if __name__ == "__main__":
    unittest.main()
