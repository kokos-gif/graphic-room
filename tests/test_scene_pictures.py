"""맞춤 그래픽의 항목 그림 — 아이콘 코드가 없는 항목 이름(곰인형 · 기차 …)에 Liner 그림 모델이 그린 작은 그림을 붙인다.
여기서는 그림을 마련하는 쪽(finder)을 가짜로 넣는다 — Liner 를 부르지 않는다."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from mapcg import scene


def png(path: Path) -> Path:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (32, 32), (240, 120, 60, 255)).save(buf, "PNG")
    path.write_bytes(buf.getvalue())
    return path


def columns() -> dict:
    return scene.normalize_scene({
        "layout": "columns", "title": "아이템 구매리스트",
        "groups": [{"id": "m", "name": "금액"}, {"id": "i", "name": "아이템"}],
        "items": [{"id": "m1", "group": "m", "label": "① 10000", "value_text": "만원"},
                  {"id": "i1", "group": "i", "label": "① 곰인형"},
                  {"id": "i2", "group": "i", "label": "2. 기차"},
                  {"id": "i3", "group": "i", "label": "공", "icon": "A01"},
                  {"id": "i4", "group": "i", "label": "빈 칸", "empty": True}],
        "links": [{"from": "m1", "to": "i2"}]})


class AttachPictures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def finder(self, have):
        asked = []

        def find(labels):
            asked.append(list(labels))
            return {"icons": {w: {"file": str(png(self.dir / f"{n}.png")), "subject": "x", "source": "Liner" if n else "보관"}
                              for n, w in enumerate(labels) if w in have}, "notes": []}
        return find, asked

    def test_names_without_icon_get_a_picture_and_leading_numbers_are_dropped(self):
        s = columns()
        find, asked = self.finder({"만원", "곰인형", "기차"})
        got = scene.attach_pictures(s, find)
        # 번호(① · 2.)는 떼고, 이름이 숫자뿐이면 값 글자(만원)로 묻는다. 아이콘이 있는 항목 · 빈 칸은 묻지 않는다
        self.assertEqual(asked, [["만원", "곰인형", "기차"]])
        self.assertEqual(scene._pic_word({"label": "2만원"}), "2만원")
        self.assertEqual(scene._pic_word({"label": "③ 30000", "value_text": "", "text": ""}), "")
        by = {it["id"]: it.get("picture") for it in s["items"]}
        self.assertTrue(by["m1"] and by["i1"] and by["i2"])
        self.assertIsNone(by["i3"])
        self.assertIsNone(by["i4"])
        self.assertEqual(got["names"], ["만원", "곰인형", "기차"])
        self.assertIn("항목 그림 3장", got["notes"][0])
        self.assertEqual(scene._pic_word({"label": "3.5kg  아령"}), "3.5kg 아령")       # 소수점은 번호가 아니다 · 빈칸은 하나로

    def test_one_picture_shared_by_several_names_is_counted_once(self):
        s = columns()
        one = png(self.dir / "돈.png")
        got = scene.attach_pictures(s, lambda labels: {"icons": {w: {"file": str(one), "source": "Liner"} for w in ("만원", "곰인형")}, "notes": []})
        self.assertEqual(got["names"], ["만원", "곰인형"])
        self.assertIn("항목 그림 1장(Liner 그림 모델 — 새로 1 · 보관 0)", got["notes"][0])

    def test_no_finder_or_a_layout_without_icon_room_does_nothing(self):
        s = columns()
        self.assertEqual(scene.attach_pictures(s, None), {"names": [], "notes": []})
        big = scene.normalize_scene({"layout": "headline", "title": "5", "items": [{"id": "a", "label": "곰인형"}]})
        find, asked = self.finder({"곰인형"})
        self.assertEqual(scene.attach_pictures(big, find)["names"], [])
        self.assertEqual(asked, [])

    def test_a_failing_finder_leaves_the_scene_without_pictures(self):
        s = columns()

        def boom(labels):
            raise RuntimeError("그림 서버가 답하지 않음")
        got = scene.attach_pictures(s, boom)
        self.assertEqual(got["names"], [])
        self.assertIn("항목 그림 생략", got["notes"][0])
        self.assertTrue(all(not it.get("picture") for it in s["items"]))

    def test_a_slow_finder_is_not_waited_for(self):
        s = columns()

        def slow(labels):
            time.sleep(1.0)
            return {"icons": {}, "notes": []}
        t = time.perf_counter()
        got = scene.attach_pictures(s, slow, wait=0.05)
        self.assertLess(time.perf_counter() - t, 0.8)
        self.assertIn("그림 없이", got["notes"][0])

    def test_a_missing_file_is_skipped(self):
        s = columns()
        got = scene.attach_pictures(s, lambda labels: {"icons": {"곰인형": {"file": str(self.dir / "없음.png"), "source": "보관"}}, "notes": []})
        self.assertEqual(got["names"], [])

    def test_install_hands_the_pictures_to_the_page(self):
        s = columns()
        find, _ = self.finder({"곰인형"})
        scene.attach_pictures(s, find)
        job = self.dir / "job"
        scene.install(job, s)
        text = (job / "job.js").read_text(encoding="utf-8")
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        self.assertEqual(list(data["pics"]), ["i1"])
        self.assertTrue(data["pics"]["i1"].startswith("data:image/png;base64,"))
        page = (job / "index.html").read_text(encoding="utf-8")
        self.assertIn("iconOf(it", page)

    def test_a_scene_without_pictures_installs_as_before(self):
        job = self.dir / "job2"
        scene.install(job, columns())
        text = (job / "job.js").read_text(encoding="utf-8")
        self.assertEqual(json.loads(text[text.index("{"):text.rindex("}") + 1])["pics"], {})


if __name__ == "__main__":
    unittest.main()
