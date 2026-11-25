"""MAPCG_ROOM — 시연 녹화 때 대화방 · 첨부 · 결과를 다른 폴더에 두고, 원래 대화방은 건드리지 않는다."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import server


class Room(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "프로젝트"
        self.room = Path(self.tmp.name) / "녹화방"

    def desk(self, **env):
        clean = {k: v for k, v in os.environ.items() if k != "MAPCG_ROOM"}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return server.Desk(self.project, liner=False)

    def test_없으면_예전처럼_프로젝트_아래(self):
        d = self.desk()
        self.assertEqual(d.store, self.project / "data" / "messenger")
        self.assertEqual(d.uploads, self.project / "data" / "uploads")
        self.assertEqual(d.out, self.project / "output")

    def test_빈_값은_없는_것과_같다(self):
        self.assertEqual(self.desk(MAPCG_ROOM="  ").store, self.project / "data" / "messenger")

    def test_주면_셋_다_그_폴더_아래(self):
        d = self.desk(MAPCG_ROOM=str(self.room))
        self.assertEqual(d.store, self.room / "messenger")
        self.assertEqual(d.uploads, self.room / "uploads")
        self.assertEqual(d.out, self.room / "output")

    def test_대화를_저장해도_원래_대화방에는_아무것도_생기지_않는다(self):
        d = self.desk(MAPCG_ROOM=str(self.room))
        d._save()
        self.assertTrue((self.room / "messenger" / "대화.json").is_file())
        self.assertFalse((self.project / "data").exists())

    def test_못_만든_의뢰_기록도_녹화방에(self):
        d = self.desk(MAPCG_ROOM=str(self.room))
        d._unmade("의뢰-1", "모르는 종류", "회전목마", [])
        self.assertTrue((self.room / server.Desk.UNMADE_LOG).is_file())
        self.assertFalse((self.project / "data" / server.Desk.UNMADE_LOG).exists())

    def test_못_만든_의뢰_기록은_예전처럼_data_에(self):
        d = self.desk()
        d._unmade("의뢰-1", "모르는 종류", "회전목마", [])
        self.assertTrue((self.project / "data" / server.Desk.UNMADE_LOG).is_file())

    def test_결과_폴더를_직접_주면_그것이_먼저(self):
        clean = {k: v for k, v in os.environ.items() if k != "MAPCG_ROOM"}
        with mock.patch.dict(os.environ, {**clean, "MAPCG_ROOM": str(self.room)}, clear=True):
            d = server.Desk(self.project, Path(self.tmp.name) / "따로", liner=False)
        self.assertEqual(d.out, Path(self.tmp.name) / "따로")
        self.assertEqual(d.store, self.room / "messenger")


if __name__ == "__main__":
    unittest.main()
