"""제출 묶음 도구 — 들어가면 안 되는 이름을 가려내는지 본다(묶음을 실제로 만들지는 않는다)."""
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("pack", Path(__file__).resolve().parent.parent / "tools" / "pack.py")
pack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pack)


class Forbidden(unittest.TestCase):
    def test_키_파일과_결과물_폴더를_가려낸다(self):
        names = [".env", "mapcg/.env", ".env.kakao", "output/a/지도.mp4", "data/geo/x.json", "시험결과/결과.md", "제출/옛것.zip", ".git/config"]
        self.assertEqual(pack.forbidden(names), names)

    def test_소스와_예시_파일은_통과한다(self):
        ok = [".env.example", ".gitignore", "README.md", "requirements.txt", "liner.py", "mapcg/server.py", "mapcg/templates/icons/icons_A_games.png",
              "tests/test_pack.py", "tools/pack.py", "examples/월드컵경기장_노들로_4컷.json", "자산/얼굴/01.tga", "mapcg/data_notes.md", "outputs.md"]
        self.assertEqual(pack.forbidden(ok), [])

    def test_금지_파일이_든_묶음은_이름을_바꿔_둔다(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "mapcg_제출_20261121-1200.zip"
            out.write_bytes(b"zip")
            blocked = pack.quarantine(out)
            self.assertEqual(blocked.name, "mapcg_제출_20261121-1200_제출금지.zip")
            self.assertEqual((out.exists(), blocked.read_bytes()), (False, b"zip"))      # 정상 묶음의 이름으로는 남지 않는다

    def test_저장소의_깃_제외_목록이_제출_폴더를_뺀다(self):
        ignore = (pack.ROOT / ".gitignore").read_text(encoding="utf-8").split()
        for name in (".env", "output/", "시험결과/", "제출/"):
            self.assertIn(name, ignore)


if __name__ == "__main__":
    unittest.main()
