"""2차 수정지침(37d100f)의 재현 사례 — 전부 가짜 응답·임시 폴더로 본다(지도 서버·Liner 를 실제로 부르지 않는다).

01 잘못 잡힌 지점으로 경로·지도를 묻지 않는다 · 02 받는 지도의 크기와 완전성 · 03 한 의뢰의 몫 · 04 오프라인과 서버 장애 때 Liner 로 넘기지 않는다
05 Liner 호출의 의뢰 번호·다시 보내면 안 되는 경우 · 06 한쪽만 못 읽은 컷 · 07 다시 만들기 · 09 프레임
"""
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from mapcg import board, build, cuts, export, geo, intake, request, server

ROOT = Path(__file__).resolve().parent.parent
SEOUL_STADIUM = {"display": "서울월드컵경기장, 마포구, 서울", "lat": 37.5683, "lon": 126.8972, "type": "leisure/stadium"}
JEONJU_STADIUM = {"display": "전주월드컵경기장, 덕진구, 전주", "lat": 35.8681, "lon": 127.0644, "type": "leisure/stadium"}
NODEUL = {"name": "노들로", "lat": 37.5391, "lon": 126.8988, "display": "노들로, 영등포구, 서울", "candidates": []}
BUSAN_ROAD = {"name": "월드컵대로", "lat": 35.1902, "lon": 129.0602}
BASE = {"from": {"name": "가"}, "to": {"name": "나"}, "output": {"width": 960, "height": 540, "fps": 30}}


def place(name, *cands):
    return {"name": name, "lat": cands[0]["lat"], "lon": cands[0]["lon"], "display": cands[0]["display"], "candidates": list(cands)}


class FakeGeo:
    """지도 받는 쪽의 가짜 — 무엇을 몇 번 불렀는지만 센다."""
    places: dict = {}
    fail: Exception | None = None

    def __init__(self, *a, **k):
        self.calls = {"geocode": [], "route": [], "osm": 0}
        self.budget = geo.Budget()
        FakeGeo.last = self

    def geocode_loose(self, name, country="kr", near=None, km=30):
        self.calls["geocode"].append(name)
        if self.fail:
            raise self.fail
        if name not in self.places:
            raise geo.GeoNotFound(f"'{name}' 없음")
        return self.places[name]

    def route_via(self, points):
        self.calls["route"].append([(round(p["lat"], 3), round(p["lon"], 3)) for p in points])
        line = [[p["lon"], p["lat"]] for p in points]
        return {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": line}, "distance": cuts.route_length(line), "duration": 600,
                                          "legs": [{"distance": cuts.route_length(line[i:i + 2])} for i in range(len(line) - 1)]}]}

    def osm(self, bbox, detail="major", wait=None):
        self.calls["osm"] += 1
        return {"elements": [], "_source": "blank", "_note": "가짜 지도"}


def cut(a, b, sec=4, **kw):
    return {"from": a, "to": b, "duration_seconds": sec, **kw}


class WrongPlace(unittest.TestCase):
    """01 — 2026-11-20 의 두 사고: 전주의 경기장이 첫 후보로 온 것, 서울 → 부산의 월드컵대로 → 서울."""

    def make(self, raw, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(cuts, "Geo", FakeGeo):
            return cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None, **kw)

    def test_전주가_첫_후보여도_서울_경로로_묻는다(self):
        FakeGeo.places, FakeGeo.fail = {"월드컵경기장": place("월드컵경기장", JEONJU_STADIUM, SEOUL_STADIUM), "노들로": NODEUL}, None
        job = self.make({**BASE, "from": {"name": "월드컵경기장"}, "to": {"name": "노들로"}, "cuts": [cut("월드컵경기장", "중간"), cut("중간", "노들로")]})
        g = FakeGeo.last
        self.assertEqual(len(g.calls["route"]), 1)
        self.assertEqual(g.calls["route"][0][0], (37.568, 126.897))       # 전주(35.87)가 아니라 서울의 경기장에서 출발한다
        self.assertLess(job["route"]["distance_m"], 10_000)
        self.assertEqual(job["state"], "done")

    def test_동떨어진_경유지는_경로도_지도도_묻지_않고_멈춘다(self):
        FakeGeo.places, FakeGeo.fail = {}, None
        raw = {**BASE, "from": {"name": "상암", "lat": 37.5683, "lon": 126.8972}, "to": {"name": "노들로", "lat": 37.5391, "lon": 126.8988},
               "via": [BUSAN_ROAD], "cuts": [cut("상암", "월드컵대로"), cut("월드컵대로", "노들로")]}
        with self.assertRaises(request.RequestError) as e:
            self.make(raw)
        self.assertIn("월드컵대로", str(e.exception.problems))
        self.assertEqual((FakeGeo.last.calls["route"], FakeGeo.last.calls["osm"]), ([], 0))

    def test_지점들을_곧게_이어도_한도를_넘으면_멈춘다(self):
        with self.assertRaises(request.RequestError):
            cuts.check_legs([{"name": "전주", "lat": 35.8681, "lon": 127.0644}, {"name": "노들로", "lat": 37.5391, "lon": 126.8988}])

    def test_직접_지정한_경유지가_든_정상_경로는_지나간다(self):
        raw = json.loads((ROOT / "examples" / "월드컵경기장_노들로_4컷.json").read_text(encoding="utf-8"))
        cuts.check_legs([{"name": "상암", "lat": 37.5683, "lon": 126.8972}, *raw["via"], raw["to"]])
        cuts.check_legs([{"name": "서울역", "lat": 37.5547, "lon": 126.9707}, {"name": "명동", "lat": 37.5636, "lon": 126.9827},
                         {"name": "DDP", "lat": 37.5665, "lon": 127.0092}])

    def test_일반_지도도_서로_가까운_후보_쌍을_고른다(self):
        a, b, note = build.near_pair(place("월드컵경기장", JEONJU_STADIUM, SEOUL_STADIUM), dict(NODEUL))
        self.assertEqual((round(a["lat"], 2), a["display"]), (37.57, SEOUL_STADIUM["display"]))
        self.assertIn("가까운 쪽", note)

    def test_가까운_첫_후보와_일부러_먼_의뢰는_그대로_둔다(self):
        hongdae = place("홍대", {"display": "홍대입구역", "lat": 37.5572, "lon": 126.9245, "type": ""}, {"display": "홍대 다른 곳", "lat": 37.52, "lon": 126.92, "type": ""})
        yeouido = place("여의도", {"display": "여의도", "lat": 37.5219, "lon": 126.9245, "type": ""})
        self.assertEqual(build.near_pair(hongdae, yeouido), (hongdae, yeouido, None))
        seoul, busan = place("서울역", {"display": "서울역", "lat": 37.5547, "lon": 126.9707, "type": ""}), place("부산역", {"display": "부산역", "lat": 35.1152, "lon": 129.0422, "type": ""})
        self.assertEqual(build.near_pair(seoul, busan), (seoul, busan, None))      # 후보가 하나씩뿐인 장거리 — 바꿀 것이 없다


class Reply:
    """가짜 지도 서버 응답."""

    def __init__(self, body: bytes, told: int | None = None):
        self.buf, self.headers, self.reads = io.BytesIO(body), ({"Content-Length": str(told)} if told is not None else {}), 0

    def read(self, n=-1):
        self.reads += 1
        return self.buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SMALL = json.dumps({"elements": [{"type": "node", "id": 1, "lat": 35.15, "lon": 129.05, "tags": {"place": "suburb", "name": "가상동"}}]}).encode()
BOX = (35.1, 129.0, 35.2, 129.1)       # 서울 묶음 밖(부산)


class MapSize(unittest.TestCase):
    """02 — 받는 지도의 크기와 완전성."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.g = geo.Geo(self.dir)
        self.opened = []
        p = mock.patch.dict(geo._rested, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def serve(self, make):
        def fake(req, timeout):
            self.opened.append(req.full_url)
            r = make()
            self.last = r
            return r
        return mock.patch.object(geo, "_open", fake)

    def saved(self):
        return [p.name for p in self.dir.glob("osm_*.json") if p.name != "osm_index.json"]

    def test_서버가_알려_준_크기가_한도를_넘으면_읽지_않는다(self):
        with self.serve(lambda: Reply(b"x" * 10, told=10_000)), self.assertRaises(geo.GeoTooBig):
            geo.Geo._http("http://x/", limit=1000)
        self.assertEqual(self.last.reads, 0)

    def test_크기를_안_알려_줘도_받다가_한도를_넘으면_멈춘다(self):
        with self.serve(lambda: Reply(b"x" * (3 * 1024 * 1024 + 5))), self.assertRaises(geo.GeoTooBig):
            geo.Geo._http("http://x/", limit=2 * 1024 * 1024)
        self.assertLessEqual(self.last.reads, 3)        # 1MB 씩 읽다 셋째 조각에서 멈춘다 — 끝까지 받지 않는다

    def test_너무_큰_지도는_저장하지_않고_다른_서버에도_다시_묻지_않는다(self):
        with self.serve(lambda: Reply(b"x" * 5000)), mock.patch.object(geo, "MAX_OSM_BYTES", 1000):
            data = self.g.osm(BOX, "major")
        self.assertEqual((data["_source"], data["elements"], len(self.opened), self.saved()), ("blank", [], 1, []))
        self.assertIn("너무 커서", data["_note"])

    def test_전에_받아_둔_큰_파일은_열지_않는다(self):
        with self.serve(lambda: Reply(SMALL)):
            self.g.osm(BOX, "major")
        name = self.saved()[0]
        (self.dir / name).write_text("{" + " " * 5000, encoding="utf-8")       # 읽으면 깨지는 큰 파일
        self.opened.clear()
        with self.serve(lambda: Reply(SMALL)), mock.patch.object(geo, "MAX_OSM_BYTES", 1000):
            data = self.g.osm(BOX, "major")
        self.assertEqual((data["_source"], self.opened), ("blank", []))       # 열지도, 다시 받지도 않는다
        self.assertGreater((self.dir / name).stat().st_size, 1000)             # 있던 파일은 지우지 않는다

    def test_응답_200_이어도_중간에_그만둔_답은_저장하지_않는다(self):
        partial = json.dumps({"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 3 after 91 seconds."}).encode()
        with self.serve(lambda: Reply(partial)):
            data = self.g.osm(BOX, "major")
        self.assertEqual((data["_source"], self.saved()), ("blank", []))
        self.assertEqual(len(self.opened), len(geo.OVERPASS))        # 이 경우는 서버 탓이라 다음 서버에 묻는다

    def test_정상인_작은_지도는_받아서_저장하고_다시_쓴다(self):
        with self.serve(lambda: Reply(SMALL, told=len(SMALL))):
            first = self.g.osm(BOX, "major")
            again = self.g.osm(BOX, "major")
        self.assertEqual((first["_source"], again["_source"], len(self.opened), len(self.saved())), ("server", "cache", 1, 1))
        self.assertEqual(first["elements"], again["elements"])

    def test_도형이_없는_정상_답은_빈_지도로_저장한다(self):
        with self.serve(lambda: Reply(b'{"elements": []}')):
            data = self.g.osm(BOX, "major")
        self.assertEqual((data["_source"], len(self.saved())), ("server", 1))
        self.assertNotIn("_note", data)

    def test_쌓아_둔_양이_한도를_넘으면_새_것은_쓰기만_하고_저장하지_않는다(self):
        (self.dir / "osm_major_old_000000000000.json").write_text("{}" + " " * 200, encoding="utf-8")       # 목록(osm_index.json)에 없는 파일도 센다
        with self.serve(lambda: Reply(SMALL)), mock.patch.object(geo, "CACHE_MAX_BYTES", 100):
            data = self.g.osm(BOX, "major")
        self.assertEqual((data["_source"], self.saved()), ("server", ["osm_major_old_000000000000.json"]))

    def test_범위가_넓으면_묻기_전에_멈춘다(self):
        with self.serve(lambda: Reply(SMALL)):
            data = self.g.osm((35.0, 127.0, 36.2, 128.2), "major")      # 1.44 도² — 전주 사고의 컷 범위와 비슷하다
        self.assertEqual((data["_source"], self.opened), ("blank", []))
        self.assertIn("범위가 넓어", data["_note"])


class SharedBudget(unittest.TestCase):
    """03 — 여러 컷과 대체 서버가 한 의뢰의 몫을 같이 쓴다."""

    def test_몫을_다_쓰면_뒤_컷은_서버를_부르지_않는다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        now = [0.0]
        g = geo.Geo(Path(tmp.name))
        g.budget = geo.Budget(seconds=100, clock=lambda: now[0])
        calls = []

        def slow(url, data=None, timeout=60, limit=None, **k):
            calls.append(timeout)
            now[0] += 60           # 서버마다 60초를 기다리다 실패한다
            raise geo.GeoError("막힘")

        with mock.patch.object(geo.Geo, "_http", staticmethod(slow)), mock.patch.dict(geo._rested, {}, clear=True):
            got = [g.osm((35.1 + i * 0.2, 129.0, 35.2 + i * 0.2, 129.1), "major") for i in range(4)]      # 컷 넷
        self.assertEqual(len(calls), 2)                    # 60초 + 60초로 몫(100초)을 넘겼다 — 나머지 서버와 뒤의 세 컷은 부르지 않는다
        self.assertLessEqual(calls[1], 40)                 # 둘째 서버는 남은 시간만큼만 기다린다
        self.assertTrue(all(d["_source"] == "blank" and "바탕 지도(도로·강) 없이" in d["_note"] for d in got))

    def test_서울의_큰길은_서버를_부르지_않는다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        boom = staticmethod(lambda *a, **k: self.fail("지도 서버를 불렀다"))
        with mock.patch.object(geo.Geo, "_http", boom):
            data = geo.Geo(Path(tmp.name)).osm((37.50, 126.90, 37.56, 126.99), "major")
        self.assertEqual(data["_source"], "pack")
        self.assertNotIn("_note", data)
        self.assertGreater(len(data["elements"]), 100)


class OfflineAndOutage(unittest.TestCase):
    """04 — 오프라인이면 키가 있어도 밖으로 보내지 않는다. 서버가 안 되는 것을 '그런 이름 없음'으로 보고 Liner 에 넘기지 않는다."""

    def test_메신저를_오프라인으로_켜면_Liner_를_쓰지_않는다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        liner = intake._liner()
        sent = []
        with mock.patch.object(intake, "readers", lambda: {"liner": True, "claude": False}), mock.patch.object(intake, "has_key", lambda: True), \
                mock.patch.object(liner, "_post", lambda *a, **k: sent.append(a) or self.fail("Liner 로 보냈다")):
            d = server.Desk(Path(tmp.name), offline=True)
            d._spawn = lambda fn, *a, **_: fn(*a)        # 그 자리에서 돌린다
            self.assertEqual(d.readers, {"liner": False, "claude": False})
            d.send("홍대에서 여의도로 버스 이동, 8초", [{"name": "의뢰지.jpg", "data": "data:image/jpeg;base64,/9j/AAAA", "images": ["data:image/jpeg;base64,/9j/AAAA"]}])
            card = d.msgs[-1]
            self.assertEqual((card["kind"], card["source"], sent), ("card", "simple", []))
            self.assertTrue(card["draft"]["rid"].startswith("의뢰-"))
            seen = {}
            with mock.patch.object(cuts, "make", lambda spec, **kw: seen.update(kw) or (_ for _ in ()).throw(geo.GeoError("저장된 지도 데이터가 없습니다"))):
                d.make({**card["draft"], "cuts": [cut("홍대", "합정"), cut("합정", "여의도")]})
        self.assertEqual((seen["offline"], seen["namer"], seen["searcher"]), (True, None, None))
        self.assertIn("저장된 지도 데이터가 없습니다", d.msgs[-1]["error"])       # 새 경로는 무엇이 없는지 알려 준다

    def test_명령줄의_오프라인도_지명_묻기와_검색을_넘기지_않는다(self):
        from mapcg import __main__ as cli
        seen = {}
        path = ROOT / "examples" / "월드컵경기장_노들로_4컷.json"
        with mock.patch.object(intake, "has_key", lambda: True), \
                mock.patch.object(cuts, "make", lambda spec, **kw: seen.update(kw) or (_ for _ in ()).throw(geo.GeoError("없음"))), \
                mock.patch("sys.stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
            code = cli.main(["make", str(path), "--offline", "--no-video"])
        self.assertEqual((code, seen["offline"], seen["namer"], seen["searcher"]), (3, True, None, None))

    def test_지명_서버가_안_되면_이름을_바꿔_가며_다시_묻지_않는다(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        calls = []

        def down(url, data=None, timeout=60, limit=None):
            calls.append(url)
            raise geo.GeoError("지도 서버 응답 503")

        with mock.patch.object(geo.Geo, "_http", staticmethod(down)), mock.patch.object(geo.time, "sleep", lambda s: None):
            with self.assertRaises(geo.GeoError) as e:
                geo.Geo(Path(tmp.name)).geocode_loose("월드컵경기장 앞")
        self.assertNotIsInstance(e.exception, geo.GeoNotFound)
        self.assertEqual(len(calls), 2)        # 지명 서버와 대체 서버 한 번씩 — '앞'을 떼고 다시 묻지 않는다

    def make(self, searcher):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raw = {**BASE, "from": {"name": "서울역"}, "to": {"name": "디디피"}, "cuts": [cut("서울역", "중간"), cut("중간", "디디피")]}
        with mock.patch.object(cuts, "Geo", FakeGeo):
            return cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None, searcher=searcher)

    def test_서버_장애에서는_Liner_검색을_부르지_않는다(self):
        FakeGeo.places, FakeGeo.fail = {}, geo.GeoError("지도 서버 응답 503")
        asked = []
        with self.assertRaises(geo.GeoError):
            self.make(lambda name, near="": asked.append(name))
        self.assertEqual(asked, [])

    def test_그런_이름이_없을_때만_Liner_검색으로_이름을_확인한다(self):
        ddp = {"name": "동대문디자인플라자", "lat": 37.5665, "lon": 127.0092, "display": "동대문디자인플라자, 중구, 서울", "candidates": []}
        mid = {"name": "명동", "lat": 37.5609, "lon": 126.9860, "display": "명동, 중구, 서울", "candidates": []}
        FakeGeo.places = {"서울역": {"name": "서울역", "lat": 37.5547, "lon": 126.9707, "display": "서울역", "candidates": []}, "동대문디자인플라자": ddp, "명동": mid}
        FakeGeo.fail = None
        asked = []
        # '중간'은 도착지와 다른 곳(명동)으로 — 같은 곳이면 컷 2가 들어갈 자리가 없어 만들기 전에 멈춘다(test_cuts_1123)
        official = {"디디피": "동대문디자인플라자", "중간": "명동"}
        job = self.make(lambda name, near="": asked.append(name) or {"search_name": official[name], "sources": ["https://example.org/ddp"]})
        self.assertEqual(asked, ["디디피", "중간"])       # 지도에 없는 이름 둘 — 찾은 '서울역'은 묻지 않는다
        self.assertTrue(any("Liner 검색으로 확인한 정식 이름" in w and "example.org" in w for w in job["warnings"]))

    def test_근거가_없거나_없는_번호를_대면_확인한_것이_아니다(self):
        results = {"results": [{"title": "남산골한옥마을", "url": "https://example.org/a", "description": "서울 중구"}]}
        answer = lambda sources: (lambda m, **o: {"choices": [{"message": {"content": json.dumps(      # noqa: E731
            {"search_name": "남산골한옥마을", "sources": sources, "sure": True}, ensure_ascii=False)}}]})
        find = lambda sources: intake.searched_name("남산한옥마을", chat=answer(sources), search=lambda q, **o: results)      # noqa: E731
        self.assertIsNone(find([9]))
        self.assertIsNone(find([]))
        self.assertEqual(find([1]), {"search_name": "남산골한옥마을", "sources": ["https://example.org/a"]})


class LinerLedger(unittest.TestCase):
    """05 — 호출 기록의 의뢰 번호, 그리고 다시 보내면 안 되는 경우."""

    def setUp(self):
        self.liner = intake._liner()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "calls.jsonl"
        self.sent = []
        for p in (mock.patch.object(self.liner, "LOG_PATH", self.log), mock.patch.object(self.liner, "api_key", lambda: "시험용-키"),
                  mock.patch.object(self.liner.time, "sleep", lambda s: None), mock.patch.dict("os.environ", {"LINER_LOG": "on", "MAPCG_READER": "auto"}),
                  mock.patch.dict(self.liner._blocked, {}, clear=True)):       # 거절 기록은 블록이 끝나도 남는다 — 시험끼리 섞이지 않게
            p.start()
            self.addCleanup(p.stop)

    def answers(self, *steps):
        """차례로: 예외면 던지고, dict 면 200 으로 답한다."""
        steps = list(steps)

        def fake(req, timeout):
            self.sent.append(req.full_url)
            step = steps.pop(0) if len(steps) > 1 else steps[0]
            if isinstance(step, Exception):
                raise step
            return Reply(json.dumps(step, ensure_ascii=False).encode())
        return mock.patch.object(self.liner, "_open", fake)

    def lines(self):
        return [json.loads(s) for s in self.log.read_text(encoding="utf-8").splitlines()]

    def refuse(self, code):
        return urllib.error.HTTPError("http://x", code, "거절", {}, io.BytesIO(b'{"error":"no"}'))

    def test_잔액_부족으로_분류가_실패하면_같은_그림을_다시_보내지_않는다(self):
        image = ["data:image/jpeg;base64,AAAA"]
        with self.answers(self.refuse(402)), self.liner.request("의뢰-1"):
            with self.assertRaises(board.Unsure):       # 순위판인지 묻다가 402 — '순위판이 아니다'와 구분해 올린다(앱은 여기서 멈춘다)
                board.read("", image, available={"liner": True, "claude": False})
            r = intake.analyze("", image, order=["liner"])                                                # 지도 쪽 읽기를 불러도 — 보내지 않는다
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(r["source"], "simple")
        self.assertTrue(any("잔액이 부족합니다" in n for n in r["notes"]), r["notes"])
        got = self.lines()
        self.assertEqual([(x["request"], x["task"], x["status"]) for x in got], [("의뢰-1", "rank_board_request", 402), ("의뢰-1", "map_request", 402)])
        self.assertIn("보내지 않음", got[1]["note"])
        tally = self.liner.tally("의뢰-1", self.log)
        self.assertEqual((tally["rank_board_request"]["attempts"], tally["map_request"]["attempts"], tally["map_request"]["failed"]), (1, 0, 1))

    def test_다른_의뢰는_앞_의뢰의_거절에_막히지_않는다(self):
        ok = {"model": "가짜", "choices": [{"message": {"content": "네"}}], "usage": {"total_tokens": 7}}
        with self.answers(self.refuse(401), ok):
            with self.liner.request("의뢰-1"), self.assertRaises(self.liner.LinerError):
                self.liner.chat([{"role": "user", "content": "가"}])
            with self.liner.request("의뢰-2"):
                self.liner.chat([{"role": "user", "content": "나"}])
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.liner.tally("의뢰-2", self.log)["글"], {"calls": 1, "attempts": 1, "failed": 0, "tokens": 7})

    def test_답을_기다리다_끊기면_다시_보내지_않고_접수_불명으로_남긴다(self):
        with self.answers(TimeoutError("timed out")), self.assertRaises(self.liner.LinerError) as e:
            self.liner.chat([{"role": "user", "content": "가"}])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("접수됐는지 알 수 없어", e.exception.body)
        self.assertIn("접수 여부 불명", self.lines()[0]["note"])

    def test_보내지_못한_것은_다시_보내고_시도마다_기록한다(self):
        ok = {"model": "가짜", "choices": [{"message": {"content": "네"}}]}
        with self.answers(urllib.error.URLError("접속 거부"), ok):
            self.liner.chat([{"role": "user", "content": "가"}])
        got = self.lines()
        self.assertEqual([(x["attempt"], x["status"], x["final"]) for x in got], [(0, 0, False), (1, 200, True)])
        self.assertEqual(got[0]["call"], got[1]["call"])
        self.assertNotIn("시험용-키", self.log.read_text(encoding="utf-8"))

    def test_순위판이_아니라는_답_뒤의_지도_읽기는_그대로_한다(self):
        not_board = {"choices": [{"message": {"content": json.dumps({"is_rank_board": False, "title": "", "unit": "", "duration_seconds": None, "unclear": [],
                                                                     "rows": [], "next_title": "", "next_rows": []})}}]}
        with self.answers(not_board, self.refuse(500)), self.liner.request("의뢰-3"):
            self.assertIsNone(board.read("", ["data:image/jpeg;base64,AAAA"], available={"liner": True, "claude": False}))
            intake.analyze("", ["data:image/jpeg;base64,AAAA"], order=["liner"])
        self.assertGreaterEqual(len(self.sent), 2)        # 분류 1번 + 지도 읽기(서버 오류라 다시 보낸 것 포함)


class PartialCuts(unittest.TestCase):
    """06 — 한 컷의 이름 한쪽만 못 읽어도 컷 나눔이 사라지지 않는다. 앱이 채운 값은 알린다."""

    def read(self, cut_list):
        obj = {"program": None, "requester_name": None, "requester_role": None, "requester_phone": None, "map_style_text": None, "map_style": "route_move",
               "from_name": "서울역", "to_name": None, "vehicle": "bus", "duration_seconds": None, "layout": "unknown", "face_count": None, "face_mode": None,
               "output_width": None, "output_height": None, "vehicle_color": None, "unclear": [], "cuts": cut_list}
        chat = lambda m, **o: {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}       # noqa: E731
        return intake.analyze("", ["data:image/jpeg;base64,AAAA"], chat=chat, order=["liner"])

    def test_둘째_컷의_끝만_못_읽은_두_컷이_카드에_그대로_남는다(self):
        raw = lambda a, b, sec: {"from_name": a, "to_name": b, "duration_seconds": sec, "bridge": False, "closeup": False, "note": "", "stops": []}      # noqa: E731
        r = self.read([raw("서울역", "동대문", 8), raw(None, None, None) | {"from_name": "동대문"}])
        d = r["draft"]
        self.assertEqual([(c["from"], c["to"]) for c in d["cuts"]], [("서울역", "동대문"), ("동대문", "")])
        self.assertTrue(any("컷 2의 끝 이름" in n for n in r["notes"]), r["notes"])
        self.assertTrue(any("앱이 채운 값" in n and "컷 2" in n for n in r["notes"]), r["notes"])
        with self.assertRaises(request.RequestError) as e:       # 빈 칸인 채로는 만들지 않는다 — 카드에서 채우게 한다
            cuts.normalize({**d, "faces": []})
        self.assertIn("컷 2: 구간 이름", " ".join(e.exception.problems))
        d["cuts"][1]["to"] = "DDP"                                # 사람이 채운 값
        d["cuts"][1]["duration_seconds"] = 6                      # 사람이 고친 길이
        spec = cuts.normalize({**d, "to": {"name": "DDP"}, "faces": []})
        self.assertEqual([(c["to"], c["duration_seconds"]) for c in spec["cuts"]], [("동대문", 8), ("DDP", 6)])
        self.assertEqual(cuts.spec_request(spec)["cuts"][1]["duration_seconds"], 6)       # 고친 값이 작업 기록·다시 만들기까지 간다


class Remake(unittest.TestCase):
    """07 — 결과 카드의 '다시 만들기'가 경유지와 도착 방향을 잃지 않는다."""

    def test_다시_낸_의뢰가_같은_경유지와_방향을_갖는다(self):
        raw = json.loads((ROOT / "examples" / "월드컵경기장_노들로_4컷.json").read_text(encoding="utf-8"))
        spec = cuts.normalize(raw)
        card = cuts.spec_request(spec)                               # 결과 카드·작업 기록에 남는 것
        again = cuts.normalize({**card, "art_style": "v4", "face_count": 4})      # 화풍만 바꿔 다시 낸다
        self.assertEqual((len(spec["via"]), spec["to_bearing"]), (2, 127))
        self.assertEqual((again["via"], again["to_bearing"]), (spec["via"], spec["to_bearing"]))
        self.assertEqual(again["cuts"], spec["cuts"])
        self.assertEqual(again["base"]["art_style"], "v4")

    def test_방향이_없던_의뢰에_방향을_지어_넣지_않는다(self):
        spec = cuts.normalize({**BASE, "cuts": [cut("가", "다"), cut("다", "나")]})
        self.assertNotIn("bearing", cuts.spec_request(spec)["to"])
        self.assertNotIn("to_bearing", cuts.normalize(cuts.spec_request(spec)))

    def test_들르는_곳에_좌표를_직접_줄_수_있다(self):
        spec = cuts.normalize({**BASE, "cuts": [cut("가", "다", stops=[{"name": "쇼핑센터", "lat": 37.5665, "lon": 127.0092}]), cut("다", "나")]})
        self.assertEqual(spec["cuts"][0]["stops"][0], {"name": "쇼핑센터", "caption": "", "pass": False, "lat": 37.5665, "lon": 127.0092})


class Frames(unittest.TestCase):
    """09 — 컷의 경계는 누적 시간으로 잡고 MP4는 컷 순서대로 잇는다."""

    def test_컷마다_반올림해_더하지_않는다(self):
        self.assertEqual(cuts.frame_plan([2.5, 2.5], 25), [63, 62])            # 합 125장(예전에는 63 + 63 = 126)
        self.assertEqual(sum(cuts.frame_plan([1.5] * 8, 1)), 12)                # 예전에는 16장
        self.assertEqual(cuts.frame_plan([4, 4, 4, 4], 30), [120] * 4)

    def export_job(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "묶음"
        root.mkdir()
        return {"dir": root, "cuts": [{"dir": root / "컷1", "duration_seconds": 2.5}, {"dir": root / "컷2", "duration_seconds": 2.5}]}

    def run_export(self, alpha, legacy=False):
        job = self.export_job()
        calls, manifest = [], []

        def capture_mp4(job_dir, out, fps, expected, profile, *, frames):
            calls.append((job_dir.name, out.name, fps, frames))
            return 63 if legacy else expected

        def capture(job_dir, view, dest, expected, profile, *, frames, offset):
            calls.append((job_dir.name, view, dest.name, expected, frames, offset))
            return expected

        def join(cmd, *, cwd, **kwargs):
            self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
            manifest.extend((cwd / "cuts.txt").read_text(encoding="ascii").splitlines())
            (cwd / cmd[-1]).write_bytes(b"joined")
            return mock.Mock(returncode=0)

        with mock.patch.object(export, "capture_mp4", capture_mp4), mock.patch.object(export, "capture", capture), \
                mock.patch.object(export, "probe", return_value=None), mock.patch.object(export, "find_ffmpeg", return_value="ffmpeg"), \
                mock.patch.object(cuts.subprocess, "run", join):
            result = cuts.export_all(job, 25, (960, 540), alpha=alpha, log=lambda s: None)
        self.assertEqual(list(job["dir"].iterdir()), [result["mp4"]])
        return result, calls, manifest

    def test_중간_PNG_없이_컷별_MP4를_계획한_순서로_잇는다(self):
        r, calls, manifest = self.run_export(alpha=False)
        self.assertEqual(sorted(calls), [("컷1", "0.mp4", 25, 63), ("컷2", "1.mp4", 25, 62)])
        self.assertEqual(manifest, ["file '0.mp4'", "duration 2.520000000000", "file '1.mp4'", "duration 2.480000000000"])
        self.assertEqual((r["frames"], r["spec"]["want"]["frames"]), (125, 125))
        self.assertNotIn("alpha_dir", r)

    def test_투명_시퀀스는_예전_페이지의_실제_장수로_잇는다(self):
        r, calls, _ = self.run_export(alpha=True, legacy=True)
        self.assertEqual(sorted(calls[2:]), [("컷1", "stage", "알파_시퀀스", 63, 63, 0), ("컷2", "stage", "알파_시퀀스", 63, 62, 63)])
        self.assertEqual((r["frames"], r["spec"]["want"]["frames"]), (126, 125))
        self.assertEqual(r["alpha_dir"].name, "알파_시퀀스")

    def test_컷을_잇지_못하면_이전_MP4를_보존한다(self):
        job = self.export_job()
        target = job["dir"] / "지도.mp4"
        target.write_bytes(b"previous")
        with mock.patch.object(export, "capture_mp4", return_value=2), mock.patch.object(export, "find_ffmpeg", return_value="ffmpeg"), \
                mock.patch.object(cuts.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="failed")), \
                self.assertRaises(export.ExportError):
            cuts.export_all(job, 25, (960, 540), log=lambda s: None)
        self.assertEqual(list(job["dir"].iterdir()), [target])
        self.assertEqual(target.read_bytes(), b"previous")

    @unittest.skipUnless(shutil.which("ffmpeg") and export.find_ffprobe(), "FFmpeg와 ffprobe 필요")
    def test_이어진_MP4의_실제_컷_경계와_순서(self):
        job = self.export_job()
        job["cuts"][0]["duration_seconds"] = 1.13
        job["cuts"][1]["duration_seconds"] = 1.19     # 24fps에서 누적 반올림한 경계는 27장, 56장

        def render_color(job_dir, out, fps, expected, profile, *, frames):
            color = "red" if job_dir.name == "컷1" else "blue"
            subprocess.run([export.find_ffmpeg(), "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=16x16:r={fps}",
                            "-frames:v", str(frames), "-threads", "1", "-c:v", "libx264", str(out)], check=True)
            return frames

        with mock.patch.object(export, "capture_mp4", render_color):
            r = cuts.export_all(job, 24, (16, 16), log=lambda s: None)
        self.assertEqual(r["spec"]["state"], "ok")
        pixels = subprocess.check_output([export.find_ffmpeg(), "-v", "error", "-i", str(r["mp4"]), "-vf", "scale=1:1",
                                          "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
        self.assertEqual([red > blue for red, blue in zip(pixels[0::3], pixels[2::3])], [True] * 27 + [False] * 29)

    def test_화면이_받는_쪽이_정해_준_장수를_따른다(self):
        self.assertIn("Q.get('frames')", (ROOT / "mapcg" / "templates" / "stage.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
