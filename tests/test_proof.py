"""완성 화면 검수 — 볼 화면 고르기 · Liner 가 옮겨 적은 글자와 작업 기록 맞대기(가짜 응답으로 본다. Liner 를 부르지 않는다)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from mapcg import intake, proof

ROWS = [{"id": "r1", "rank": 1, "name": "김가상", "value_text": "1,250", "value": 1250.0, "show": "1,250점"},
        {"id": "r2", "rank": 2, "name": "이가상", "value_text": "1,180", "value": 1180.0, "show": "1,180점"},
        {"id": "r3", "rank": 3, "name": "박가상", "value_text": "965", "value": 965.0, "show": "965점"}]
NEXT = [{"id": "r2", "rank": 1, "name": "이가상", "show": "1,400점"}, {"id": "r1", "rank": 2, "name": "김가상", "show": "1,300점"},
        {"id": "r3", "rank": 3, "name": "박가상", "show": "탈락 · 기권"}]


def seen_board(title="오늘의 점수", rows=ROWS, **over):
    return {"index": 1, "title": title, "rows": [{"rank": str(r["rank"]), "name": r["name"], "value": r["show"]} for r in rows], "texts": [], "hard_to_read": [], **over}


def seen_map(texts, **over):
    return {"index": 1, "title": "", "rows": [], "texts": texts, "hard_to_read": [], **over}


def chat_saying(*shots, model="gpt-6.2-sol"):
    calls = []

    def chat(messages, **options):
        calls.append((messages, options))
        body = {"shots": [{**s, "index": i} for i, s in enumerate(shots, 1)]}
        return {"model": model, "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}, "finish_reason": "stop"}]}
    chat.calls = calls
    return chat


class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def job(self, name, record, frames=10, size=(960, 540)):
        d = self.root / name
        (d / "frames_panel").mkdir(parents=True)
        (d / "job.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        for i in range(frames):
            Image.new("RGB", size, (255, 245, 225)).save(d / "frames_panel" / f"{i:05d}.png")
        return d

    def board(self, **over):
        return self.job("순위판", {"request": {"kind": "rank_board", "title": "오늘의 점수", "rows": ROWS}, "board": {"rows": ROWS}, **over})

    def map(self, name="지도", frm="노들섬", to="여의도", drawn=4400, frames=10, **kw):
        return self.job(name, {"request": {"map_style": "nav", "from": {"name": frm}, "to": {"name": to}}, "route": {"distance_m": drawn + 9, "drawn_m": drawn}}, frames, **kw)


class Compare(unittest.TestCase):
    WANT = proof._board_want("오늘의 점수", ROWS)

    def test_같으면_문제가_없다(self):
        self.assertEqual(proof.compare_board(self.WANT, seen_board()), [])

    def test_빈칸_쉼표_단위_표기_차이는_같다고_본다(self):
        seen = seen_board(title="오늘의  점수")
        seen["rows"][0].update(rank="1위", value="1250 점")
        seen["rows"][1]["value"] = "1,180"          # 모델이 단위를 빼고 옮겨 적음
        self.assertEqual(proof.compare_board(self.WANT, seen), [])

    def test_값과_이름이_다르면_잡는다(self):
        seen = seen_board()
        seen["rows"][1]["value"] = "1,108점"
        seen["rows"][2]["name"] = "박기상"
        got = proof.compare_board(self.WANT, seen)
        self.assertEqual([(p["kind"], p["where"], p["want"], p["seen"]) for p in got],
                         [("값이 다름", "2위 값", "1,180점", "1,108점"), ("이름이 다름", "3위 이름", "박가상", "박기상")])

    def test_단위가_서로_다르면_다르다(self):
        self.assertFalse(proof.same_value("1,250점", "1,250명"))
        self.assertFalse(proof.same_value("탈락 · 기권", "탈락"))
        self.assertTrue(proof.same_value("탈락 · 기권", "탈락·기권"))

    def test_제목이_다르거나_줄이_빠지고_남으면_잡는다(self):
        seen = seen_board(title="어제의 점수", rows=ROWS[:2])
        seen["rows"].append({"rank": "4", "name": "최가상", "value": "10점"})
        kinds = [(p["kind"], p["where"]) for p in proof.compare_board(self.WANT, seen)]
        self.assertEqual(kinds, [("제목이 다름", "제목"), ("빠진 줄", "3위"), ("남는 줄", "4위")])

    def test_의뢰에_제목이_없으면_제목은_보지_않는다(self):
        self.assertEqual(proof.compare_board(proof._board_want("", ROWS), seen_board(title="무엇이든")), [])

    def test_공동_순위는_놓인_차례로_맞춘다(self):
        tie = [dict(ROWS[0]), {**ROWS[1], "rank": 1}, dict(ROWS[2])]
        seen = seen_board(rows=tie)
        self.assertEqual(proof.compare_board(proof._board_want("오늘의 점수", tie), seen), [])
        seen["rows"][1]["name"] = "이기상"
        self.assertEqual([p["kind"] for p in proof.compare_board(proof._board_want("오늘의 점수", tie), seen)], ["이름이 다름"])

    def test_지도는_이름표와_거리_숫자를_본다(self):
        want = {"texts": [{"text": "노들섬", "what": "출발지 이름표"}, {"text": "여의도", "what": "도착지 이름표"}], "km": 4.4}
        ok = seen_map(["노들섬 → 여의도", "도로 거리 4.4 km", "한강", "여의나루역"])
        self.assertEqual(proof.compare_map(want, ok), [])
        self.assertEqual(proof.compare_map(want, seen_map(["여의도", "노들섬", "4.5km"])), [])          # 0.1km 안은 같다
        bad = proof.compare_map(want, seen_map(["여의도", "도로 거리 14.4km", "여의도까지 0.3km"]))
        self.assertEqual([(p["kind"], p["want"]) for p in bad], [("이름표가 안 보임", "노들섬"), ("거리 숫자가 다름", "4.4km")])

    def test_지도_위의_비슷한_지명으로는_통과하지_않는다(self):
        want = {"texts": [{"text": "여의나루", "what": "도착지 이름표"}], "km": None}
        got = proof.compare_map(want, seen_map(["여의도", "여의나루역", "한강"], title="노들섬 → 여의도"))
        self.assertEqual(got, [{"kind": "이름표가 안 보임", "where": "도착지 이름표", "want": "여의나루", "seen": "여의나루역"}])
        self.assertEqual(proof.compare_map(want, seen_map(["여의나루", "여의나루역"])), [])
        spaced = {"texts": [{"text": "월드컵경기장 앞", "what": "출발지 이름표"}, {"text": "월드컵대교 방향", "what": "도착지 이름표"}], "km": None}
        self.assertEqual(proof.compare_map(spaced, seen_map(["월드컵경기장 앞"], title="월드컵경기장 앞 -> 월드컵대교 방향")), [])

    def test_화면에_거리_숫자가_없으면_거리는_보지_않는다(self):
        want = {"texts": [{"text": "노들섬", "what": "출발지 이름표"}], "km": 4.4}
        self.assertEqual(proof.compare_map(want, seen_map(["노들섬"])), [])

    def test_제목판에만_있는_이름도_있는_것으로_본다(self):
        want = {"texts": [{"text": "여의도", "what": "도착지 이름표"}], "km": None}
        self.assertEqual(proof.compare_map(want, seen_map([], title="노들섬 → 여의도")), [])


class Shots(Jobs):
    def test_순위판은_마지막_화면_한_장(self):
        items, notes = proof.shots(self.board())
        self.assertEqual([(s["label"], s["frame"].name, s["kind"]) for s in items], [("순위판", "00009.png", "board")])
        self.assertEqual(items[0]["want"]["rows"][0], {"rank": 1, "name": "김가상", "value": "1,250점"})

    def test_순위_변동판은_바뀌기_전과_뒤_두_장(self):
        d = self.job("변동", {"request": {"kind": "rank_board", "title": "1차", "rows": ROWS, "next": {"title": "2차", "rows": NEXT}},
                            "board": {"rows": ROWS, "next_rows": NEXT}}, frames=100)
        items, _ = proof.shots(d)
        self.assertEqual([(s["label"], s["frame"].name, s["want"]["title"]) for s in items], [("바뀌기 전", "00050.png", "1차"), ("바뀐 뒤", "00099.png", "2차")])
        self.assertEqual(items[1]["want"]["rows"][2]["value"], "탈락 · 기권")

    def test_지도는_마지막_화면에서_이름표와_거리를_본다(self):
        items, _ = proof.shots(self.map())
        self.assertEqual(items[0]["want"], {"texts": [{"text": "노들섬", "what": "출발지 이름표"}, {"text": "여의도", "what": "도착지 이름표"}], "km": 4.4})
        self.assertEqual((items[0]["kind"], items[0]["frame"].name), ("map", "00009.png"))

    def cuts(self, spans):
        root = self.job("컷나눔", {"kind": "cuts", "request": {"kind": "cuts", "cuts": [{"duration_seconds": s} for s in spans]},
                                "cuts": [{"dir": f"컷{k}"} for k in range(1, len(spans) + 1)]}, frames=40)
        for k in range(1, len(spans) + 1):
            (root / f"컷{k}").mkdir()
            (root / f"컷{k}" / "job.json").write_text(json.dumps({"request": {"from": {"name": f"출발{k}"}, "to": {"name": f"도착{k}"}}, "route": {"drawn_m": 1000 * k}},
                                                                ensure_ascii=False), encoding="utf-8")
        return root

    def test_컷_나눔은_컷마다_끝_화면(self):
        items, notes = proof.shots(self.cuts([2.5, 2.5, 1.5, 1.5]))
        self.assertEqual([(s["label"], s["frame"].name) for s in items], [("컷 1", "00011.png"), ("컷 2", "00023.png"), ("컷 3", "00031.png"), ("컷 4", "00038.png")])
        self.assertEqual(items[2]["want"]["texts"][1]["text"], "도착3")
        self.assertEqual(items[2]["want"]["km"], 3.0)
        self.assertEqual(notes, [])

    def test_컷이_많으면_네_장만_보고_적어_둔다(self):
        items, notes = proof.shots(self.cuts([1] * 8))
        self.assertEqual([s["label"] for s in items], ["컷 1", "컷 3", "컷 6", "컷 8"])
        self.assertIn("컷이 8개라 4개만", notes[0])

    def test_화면이_없거나_모르는_종류면_사유를_낸다(self):
        d = self.job("빈것", {"request": {"kind": "rank_board"}, "board": {"rows": ROWS}}, frames=0)
        with self.assertRaises(ValueError) as e:
            proof.shots(d)
        self.assertIn("영상을 먼저", str(e.exception))
        with self.assertRaises(ValueError) as e:
            proof.shots(self.job("정보판", {"request": {"kind": "plan", "shape": "versus"}}))
        self.assertIn("아직 화면 검수를 하지 않습니다", str(e.exception))


class Check(Jobs):
    def test_같으면_같음이고_기록을_남긴다(self):
        d = self.board()
        chat = chat_saying(seen_board())
        got = proof.check(d, chat=chat)
        self.assertEqual((got["state"], got["problems"], got["model"]), ("같음", [], "gpt-6.2-sol"))
        self.assertEqual(got["shots"], [{"label": "순위판", "frame": "00009.png"}])
        self.assertEqual(json.loads((d / proof.SAVE_NAME).read_text(encoding="utf-8"))["state"], "같음")
        messages, options = chat.calls[0]
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(options["routing_strategy"], "quality")
        self.assertEqual(options["response_format"]["json_schema"]["name"], "screen_proof")
        parts = messages[1]["content"]
        self.assertEqual([p["type"] for p in parts], ["text", "image_url"])
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("김가상", json.dumps(messages, ensure_ascii=False))       # 의뢰 값은 모델에 주지 않는다 — 보고 따라 적지 못하게

    def test_다른_곳이_있으면_어느_화면의_어디인지_적는다(self):
        d = self.job("변동", {"request": {"kind": "rank_board", "title": "1차", "rows": ROWS, "next": {"title": "2차", "rows": NEXT}},
                            "board": {"rows": ROWS, "next_rows": NEXT}})
        after = seen_board(title="2차", rows=NEXT)
        after["rows"][0]["value"] = "1,040점"
        got = proof.check(d, chat=chat_saying(seen_board(title="1차"), after))
        self.assertEqual(got["state"], "다른 곳 있음")
        self.assertEqual(got["problems"], [{"shot": "바뀐 뒤", "kind": "값이 다름", "where": "1위 값", "want": "1,400점", "seen": "1,040점"}])
        self.assertTrue(any("의뢰 '1,400점' ↔ 화면 '1,040점'" in line for line in proof.lines(got)))

    def test_읽기_어려운_글자는_의견으로만_싣는다(self):
        got = proof.check(self.map(), chat=chat_saying(seen_map(["노들섬", "여의도", "4.4km"], hard_to_read=[{"text": "가상 예능", "why": "잘림"}])))
        self.assertEqual((got["state"], got["opinions"]), ("같음", [{"shot": "지도", "text": "가상 예능", "why": "잘림"}]))

    def test_모델이_어떤_화면을_빼먹으면_읽지_못함이다(self):
        d = self.job("변동", {"request": {"kind": "rank_board", "title": "1차", "rows": ROWS, "next": {"title": "2차", "rows": NEXT}},
                            "board": {"rows": ROWS, "next_rows": NEXT}})
        got = proof.check(d, chat=chat_saying(seen_board(title="1차")))
        self.assertEqual([(p["shot"], p["kind"]) for p in got["problems"]], [("바뀐 뒤", "읽지 못함")])

    def test_오프라인이면_부르지_않고_건너뛴다(self):
        d, chat = self.board(), mock.Mock()
        got = proof.check(d, offline=True, chat=chat)
        chat.assert_not_called()
        self.assertEqual(got["state"], "건너뜀")
        self.assertFalse((d / proof.SAVE_NAME).exists())

    def test_Liner_가_안_되면_예외_없이_검수_못_함이다(self):
        liner = intake._liner()

        def chat(*a, **k):
            raise liner.LinerError(402, "잔액 부족")
        got = proof.check(self.board(), chat=chat)
        self.assertEqual(got["state"], "검수 못 함")
        self.assertIn("읽지 못했습니다", got["note"])

    def test_잘린_답과_화면_없음도_검수_못_함이다(self):
        cut = lambda *a, **k: {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
        self.assertEqual(proof.check(self.board(), chat=cut)["state"], "검수 못 함")
        chat = mock.Mock()
        got = proof.check(self.job("빈것", {"request": {"kind": "rank_board"}, "board": {"rows": ROWS}}, frames=0), chat=chat)
        chat.assert_not_called()
        self.assertEqual(got["state"], "검수 못 함")
        self.assertEqual(proof.check(self.root / "없는폴더", chat=chat)["state"], "검수 못 함")

    def test_작은_시험_화면은_키워서_보낸다(self):
        import base64
        import io
        d = self.map("작은지도", size=(480, 270))
        url = proof.picture(d / "frames_panel" / "00000.png")
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size, (960, 540))
        big = self.map("큰지도", size=(1920, 1080), frames=1)
        url = proof.picture(big / "frames_panel" / "00000.png")
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size, (1280, 720))


if __name__ == "__main__":
    unittest.main()
