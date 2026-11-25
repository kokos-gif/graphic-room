"""각자 이동(컷마다 다른 사람이 따로) · 장소 안의 시설 찾기 · 걸어서 가는 길 · 따라가는 카메라. 가짜 지도·가짜 Liner 로 본다(밖으로 아무것도 보내지 않는다)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import build, cuts, geo, intake, moves, request

ROOT = Path(__file__).resolve().parent.parent
PARK = "여의도 공원"
# 공원 경계(비스듬한 네모)와 그 안팎의 시설
RING = [[37.5240, 126.9180], [37.5300, 126.9240], [37.5285, 126.9270], [37.5225, 126.9210]]
FEATS = [
    {"name": "여의도공원", "tags": {"leisure": "park"}, "lat": 37.5262, "lon": 126.9225, "bounds": [37.5225, 126.9180, 37.5300, 126.9270], "ring": RING},
    {"name": "여의도공원 문화의마당", "tags": {"highway": "pedestrian"}, "lat": 37.5248, "lon": 126.9203},
    {"name": "여의도공원 태극기게양대", "tags": {"man_made": "flagpole"}, "lat": 37.5255, "lon": 126.9215},
    {"name": "", "tags": {"leisure": "playground"}, "lat": 37.5264, "lon": 126.9230},
    {"name": "여의도공원 자연생태의숲", "tags": {"landuse": "forest"}, "lat": 37.5240, "lon": 126.9200, "bounds": [37.5235, 126.9192, 37.5246, 126.9208]},
    {"name": "", "tags": {"natural": "water", "water": "pond"}, "lat": 37.5240, "lon": 126.9201},
    {"name": "여의도공원 지당", "tags": {"natural": "water", "water": "pond"}, "lat": 37.5285, "lon": 126.9250},
    {"name": "", "tags": {"entrance": "yes", "ref": "5"}, "lat": 37.5252, "lon": 126.9287},       # 공원 밖 건물의 5번 출입구
    {"name": "여의도공원1번출입구", "tags": {"highway": "bus_stop"}, "lat": 37.5236, "lon": 126.9205},
]


def fake_geo(tmp: str) -> geo.Geo:
    g = geo.Geo(Path(tmp), offline=True)
    g.features = lambda lat, lon, radius_m=700: [dict(f) for f in FEATS]
    return g


class FindFeature(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.g = fake_geo(self.tmp.name)

    def find(self, name):
        return self.g.find_feature(name, 37.5262, 126.9225, area=PARK)

    def test_공원_이름이_붙어_실린_시설을_적힌_이름으로_찾는다(self):
        hit = self.find("문화의 마당중앙")
        self.assertEqual((hit["display"], hit["how"]), ("여의도공원 문화의마당", "name"))

    def test_한두_글자_틀린_이름도_찾는다(self):
        self.assertEqual(self.find("태극기 계양대")["display"], "여의도공원 태극기게양대")

    def test_이름_없는_시설은_종류로_하나뿐일_때만(self):
        hit = self.find("여의도 공원놀이터")
        self.assertEqual((hit["how"], hit["lat"]), ("kind", 37.5264))

    def test_고유한_말이_붙은_이름은_종류로_고르지_않는다(self):
        with self.assertRaises(geo.GeoNotFound):      # '생태연못'을 다른 연못('지당')으로 잡지 않는다
            self.find("여의도공원생태연못")

    def test_공원_밖의_같은_번호_출입구와_공원_그_자체는_집지_않는다(self):
        with self.assertRaises(geo.GeoNotFound):
            self.find("공원출입구 5번인근")

    def test_후보_목록은_공원_안의_것만_어느_숲_안인지와_함께(self):
        labels = [c["label"] for c in self.g.candidates(37.5262, 126.9225, area=PARK)]
        self.assertIn("이름 없는 연못 — '여의도공원 자연생태의숲' 안", labels)
        self.assertFalse(any("번호 5" in s for s in labels), labels)
        self.assertFalse(any(s.startswith("여의도공원 (") or s == "여의도공원" for s in labels), labels)


class Reading(unittest.TestCase):
    def test_각자_이동_장소_카메라_걷기를_초안에_싣는다(self):
        draft, missing = intake._draft(src="문화의 마당", dst="출입구", vehicle="walk", duration=None)
        d = {"area_name": PARK, "camera": "follow", "separate_moves": True, "cuts": [
            {"who": "A", "from_name": "문화의 마당", "to_name": "놀이터", "duration_seconds": 6, "stops": [], "note": ""},
            {"who": "B", "from_name": "잔디마당", "to_name": "생태연못", "duration_seconds": 6, "stops": [], "note": ""}]}
        intake._extras(draft, missing, d, [])
        self.assertEqual((draft["vehicle"], draft["area"], draft["camera"], draft["separate"]), ("walk", {"name": PARK}, "follow", True))
        self.assertEqual([c["who"] for c in draft["cuts"]], ["A", "B"])

    def test_간이_해석도_걸어서를_알아본다(self):
        self.assertEqual(intake._vehicle("출연진이 걸어서 이동"), "walk")

    def test_봉투가_카메라와_장소를_받는다(self):
        req = request.normalize({"from": "가", "to": "나", "duration_seconds": 6, "vehicle": "walk", "camera": "follow", "area": PARK, "who": "A"})
        self.assertEqual((req["vehicle"], req["camera"], req["area"], req["who"]), ("walk", "follow", {"name": PARK}, "A"))
        self.assertNotIn("camera", request.normalize({"from": "가", "to": "나", "duration_seconds": 6}))      # 고정은 적지 않는다(예전 봉투와 같은 모양)
        with self.assertRaises(request.RequestError):
            request.normalize({"from": "가", "to": "나", "duration_seconds": 6, "camera": "drone"})


class Camera(unittest.TestCase):
    def osrm(self, km):
        return {"routes": [{"geometry": {"coordinates": [[126.92, 37.52], [126.92 + km / 88.3, 37.52]]}}]}

    def test_따라가는_카메라는_경로의_40퍼센트를_한_화면에(self):
        cam = build.follow_camera({"camera": "follow"}, self.osrm(0.6), view_w=540, pw=1280)
        self.assertEqual(cam["mode"], "follow")
        self.assertAlmostEqual(cam["zoom"], 540 / 240, delta=0.2)

    def test_바탕이_너무_커지지_않게_막는다(self):
        cam = build.follow_camera({"camera": "follow"}, self.osrm(0.4), view_w=5000, pw=1920)
        self.assertLessEqual(cam["zoom"] * 1920, build.MAX_WORLD_PX + 1)

    def test_따라갈_것이_없으면_고정(self):
        self.assertIsNone(build.follow_camera({"camera": "follow"}, self.osrm(3), view_w=1200, pw=1920))
        self.assertIsNone(build.follow_camera({}, self.osrm(3), view_w=9000, pw=1920))

    def test_화면이_카메라와_걷는_사람을_그린다(self):
        html = (ROOT / "mapcg" / "templates" / "route_move.html").read_text(encoding="utf-8")
        for word in ("function camAt", "function walker", "MapKit(M,MW,MH", "J.vehicle==='walk'"):
            self.assertIn(word, html)
        self.assertIn("걸은 거리", (ROOT / "mapcg" / "templates" / "style_v5.js").read_text(encoding="utf-8"))

    def test_얼굴은_따라가는_카메라의_바탕_안에서_차를_따라간다(self):
        # 11-24: 화면 너비(PW)로 묶어 차가 바탕 오른쪽으로 가면 얼굴이 한 자리에 걸려 차와 떨어졌다
        html = (ROOT / "mapcg" / "templates" / "route_move.html").read_text(encoding="utf-8")
        faces = html[html.index("function drawFaces"):html.index("function ground")]
        self.assertIn("Math.min(MW-w-10*U", faces)
        self.assertNotIn("PW-w", faces)


class FakeGeo:
    """moves.make 가 부르는 지도 쪽 — 이름표대로 자리를 돌려주고, 경로는 두 점을 곧게 잇는다."""
    PLACES = {"여의도공원": (37.5262, 126.9225), "문화의마당": (37.5248, 126.9203), "놀이터": (37.5264, 126.9230), "생태연못": (37.5240, 126.9201),
              "잔디마당전국": (35.10, 129.03)}

    def __init__(self, *a, **k):
        self.routes = []

    def geocode_loose(self, name, near=None, km=30):
        key = name.replace(" ", "")
        if key == "잔디마당":       # 전국에서 찾으면 부산의 같은 이름이 걸린다
            lat, lon = self.PLACES["잔디마당전국"]
            return {"name": name, "lat": lat, "lon": lon, "display": "부산 어딘가 잔디마당", "candidates": []}
        if key not in self.PLACES:
            raise geo.GeoNotFound(name)
        lat, lon = self.PLACES[key]
        return {"name": name, "lat": lat, "lon": lon, "display": key, "candidates": []}

    def find_feature(self, name, lat, lon, area="", radius_m=700):
        key = name.replace(" ", "").replace("여의도공원", "")
        if key in ("문화의마당", "놀이터"):
            return {**self.geocode_loose(key), "name": name, "how": "name"}
        raise geo.GeoNotFound(name)

    def candidates(self, lat, lon, area="", radius_m=700, limit=60):
        return [{"label": "이름 없는 연못 — '자연생태의숲' 안", "lat": 37.5240, "lon": 126.9201, "display": "이름 없는 연못"}]

    def route(self, a, b, foot=False):
        self.routes.append(("route", foot))
        return {"routes": [{"geometry": {"coordinates": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]]}, "legs": [{"distance": 300}]}]}

    def route_via(self, pts, foot=False):
        self.routes.append(("via", foot))
        return {"routes": [{"geometry": {"coordinates": [[p["lon"], p["lat"]] for p in pts]}, "legs": [{"distance": 100}] * (len(pts) - 1)}]}


class Make(unittest.TestCase):
    RAW = {"from": "문화의 마당", "to": "생태연못", "vehicle": "walk", "camera": "follow", "area": PARK, "art_style": "v5", "separate": True,
           "cuts": [{"who": "A", "from": "문화의 마당", "to": "놀이터", "duration_seconds": 6, "stops": [{"name": "놀이터", "caption": "발견", "pass": False}]},
                    {"who": "B", "from": "잔디마당", "to": "생태연못", "duration_seconds": 6}]}

    def run_make(self, raw, picker=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made, fake = [], FakeGeo()

        def make_job(req, *, job_dir, extra, osrm, **k):
            job_dir.mkdir(parents=True)
            made.append((req, extra, osrm))
            return {"dir": job_dir, "warnings": [], "map": {"source": "server", "detail": "foot", "counts": {"roads": 3}}}
        with mock.patch.object(moves, "Geo", lambda *a, **k: fake), mock.patch.object(moves.build, "make_job", make_job):
            job = cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None, picker=picker)
        return job, made, fake

    def test_지도에_없는_시작은_멈추고_찍어_달라고_한다_다른_지역의_같은_이름은_버린다(self):
        with self.assertRaises(request.RequestError) as e:
            self.run_make(self.RAW)
        said = " ".join(e.exception.problems)
        self.assertIn("컷 2 · B의 시작 '잔디마당'", said)
        self.assertIn("📍", said)
        self.assertIn("떨어진 곳", said)       # 부산의 '잔디마당'은 쓰지 않았다고 알린다

    def test_찍은_지점으로_컷마다_걸어서_따로_경로를_받는다(self):
        raw = json.loads(json.dumps(self.RAW))
        raw["cuts"][1]["from_pin"] = {"lat": 37.5277, "lon": 126.9240}
        job, made, fake = self.run_make(raw)
        self.assertEqual(len(made), 2)
        self.assertTrue(all(foot for _, foot in fake.routes))       # 걸어서 가는 길
        req1, extra1, _ = made[0]
        self.assertEqual((req1["who"], req1["camera"], req1["vehicle"], extra1["to_caption"]), ("A", "follow", "walk", "발견"))
        self.assertEqual((made[1][0]["from"]["lat"], made[1][1]["flow"]["card"]), (37.5277, True))
        rec = job["request"]
        self.assertTrue(rec["separate"])
        self.assertEqual(rec["cuts"][0]["from_pin"], {"lat": 37.5248, "lon": 126.9203})      # 다시 만들 때 지명을 다시 찾지 않는다
        self.assertTrue((job["dir"] / "index.html").is_file())

    def test_지도에_그_이름이_없으면_Liner_가_검색_근거로_후보를_고른다(self):
        raw = json.loads(json.dumps(self.RAW))
        raw["cuts"][1]["from_pin"] = {"lat": 37.5277, "lon": 126.9240}
        raw["cuts"][1]["to"] = "자연생태연못"
        seen = []

        def picker(name, area, cands):
            seen.append((name, area, len(cands)))
            return {"index": 0, "sources": ["https://예시"], "why": "검색 결과에 자연생태의 숲 안의 연못"}
        job, made, _ = self.run_make(raw, picker=picker)
        self.assertEqual(seen, [("자연생태연못", PARK, 1)])
        self.assertEqual(made[1][0]["to"]["lat"], 37.5240)
        self.assertTrue(any("Liner 가 웹 검색을 근거로" in w and "확인이 필요한 지점" in w for w in job["warnings"]))


class MakeChecks(unittest.TestCase):
    """입력 검사는 작업 폴더를 만들기 전에 · 못 찾은 이름은 팀마다 다시 묻지 않는다."""

    def make(self, raw, searcher=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "out"

        def make_job(req, *, job_dir, extra, osrm, **k):
            job_dir.mkdir(parents=True)
            return {"dir": job_dir, "warnings": [], "map": {"source": "server", "detail": "foot", "counts": {"roads": 3}}}
        with mock.patch.object(moves, "Geo", lambda *a, **k: FakeGeo()), mock.patch.object(moves.build, "make_job", make_job):
            job = cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=out, log=lambda s: None, searcher=searcher)
        return job, out

    def test_같은_자리_컷은_폴더를_만들기_전에_멈춘다(self):
        raw = json.loads(json.dumps(Make.RAW))
        raw["cuts"][1].update(from_pin={"lat": 37.5277, "lon": 126.9240}, to_pin={"lat": 37.5277, "lon": 126.9240})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "out"
        with mock.patch.object(moves, "Geo", lambda *a, **k: FakeGeo()), self.assertRaises(request.RequestError) as e:
            cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=out, log=lambda s: None)
        self.assertIn("같은 자리로 잡혔습니다", " ".join(e.exception.problems))
        self.assertFalse(out.exists() and any(out.iterdir()))       # 빈 작업 폴더를 남기지 않는다

    def test_못_찾은_이름은_팀마다_다시_검색하지_않는다(self):
        raw = json.loads(json.dumps(Make.RAW))
        raw["cuts"][1]["from_pin"] = {"lat": 37.5277, "lon": 126.9240}
        for c in raw["cuts"]:
            c["stops"] = [{"name": "없는정자", "caption": "", "pass": True}]
        asked = []
        job, _ = self.make(raw, searcher=lambda name, near=None: asked.append(name))
        self.assertEqual(asked, ["없는정자"])
        self.assertEqual(sum("없는정자" in w for w in job["warnings"]), 2)      # 알림은 컷마다


class Picker(unittest.TestCase):
    RESULTS = {"results": [{"title": "여의도공원 둘러보기", "url": "https://예시/1", "description": "자연생태의 숲은 생태연못을 중심으로"}]}
    CANDS = [{"label": "놀이터"}, {"label": "이름 없는 연못 — '여의도공원 자연생태의숲' 안"}]

    def ask(self, answer):
        chat = lambda messages, **o: {"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]}      # noqa: E731
        return intake.picked_place("생태연못", PARK, self.CANDS, chat=chat, search=lambda q, **k: self.RESULTS)

    def test_근거와_함께_고른_후보(self):
        self.assertEqual(self.ask({"choice": 2, "sources": [1], "why": "검색 결과"})["index"], 1)

    def test_근거가_없거나_고르지_않으면_받지_않는다(self):
        self.assertIsNone(self.ask({"choice": 2, "sources": [], "why": ""}))
        self.assertIsNone(self.ask({"choice": 0, "sources": [1], "why": "모름"}))
        self.assertIsNone(self.ask({"choice": 9, "sources": [1], "why": ""}))


if __name__ == "__main__":
    unittest.main()
