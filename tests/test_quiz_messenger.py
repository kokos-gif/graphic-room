"""카드 확인 → 제작 → MP4 저장에 같은 대화와 의뢰 번호를 쓰는지 검사한다."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mapcg import board, export, intake, quiz, quiz_build, server


class QuizMessenger(unittest.TestCase):
    def test_keeps_read_build_and_export_in_the_same_request(self):
        with tempfile.TemporaryDirectory() as temp:
            desk = server.Desk(Path(temp), liner=True)
            draft = {"kind": quiz.KIND, "title": "장소 퀴즈", "question": "어디일까요?",
                     "cards": [{"kind": "text", "text": "힌트"}, {"kind": "missing", "reveal": "원"}],
                     "first_answer": "한강공원", "final_answer": "강원", "duration_seconds": 15,
                     "first_at": 10.3, "final_at": 13.5}
            read = {"kind": quiz.KIND, "source": "liner", "draft": draft, "notes": []}
            with patch.object(desk, "_spawn", side_effect=lambda fn, *args, **kw: fn(*args)), \
                 patch.object(board, "read", return_value=read):
                desk.send("카드 퀴즈", [], room=desk.room)
                card = desk.msgs[-1]
                self.assertEqual((card["kind"], card["source"]), (quiz.KIND, "liner"))
                rid = card["draft"]["rid"]

                def make_job(req, *, project_dir, out_root, log, offline):
                    self.assertEqual(intake._liner()._ctx.request, rid)
                    folder = out_root / "퀴즈"
                    folder.mkdir(parents=True)
                    (folder / "index.html").touch()
                    return {"dir": folder, "warnings": [], "timing": {"build_seconds": 2}}

                with patch.object(quiz_build, "make_job", side_effect=make_job):
                    desk.make(card["draft"], room=desk.room)
                result = desk.msgs[-1]
                self.assertEqual((result["kind"], result["request"]["rid"]), ("result", rid))
                folder = desk.out / result["job"]
                video = folder / "카드 퀴즈.mp4"
                with patch.object(export, "export", return_value={"mp4": video, "frames": 450}) as render:
                    desk.export(result["id"], False, room=desk.room)
                args, kwargs = render.call_args
                self.assertEqual(args, (folder, 15, 30))
                self.assertEqual((kwargs["name"], kwargs["size"]), (quiz.KIND_LABEL, (1920, 1080)))
                self.assertEqual(result["video"]["path"], str(video))
