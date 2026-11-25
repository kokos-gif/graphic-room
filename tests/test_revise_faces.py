"""수정 지시로 얼굴 · 아이콘 그림 고치기 — 출연진 시트에서 얼굴 고르기, 얼굴 크기 배율, Liner 그림 모델로 그린 이동 아이콘(가짜 응답으로 본다)."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from mapcg import build, cuts, facesheet, request, revise, server

RESOLVED = {
    "program": "가상 예능", "requester": {"name": "김가상", "role": "조연출", "phone": ""}, "map_style": "route_move",
    "from": {"name": "여의도 선착장", "lat": 37.5248, "lon": 126.9353, "display": "여의도선착장"},
    "to": {"name": "뚝섬", "lat": 37.529, "lon": 127.069, "display": "뚝섬 선착장"},
    "duration_seconds": 10, "layout": "full", "vehicle": "boat", "faces": ["자산/얼굴/01.tga", "자산/얼굴/02.tga"],
    "face_mode": "ride", "output": {"width": 1920, "height": 1080, "fps": 30}, "art_style": "v5", "rid": "의뢰-9",
}


def answer(**over):
    return {"from_name": None, "to_name": None, "vehicle": "same", "vehicle_color": None, "duration_seconds": None, "layout": "same",
            "face_count": None, "face_mode": "same", "face_scale": None, "face_picks": [], "vehicle_picture": "same",
            "map_style": "same", "art_style": "same", "output_width": None, "output_height": None,
            "fps": None, "cuts_changed": False, "cuts": [], "unsupported": [], "unclear": [], **over}


def make_sheet(path: Path) -> Path:
    """바탕을 지운 출연진 시트: 2줄 × 3명(가로 120 · 세로 150 타원 머리)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", (600, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for r in range(2):
        for c in range(3):
            x, y = c * 200 + 100, r * 200 + 100
            d.ellipse((x - 60, y - 75, x + 60, y + 75), fill=(230, 190, 170, 255))
    im.save(path)
    return path


class Picks(unittest.TestCase):
    def test_top_row_and_bottom_middle(self):
        got, notes, said = revise.pick_faces([3, 3], [{"row": 1, "col": None}, {"row": -1, "col": 0}])
        self.assertEqual((got, notes, said), ([(0, 0), (0, 1), (0, 2), (1, 1)], [], "1줄 전부 · 2줄 가운데"))

    def test_missing_places_are_told_not_guessed(self):
        got, notes, _ = revise.pick_faces([3, 4], [{"row": 3, "col": 1}, {"row": 1, "col": 5}, {"row": 2, "col": 0}, {"row": 1, "col": -1}])
        self.assertEqual(got, [(1, 1), (0, 2)])          # 4명 줄의 가운데는 왼쪽 가운데 · 맨 오른쪽
        self.assertEqual(len(notes), 3)
        self.assertIn("가운데가 둘", notes[2])


class ApplyFaces(unittest.TestCase):
    SHEET = {"name": "출연진.png", "rows": [3, 3]}

    def test_sheet_faces_scale_and_picture(self):
        cut = mock.Mock(return_value=[f"자산/얼굴/시트/x/{i:02d}.png" for i in range(1, 5)])
        got = answer(face_picks=[{"row": 1, "col": None}, {"row": -1, "col": 0}], face_scale=1.2, vehicle_picture="liner")
        d, changed, notes = revise.apply_map(RESOLVED, got, self.SHEET, cut)
        cut.assert_called_once_with([(0, 0), (0, 1), (0, 2), (1, 1)])
        self.assertEqual((d["face_count"], d["faces"][0], d["face_scale"], d["vehicle_picture"]), (4, "자산/얼굴/시트/x/01.png", 1.2, "liner"))
        self.assertEqual([c["field"] for c in changed], ["vehicle_picture", "faces", "face_scale"])
        self.assertIn("1줄 전부 · 2줄 가운데", changed[1]["after"])
        self.assertEqual(notes, [])
        # 고친 카드를 다시 고칠 때: 시트에서 고른 얼굴 · 크기 · 그림은 그대로 이어 간다
        again = {**d, "faces": d["faces"]}
        self.assertEqual(revise._base_draft(again)["faces"], d["faces"])
        now = revise.current_map(again)
        self.assertEqual((now["faces_from_sheet"], now["face_scale"], now["vehicle_picture"]), (True, 1.2, "liner"))

    def test_picks_without_a_sheet_are_told(self):
        d, changed, notes = revise.apply_map(RESOLVED, answer(face_picks=[{"row": 1, "col": None}]))
        self.assertEqual((changed, "faces" in d), ([], False))
        self.assertIn("시트 그림을 찾지 못했습니다", notes[0])

    def test_scale_out_of_range_and_count_change_drop_sheet_faces(self):
        req = {**RESOLVED, "faces": ["자산/얼굴/시트/x/01.png", "자산/얼굴/시트/x/02.png"]}
        d, changed, notes = revise.apply_map(req, answer(face_scale=3, face_count=3))
        self.assertEqual((d["face_count"], "faces" in d, "face_scale" in d), (3, False, False))
        self.assertIn("기본 얼굴 파일", changed[0]["after"])
        self.assertIn("0.5~2배 밖", notes[0])

    def test_picture_follows_the_vehicle(self):
        req = {**RESOLVED, "vehicle_image": "data/icons/배.png"}
        d, _, notes = revise.apply_map(req, answer(vehicle="bus"))            # 배 그림은 버리고, 만들 때 버스 그림을 새로
        self.assertEqual(("vehicle_image" in d, d["vehicle_picture"]), (False, "liner"))
        d, _, _ = revise.apply_map(req, answer(vehicle="walk"))
        self.assertEqual(("vehicle_image" in d, "vehicle_picture" in d), (False, False))
        d, changed, _ = revise.apply_map(req, answer(vehicle_picture="code"))
        self.assertEqual(("vehicle_image" in d, changed[0]["after"]), (False, "코드로 그린 아이콘"))
        d, changed, notes = revise.apply_map({**RESOLVED, "vehicle": "dot"}, answer(vehicle_picture="liner"))
        self.assertEqual(("vehicle_picture" in d, changed), (False, []))
        self.assertIn("그림으로 바꾸지 않습니다", notes[0])


class SheetPath(unittest.TestCase):
    def test_folder_path_with_words_after_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sheet = make_sheet(root / "디자인" / "출연진" / "시트.png")
            (root / "디자인" / "출연진" / "얼굴_4명").mkdir()
            got, why = facesheet.find(f"얼굴은 {root / '디자인' / '출연진'} 위에 3명 밑에 가운데 1명", root)
            self.assertEqual((got, why), (sheet.resolve(), None))
            got, why = facesheet.find("디자인\\출연진에서 위 3명", root)       # 작업 폴더 기준 · 조사가 붙어도
            self.assertEqual(got, sheet.resolve())
            self.assertEqual([len(r) for r in facesheet.detect(sheet)], [3, 3])
            make_sheet(root / "디자인" / "출연진" / "둘째.png")
            got, why = facesheet.find(str(root / "디자인" / "출연진"), root)
            self.assertIsNone(got)
            self.assertIn("그림이 2장", why)

    def test_outside_the_workspace_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            make_sheet(Path(other) / "시트.png")
            got, why = facesheet.find(f"{Path(other) / '시트.png'} 에서 골라", Path(tmp))
            self.assertIsNone(got)
            self.assertIn("밖이라 읽지 않았습니다", why)


class DeskSheet(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = self.root / "앱"
        self.app.mkdir()

    def test_revision_cuts_the_sheet_and_make_uses_those_faces(self):
        make_sheet(self.root / "의뢰" / "출연진" / "출연진.png")
        d = server.Desk(self.app, liner=True)
        d._spawn = lambda fn, *a, **_: fn(*a)
        seen = {}
        got = answer(face_picks=[{"row": 1, "col": None}, {"row": -1, "col": 0}], face_scale=1.2, vehicle_picture="liner")
        with mock.patch.object(revise.intake, "ask_liner", lambda *a, **k: (seen.update(body=a[1]), got)[1]):
            d.revise(copy.deepcopy(RESOLVED), f"얼굴을 {self.root / '의뢰' / '출연진'} 위에 3명 밑에 가운데 1명, 1.2배, 배는 라이너 그림으로")
        self.assertIn("[출연진 시트]\n그림 '출연진.png' · 2줄 — 1줄 3명, 2줄 3명", seen["body"])
        card = d.msgs[-1]
        faces = card["draft"]["faces"]
        self.assertEqual((card["kind"], len(faces), len(card["faces_preview"]), card["draft"]["face_count"]), ("card", 4, 4, 4))
        self.assertTrue(all((self.app / f).is_file() and f.startswith("자산/얼굴/시트/의뢰-9-") for f in faces))
        with Image.open(self.app / faces[0]) as im:
            self.assertEqual(im.size, (facesheet.SIDE, facesheet.SIDE))
        # 확정하면 그 얼굴 파일 그대로 · 크기 1.2 · 그림 아이콘으로 만든다
        built = {}
        with mock.patch.object(build, "make_job", lambda req, **kw: built.update(req=req) or (_ for _ in ()).throw(RuntimeError("멈춤"))):
            d.make(card["draft"])
        req = built["req"]
        self.assertEqual((req["faces"], req["face_scale"], req["vehicle_picture"]), (faces, 1.2, "liner"))

    def test_attached_sheet_is_used_instead_of_a_path(self):
        import base64
        sheet = make_sheet(self.root / "붙임" / "시트.png")
        d = server.Desk(self.app, liner=True)
        d._spawn = lambda fn, *a, **_: fn(*a)
        seen = {}
        got = answer(face_picks=[{"row": 2, "col": -1}])
        data = "data:image/png;base64," + base64.b64encode(sheet.read_bytes()).decode()
        with mock.patch.object(revise.intake, "ask_liner", lambda *a, **k: (seen.update(body=a[1]), got)[1]):
            d.revise(copy.deepcopy(RESOLVED), "붙인 시트에서 아래 줄 맨 오른쪽 한 명만", sheet={"name": "출연진 시트.png", "data": data})
        said = d.msgs[-3]
        self.assertEqual((said["files"][0]["name"], said["files"][0]["url"].startswith("/uploads/")), ("출연진_시트.png", True))
        self.assertIn("2줄 — 1줄 3명, 2줄 3명", seen["body"])
        card = d.msgs[-1]
        self.assertEqual((card["draft"]["face_count"], len(card["faces_preview"])), (1, 1))
        with self.assertRaises(server.DeskError):        # 그림이 아닌 것은 받지 않는다
            d.revise(copy.deepcopy(RESOLVED), "얼굴 바꿔", sheet={"name": "a.txt", "data": "data:text/plain;base64,YQ=="})

    def test_faces_from_elsewhere_are_not_taken(self):
        d = server.Desk(self.app, liner=True)
        d._spawn = lambda fn, *a, **_: fn(*a)
        built = {}
        with mock.patch.object(build, "make_job", lambda req, **kw: built.update(req=req) or (_ for _ in ()).throw(RuntimeError("멈춤"))):
            d.make({**copy.deepcopy(RESOLVED), "faces": ["C:/Windows/a.png", "자산/얼굴/시트/../../x.png"], "face_count": 2})
        self.assertEqual(built["req"]["faces"], ["자산/얼굴/01.tga", "자산/얼굴/02.tga"])

    def test_sheet_note_reaches_the_card(self):
        d = server.Desk(self.app, liner=True)
        d._spawn = lambda fn, *a, **_: fn(*a)
        with mock.patch.object(revise.intake, "ask_liner", lambda *a, **k: answer()):
            d.revise(copy.deepcopy(RESOLVED), f"{self.root / '없는폴더'} 에서 얼굴 골라")
        self.assertTrue(any("찾지 못했습니다" in n for n in d.msgs[-1]["notes"]))


class LinerPicture(unittest.TestCase):
    def test_request_and_build(self):
        base = {"from": {"name": "가"}, "to": {"name": "나"}, "duration_seconds": 5, "vehicle": "boat"}
        self.assertEqual(request.normalize({**base, "vehicle_picture": "liner"})["vehicle_picture"], "liner")
        self.assertNotIn("vehicle_picture", request.normalize({**base, "vehicle_picture": "code"}))
        with self.assertRaises(request.RequestError):
            request.normalize({**base, "vehicle_picture": "사진"})
        with tempfile.TemporaryDirectory() as tmp:
            kept = Path(tmp) / "배.png"
            warnings = []
            with mock.patch("mapcg.icons.path_for", return_value=kept), mock.patch("mapcg.icons.make", side_effect=AssertionError("그렸다")):
                self.assertIsNone(build._liner_picture("boat", True, warnings))         # 오프라인 · 보관 그림 없음
                self.assertIn("오프라인", warnings[-1])
                kept.write_bytes(b"x")
                self.assertEqual(build._liner_picture("boat", True, warnings), str(kept))   # 보관 그림은 오프라인에서도 쓴다
            self.assertIsNone(build._liner_picture("walk", False, warnings))
            self.assertIn("그림으로 그리지 않습니다", warnings[-1])
            with mock.patch("mapcg.icons.path_for", return_value=Path(tmp) / "새.png"), \
                    mock.patch("mapcg.icons.make", side_effect=RuntimeError("돈 없음")):
                self.assertIsNone(build._liner_picture("bus", False, warnings, log=lambda s: None))
                self.assertIn("돈 없음", warnings[-1])


if __name__ == "__main__":
    unittest.main()
