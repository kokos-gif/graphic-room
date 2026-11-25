"""수정 지시 — 결과 카드의 고칠 점을 Liner 가 읽어 바뀔 항목만 얹고, 바뀐 곳을 표시한 확인 카드를 다시 낸다(가짜 응답으로 본다)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, cuts, revise, server

# 앞서 만든 컷 나눔의 결과 카드 의뢰(cuts.fixed_request 모양): 좌표·경유지·컷 구간·핀 자리가 들어 있다
RESOLVED = {
    "program": "가상 예능", "requester": {"name": "김가상", "role": "조연출", "phone": ""}, "map_style": "route_move",
    "from": {"name": "서울역", "lat": 37.5547, "lon": 126.9707, "display": "서울역, 중구"},
    "to": {"name": "DDP", "lat": 37.5665, "lon": 127.0092, "display": "동대문디자인플라자, 중구"},
    "duration_seconds": 14, "layout": "full", "vehicle": "suv", "faces": ["자산/얼굴/01.tga", "자산/얼굴/02.tga", "자산/얼굴/03.tga"],
    "face_mode": "above", "output": {"width": 1920, "height": 1080, "fps": 30}, "art_style": "v5", "kind": "cuts", "rid": "의뢰-1",
    "via": [{"name": "동대문", "lat": 37.5714, "lon": 127.0098}],
    "cuts": [{"from": "서울역", "to": "동대문", "duration_seconds": 8, "from_m": 0.0, "to_m": 3000.0,
              "stops": [{"name": "명동", "caption": "미션1 오목", "pass": False, "lat": 37.5609, "lon": 126.9860, "at_m": 1200.0}]},
             {"from": "동대문", "to": "DDP", "duration_seconds": 6, "from_m": 3000.0, "to_m": 4100.0}],
}
BOARD = {"kind": board.KIND, "rid": "의뢰-2", "title": "1차 미션 점수", "unit": "점", "order": "input", "duration_seconds": 8, "layout": "full",
         "art_style": "v5", "output": {"width": 1920, "height": 1080, "fps": 30}, "program": "", "requester": {"name": "", "role": "", "phone": ""},
         "rows": [{"id": "r1", "rank": 1, "name": "가람", "value_text": "120", "value": 120.0}, {"id": "r2", "rank": 2, "name": "나래", "value_text": "95", "value": 95.0}],
         "next": {"title": "2차 미션 점수", "rows": [{"id": "r1", "rank": 1, "name": "나래", "value_text": "130"}, {"id": "r2", "rank": 2, "name": "가람", "value_text": "110"}]}}


def answer(**over):
    """모델의 답: 말하지 않은 항목은 null · same · false · 빈 배열."""
    return {"from_name": None, "to_name": None, "vehicle": "same", "vehicle_color": None, "duration_seconds": None, "layout": "same",
            "face_count": None, "face_mode": "same", "map_style": "same", "art_style": "same", "output_width": None, "output_height": None,
            "fps": None, "cuts_changed": False, "cuts": [], "unsupported": [], "unclear": [], **over}


def mcut(a, b, sec, stops=(), bridge=False):
    return {"from_name": a, "to_name": b, "duration_seconds": sec, "bridge": bridge, "closeup": False,
            "stops": [{"name": n, "caption": c, "pass": False} for n, c in stops], "note": ""}


def board_answer(**over):
    return {"title": None, "unit": None, "order": "same", "duration_seconds": None, "art_style": "same", "fps": None,
            "rows_changed": False, "rows": [], "next_changed": False, "next_title": "", "next_rows": [], "unsupported": [], "unclear": [], **over}


def fields(changed):
    return [c["field"] for c in changed]


class MapRevision(unittest.TestCase):
    def test_컷_길이만_바꾸면_경계와_핀_자리를_그대로_쓴다(self):
        got = answer(cuts_changed=True, cuts=[mcut("서울역", "동대문", 4, [("명동", "미션1 오목")]), mcut("동대문", "DDP", 4)])
        d, changed, notes = revise.apply_map(RESOLVED, got)
        self.assertEqual([c["duration_seconds"] for c in d["cuts"]], [4, 4])
        self.assertEqual((d["duration_seconds"], d["via"], [(c["from_m"], c["to_m"]) for c in d["cuts"]]), (8, RESOLVED["via"], [(0.0, 3000.0), (3000.0, 4100.0)]))
        self.assertEqual(d["cuts"][0]["stops"][0]["at_m"], 1200.0)
        self.assertEqual(d["from"], RESOLVED["from"])
        self.assertEqual(fields(changed), ["cuts", "duration_seconds"])
        self.assertTrue(any("앞서 만든 그대로" in n for n in notes), notes)
        self.assertEqual((d["face_count"], "faces" in d, "kind" in d, "rid" in d), (3, False, False, False))
        self.assertEqual(RESOLVED["cuts"][0]["duration_seconds"], 8)        # 결과 카드의 의뢰는 건드리지 않는다

    def test_도착지를_바꾸면_앞서_찾은_자리를_버리고_다시_찾는다(self):
        d, changed, notes = revise.apply_map(RESOLVED, answer(to_name="여의도역"))
        self.assertEqual(d["to"], {"name": "여의도역"})
        self.assertEqual(d["from"], RESOLVED["from"])         # 안 바뀐 출발지의 좌표는 그대로
        self.assertNotIn("via", d)
        self.assertTrue(all("from_m" not in c for c in d["cuts"]))
        self.assertNotIn("at_m", d["cuts"][0]["stops"][0])
        self.assertEqual(fields(changed), ["to"])
        self.assertTrue(any("새로 찾습니다" in n for n in notes))

    def test_색_배치_화풍은_자리와_무관하게_바꾼다(self):
        d, changed, _ = revise.apply_map(RESOLVED, answer(vehicle_color="빨간색", layout="bottom_left_quarter", art_style="v4", fps=25))
        self.assertEqual((d["vehicle_color"], d["layout"], d["art_style"], d["output"]["fps"]), ("빨간색", "bottom_left_quarter", "v4", 25))      # 읽을 때와 같은 색 규칙
        self.assertEqual(sorted(fields(changed)), ["art_style", "fps", "layout", "vehicle_color"])
        self.assertEqual(d["via"], RESOLVED["via"])

    def test_컷_수가_바뀌면_구간을_다시_찾는다(self):
        got = answer(cuts_changed=True, cuts=[mcut("서울역", "명동", 4), mcut("명동", "동대문", 4), mcut("동대문", "DDP", 4)])
        d, changed, _ = revise.apply_map(RESOLVED, got)
        self.assertEqual([(c["from"], c["to"]) for c in d["cuts"]], [("서울역", "명동"), ("명동", "동대문"), ("동대문", "DDP")])
        self.assertNotIn("via", d)
        self.assertEqual(d["duration_seconds"], 12)
        self.assertIn("cuts", fields(changed))

    def test_컷을_나눈_의뢰의_전체_길이만_바꾸면_컷_길이를_비율대로(self):
        d, changed, _ = revise.apply_map(RESOLVED, answer(duration_seconds=7))
        self.assertEqual([c["duration_seconds"] for c in d["cuts"]], [4, 3])
        self.assertEqual((d["duration_seconds"], fields(changed)), (7, ["duration_seconds"]))
        self.assertEqual(d["via"], RESOLVED["via"])

    def test_컷_나눔을_풀면_한_화면으로(self):
        d, changed, _ = revise.apply_map(RESOLVED, answer(cuts_changed=True, cuts=[]))
        self.assertEqual(("cuts" in d, "via" in d, fields(changed)), (False, False, ["cuts"]))

    def test_바꿀_수_없는_요청은_지어_넣지_않고_알린다(self):
        r = revise.revise(RESOLVED, "3컷은 다리를 비스듬히", chat=lambda m, **o: {"choices": [{"message": {"content": json.dumps(
            answer(unsupported=["3컷의 다리를 비스듬히 그리기"]), ensure_ascii=False)}}]})
        self.assertEqual(r["changed"], [])
        self.assertIn("찾지 못했습니다", r["summary"])
        self.assertTrue(any("반영하지 못한 요청: 3컷의 다리를 비스듬히" in n for n in r["notes"]), r["notes"])

    def test_모델에게_지금_값과_수정_지시만_보내고_그림은_보내지_않는다(self):
        sent = {}

        def chat(messages, **o):
            sent.update(messages=messages, options=o)
            return {"choices": [{"message": {"content": json.dumps(answer(), ensure_ascii=False)}}]}
        revise.revise(RESOLVED, "버스를 빨강으로", chat=chat)
        body = sent["messages"][1]["content"]
        self.assertEqual([p["type"] for p in body], ["text"])
        self.assertIn("버스를 빨강으로", body[0]["text"])
        self.assertIn('"from_name": "서울역"', body[0]["text"])
        self.assertNotIn("37.5547", body[0]["text"])         # 좌표 같은 앱 내부 값은 보내지 않는다
        self.assertEqual(sent["options"]["response_format"]["json_schema"]["name"], "map_revision")


class BoardRevision(unittest.TestCase):
    def test_제목과_한_줄의_값을_바꾸고_줄_이름표를_이어받는다(self):
        got = board_answer(title="1차 미션 최종 점수", rows_changed=True, rows=[{"rank": 1, "name": "가람", "value_text": "120"}, {"rank": 2, "name": "나래", "value_text": "99"}])
        d, changed, _ = revise.apply_board(BOARD, got)
        self.assertEqual((d["title"], d["kind"]), ("1차 미션 최종 점수", board.KIND))
        self.assertEqual([(r["id"], r["value_text"]) for r in d["rows"]], [("r1", "120"), ("r2", "99")])
        self.assertTrue(all("value" not in r for r in d["rows"]))
        self.assertEqual(fields(changed), ["title", "rows"])
        board.normalize(d)      # 고친 초안이 그대로 만들어진다

    def test_다음_순위를_빼라고_하면_한_장으로(self):
        d, changed, _ = revise.apply_board(BOARD, board_answer(next_changed=True))
        self.assertEqual(("next" in d, fields(changed)), (False, ["next"]))


class DeskRevision(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def desk(self, liner):
        d = server.Desk(self.tmp, liner=liner)
        d._spawn = lambda fn, *a, **_: fn(*a)
        return d

    def test_고칠_점을_보내면_바뀐_곳을_표시한_확인_카드가_같은_의뢰_번호로_나온다(self):
        d = self.desk(True)
        got = answer(cuts_changed=True, cuts=[mcut("서울역", "동대문", 4, [("명동", "미션1 오목")]), mcut("동대문", "DDP", 4)])
        with mock.patch.object(revise.intake, "ask_liner", lambda *a, **k: got):
            d.revise(copy.deepcopy(RESOLVED), "4초씩으로")
        said, p, card = d.msgs[-3:]
        self.assertEqual((said["kind"], said["text"]), ("text", "수정 지시: 4초씩으로"))
        self.assertEqual((p["kind"], p["state"]), ("progress", "ok"))
        self.assertEqual((card["kind"], card["revision"], card["draft"]["rid"]), ("card", "4초씩으로", "의뢰-1"))
        self.assertEqual(fields(card["changed"]), ["cuts", "duration_seconds"])
        # 그 카드를 확정하면 앞서 만든 경유지·구간·핀 자리 그대로 다시 만든다
        seen = {}
        with mock.patch.object(cuts, "make", lambda spec, **kw: seen.update(spec=spec) or (_ for _ in ()).throw(RuntimeError("멈춤"))):
            d.make(card["draft"])
        spec = seen["spec"]
        self.assertEqual((spec["via"], [c["from_m"] for c in spec["cuts"]], spec["cuts"][0]["stops"][0]["at_m"]), (RESOLVED["via"], [0.0, 3000.0], 1200.0))
        self.assertEqual(len(spec["base"]["faces"]), 3)

    def test_Liner_가_없으면_부르지_않고_지금_의뢰로_카드를_낸다(self):
        d = self.desk(False)
        with mock.patch.object(revise.intake, "ask_liner", lambda *a, **k: self.fail("Liner 를 불렀다")):
            d.revise(copy.deepcopy(BOARD), "제목을 바꿔 줘")
        p, card = d.msgs[-2:]
        self.assertEqual((p["state"], card["kind"], card["changed"]), ("fail", "board", []))
        self.assertEqual((card["draft"]["title"], card["draft"]["rid"]), ("1차 미션 점수", "의뢰-2"))
        self.assertTrue(any("직접 고쳐" in n for n in card["notes"]))

    def test_빈_고칠_점은_받지_않는다(self):
        with self.assertRaises(server.DeskError):
            self.desk(True).revise(copy.deepcopy(RESOLVED), "  ")


if __name__ == "__main__":
    unittest.main()
