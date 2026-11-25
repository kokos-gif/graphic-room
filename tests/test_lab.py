"""연구실 도구 — 받은 코드의 글자 검사 · 격리 넣기 · 물음 짜기 · 회차 기록(가짜 응답으로 본다. Liner 도 브라우저도 부르지 않는다)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from mapcg import export

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("lab", ROOT / "tools" / "lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)

SCRIPTS = "".join(f'<script src="{s}"></script>' for s in lab.ALLOWED_SRC)
GOOD = f"<!doctype html>\n<html><head><meta charset=\"utf-8\"><style>body{{margin:0}}</style></head><body><canvas id=\"c\"></canvas>{SCRIPTS}<script>const J = window.JOB;</script></body></html>"
TASK = {"name": "대진표", "brief": "- 세 단을 왼쪽에서 오른쪽으로 놓는다.", "job": {"title": "8강 대진표", "rounds": [[{"a": "가람", "b": "나래", "winner": "a"}]]},
        "variants": [{"label": "긴 이름", "job": {"title": "8강 대진표", "rounds": [[{"a": "가람이네", "b": "나래", "winner": "b"}]]}}], "duration_seconds": 10}
BASE_JOB = {"output": {"width": 1920, "height": 1080, "fps": 30}, "layout_rect": [0, 0, 1, 1]}


class Scan(unittest.TestCase):
    def test_약속을_지킨_코드는_통과한다(self):
        self.assertEqual(lab.scan(GOOD), [])

    def test_지금_쓰는_화면_틀도_통과한다(self):
        """검사가 우리 코드조차 못 통과시킬 만큼 빡빡하면 모델에게 본보기대로 짜라고 할 수 없다(화풍 스크립트는 작업 폴더를 만들 때 끼워 넣는다)."""
        for name in ("plan_board.html", "rank_board.html", "building_move.html"):
            code = (ROOT / "mapcg" / "templates" / name).read_text(encoding="utf-8")
            self.assertEqual(lab.scan(code), ["빠진 스크립트 'style_v5.js'"], name)

    def test_바깥_접속_시계_난수_우회_수법을_잡는다(self):
        bad = {"fetch(": "fetch('/x')", "https://": "const u = 'https://example.com'", "Math.random": "Math.random()", "Date.now": "Date.now()",
               "new Date": "new Date()", "setTimeout": "setTimeout(f, 9)", "requestAnimationFrame": "requestAnimationFrame(f)", "eval(": "eval('1')",
               "new Function": "new Function('return 1')", "window[": "window['fe' + 'tch']('/x')", "globalThis": "globalThis.fetch", "constructor": "[].map.constructor('x')()",
               "fromCharCode": "String.fromCharCode(102)", "atob(": "atob('ZmV0Y2g=')", "\\x": "'\\x66etch'", "location.": "location.href = '/'", "<img": "<img src='a.png'>",
               "innerHTML": "document.body.innerHTML = ''", "localStorage": "localStorage.a = 1", "import(": "import('./x.js')",
               "defaultView": "document.defaultView['fe' + 'tch']('//example.com/x')", "ownerDocument": "X.canvas.ownerDocument",
               "new Image": "const im = new Image(); im.src = '//example.com/y.png'"}
        for word, line in bad.items():
            found = lab.scan(GOOD.replace("const J = window.JOB;", "const J = window.JOB; " + line))
            self.assertTrue(any(repr(word) in f for f in found), (word, found))

    def test_다른_스크립트를_부르거나_빠뜨리면_잡는다(self):
        self.assertIn("허용하지 않은 스크립트 'evil.js'", lab.scan(GOOD.replace("</body>", '<script src="evil.js"></script></body>')))
        self.assertIn("빠진 스크립트 'brush.js'", lab.scan(GOOD.replace('<script src="brush.js"></script>', "")))
        self.assertTrue(any("on…=" in f for f in lab.scan(GOOD.replace('<canvas id="c">', '<canvas id="c" onclick="go()">'))))

    def test_머리보다_앞에_둔_스크립트를_잡는다(self):
        """<head> 앞의 스크립트는 예전에는 내용 보안 정책보다 먼저 돌았다 — 검사에서도 걸러 둔다."""
        early = GOOD.replace("<html><head>", "<html><script>const a = 1;</script><head>")
        self.assertIn("<head> 보다 앞에 있는 스크립트", lab.scan(early))
        self.assertIn("<head> 보다 앞에 있는 스크립트", lab.scan(GOOD.replace("<head>", "").replace("</head>", "")))
        self.assertNotIn("<head> 보다 앞에 있는 스크립트", lab.scan(GOOD))

    def test_전문이_아니면_잡는다(self):
        self.assertTrue(any("전문이 아니다" in f for f in lab.scan("설명입니다.\n" + GOOD)))
        self.assertTrue(any("전문이 아니다" in f for f in lab.scan(GOOD[:-7])))

    def test_답에서_코드만_떼어_낸다(self):
        self.assertEqual(lab.strip_fence("```html\n" + GOOD + "\n```"), GOOD)
        self.assertEqual(lab.strip_fence("다음과 같습니다.\n" + GOOD + "\n이상입니다."), GOOD)
        self.assertEqual(lab.strip_fence(GOOD), GOOD)


class Seal(unittest.TestCase):
    def test_실행하는_사본의_머리에_내용_보안_정책을_넣는다(self):
        sealed = lab.seal(GOOD)
        self.assertTrue(sealed.startswith("<!doctype html>\n<meta http-equiv=\"Content-Security-Policy\""))
        # 정책은 어떤 스크립트보다도 앞에 있어야 한다 — <head> 앞에 둔 스크립트가 정책 없이 먼저 돌지 못하게
        early = lab.seal(GOOD.replace("<html><head>", "<html><script>const a = 1;</script><head>"))
        self.assertLess(early.index("Content-Security-Policy"), early.index("<script"))
        self.assertIn("connect-src 'self' data: blob:;", sealed)       # 바깥은 없다. data · blob 은 무대가 캔버스 그림을 읽는 데 쓴다(메모리 안)
        self.assertIn("default-src 'none'", sealed)
        self.assertEqual(sealed.replace(f'\n<meta http-equiv="Content-Security-Policy" content="{lab.CSP}">', ""), GOOD)      # 그 밖의 코드는 그대로
        self.assertTrue(lab.seal("<!doctype html><canvas></canvas>").startswith("<!doctype html>\n<meta http-equiv"))
        # 앞머리에 BOM · 주석이 있어도 정책 태그는 <!doctype> 뒤에 — 앞에 가면 doctype 이 무시돼 호환 모드로 그려진다
        for lead in ("﻿", "<!-- 머리말\n두 줄 -->\n", "﻿ \n<!-- a --><!-- b -->"):
            got = lab.seal(lead + "<!DOCTYPE html><canvas></canvas>")
            self.assertTrue(got.startswith(lead + "<!DOCTYPE html>\n<meta http-equiv"), repr(got[:60]))

    def test_격리한_브라우저_옵션은_쓰고_나면_되돌린다(self):
        before = (list(export.CPU_FLAGS), list(export.GPU_FLAGS))
        with lab.sealed_browser():
            self.assertTrue(all(f in export.CPU_FLAGS and f in export.GPU_FLAGS for f in lab.NET_OFF))
        self.assertEqual((export.CPU_FLAGS, export.GPU_FLAGS), before)
        with self.assertRaises(RuntimeError), lab.sealed_browser():
            raise RuntimeError
        self.assertEqual((export.CPU_FLAGS, export.GPU_FLAGS), before)


class Ask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.job = lab.full_job(TASK["job"], BASE_JOB, 10)

    def chat(self, text, model="claude-fable-5.2", finish="stop"):
        calls = []

        def chat(messages, **options):
            calls.append((messages, options))
            return {"model": model, "usage": {"total_tokens": 1234}, "choices": [{"message": {"content": text}, "finish_reason": finish}]}
        chat.calls = calls
        return chat

    def test_과제_파일은_이름_요구_자료가_있어야_한다(self):
        p = self.dir / "과제.json"
        p.write_text(json.dumps({**TASK, "name": "대진표 ../../x"}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(lab.load_task(p)["name"], "대진표x")            # 폴더 이름으로 쓰니 글자 · 숫자만 남긴다
        p.write_text(json.dumps({"name": "x", "brief": "", "job": {"title": "t"}}), encoding="utf-8")
        with self.assertRaises(lab.LabError):
            lab.load_task(p)

    def test_첫_물음에_무대_약속_본보기_자료_요구가_다_들어간다(self):
        text = lab.first_prompt(TASK, self.job)
        for part in ("[무대 약속", "ST.run(content", "plan_board.html 전문", "8강 대진표", "세 단을 왼쪽에서 오른쪽으로", "index.html 전문 하나만", '"layout_rect"'):
            self.assertIn(part, text)

    def test_받은_코드를_적고_검사_결과를_기록한다(self):
        chat = self.chat("```html\n" + GOOD + "\n```")
        info = lab.ask(self.dir, TASK, self.job, 1, chat)
        self.assertEqual((info["model"], info["scan"], info["round"]), ("claude-fable-5.2", [], 1))
        self.assertEqual((self.dir / "회차1" / "받은코드.html").read_text(encoding="utf-8"), GOOD)
        options = chat.calls[0][1]
        self.assertEqual((options["routing_strategy"], options["retries"]), ("quality", 0))       # 끊긴 긴 요청을 다시 보내 두 번 치르지 않는다

    def test_잘린_답과_나쁜_코드는_실행_전에_걸린다(self):
        self.assertIn("답이 길이 한도에서 잘렸다", lab.ask(self.dir, TASK, self.job, 1, self.chat(GOOD, finish="length"))["scan"])
        bad = lab.ask(self.dir, TASK, self.job, 1, self.chat(GOOD.replace("const J", "fetch('/x'); const J")))
        self.assertTrue(any("fetch(" in f for f in bad["scan"]))

    def test_고칠_때는_앞_코드와_고칠_점과_그_화면을_같이_보낸다(self):
        lab.ask(self.dir, TASK, self.job, 1, self.chat(GOOD))
        Image.new("RGB", (960, 540), (255, 245, 225)).save(self.dir / "회차1" / "화면_끝.png")
        (self.dir / "회차2").mkdir()
        (self.dir / "회차2" / "고칠점.txt").write_text("이름 글자를 키운다.", encoding="utf-8")
        msgs = lab.messages_for(self.dir, TASK, self.job, 2)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[1]["content"], GOOD)
        text, picture = msgs[2]["content"]
        self.assertIn("이름 글자를 키운다.", text["text"])
        self.assertIn("그대로 둘 것", text["text"])
        self.assertTrue(picture["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_실행_폴더에는_정책을_넣은_사본과_자료가_들어간다(self):
        lab.ask(self.dir, TASK, self.job, 1, self.chat(GOOD))
        base = self.dir / "기준폴더"
        base.mkdir()
        (base / "stage.js").write_text("// 무대", encoding="utf-8")
        run = lab.stage(self.dir / "회차1", base, self.job)
        self.assertIn("Content-Security-Policy", (run / "index.html").read_text(encoding="utf-8"))
        self.assertNotIn("Content-Security-Policy", (self.dir / "회차1" / "받은코드.html").read_text(encoding="utf-8"))      # 받은 원문은 그대로 둔다
        self.assertEqual(json.loads((run / "job.js").read_text(encoding="utf-8")[len("window.JOB="):-1])["title"], "8강 대진표")
        self.assertTrue((run / "stage.js").is_file())

    def test_자료에서_화면에_나와야_할_글자를_뽑는다(self):
        self.assertEqual(lab.words(TASK["job"]), ["8강 대진표", "가람", "나래"])       # winner 의 'a' 같은 코드 값은 뺀다

    def test_호출이_실패해_고칠_점만_남은_폴더는_회차로_치지_않는다(self):
        """fix 가 Liner 호출에서 실패하면 고칠점.txt 만 든 폴더가 남는다. 그 폴더를 회차로 세면 다음 fix 가 없는 코드를 읽으려다 과제가 막힌다."""
        lab.ask(self.dir, TASK, self.job, 1, self.chat(GOOD))
        (self.dir / "회차2").mkdir()
        (self.dir / "회차2" / "고칠점.txt").write_text("실패한 물음", encoding="utf-8")
        self.assertEqual(lab.rounds(self.dir), [1])
        seen = {}
        with mock.patch.object(lab, "LAB", self.dir.parent), mock.patch.object(lab, "run_round", lambda task, n, **k: seen.update(n=n)), \
                mock.patch.object(lab, "load_task", return_value=TASK):
            self.assertEqual(lab.main(["fix", self.dir.name, "이름 글자를 키운다."]), 0)       # 2회차 번호를 다시 쓴다
            self.assertEqual(seen["n"], 2)
            self.assertEqual((self.dir / "회차2" / "고칠점.txt").read_text(encoding="utf-8"), "이름 글자를 키운다.")
            self.assertEqual(lab.main(["shoot", self.dir.name]), 0)                          # 다시 찍기는 코드를 받은 마지막 회차로
            self.assertEqual(seen["n"], 1)
            self.assertEqual(lab.main(["shoot", self.dir.name, "2"]), 1)                     # 코드가 없는 회차는 거절

    def test_격리를_확인한_기록이_없으면_먼저_점검하고_안_막히면_실행하지_않는다(self):
        mark = {"browser": "C:/x/chrome.exe", "size": 1, "mtime": 2}
        with mock.patch.object(lab, "LAB", self.dir), mock.patch.object(lab, "_browser_mark", return_value=mark):
            with mock.patch.object(lab, "selftest", return_value=(False, True)), self.assertRaises(lab.LabError):
                lab.ensure_sealed(lambda *a: None)
            with mock.patch.object(lab, "selftest", return_value=(True, False)) as offline:      # 인터넷이 안 되면 통과로 적지 않는다
                lab.ensure_sealed(lambda *a: None)
                lab.ensure_sealed(lambda *a: None)
                self.assertEqual(offline.call_count, 2)
            with mock.patch.object(lab, "selftest", return_value=(True, True)) as checked:
                lab.ensure_sealed(lambda *a: None)
                lab.ensure_sealed(lambda *a: None)                                              # 통과 기록이 있으면 다시 점검하지 않는다
                self.assertEqual(checked.call_count, 1)
            with mock.patch.object(lab, "_browser_mark", return_value={**mark, "mtime": 3}), \
                    mock.patch.object(lab, "selftest", return_value=(True, True)) as changed:      # 브라우저가 바뀌면 다시 점검한다
                lab.ensure_sealed(lambda *a: None)
                self.assertEqual(changed.call_count, 1)

    def test_걸린_코드는_찍지_않고_기록만_남긴다(self):
        with mock.patch.object(lab, "LAB", self.dir), mock.patch.object(lab, "base_folder", return_value=(self.dir, BASE_JOB)), \
                mock.patch.object(lab, "shoot") as shoot:
            info = lab.run_round(lab.load_task(self._task_file()), 1, fresh=True, chat=self.chat(GOOD.replace("const J", "eval('1'); const J")), log=lambda *a: None)
        shoot.assert_not_called()
        self.assertEqual(info["errors"], ["글자 검사 불합격으로 실행하지 않음"])
        self.assertTrue((self.dir / "대진표" / "보기.html").is_file())
        self.assertNotIn("<script", (self.dir / "대진표" / "보기.html").read_text(encoding="utf-8"))

    def _task_file(self):
        p = self.dir / "과제파일.json"
        p.write_text(json.dumps(TASK, ensure_ascii=False), encoding="utf-8")
        return p


if __name__ == "__main__":
    unittest.main()
