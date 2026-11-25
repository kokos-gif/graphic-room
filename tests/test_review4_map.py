"""앱 수정지침(a27d633) 03 · 04 · 05 의 재현 사례 — 전부 가짜 지도·가짜 경로 서비스·임시 폴더로 본다(지도 서버·Liner 를 실제로 부르지 않는다).

03 각자 이동의 컷 길이만 고쳐도 출연자와 찍은 양 끝 좌표가 남는다 · 04 이어지는 도보 컷은 보행 경로를 받는다 · 05 다시 들르는 경유지를 빼지 않는다
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mapcg import cuts, geo, moves, revise

ROOT = Path(__file__).resolve().parent.parent
BASE = {"from": {"name": "가"}, "to": {"name": "라"}, "output": {"width": 960, "height": 540, "fps": 30}}


def spot(name, lat, lon, **kw):
    return {"name": name, "lat": lat, "lon": lon, "display": f"{name}, 서울", "candidates": [], **kw}


# 가 → 나 → 다 는 동쪽으로 한 줄, 라 는 나 의 북쪽 — '가 → 나 → 다 → 나 → 라' 는 나 를 두 번 지난다
A, B, C, D = spot("가", 37.50, 126.90), spot("나", 37.50, 126.92), spot("다", 37.50, 126.94), spot("라", 37.52, 126.92)


def cut(a, b, sec=4, **kw):
    return {"from": a, "to": b, "duration_seconds": sec, **kw}


def at(p):
    return (round(p["lat"], 5), round(p["lon"], 5))


def line_m(*pts):
    return cuts.route_length([[p["lon"], p["lat"]] for p in pts])


def fake_job(seen):
    """화면을 만들지 않는 가짜 build.make_job — 컷마다 넘어온 의뢰만 적어 둔다."""
    def make_job(req, *, job_dir, extra, osrm, **k):
        job_dir.mkdir(parents=True)
        seen.append(req)
        return {"dir": job_dir, "warnings": [], "map": {"source": "server", "detail": "minor", "counts": {"roads": 3}}}
    return make_job


class FakeGeo:
    """이어지는 컷(cuts.make)의 가짜 지도 — 경로는 지점들을 곧게 잇고, 어떤 경로를 무엇으로 물었는지 적어 둔다."""
    places: dict = {}

    def __init__(self, *a, **k):
        self.calls = {"geocode": [], "route": [], "water": []}
        self.budget = geo.Budget()
        FakeGeo.last = self

    def geocode_loose(self, name, country="kr", near=None, km=30):
        self.calls["geocode"].append(name)
        if name not in self.places:
            raise geo.GeoNotFound(f"'{name}' 없음")
        return self.places[name]

    @staticmethod
    def answer(points):
        line = [[p["lon"], p["lat"]] for p in points]
        return {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": line}, "distance": cuts.route_length(line), "duration": 600,
                                          "legs": [{"distance": cuts.route_length(line[i:i + 2])} for i in range(len(line) - 1)]}]}

    def route_via(self, points, foot=False):
        self.calls["route"].append((foot, [at(p) for p in points]))
        return self.answer(points)

    def water_route_via(self, points):
        self.calls["water"].append([at(p) for p in points])
        return self.answer(points)


class CutsBase(unittest.TestCase):
    def setUp(self):
        FakeGeo.places = {"가": A, "나": B, "다": C, "라": D}

    def make(self, raw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(cuts, "Geo", FakeGeo), mock.patch.object(cuts.build, "make_job", fake_job([])):
            job = cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None)
        return job, FakeGeo.last.calls


class MovesGeo:
    """각자 이동(moves.make)의 가짜 지도 — 지명은 하나도 모른다(찾으러 오면 이름을 적어 두고 못 찾았다고 답한다)."""

    def __init__(self):
        self.asked, self.routes = [], []

    def geocode_loose(self, name, *a, **k):
        self.asked.append(name)
        raise geo.GeoNotFound(name)

    def find_feature(self, name, *a, **k):
        self.asked.append(name)
        raise geo.GeoNotFound(name)

    def candidates(self, *a, **k):
        return []

    def route(self, a, b, foot=False, **k):
        self.routes.append(foot)
        return FakeGeo.answer([a, b])

    def route_via(self, pts, foot=False):
        self.routes.append(foot)
        return FakeGeo.answer(pts)


def revision(**over):
    """수정 모델의 답: 말하지 않은 항목은 null · same · false · 빈 배열."""
    return {"from_name": None, "to_name": None, "vehicle": "same", "vehicle_color": None, "duration_seconds": None, "layout": "same",
            "face_count": None, "face_mode": "same", "map_style": "same", "art_style": "same", "output_width": None, "output_height": None,
            "fps": None, "cuts_changed": False, "cuts": [], "unsupported": [], "unclear": [], **over}


def said(a, b, sec):
    """수정 모델이 적어 준 컷 — 모델은 출연자(who)와 찍은 좌표를 받지 못하므로 비워서 돌려준다."""
    return {"who": "", "from_name": a, "to_name": b, "duration_seconds": sec, "bridge": False, "closeup": False, "stops": [], "note": ""}


class SeparateMovesRevision(unittest.TestCase):
    """03 — 두 출연자가 각자 이동하는 컷을 2초에서 4초로 고쳐도 두 사람의 이름과 네 좌표가 남고, 양 끝 지명을 다시 찾지 않는다."""
    PINS = [({"lat": 37.5248, "lon": 126.9203}, {"lat": 37.5264, "lon": 126.9230}), ({"lat": 37.5277, "lon": 126.9240}, {"lat": 37.5240, "lon": 126.9201})]
    RAW = {"from": {"name": "문화의 마당"}, "to": {"name": "생태연못"}, "vehicle": "walk", "separate": True, "output": {"width": 960, "height": 540, "fps": 30},
           "cuts": [{"who": "가람", "from": "문화의 마당", "to": "놀이터", "duration_seconds": 2, "from_pin": PINS[0][0], "to_pin": PINS[0][1]},
                    {"who": "나래", "from": "잔디마당", "to": "생태연못", "duration_seconds": 2, "from_pin": PINS[1][0], "to_pin": PINS[1][1]}]}

    def make(self, raw):
        """각자 이동을 만든다. 돌려주는 것: (작업 기록, 컷마다 화면에 넘긴 의뢰, 가짜 지도, Liner 쪽 함수가 불린 기록)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen, fake, liner = [], MovesGeo(), []
        with mock.patch.object(moves, "Geo", lambda *a, **k: fake), mock.patch.object(moves.build, "make_job", fake_job(seen)):
            job = cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None,
                            namer=lambda names, **k: liner.append(("지명 묻기", list(names))) or {},
                            searcher=lambda name, **k: liner.append(("검색", name)),
                            picker=lambda *a, **k: liner.append(("고르기", a[0])))
        return job, seen, fake, liner

    def revised(self, *model_cuts):
        first, _, _, _ = self.make(self.RAW)
        return revise.apply_map({**first["request"], "rid": "의뢰-7"}, revision(cuts_changed=True, cuts=list(model_cuts)))

    def test_각자_이동의_컷_길이만_고치면_출연자와_네_좌표가_그대로_남는다(self):
        d, changed, _ = self.revised(said("문화의 마당", "놀이터", 4), said("잔디마당", "생태연못", 4))
        self.assertEqual([c["duration_seconds"] for c in d["cuts"]], [4, 4])
        self.assertEqual([c.get("who") for c in d["cuts"]], ["가람", "나래"])
        self.assertEqual([(c.get("from_pin"), c.get("to_pin")) for c in d["cuts"]], self.PINS)
        self.assertTrue(d.get("separate"))
        self.assertEqual([c["field"] for c in changed], ["cuts", "duration_seconds"])

    def test_길이만_고친_의뢰로_다시_만들면_양_끝_지명을_다시_찾지_않는다(self):
        d, _, _ = self.revised(said("문화의 마당", "놀이터", 4), said("잔디마당", "생태연못", 4))
        job, seen, fake, liner = self.make(d)
        self.assertEqual((fake.asked, liner), ([], []))        # 지명 찾기 · 지명 묻기 · 검색 · 고르기 0번
        self.assertEqual([r.get("who") for r in seen], ["가람", "나래"])
        self.assertEqual([({"lat": r["from"]["lat"], "lon": r["from"]["lon"]}, {"lat": r["to"]["lat"], "lon": r["to"]["lon"]}) for r in seen], self.PINS)
        self.assertEqual([r["duration_seconds"] for r in seen], [4, 4])
        self.assertEqual(fake.routes, [True, True])        # 걸어서 가는 길 그대로

    def test_장소가_바뀐_끝점의_좌표만_버린다(self):
        d, _, notes = self.revised(said("문화의 마당", "놀이터", 2), said("잔디마당", "지당", 2))
        first, second = d["cuts"]
        self.assertEqual((first.get("who"), first.get("from_pin"), first.get("to_pin")), ("가람", *self.PINS[0]))      # 안 바뀐 컷은 그대로
        self.assertEqual((second.get("who"), second.get("from_pin")), ("나래", self.PINS[1][0]))      # 안 바뀐 시작 지점은 그대로
        self.assertNotIn("to_pin", second)      # 끝 지점은 다른 곳이 됐다 — 옛 좌표로 그리면 안 된다
        self.assertTrue(any("새로 찾습니다" in n for n in notes), notes)


class WalkingCuts(CutsBase):
    """04 — 여러 컷이 한 경로를 나눠 쓸 때도 걸어서 가는 의뢰는 보행 경로를, 자동차 의뢰는 도로 경로를, 배 의뢰는 뱃길을 묻는다."""

    def raw(self, vehicle):
        return {**BASE, "vehicle": vehicle, "from": {"name": "가", "lat": A["lat"], "lon": A["lon"]}, "to": {"name": "라", "lat": D["lat"], "lon": D["lon"]},
                "via": [{"name": "나", "lat": B["lat"], "lon": B["lon"]}], "cuts": [cut("가", "나"), cut("나", "가운데"), cut("가운데", "라")]}

    def test_같은_좌표의_도보_의뢰는_보행_경로를_자동차_의뢰는_도로_경로를_묻는다(self):
        _, walk = self.make(self.raw("walk"))
        _, car = self.make(self.raw("suv"))
        pts = [at(A), at(B), at(D)]
        self.assertEqual(walk["route"], [(True, pts)])
        self.assertEqual(car["route"], [(False, pts)])
        self.assertEqual((walk["water"], car["water"]), ([], []))

    def test_지명으로_잡는_도보_컷도_보행_경로를_묻는다(self):
        _, calls = self.make({**BASE, "vehicle": "walk", "cuts": [cut("가", "나"), cut("나", "다"), cut("다", "라")]})
        self.assertEqual(calls["route"], [(True, [at(A), at(B), at(C), at(D)])])

    def test_배_의뢰는_뱃길_경로를_그대로_쓴다(self):
        _, boat = self.make(self.raw("boat"))
        self.assertEqual((boat["route"], boat["water"]), ([], [[at(A), at(B), at(D)]]))


class RevisitedStop(CutsBase):
    """05 — 다른 지점을 거쳐 다시 들르는 곳은 경로에 차례대로 다시 넣는다. 바로 이웃한 같은 지점만 한 번으로 합친다."""
    AGAIN = {**BASE, "cuts": [cut("가", "나"), cut("나", "다"), cut("다", "나"), cut("나", "라")]}

    def test_다시_들르는_곳이_있는_경로는_다섯_지점_그대로_묻는다(self):
        job, calls = self.make(self.AGAIN)
        self.assertEqual(calls["route"], [(False, [at(A), at(B), at(C), at(B), at(D)])])
        self.assertEqual([v["name"] for v in job["request"]["via"]], ["나", "다", "나"])

    def test_두_번째로_지나는_곳의_컷_경계가_첫_번째_자리로_되감기지_않는다(self):
        job, _ = self.make(self.AGAIN)
        marks = [0.0, line_m(A, B), line_m(A, B, C), line_m(A, B, C, B), line_m(A, B, C, B, D)]
        got = [(c["from_m"], c["to_m"]) for c in job["request"]["cuts"]]
        for (s0, s1), want0, want1 in zip(got, marks, marks[1:]):
            self.assertAlmostEqual(s0, want0, delta=1)
            self.assertAlmostEqual(s1, want1, delta=1)
        self.assertFalse([w for w in job["warnings"] if "차례와 맞지 않는" in w or "어림" in w], job["warnings"])

    def test_바로_이웃한_같은_지점은_경로에_한_번만_넣는다(self):
        FakeGeo.places["나광장"] = spot("나광장", 37.5001, 126.9201)       # '나' 에서 약 14m
        job, calls = self.make({**BASE, "cuts": [cut("가", "나", stops=[{"name": "나광장", "caption": "집결"}]), cut("나", "라")]})
        self.assertEqual(calls["route"], [(False, [at(A), at(FakeGeo.places["나광장"]), at(D)])])
        self.assertFalse([w for w in job["warnings"] if "차례와 맞지 않는" in w], job["warnings"])

    def test_다리의_북단과_남단_사이에_들르는_곳이_있어도_다리는_한_번만_건넌다(self):
        # 북단·남단은 꼬리말을 떼고 찾은 같은 다리 지점이다 — 다시 들르는 곳이 아니라 한 번 건너는 다리의 양 끝이다
        top, deck, isle, end = spot("가", 37.56, 126.95), spot("가람대교", 37.52, 126.95, searched="가람대교"), spot("섬", 37.517, 126.95), spot("라", 37.48, 126.95)
        FakeGeo.places = {"가": top, "가람대교 북단": {**deck, "name": "가람대교 북단"}, "가람대교 남단": {**deck, "name": "가람대교 남단"}, "섬": isle, "라": end}
        job, calls = self.make({**BASE, "cuts": [cut("가", "가람대교 북단"), cut("가람대교 북단", "가람대교 남단", stops=[{"name": "섬", "caption": "미션"}]),
                                                 cut("가람대교 남단", "라")]})
        self.assertEqual(calls["route"], [(False, [at(top), at(deck), at(isle), at(end)])])
        self.assertFalse([w for w in job["warnings"] if "차례와 맞지 않는" in w], job["warnings"])
        bounds = [(c["from_m"], c["to_m"]) for c in job["request"]["cuts"]]
        self.assertAlmostEqual(bounds[1][0], line_m(top, deck) - cuts.BRIDGE_HALF_M, delta=1)      # 북단 = 다리 지점의 700m 앞
        self.assertAlmostEqual(bounds[1][1], line_m(top, deck) + cuts.BRIDGE_HALF_M, delta=1)      # 남단 = 다리 지점의 700m 뒤


if __name__ == "__main__":
    unittest.main()
