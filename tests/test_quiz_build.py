"""의뢰별 카드 그림과 공개 시각을 과금 없이 검사한다."""
import base64
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mapcg import intake, quiz_build


class QuizBuild(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.out = self.project / "output"
        self.req = {
            "rid": "test-request", "kind": "card_quiz", "title": '새 의뢰 </script><b>',
            "question": "다음 그림은?", "clue_line": "가 ? 가",
            "first_answer": "첫 대답", "final_answer": "끝 대답", "first_at": 10.3, "final_at": 13.5,
            "duration_seconds": 15, "output": {"width": 1920, "height": 1080, "fps": 30},
            "cards": [{"kind": "image", "subject": "빨간 공", "syllable": "가"},
                      {"kind": "missing", "text": "?", "reveal": "답"},
                      {"kind": "image", "subject": "빨간 공", "syllable": "가"}],
        }
        source = Image.new("RGB", (256, 256), "white")
        source.paste("blue", (48, 44, 208, 212))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        self.data = buffer.getvalue()
        self.reply = {"id": "response", "data": [{"b64_json": base64.b64encode(self.data).decode("ascii")}],
                      "usage": {"cost": .01}}
        self.liner = intake._liner()
        self.post = self.enterContext(patch.object(self.liner, "_post", return_value=self.reply))

    def build(self, **kwargs):
        return quiz_build.make_job(self.req, project_dir=self.project, out_root=self.out, log=lambda _: None, **kwargs)

    def test_one_call_per_distinct_image_and_offline_reuse(self):
        result = self.build()
        self.assertEqual(self.post.call_count, 2)
        back, front = self.post.call_args_list
        self.assertEqual(back.args[0], "/images/generations")
        self.assertEqual(back.args[1]["aspect_ratio"], "2:3")
        self.assertEqual(front.args[1]["aspect_ratio"], "1:1")
        self.assertIn("빨간 공", front.args[1]["prompt"])
        self.assertEqual(front.kwargs["retries"], 0)
        self.assertIn("image_url", back.args[1]["input_references"][0])
        self.assertEqual(result["art"]["card-3"]["source"], "cache")
        self.assertEqual(result["art"]["back"]["response_id"], "response")
        for part in result["art"].values():
            self.assertEqual(part["source_sha256"], hashlib.sha256(self.data).hexdigest())
            self.assertEqual(part["sha256"], hashlib.sha256((result["dir"] / part["file"]).read_bytes()).hexdigest())
            self.assertTrue(Path(part["source_file"]).is_file())
        for name in ("index.html", "style_v5.js", "card_quiz.js", "fonts/Pretendard-Black.otf", "job.json"):
            self.assertTrue((result["dir"] / name).is_file(), name)
        self.assertNotIn(self.req["title"], (result["dir"] / "index.html").read_text("utf-8"))
        packed = (result["dir"] / "job.js").read_text("utf-8")
        self.assertEqual(json.loads(packed.removeprefix("window.JOB=")[:-1])["title"], self.req["title"])

        again = self.build(offline=True)
        self.assertNotEqual(again["dir"], result["dir"])
        self.assertTrue(all(a["source"] == "cache" for a in again["art"].values()))
        self.assertEqual(self.post.call_count, 2)
        self.req["cards"][0]["subject"] = "노란 공"
        with self.assertRaisesRegex(ValueError, "오프라인"):
            self.build(offline=True)
        self.assertEqual(self.post.call_count, 2)

    def test_unknown_submission_is_never_sent_twice(self):
        self.post.side_effect = self.liner.LinerError(0, "라이너 응답을 기다리다 끊겼습니다.")
        with self.assertRaises(self.liner.LinerError):
            self.build()
        self.post.side_effect = None
        with self.assertRaisesRegex(ValueError, "접수 여부"):
            self.build()
        self.assertEqual(self.post.call_count, 1)
        self.assertFalse(list(self.out.iterdir()))

    def test_stale_pending_mark_expires_and_is_requested_again(self):
        """끊긴 요청의 접수 표시는 기다리는 동안에는 다시 보내기를 막고, 한도를 넘기면 풀려서 새로 접수된다(영영 막히지 않는다)."""
        import os
        import time
        self.post.side_effect = self.liner.LinerError(0, "라이너 응답을 기다리다 끊겼습니다.")
        with self.assertRaises(self.liner.LinerError):
            self.build()
        self.post.side_effect = None
        with self.assertRaisesRegex(ValueError, "분 뒤에 다시 보내면"):
            self.build()
        mark, = (self.project / "data" / "quiz_art").rglob("pending")
        old = time.time() - quiz_build.PENDING_SECONDS - 5
        os.utime(mark, (old, old))
        result = self.build()
        self.assertEqual(result["art"]["back"]["source"], "Liner")
        self.assertFalse(list((self.project / "data" / "quiz_art").rglob("pending")))
        self.assertEqual(self.post.call_count, 3)       # 끊긴 요청 1 + 뒷면 다시 1 + 그림 카드 1

    def test_definite_refusal_can_resume_from_completed_images(self):
        self.post.side_effect = [self.reply, self.liner.LinerError(402, "거절")]
        with self.assertRaises(self.liner.LinerError):
            self.build()
        self.post.side_effect = None
        result = self.build()
        self.assertEqual(result["art"]["back"]["source"], "cache")
        self.assertEqual(result["art"]["card-1"]["source"], "Liner")
        self.assertEqual(self.post.call_count, 3)

    def check_scene(self, assertions):
        result = self.build()
        # Stage의 재생만 정지하고, 실제 작업 폴더의 문제와 장면 코드를 그대로 읽는다.
        check = """
            global.window = global;
            require('./job.js');
            const pending = new Promise(() => {});
            global.Stage = () => ({PW: 1920, PH: 1080, ready: pending, run() {}});
            global.Brush = () => ({});
            global.Image = class { decode() {return pending;} };
            require('./card_quiz.js');
            const assert = require('node:assert/strict');
        """
        subprocess.run(["node", "-e", check + assertions], cwd=result["dir"], check=True)

    @unittest.skipUnless(shutil.which("node"), "node 미설치")
    def test_render_state_on_every_export_frame(self):
        self.check_scene("""
            for (let f = 0; f < 450; f++) {
                const t = f / 30, s = quizState(t);
                assert.equal(s.finalAnswer, t >= 13.5 ? JOB.final_answer : '');
                assert.equal(s.firstAnswer, t >= 10.3 && t < 13.5 ? JOB.first_answer : '');
                assert.equal(s.cards[1].resolved, t >= 13.5);
            }
            assert.equal(quizState(9).clueText, JOB.clue_line);
            assert.ok(quizState(0).cards.every(c => !c.front));
            assert.ok(quizState(9).cards.every(c => c.front));
        """)

    @unittest.skipUnless(shutil.which("node"), "node 미설치")
    def test_one_reveal_with_standalone_hint_at_24_fps(self):
        self.req.update(first_answer="", first_at=None, final_answer="수박", final_at=6, duration_seconds=8,
                        output={"width": 1280, "height": 720, "fps": 24}, clue_line="여름에 먹는 과일입니다.")
        self.check_scene("""
            for (let f = 0; f < 192; f++) {
                const s = quizState(f / 24);
                assert.equal(s.phase, f < 144 ? 'clues' : 'final');
                assert.equal(s.firstAnswer, '');
                assert.equal(s.finalAnswer, f < 144 ? '' : '수박');
                assert.equal(s.clueText, JOB.clue_line);
                assert.equal(s.cards[1].resolved, f >= 144);
            }
            assert.ok(quizState(0).cards.every(c => !c.front));
            assert.ok(quizState(5.5).cards.every(c => c.front));
        """)

    @unittest.skipUnless(shutil.which("node"), "node 미설치")
    def test_audit_reports_squeezed_answer(self):
        """답 판에 25% 넘게 눌러 넣는 글은 검수판의 자동 검사에 적힌다(짧은 단서 · 1차 답은 적지 않는다)."""
        self.req.update(final_answer="가상 백화점 꼭대기 하늘정원 전망대 매표소 앞")
        result = self.build()
        script = """
            global.window = global;
            require('./job.js');
            const pending = new Promise(() => {});
            const X = {font: '', measureText(s) { return {width: Array.from(s).length * +/(\\d+)px/.exec(this.font)[1]}; }};
            let audit = null;
            global.BOARDSKIN = {font: 'test'};
            global.Stage = () => ({PW: 1920, PH: 1080, X, ready: pending, run(draw, ready, a) { audit = a; }});
            global.Brush = () => ({});
            global.Image = class { decode() {return pending;} };
            require('./card_quiz.js');
            const assert = require('node:assert/strict');
            const got = audit();
            assert.equal(got.length, 1, JSON.stringify(got));
            assert.match(got[0], /^정답: 글자를 \\d+% 눌러야 들어감 — 가상 백화점/);
        """
        subprocess.run(["node", "-e", script], cwd=result["dir"], check=True)
