"""영상 내보내기 뒤의 완성 화면 검수 — 메신저가 같은 의뢰 번호로 부르고 결과 카드에 싣는지(가짜 응답으로 본다. Liner 를 부르지 않는다)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from mapcg import export, intake, navi, plan, proof, quiz, scene, server

ROWS = [{"id": "r1", "rank": 1, "name": "김가상", "value_text": "1,250", "value": 1250.0, "show": "1,250점"},
        {"id": "r2", "rank": 2, "name": "이가상", "value_text": "1,180", "value": 1180.0, "show": "1,180점"}]
RID = "의뢰-시험-0001"
REQ = {"kind": "rank_board", "title": "오늘의 점수", "rows": ROWS, "layout": "full", "duration_seconds": 8,
       "output": {"width": 1920, "height": 1080, "fps": 30}, "rid": RID}


def seen(rows=ROWS, title="오늘의 점수"):
    return {"title": title, "rows": [{"rank": str(r["rank"]), "name": r["name"], "value": r["show"]} for r in rows], "texts": [], "hard_to_read": []}


class FakeChat:
    """Liner 대신 답한다. 부를 때의 의뢰 번호를 적어 둔다."""

    def __init__(self, *shots, error=None):
        self.shots, self.error, self.rids = shots, error, []

    def __call__(self, messages, **options):
        self.rids.append(intake._liner()._ctx.request)
        if self.error:
            raise self.error
        body = {"shots": [{**s, "index": i} for i, s in enumerate(self.shots, 1)]}
        return {"model": "gpt-6.2-sol", "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}, "finish_reason": "stop"}]}


class ExportThenProof(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def desk(self, **kw):
        return server.Desk(self.root, **{"liner": True, **kw})

    def result(self, desk, record=None, req=REQ, frames=3):
        """작업 폴더(이전 작업처럼 frames_panel 이 남은 것)와 결과 메시지를 만든다."""
        folder = desk.out / "순위판_시험"
        (folder / "frames_panel").mkdir(parents=True)
        (folder / "index.html").touch()
        (folder / "job.json").write_text(json.dumps(record or {"request": {"kind": "rank_board", "title": "오늘의 점수", "rows": ROWS},
                                                             "board": {"rows": ROWS}}, ensure_ascii=False), encoding="utf-8")
        for i in range(frames):
            Image.new("RGB", (960, 540), (255, 245, 225)).save(folder / "frames_panel" / f"{i:05d}.png")
        return desk.add("desk", "result", job=folder.name, request=req, rows=ROWS, warnings=[], seconds=1,
                        video=None, exporting=False, export_lines=[], export_error="")

    def export(self, desk, m, chat, render=None):
        mp4 = desk.out / m["job"] / "순위판.mp4"        # 만들지 않는다 — 검수는 남은 frames_panel 에서 읽는다
        with mock.patch.object(desk, "_spawn", side_effect=lambda fn, *args, **kw: fn(*args)), \
                mock.patch.object(export, "export", render or mock.Mock(return_value={"mp4": mp4, "frames": 3})), \
                mock.patch.object(intake._liner(), "chat", chat):
            desk.export(m["id"], False, room=desk.room)
        return m

    def test_같으면_카드에_같음을_싣고_같은_의뢰_번호로_한_번_부른다(self):
        desk = self.desk()
        chat = FakeChat(seen())
        m = self.export(desk, self.result(desk), chat)
        self.assertEqual(m["video"]["frames"], 3)
        self.assertFalse(m["proofing"])
        self.assertEqual((m["proof"]["state"], m["proof"]["problems"], m["proof"]["shots"]), ("같음", [], ["순위판"]))
        self.assertEqual(chat.rids, [RID])
        self.assertTrue((desk.out / m["job"] / proof.SAVE_NAME).is_file())

    def test_다른_곳이_있으면_목록을_싣는다(self):
        desk = self.desk()
        bad = seen()
        bad["rows"][1]["value"] = "1,108점"
        bad["hard_to_read"] = [{"text": "가상 예능", "why": "잘림"}]
        m = self.export(desk, self.result(desk), FakeChat(bad))
        self.assertEqual(m["proof"]["state"], "다른 곳 있음")
        self.assertEqual([(p["kind"], p["want"], p["seen"]) for p in m["proof"]["problems"]], [("값이 다름", "1,180점", "1,108점")])
        self.assertEqual(m["proof"]["opinions"], [{"shot": "순위판", "text": "가상 예능", "why": "잘림"}])

    def test_Liner_가_안_되면_검수_못_함이고_영상은_그대로다(self):
        desk = self.desk()
        m = self.export(desk, self.result(desk), FakeChat(error=intake._liner().LinerError(402, "insufficient balance")))
        self.assertTrue(m["video"]["path"].endswith("순위판.mp4"))
        self.assertEqual(m["export_error"], "")
        self.assertEqual(m["proof"]["state"], "검수 못 함")
        self.assertIn("잔액이 부족합니다", m["proof"]["note"])
        self.assertNotIn("insufficient", m["proof"]["note"])        # 영어 응답 본문을 화면에 싣지 않는다

    def test_오프라인이거나_Liner_가_꺼져_있으면_부르지_않고_건너뛴다(self):
        for kw, why in (({"offline": True}, "오프라인"), ({"liner": False}, "연결되지 않아")):
            with self.subTest(kw=kw):
                desk = self.desk(**kw)
                chat = FakeChat(seen())
                m = self.export(desk, self.result(desk), chat)
                self.assertEqual(chat.rids, [])
                self.assertEqual(m["proof"]["state"], "건너뜀")
                self.assertIn(why, m["proof"]["note"])
                (desk.out / m["job"]).rename(desk.out / f"끝난_{why}")      # 다음 회차가 같은 이름의 폴더를 쓰게

    def test_정보판_카드_퀴즈_네비는_건너뛴다(self):
        for record in ({"kind": "plan", "request": {"kind": "plan", "shape": "versus"}},
                       {"kind": "card_quiz", "request": {"kind": "card_quiz"}},
                       {"request": {"map_style": "navi_screen", "from": {"name": "가"}, "to": {"name": "나"}}}):
            with self.subTest(record=record):
                desk = self.desk()
                chat = FakeChat(seen())
                m = self.export(desk, self.result(desk, record=record, frames=0), chat)
                self.assertEqual(chat.rids, [])
                self.assertEqual(m["proof"]["state"], "건너뜀")
                self.assertIn("아직 화면 검수를 하지 않습니다", m["proof"]["note"])
                folder = desk.out / m["job"]
                self.assertFalse((folder / proof.SAVE_NAME).exists())
                folder.rename(desk.out / f"끝난_{len(list(desk.out.iterdir()))}")

    def test_다시_내보내면_앞_검수는_지운다(self):
        desk = self.desk()
        m = self.export(desk, self.result(desk), FakeChat(seen()))
        self.assertEqual(m["proof"]["state"], "같음")
        self.export(desk, m, FakeChat(seen()), render=mock.Mock(side_effect=export.ExportError("프레임 수가 맞지 않습니다")))
        self.assertIsNone(m["proof"])
        self.assertIn("프레임 수가 맞지 않습니다", m["export_error"])

    def test_검수_도중에_앱이_꺼지면_다시_켤_때_검수_못_함으로_바꾼다(self):
        desk = self.desk()
        m = self.result(desk)
        desk.touch(m, video={"url": "/jobs/x/순위판.mp4", "path": "x", "frames": 3}, proofing=True)
        again = server.Desk(self.root, liner=True).msgs[-1]
        self.assertFalse(again["proofing"])
        self.assertEqual(again["proof"]["state"], "검수 못 함")
        self.assertEqual(again["video"]["frames"], 3)       # 영상은 그대로


class Want(unittest.TestCase):
    def test_지도의_거리는_화면처럼_실제_주행거리를_본다(self):
        job = {"request": {"from": {"name": "가"}, "to": {"name": "나"}}, "route": {"distance_m": 5230, "drawn_m": 4400}}
        self.assertEqual(proof._map_want(job)["km"], 5.2)
        self.assertEqual(proof._map_want({"route": {"drawn_m": 4400}})["km"], 4.4)       # 주행거리가 없는 옛 기록

    def test_건너뛰는_종류의_이름이_각_모듈과_같다(self):
        self.assertEqual(set(proof.SKIP_KINDS), {plan.KIND, quiz.KIND, scene.KIND})
        self.assertEqual(proof.NAVI_STYLE, navi.STYLE)


if __name__ == "__main__":
    unittest.main()
