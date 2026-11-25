"""11-24 저녁 상암동 SUV 의뢰에서 나온 두 가지.

① 들르는 곳의 화면 이름(label): '스탠포드호텔 (상호노출X)'을 지도 카드에 담을 칸이 없어 맞춤 그래픽의 하단 띠로 넘어가고,
   지도에는 상호가 그대로 나왔다. 지도에서는 name 으로 찾고, 이름표에는 label 을 쓴다.
② 이름은 확인했는데 주소가 없는 곳: '상암디지털 우체국'은 Liner 검색이 정식 이름만 주고 주소를 주지 않아 빠졌다.
   주소만 한 번 더 찾는다. 지도 서버 · Liner 는 부르지 않는다(가짜로 바꿔 끼운다).
"""
import json
import unittest
from pathlib import Path

from mapcg import build, cuts, intake, request, revise
from mapcg.geo import GeoNotFound

ROOT = Path(__file__).resolve().parents[1]


def reply(obj):
    return lambda messages, **options: {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


class 화면_이름(unittest.TestCase):
    def test_읽은_들르는_곳은_상호를_가린_화면_이름을_따로_가진다(self):
        notes = []
        got = intake.route_stops([
            {"name": "스탠포드호텔", "label": "호텔", "caption": "", "pass": False, "stop": True, "hold_seconds": 2},
            {"name": "부엉이 근린공원", "label": "부엉이 근린공원", "caption": "미션 1", "pass": False, "stop": False, "hold_seconds": None},
            {"name": "상암디지털 우체국", "label": "", "caption": "미션2", "pass": False, "stop": False, "hold_seconds": None}], 12, notes)
        self.assertEqual([s.get("label") for s in got], ["호텔", None, None])     # 지명과 같거나 비면 따로 두지 않는다
        self.assertEqual(got[0]["name"], "스탠포드호텔")                          # 지도에서는 적힌 지명으로 찾는다
        self.assertIn("스탠포드호텔[화면에는 '호텔']", notes[-1])

    def test_읽기_지시문과_틀에_화면_이름_칸이_있다(self):
        props = intake.SCHEMA["properties"]
        for items in (props["route_stops"]["items"], props["cuts"]["items"]["properties"]["stops"]["items"]):
            self.assertIn("label", items["required"])
        self.assertIn("상호노출X", intake.KIND_ITEMS)
        self.assertIn("상호노출X", intake.ITEMS)
        self.assertIn("label 로 담았으므로 여기에 적지 않는다", intake.KIND_ITEMS)      # 남은 요청(하단 띠)으로 넘기지 않는다

    def test_의뢰_봉투와_컷_나눔이_화면_이름을_지킨다(self):
        problems = []
        stops = request.route_stops([{"name": "스탠포드호텔", "label": " 호텔 ", "hold": 2}], 12, problems)
        self.assertEqual((stops[0]["name"], stops[0]["label"], problems), ("스탠포드호텔", "호텔", []))
        self.assertNotIn("label", request.route_stops([{"name": "부엉이근린공원"}], 12, [])[0])
        src = (ROOT / "mapcg" / "cuts.py").read_text(encoding="utf-8")
        self.assertIn('"name": st.get("label") or show(st["name"])', src)

    def test_지도_이름표는_화면_이름을_먼저_쓴다(self):
        html = (ROOT / "mapcg" / "templates" / "route_move.html").read_text(encoding="utf-8")
        self.assertIn("name:s.label||s.name", html)
        self.assertIn("label", (ROOT / "mapcg" / "web" / "app.js").read_text(encoding="utf-8").split("const rows = d.stops.map")[1][:400])

    def test_수정_지시에서_화면_이름만_바뀌어도_바뀐_것으로_본다(self):
        before = revise.stops_line([{"name": "스탠포드호텔", "caption": "", "pass": False, "hold": 2}])
        after = revise.stops_line([{"name": "스탠포드호텔", "label": "호텔", "caption": "", "pass": False, "hold": 2}])
        self.assertNotEqual(before, after)
        self.assertIn("label", revise.current_map({"stops": [{"name": "스탠포드호텔", "label": "호텔"}]})["route_stops"][0])


SPOTS = {"서울 마포구 상암산로1길 75": (37.5791, 126.8899)}


class FakeGeo:
    def __init__(self):
        self.asked = []

    def geocode_loose(self, name, country="kr", near=None, km=30):
        self.asked.append(name)
        if name not in SPOTS:
            raise GeoNotFound(f"'{name}' 을(를) 지도에서 찾지 못했습니다. 더 구체적인 이름이나 좌표를 넣어 주세요.")
        lat, lon = SPOTS[name]
        return {"name": name, "lat": lat, "lon": lon, "display": name, "candidates": []}


def find(geo, place):
    return build._resolve(geo, place)


class 주소_한_번_더(unittest.TestCase):
    def searcher(self, name_answer, address_answer):
        calls = []

        def search(name, near="", want="name"):
            calls.append((name, want))
            return address_answer if want == "address" else name_answer
        search.calls = calls
        return search

    def test_이름만_확인되고_주소가_없으면_주소를_한_번_더_찾아_잡는다(self):
        geo = FakeGeo()
        search = self.searcher({"search_name": "상암디지털 우체국", "sources": ["https://example.invalid/name"]},
                               {"address": "서울 마포구 상암산로1길 75", "sources": ["https://example.invalid/addr"]})
        hit = build.resolve_or_search(geo, {"name": "상암디지털 우체국"}, find, search, ("하늘초등학교",), notes := [])
        self.assertEqual((hit["name"], hit["lat"]), ("상암디지털 우체국", 37.5791))      # 화면 이름표는 적힌 이름 그대로
        self.assertIn("주소로 찾음", hit["display"])
        self.assertEqual(search.calls, [("상암디지털 우체국", "name"), ("상암디지털 우체국", "address")])
        self.assertIn("근거: https://example.invalid/addr", notes[0])

    def test_이름을_확인하지_못했으면_주소를_찾지_않는다(self):
        search = self.searcher(None, {"address": "서울 마포구 상암산로1길 75", "sources": ["https://example.invalid/addr"]})
        with self.assertRaises(GeoNotFound):
            build.resolve_or_search(FakeGeo(), {"name": "어딘가"}, find, search, (), [])
        self.assertEqual(search.calls, [("어딘가", "name")])

    def test_주소_찾기를_모르는_검색_함수면_처음의_찾지_못함을_올린다(self):
        def old_search(name, near=""):
            return {"search_name": name, "sources": ["https://example.invalid"]}
        with self.assertRaises(GeoNotFound) as got:
            build.resolve_or_search(FakeGeo(), {"name": "상암동 쉼터정원"}, find, old_search, (), [])
        self.assertIn("'상암동 쉼터정원' 을(를) 지도에서 찾지 못했습니다", str(got.exception))

    def test_주소_찾기는_링크_주소를_풀어서_근거로_넘기고_근거_없는_주소는_버린다(self):
        url = "https://www.waze.com/live-map/directions/%EC%83%81%EC%95%94%EC%82%B0%EB%A1%9C1%EA%B8%B8-75"
        asked = {}

        def search(query, **o):
            asked["query"] = query
            return {"results": [{"title": "길안내", "url": url, "description": ""}]}

        def chat(messages, **o):
            asked["body"] = messages[1]["content"][0]["text"]
            return reply({"address": "서울 마포구 상암산로1길 75", "sources": [1]})(messages, **o)

        got = intake.searched_name("상암디지털 우체국", near="하늘초등학교", chat=chat, search=search, want="address")
        self.assertEqual(got, {"address": "서울 마포구 상암산로1길 75", "sources": [url]})
        self.assertEqual(asked["query"], "상암디지털 우체국 도로명주소")      # 다른 지명을 붙이지 않는다
        self.assertIn("상암산로1길-75", asked["body"])
        no_source = reply({"address": "서울 마포구 상암산로1길 75", "sources": []})
        self.assertIsNone(intake.searched_address("상암디지털 우체국", chat=no_source, search=search))


if __name__ == "__main__":
    unittest.main()
