"""앱 코드리뷰(a27d633)의 서버 · 접수 · 화면 쪽 회귀 시험 — 02 대화 유실 · 07 대화 간 첨부 혼입 · 09 확인 질문 유실.
임시 폴더와 가짜 응답만 쓴다(Liner 를 부르지 않는다). 07 은 실제 화면 코드(web/app.js)를 크롬으로 띄워 본다 — 크롬이 없으면 건너뛴다."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, export, server
from mapcg import director as D

ROOT = Path(__file__).resolve().parent.parent
PIC = "data:image/jpeg;base64,/9j/2Q=="      # 가장 짧은 그림 자료(읽지 않고 실어 보내기만 한다)


def call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def reply(*calls):
    return {"model": "시험", "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": list(calls)}, "finish_reason": "tool_calls"}]}


class FakeServer:
    server_address = ("127.0.0.1", 8799)

    def serve_forever(self):
        pass

    def server_close(self):
        pass


class 대화방은_한_앱만_쓴다(unittest.TestCase):
    """02 — 같은 대화 저장소를 두 앱이 열면 나중에 쓴 쪽이 먼저 받은 대화를 지운다. 두 번째 앱은 켜지지 않아야 한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = server.store_of(self.root)
        env = mock.patch.dict("os.environ", {"MAPCG_ROOM": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_같은_저장소를_두_번_잡지_못한다(self):
        first, _ = server.hold_store(self.store)
        self.assertIsNotNone(first)
        second, _ = server.hold_store(self.store)
        self.assertIsNone(second)
        first.close()
        again, _ = server.hold_store(self.store)       # 먼저 연 앱이 끝나면 다시 잡힌다
        self.assertIsNotNone(again)
        again.close()

    def test_두_번째_앱은_대화를_열지도_덮어쓰지도_않는다(self):
        first = server.Desk(self.root, offline=True)
        first.add("requester", "text", text="첫 앱이 받은 의뢰", files=[])
        before = (self.store / "대화.json").read_bytes()
        held, _ = server.hold_store(self.store)         # 첫 앱이 켜져 있는 동안
        (self.store / "앱주소.txt").write_text("http://127.0.0.1:8765/", encoding="utf-8")
        said = io.StringIO()
        with mock.patch.object(server, "Desk", side_effect=AssertionError("두 번째 앱이 대화를 열었다")), contextlib.redirect_stdout(said):
            code = server.serve(self.root, port=8766, offline=True, open_browser=False)
        held.close()
        self.assertEqual(code, 6)
        self.assertIn("http://127.0.0.1:8765/", said.getvalue())                   # 먼저 연 앱의 주소를 알려 준다
        self.assertEqual((self.store / "대화.json").read_bytes(), before)
        self.assertEqual(server.Desk(self.root, offline=True).msgs[0]["text"], "첫 앱이 받은 의뢰")      # 다시 켜도 남아 있다

    def test_앱이_끝나면_다음_앱이_열_수_있다(self):
        with mock.patch.object(server, "make_server", return_value=FakeServer()), mock.patch.object(server, "director_on", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.serve(self.root, port=8799, offline=True, open_browser=False), 0)
        self.assertEqual((self.store / "앱주소.txt").read_text(encoding="utf-8"), "http://127.0.0.1:8799/")
        held, _ = server.hold_store(self.store)
        self.assertIsNotNone(held)
        held.close()

    def test_먼저_연_앱이_비정상으로_꺼지면_다시_열_수_있다(self):
        code = ("import sys, time; from pathlib import Path; from mapcg import server; "
                "f, _ = server.hold_store(Path(sys.argv[1])); print('잡음' if f else '못 잡음', flush=True); time.sleep(60)")
        # 자식은 한글을 콘솔 글자표(한국어 윈도우는 cp949)로 찍는다 — UTF-8 로 찍게 하고, 못 읽는 글자가 있어도 시험이 그 때문에 죽지 않게 한다
        proc = subprocess.Popen([sys.executable, "-c", code, str(self.store)], cwd=ROOT, stdout=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        try:
            self.assertEqual(proc.stdout.readline().strip(), "잡음")
            self.assertIsNone(server.hold_store(self.store)[0])        # 다른 프로세스가 쥐고 있다
        finally:
            proc.kill()                                                # 잠금을 풀지 못하고 죽는다
            proc.wait(timeout=10)
            proc.stdout.close()
        held = None
        for _ in range(40):                                            # 운영체제가 잠금을 거두는 데 잠깐 걸릴 수 있다
            held, _ = server.hold_store(self.store)
            if held:
                break
            time.sleep(0.05)
        self.assertIsNotNone(held)
        held.close()


ASKED = {"ok": False, "waiting": True, "why": "확인할 것이 있어 일을 나누지 않았습니다", "summary": "", "assign": None, "custom": [],
         "ask": ["몇 초짜리로 만들까요?"], "applied": [], "searches": [], "seconds": 1.0}
MAPPED = {"ok": True, "waiting": False, "why": "", "summary": "지도", "ask": [], "seconds": 1.0, "applied": [], "searches": [], "custom": [],
          "assign": {"specialist": "지도", "prefer": "", "part": "지도", "rules": []}}


class 확인_질문만_온_의뢰(unittest.TestCase):
    """09 — 접수(CG Director)가 일을 나누지 않고 질문만 했을 때: 질문을 보이고 답을 기다린다. 답 없이 다른 길로 읽지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def desk(self, director, analyze=None):
        d = server.Desk(self.root, liner=True, audit=mock.Mock(return_value={"state": "skipped"}),
                        analyze=analyze or mock.Mock(side_effect=AssertionError("일반 판독으로 넘어가면 안 된다")), director=director)
        sync = mock.patch.object(d, "_spawn", side_effect=lambda fn, *args, **k: fn(*args))
        sync.start()
        self.addCleanup(sync.stop)
        return d

    def test_접수가_질문과_종료만_주면_답변_대기다(self):
        chat = mock.Mock(return_value=reply(call("ask", {"questions": ["몇 초짜리로 만들까요?"]}, "c1"), call("finish", {"summary": ""}, "c2")))
        got = D.run("", [PIC], chat=chat, log=lambda *a: None)
        self.assertFalse(got["ok"])
        self.assertTrue(got["waiting"])
        self.assertEqual(got["ask"], ["몇 초짜리로 만들까요?"])
        done = mock.Mock(return_value=reply(call("finish", {"summary": ""}, "c1")))      # 질문도 일도 없으면 대기가 아니다(예전 순서로 읽는다)
        self.assertFalse(D.run("안녕하세요", chat=done, log=lambda *a: None)["waiting"])

    def test_질문은_한_번_보이고_일반_판독은_부르지_않는다(self):
        d = self.desk(mock.Mock(return_value=ASKED))
        with mock.patch.object(board, "read", side_effect=AssertionError("순위판 판별로 넘어가면 안 된다")) as read:
            d.send("", [{"name": "의뢰지.jpg", "data": PIC, "images": [PIC], "state": "read"}], room=d.room)
        read.assert_not_called()
        d.analyze.assert_not_called()
        cards = [m for m in d.msgs if m["kind"] == "director"]
        self.assertEqual(len(cards), 1)
        self.assertEqual((cards[0]["ask"], cards[0]["waiting"]), (["몇 초짜리로 만들까요?"], True))
        progress = next(m for m in d.msgs if m["kind"] == "progress")
        self.assertEqual(progress["state"], "ok")                                   # 실패가 아니다
        self.assertIn("답을 보내 주시면", progress["title"])

    def test_답을_보내면_원래_의뢰에_붙여_다시_접수한다(self):
        director = mock.Mock(side_effect=[ASKED, MAPPED])
        stop = mock.Mock(side_effect=RuntimeError("시험: 지도 읽기에서 멈춤"))
        d = self.desk(director, analyze=stop)
        with mock.patch.object(board, "read") as read:
            d.send("홍대에서 여의도로 가는 지도", [{"name": "의뢰지.jpg", "data": PIC, "images": [PIC], "state": "read"}], room=d.room)
            self.assertEqual(d.pending["ask"], ["몇 초짜리로 만들까요?"])
            d.send("8초입니다", [], room=d.room)
        read.assert_not_called()
        text, images = director.call_args.args[:2]
        for part in ("홍대에서 여의도로 가는 지도", "몇 초짜리로 만들까요?", "8초입니다"):
            self.assertIn(part, text)                                               # 원래 의뢰 · 질문 · 답이 함께 간다
        self.assertEqual(images, [PIC])                                             # 원래 의뢰의 그림도
        self.assertIsNone(d.pending)
        self.assertEqual(stop.call_args.args[0], text)                              # 나눈 대로(지도) 같은 글로 읽는다
        self.assertEqual([m["text"] for m in d.msgs if m["who"] == "requester"], ["홍대에서 여의도로 가는 지도", "8초입니다"])     # 대화에는 답만 보인다

    def test_새_대화를_시작하면_기다리던_의뢰를_붙이지_않는다(self):
        director = mock.Mock(side_effect=[ASKED, ASKED])
        d = self.desk(director)
        with mock.patch.object(board, "read"):
            d.send("홍대에서 여의도로 가는 지도", [], room=d.room)
            d.reset(d.room)
            self.assertIsNone(d.pending)
            d.send("강남역 순위판", [], room=d.room)
        self.assertEqual(director.call_args.args[0], "강남역 순위판")


def browser():
    try:
        return export.find_browser()
    except Exception:
        return None


# 화면을 띄우는 데 녹화 도구(tools/demo_rec.py)의 크롬 연결을 빌려 쓴다. 이 도구는 제출 묶음에 없다(.gitattributes 의 export-ignore) —
# 묶음을 풀어 돌릴 때는 오류로 끝나지 않고 건너뛴다
HAS_REC = (ROOT / "tools" / "demo_rec.py").is_file()


@unittest.skipUnless(HAS_REC and browser(), "크롬 · 엣지나 화면을 띄우는 도구(tools/demo_rec.py — 제출 묶음에는 없음)가 없어 화면 코드를 띄우지 못함")
class 입력칸은_대화_하나에_묶인다(unittest.TestCase):
    """07 — 다른 화면이 새 대화를 시작하면, 이 화면에 남아 있던 글 · 첨부 · 고른 판독 방식이 새 대화의 전송에 실려 가면 안 된다."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import demo_rec
        self.rec = demo_rec
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = mock.patch.dict("os.environ", {"MAPCG_ROOM": ""})
        env.start()
        self.addCleanup(env.stop)
        self.desk = server.Desk(root / "앱", offline=True)
        self.http = server.make_server(self.desk, 0)
        self.http.handle_error = lambda *a: None        # 크롬을 끌 때 끊기는 연결은 시험 출력에 찍지 않는다
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.addCleanup(self.idle)
        self.chrome, self.cdp = demo_rec.start_chrome((1280, 800), False, None, root / "_크롬")
        self.addCleanup(demo_rec.kill_tree, self.chrome)
        self.addCleanup(self.cdp.close)
        self.cdp.send("Page.enable")
        self.cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{self.http.server_address[1]}/"})
        for _ in range(100):
            if self.js("return typeof S !== 'undefined' && S.room"):
                break
            time.sleep(0.1)
        else:
            self.fail("화면이 열리지 않았습니다")
        # 보내는 본문을 적어 둔다
        self.js("window.__sent = []; const f0 = window.fetch; window.fetch = (u, o) => { "
                "if (String(u).includes('/api/send')) window.__sent.push(JSON.parse(o.body)); return f0(u, o); };")

    def js(self, code):
        return self.cdp.js(code, timeout=20)

    def idle(self):
        for _ in range(100):        # 보낸 의뢰를 읽는 작업이 끝난 뒤에 임시 폴더를 치운다
            if not self.desk.busy:
                return
            time.sleep(0.1)

    def test_다른_화면이_새_대화를_열면_옛_첨부와_판독_방식이_따라가지_않는다(self):
        self.js("$('text').value = '옛 대화에서 쓰던 글';"
                "attached.push({ name: '옛 의뢰서.pdf', data: 'data:application/pdf;base64,JVBERi0=', images: [], note: '', busy: false, state: 'keep' });"
                "drawAttached(); readAs = { key: 'rules', label: '설명판' }; drawReadAs();")
        old = self.desk.room
        self.desk.reset(old)                                         # 다른 화면(탭)이 새 대화를 시작했다
        self.js("await poll();")
        self.assertEqual(self.js("return [attached.length, readAs === null, $('text').value, S.room !== '%s']" % old), [0, True, "", True])
        self.js("$('text').value = '새 대화의 의뢰'; await send();")
        sent = self.js("return window.__sent")
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]["text"], sent[0]["files"], "as" in sent[0], sent[0]["room"]), ("새 대화의 의뢰", [], False, self.desk.room))
        mine = [m for m in self.desk.msgs if m["who"] == "requester"]
        self.assertEqual([(m["text"], m["files"]) for m in mine], [("새 대화의 의뢰", [])])

    def test_오프라인이면_PDF_글자표와_글꼴을_밖에서_받지_않는다(self):
        """12 — 오프라인으로 켠 앱은 PDF 를 그림으로 바꿀 때도 밖의 주소를 쓰지 않는다(온라인이면 같은 판의 자료 주소를 준다)."""
        self.assertTrue(self.desk.state()["offline"])
        self.assertEqual(self.js("return [S.info.offline, Object.keys(pdfData()).length]"), [True, 0])
        self.assertTrue(self.js("S.info.offline = false; return pdfData().cMapUrl").startswith("https://"))

    def test_첨부를_읽는_사이에_대화가_바뀌면_새_대화에_붙지_않는다(self):
        # 파일 읽기를 붙잡아 둔다 — window.__go() 를 부르면 끝난다
        self.js("const read = FileReader.prototype.readAsDataURL;"
                "FileReader.prototype.readAsDataURL = function (f) { window.__go = () => read.call(this, f); };"
                "window.__p = attachFiles([new File(['a'], '늦게 끝나는.txt'), new File(['b'], '그다음.txt')]);")
        self.assertEqual(self.js("return attached.map(a => a.busy)"), [True])
        self.desk.reset(self.desk.room)
        self.js("await poll();")
        self.assertEqual(self.js("return attached.length"), 0)
        self.js("window.__go(); await window.__p;")
        self.assertEqual(self.js("return attached.length"), 0)                       # 늦게 끝난 것도, 남은 파일도 붙지 않는다
        self.assertFalse(self.js("return $('send').disabled"))


if __name__ == "__main__":
    unittest.main()
