import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import board, build, quiz, request, scene, server


class ArtStyleInputs(unittest.TestCase):
    def setUp(self):
        self.requests = (
            (request.normalize, {"from": "홍대", "to": "여의도", "duration_seconds": 8}),
            (board.normalize, {"title": "점수", "duration_seconds": 8,
                               "rows": [{"name": "가", "value_text": "2"}, {"name": "나", "value_text": "1"}]}),
        )

    def test_saved_requests_keep_their_style_when_normalized_again(self):
        for normalize, raw in self.requests:
            for choice in (None, *request.ART_STYLES):
                with self.subTest(kind=normalize.__module__, choice=choice):
                    sent = raw if choice is None else {**raw, "art_style": choice}
                    saved = normalize(sent)
                    self.assertEqual(saved["art_style"], choice or "claude")
                    self.assertEqual(normalize(saved)["art_style"], saved["art_style"])

    def test_unknown_style_returns_an_actionable_input_error(self):
        for normalize, raw in self.requests:
            with self.subTest(kind=normalize.__module__):
                with self.assertRaisesRegex(request.RequestError, "art_style"):
                    normalize({**raw, "art_style": "unknown"})

    def test_화풍마다_같은_화면에_화풍_파일_하나만_더_싣는다(self):
        scenes = (
            ("route_move.html", (build.STAGE, *build.SHARED), "mapskin.js"),
            ("rank_board.html", board.FILES, "boardskin.js"),
        )
        self.assertEqual(set(build.STYLE_FILES), set(request.ART_STYLES) - {request.DEFAULT_ART_STYLE})      # 기본 화풍만 화풍 파일이 없다
        for base, assets, skin in scenes:
            plain = set(re.findall(r'<script src="([^"]+)"', (build.TEMPLATES / base).read_text(encoding="utf-8")))
            for style in request.ART_STYLES:
                with self.subTest(scene=base, style=style), tempfile.TemporaryDirectory() as temp:
                    folder = Path(temp)
                    build.install_scene(folder, style, base, assets, skin)
                    page = (folder / "index.html").read_text(encoding="utf-8")
                    scripts = re.findall(r'<script src="([^"]+)"', page)
                    extra = build.STYLE_FILES.get(style)
                    self.assertEqual(set(scripts) - plain, {extra} if extra else set())      # 화면 틀은 화풍과 무관하게 같다
                    if extra:       # 화풍 파일은 기준 묶음 바로 뒤, 그리는 코드 앞에서 불린다
                        self.assertEqual(scripts.index(extra), scripts.index(skin) + 1)
                    for name in set(scripts) - {"job.js", "map-data.js"}:
                        self.assertTrue((folder / name).is_file(), name)
                    for name in build.STYLE_ASSETS.get(style, ()):      # 화풍에 딸린 파일(글꼴)도 작업 폴더에 같이 들어간다
                        self.assertTrue((folder / name).is_file(), name)

    def test_화풍에_딸린_파일은_있는_화풍의_것이고_실제로_있다(self):
        self.assertLessEqual(set(build.STYLE_ASSETS), set(build.STYLE_FILES))
        for names in build.STYLE_ASSETS.values():
            for name in names:
                self.assertTrue((build.TEMPLATES / name).is_file(), name)


def scripts_of(folder: Path) -> list[str]:
    return re.findall(r'<script src="([^"]+)"', (folder / "index.html").read_text(encoding="utf-8"))


class BroadcastBase(unittest.TestCase):
    """방송 기본 묶음(broadcast.js — 안전 영역 · 같이 싣는 글꼴 · 소품 아이콘)과 방송 화풍(HOUSE_STYLE) — 화풍을 통째로 바꿀 때를 대비한다."""

    def test_old_pages_in_old_styles_do_not_get_the_base(self):
        for style in build.LEGACY_STYLES:
            with self.subTest(style=style), tempfile.TemporaryDirectory() as temp:
                build.install_scene(Path(temp), style, "rank_board.html", board.FILES, "boardskin.js")
                self.assertNotIn(build.BROADCAST_FILE, scripts_of(Path(temp)))      # V1~V5 로 그리던 순위판은 예전 그대로

    def test_a_new_style_gets_the_base_on_every_page_before_its_own_file(self):
        with mock.patch.dict(request.ART_STYLES, {"v9": "V9 · 시험"}), mock.patch.dict(build.STYLE_FILES, {"v9": "style_v4.js"}):
            for page, files, skin in (("route_move.html", (build.STAGE, *build.SHARED), "mapskin.js"), ("rank_board.html", board.FILES, "boardskin.js")):
                with self.subTest(page=page), tempfile.TemporaryDirectory() as temp:
                    folder = Path(temp)
                    build.install_scene(folder, "v9", page, files, skin)
                    got = scripts_of(folder)
                    self.assertEqual(got.index(build.BROADCAST_FILE), got.index(skin) + 1)       # 기준 묶음 → 방송 기본 묶음 → 화풍 파일
                    self.assertEqual(got.index("style_v4.js"), got.index(skin) + 2)
                    for name in build.BROADCAST_ASSETS:
                        self.assertTrue((folder / name).is_file(), name)

    def test_scene_and_quiz_pages_always_get_the_base_whatever_the_style(self):
        for style in request.ART_STYLES:
            with self.subTest(style=style), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp) / "s"
                scene.install(folder, scene.SAMPLES["rows"], style)
                got = scripts_of(folder)
                self.assertIn(build.BROADCAST_FILE, got)
                if build.STYLE_FILES.get(style):
                    self.assertLess(got.index(build.BROADCAST_FILE), got.index(build.STYLE_FILES[style]))
                self.assertEqual(json.loads((folder / "job.json").read_text(encoding="utf-8"))["art_style"], style)
                self.assertTrue((folder / "fonts" / "Pretendard-Black.otf").is_file())

    def test_house_style_is_the_one_switch(self):
        self.assertIn(request.HOUSE_STYLE, request.ART_STYLES)
        self.assertEqual(request.DEFAULT_ART_STYLE, "claude")          # 화풍을 적지 않은 옛 봉투의 뜻은 그대로
        with tempfile.TemporaryDirectory() as temp:
            d = server.Desk(Path(temp), liner=False)
            self.assertEqual(d.state()["default_art_style"], request.HOUSE_STYLE)      # 확인 카드들이 고른 채로 시작하는 화풍
            folder = Path(temp) / "s"
            scene.install(folder, scene.SAMPLES["rows"])
            self.assertEqual(json.loads((folder / "job.json").read_text(encoding="utf-8"))["art_style"], request.HOUSE_STYLE)
        with self.assertRaises(ValueError):
            scene.install(Path(tempfile.gettempdir()) / "없는화풍", scene.SAMPLES["rows"], "v99")

    def test_card_quiz_takes_any_listed_style(self):
        raw = {"kind": "card_quiz", "title": "그림 퀴즈", "question": "다음 그림은?", "clue_line": "가 ? 가", "first_answer": "첫", "final_answer": "끝",
               "first_at": 10.3, "final_at": 13.5, "duration_seconds": 15, "output": {"width": 1920, "height": 1080, "fps": 30},
               "cards": [{"kind": "image", "subject": "빨간 공", "syllable": "가"}, {"kind": "missing", "text": "?", "reveal": "답"}]}
        self.assertEqual(quiz.normalize(raw)["art_style"], request.HOUSE_STYLE)
        self.assertEqual(quiz.normalize({**raw, "art_style": "v4"})["art_style"], "v4")
        with self.assertRaisesRegex(request.RequestError, "art_style"):
            quiz.normalize({**raw, "art_style": "v99"})

    def test_every_bundled_font_has_its_license(self):
        """같이 싣는 글꼴마다 사용권 원문(OFL_*.txt)이 있고 사용권 목록에 적혀 있다 — 글꼴을 더할 때 원문을 빠뜨리지 않게."""
        fonts = build.TEMPLATES / "fonts"
        table = (fonts / "글꼴_사용권.md").read_text(encoding="utf-8")
        for f in sorted([*fonts.glob("*.ttf"), *fonts.glob("*.otf")]):
            with self.subTest(font=f.name):
                family = f.stem.split("-")[0]
                lic = fonts / f"OFL_{family}.txt"
                self.assertTrue(lic.is_file(), lic.name)
                self.assertIn("SIL OPEN FONT LICENSE Version 1.1", lic.read_text(encoding="utf-8"))
                self.assertIn(f.name, table)
        self.assertIn("fonts/OFL_Pretendard.txt", build.BROADCAST_ASSETS)          # 프리텐다드를 싣는 작업 폴더에는 원문도 같이

    def test_base_values_match_v5(self):
        """V5 로 그린 화면이 이 묶음의 유무와 상관없이 같게 — 두 파일의 값이 어긋나지 않는지 본다."""
        base = (build.TEMPLATES / "broadcast.js").read_text(encoding="utf-8")
        v5 = (build.TEMPLATES / "style_v5.js").read_text(encoding="utf-8")
        for piece in ("safe: .05", "minText: 18", "const FONT = \"'MapcgPretendard','Pretendard','Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif\";",
                      "[['Pretendard-Black.otf', '800 900'], ['Pretendard-Bold.otf', '100 700']]",
                      "{ A: sheet('icons/icons_A_games.png'), B: sheet('icons/icons_B_score.png'), cell: 256, inset: 72, crop: 112 }"):
            self.assertIn(piece, base, piece)
            self.assertIn(piece, v5, piece)
