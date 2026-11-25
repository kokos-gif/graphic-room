"""앱 코드리뷰(42be7d8)의 재현 사례 — 전부 가짜 응답·임시 폴더로 본다(지도 서버·Liner 를 실제로 부르지 않는다).

1 경유지를 준 컷의 핀 · 2 화풍만 바꿔 다시 만들기 · 3 분류 응답 유실·거절 뒤 후속 전송 · 4 그림에만 적힌 fps
5 받는 도중의 몫 초과 · 6 캐시 총량 · 7 표 두 개 붙여넣기 · 8 컷 전환의 카드 등장 · 경계 조건(이름표 불일치 · 예전 실패 캐시)
"""
import http.client
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from mapcg import audit, board, build, cuts, geo, intake, server

ROOT = Path(__file__).resolve().parent.parent
BASE = {"from": {"name": "가"}, "to": {"name": "나"}, "output": {"width": 960, "height": 540, "fps": 30}}
A = {"name": "가", "lat": 37.50, "lon": 126.90, "display": "가, 서울", "candidates": []}
B = {"name": "다", "lat": 37.50, "lon": 126.95, "display": "다, 서울", "candidates": []}
C = {"name": "나", "lat": 37.50, "lon": 127.00, "display": "나, 서울", "candidates": []}
MISSION = {"name": "미션장소", "caption": "미션1 오목", "lat": 37.50, "lon": 126.925}       # 가 → 다 의 한가운데
BOX = (35.1, 129.0, 35.2, 129.1)       # 서울 묶음 밖(부산)
SMALL = json.dumps({"elements": [{"type": "node", "id": 1, "lat": 35.15, "lon": 129.05, "tags": {"place": "suburb", "name": "가상동"}}]}).encode()


def cut(a, b, sec=4, **kw):
    return {"from": a, "to": b, "duration_seconds": sec, **kw}


class FakeGeo:
    """지도 받는 쪽의 가짜 — 무엇을 몇 번 불렀는지만 센다. 경로는 지점들을 곧게 잇는다."""
    places: dict = {}

    def __init__(self, *a, **k):
        self.calls = {"geocode": [], "route": [], "osm": 0}
        self.budget = geo.Budget()
        FakeGeo.last = self

    def geocode_loose(self, name, country="kr", near=None, km=30):
        self.calls["geocode"].append(name)
        if name not in self.places:
            raise geo.GeoNotFound(f"'{name}' 없음")
        return self.places[name]

    def route_via(self, points):
        self.calls["route"].append([(round(p["lat"], 5), round(p["lon"], 5)) for p in points])
        line = [[p["lon"], p["lat"]] for p in points]
        return {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": line}, "distance": cuts.route_length(line), "duration": 600,
                                          "legs": [{"distance": cuts.route_length(line[i:i + 2])} for i in range(len(line) - 1)]}]}

    def osm(self, bbox, detail="major", wait=None):
        self.calls["osm"] += 1
        return {"elements": [], "_source": "blank", "_note": "가짜 지도"}


class Reply:
    """가짜 서버 응답. step 마다 시계를 밀 수 있다(받는 도중에 시간이 가는 것)."""

    def __init__(self, body: bytes, chunk: int = 1 << 20, tick=None):
        self.buf, self.headers, self.chunk, self.tick = io.BytesIO(body), {}, chunk, tick

    def read(self, n=-1):
        if self.tick:
            self.tick()
        return self.buf.read(self.chunk if n is None or n < 0 else min(n, self.chunk))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CutsBase(unittest.TestCase):
    def setUp(self):
        FakeGeo.places = {"가": A, "다": B, "나": C}

    def make(self, raw, **kw):
        """컷 나눔을 만들고, 컷마다 화면에 넘긴 값(extra)도 같이 돌려준다."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen, real = [], build.make_job

        def spy(req, **k):
            seen.append(k["extra"])
            return real(req, **k)
        with mock.patch.object(cuts, "Geo", FakeGeo), mock.patch.object(cuts.build, "make_job", spy):
            job = cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=Path(tmp.name), log=lambda s: None, **kw)
        return job, seen


class StopsOnFixedRoute(CutsBase):
    """1 — 경유지·거리 경계를 준 경로에서도 들르는 곳(핀)이 경고 없이 빠지지 않는다."""

    def stops(self, raw):
        job, seen = self.make(raw)
        self.assertEqual(job["state"], "done")
        return [(s["name"], s["caption"], round(s["at"], 3)) for s in seen[0].get("stops", [])], seen[1].get("stops", []), job

    def test_경유지를_명시해도_이름으로_잡은_경로와_같은_핀이_남는다(self):
        by_names, _, _ = self.stops({**BASE, "cuts": [cut("가", "다", stops=[MISSION]), cut("다", "나")]})
        fixed, second, _ = self.stops({**BASE, "via": [{"name": "다", "lat": B["lat"], "lon": B["lon"]}], "cuts": [cut("가", "다", stops=[MISSION]), cut("다", "나")]})
        self.assertEqual(by_names, [("미션장소", "미션1 오목", 0.5)])
        self.assertEqual(fixed, by_names)
        self.assertEqual(second, [])

    def test_거리_경계를_준_컷에도_핀이_남는다(self):
        total = cuts.route_length([[A["lon"], A["lat"]], [C["lon"], C["lat"]]])
        got, _, _ = self.stops({**BASE, "cuts": [cut("가", "다", stops=[MISSION], from_m=0, to_m=total / 2), cut("다", "나", from_m=total / 2, to_m=total)]})
        self.assertEqual([(n, c) for n, c, _ in got], [("미션장소", "미션1 오목")])

    def test_경로에서_크게_벗어난_핀은_까닭을_알린다(self):
        far = {**MISSION, "lat": 37.52}      # 경로(위도 37.50)에서 약 2.2km
        got, _, job = self.stops({**BASE, "via": [{"name": "다", "lat": B["lat"], "lon": B["lon"]}], "cuts": [cut("가", "다", stops=[far]), cut("다", "나")]})
        self.assertEqual(len(got), 1)
        self.assertTrue(any("미션장소" in w and "떨어져" in w for w in job["warnings"]), job["warnings"])


class RemakeKeepsRoute(CutsBase):
    """2 — 결과 카드에서 화풍만 바꿔 다시 내면 지명을 다시 읽거나 찾지 않고 같은 도로선·경계·핀으로 그린다."""

    def test_화풍만_바꾸면_지명_묻기_검색_지명_찾기가_0번이고_거리_경계_핀이_같다(self):
        asked = []
        namer = lambda names: asked.append(list(names)) or {}       # noqa: E731
        searcher = lambda name, near="": asked.append(name)        # noqa: E731
        first, seen1 = self.make({**BASE, "cuts": [cut("가", "다", stops=[MISSION, {"name": "어딘가"}]), cut("다", "나")]}, namer=namer, searcher=searcher)
        route1 = FakeGeo.last.calls["route"]
        asked.clear()
        FakeGeo.places = {}         # 다시 찾으면 못 찾는다 — 찾지 않아야 같은 결과가 나온다
        again, seen2 = self.make({**first["request"], "art_style": "v4"}, namer=namer, searcher=searcher)
        g = FakeGeo.last
        self.assertEqual((asked, g.calls["geocode"]), ([], []))
        self.assertEqual(g.calls["route"], route1)                  # 같은 지점들로 묻는다(실제로는 저장해 둔 경로 응답을 다시 쓴다)
        self.assertEqual(again["route"], first["route"])
        self.assertEqual([(c["from_m"], c["to_m"]) for c in again["cuts"]], [(c["from_m"], c["to_m"]) for c in first["cuts"]])
        self.assertEqual(seen2[0]["stops"], seen1[0]["stops"])      # 어림으로 놓았던 곳은 어림 표시까지 그대로
        self.assertEqual(again["cuts"][0]["approx"], ["어딘가"])
        self.assertEqual(again["places"]["from"]["display"], "가, 서울")
        self.assertEqual(again["request"]["art_style"], "v4")


class NameOnlyChange(CutsBase):
    """경계 조건 — 마지막 컷의 끝 이름만 바꾸면 자리는 도착지 그대로다. 그 어긋남을 알린다."""

    def test_마지막_컷의_끝_이름이_도착지와_다르면_알린다(self):
        job, seen = self.make({**BASE, "cuts": [cut("가", "다"), cut("다", "여의도역")]})
        self.assertEqual(seen[-1]["goal"], "나")
        self.assertTrue(any("마지막 컷의 끝 이름 '여의도역'" in w for w in job["warnings"]), job["warnings"])


class TypoLabel(CutsBase):
    """사용자 시험(11-20) — 의뢰서의 '월드컵겨이장'을 Liner 가 정식 이름으로 찾고도 화면에는 오타 그대로 냈다. 오타는 고친 표기로 적는다."""

    TYPO = "월드컵거이장 주차장"
    RAW = {**BASE, "from": {"name": TYPO}, "cuts": [cut(TYPO, "다", stops=[{"name": TYPO, "caption": "SUV 출연진 4명"}]), cut("다", "나")]}

    def setUp(self):
        FakeGeo.places = {"서울월드컵경기장 주차장": {**A, "name": "서울월드컵경기장 주차장"}, "다": B, "나": C}

    def check(self, job, seen):
        self.assertEqual((job["cuts"][0]["from"], job["request"]["from"]["name"], job["request"]["cuts"][0]["from"]), ("월드컵경기장 주차장",) * 3)
        self.assertEqual(seen[0]["from_caption"], "SUV 출연진 4명")       # 시작점에 붙은 설명은 그대로 이름표 안에
        self.assertTrue(any("오타로 보여" in w and "월드컵경기장 주차장" in w for w in job["warnings"]), job["warnings"])

    def test_지명_확인에서_오타로_본_이름은_고친_표기로_적는다(self):
        namer = lambda names: {self.TYPO: {"search_name": "서울월드컵경기장 주차장", "kind": "place", "label": "월드컵경기장 주차장"}}      # noqa: E731
        self.check(*self.make(self.RAW, namer=namer))

    def test_검색에서_오타로_본_이름도_고친_표기로_적는다(self):
        searcher = lambda name, near="": {"search_name": "서울월드컵경기장 주차장", "sources": ["https://example.org/a"], "label": "월드컵경기장 주차장"}      # noqa: E731
        self.check(*self.make(self.RAW, searcher=searcher))

    def test_줄임말을_정식_이름으로_늘린_것은_오타_고침으로_받지_않는다(self):
        self.assertEqual(intake._label("홍대", "홍익대학교"), {})
        self.assertEqual(intake._label("DDP", "동대문디자인플라자"), {})
        self.assertEqual(intake._label("홍대입궁", "홍대입구"), {"label": "홍대입구"})
        self.assertEqual(intake._label(self.TYPO, "월드컵경기장 주차장"), {"label": "월드컵경기장 주차장"})

    def test_지명_묻기_답의_고친_표기를_돌려준다(self):
        chat = lambda m, **o: {"choices": [{"message": {"content": json.dumps({"places": [      # noqa: E731
            {"written": self.TYPO, "search_name": "서울월드컵경기장 주차장", "kind": "place", "label": "월드컵경기장 주차장"},
            {"written": "홍대", "search_name": "홍대입구역", "kind": "place", "label": "홍대"}]}, ensure_ascii=False)}}]}
        got = intake.official_names([self.TYPO, "홍대"], chat=chat)
        self.assertEqual((got[self.TYPO].get("label"), "label" in got["홍대"]), ("월드컵경기장 주차장", False))


class LinerCalls(unittest.TestCase):
    """3 — 분류 응답을 잃으면 같은 그림을 다른 단계에서 다시 보내지 않는다. 거절된 의뢰는 다음 단계에서도 보내지 않는다."""

    def setUp(self):
        self.liner = intake._liner()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp, self.log, self.sent = Path(tmp.name), Path(tmp.name) / "calls.jsonl", []
        for p in (mock.patch.object(self.liner, "LOG_PATH", self.log), mock.patch.object(self.liner, "api_key", lambda: "시험용-키"),
                  mock.patch.object(self.liner.time, "sleep", lambda s: None), mock.patch.dict("os.environ", {"LINER_LOG": "on", "MAPCG_READER": "auto"}),
                  mock.patch.dict(self.liner._blocked, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def serve(self, make):
        def fake(req, timeout):
            self.sent.append(req.full_url)
            return make()
        return mock.patch.object(self.liner, "_open", fake)

    def lines(self):
        return [json.loads(s) for s in self.log.read_text(encoding="utf-8").splitlines()] if self.log.is_file() else []

    def test_분류_답을_기다리다_끊기면_지도_읽기로_다시_보내지_않는다(self):
        analyzed = []

        def timeout():
            raise TimeoutError("timed out")
        with self.serve(timeout):
            d = server.Desk(self.tmp, analyze=lambda t, i=(): analyzed.append(1), liner=True, audit=lambda *a, **k: {"state": "skipped"})
            d._spawn = lambda fn, *a, **_: fn(*a)
            d.send("", [{"name": "의뢰지.jpg", "data": "data:image/jpeg;base64,/9j/AAAA", "images": ["data:image/jpeg;base64,/9j/AAAA"]}])
        p = d.msgs[-1]
        self.assertEqual((p["kind"], p["state"]), ("progress", "fail"))
        self.assertIn("순위판인지", p["error"])
        self.assertEqual((len(self.sent), analyzed), (1, []))
        got = self.lines()
        self.assertEqual(len(got), 1)
        self.assertIn("접수 여부 불명", got[0]["note"])

    def test_답이_중간에_끊기면_한_번만_보내고_기록을_남긴다(self):
        class Cut(Reply):
            def read(self, n=-1):
                raise http.client.IncompleteRead(b'{"choi')
        with self.serve(lambda: Cut(b"")), self.assertRaises(self.liner.LinerError) as e:
            self.liner.chat([{"role": "user", "content": "가"}])
        self.assertIn("중간에 끊겼습니다", e.exception.body)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual([x["note"] for x in self.lines()], ["접수 여부 불명: 답이 중간에 끊김"])

    def test_읽을_때_잔액_부족이면_같은_의뢰의_제작_단계도_보내지_않는다(self):
        refuse = lambda: (_ for _ in ()).throw(urllib.error.HTTPError("http://x", 402, "거절", {}, io.BytesIO(b'{"error":"no"}')))      # noqa: E731
        with self.serve(refuse):
            with self.liner.request("의뢰-7"), self.assertRaises(self.liner.LinerError):        # 읽기(카드 내기 전)
                self.liner.chat([{"role": "user", "content": "읽기"}])
            with self.liner.request("의뢰-7"), self.assertRaises(self.liner.LinerError):        # 확인 카드를 거쳐 같은 번호로 연 제작 단계(지명 묻기)
                self.liner.chat([{"role": "user", "content": "지명"}])
            with self.liner.request("의뢰-7"), self.assertRaises(self.liner.LinerError):        # 검색
                self.liner.search_web("지명")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual([x["request"] for x in self.lines()], ["의뢰-7"] * 3)

    def test_명령줄_컷_제작도_의뢰_번호로_묶는다(self):
        from mapcg import __main__ as cli
        seen = []
        path = ROOT / "examples" / "월드컵경기장_노들로_4컷.json"
        with mock.patch.object(intake, "has_key", lambda: False), \
                mock.patch.object(cuts, "make", lambda spec, **kw: seen.append(getattr(self.liner._ctx, "request", None)) or (_ for _ in ()).throw(geo.GeoError("없음"))), \
                mock.patch("sys.stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
            cli.main(["make", str(path), "--no-video"])
        self.assertTrue(seen[0].startswith("명령줄-"), seen)


class FpsInImage(unittest.TestCase):
    """4 — 의뢰서 그림에만 적힌 25fps 가 30fps 로 바뀌지 않고, 감사도 fps 를 본다."""

    def read(self, text, fps):
        obj = {"program": None, "requester_name": None, "requester_role": None, "requester_phone": None, "map_style_text": None, "map_style": "route_move",
               "from_name": "서울역", "to_name": "DDP", "vehicle": "bus", "duration_seconds": 8, "layout": "full", "face_count": None, "face_mode": "unknown",
               "output_width": None, "output_height": None, "fps": fps, "vehicle_color": None, "unclear": [], "cuts": []}
        chat = lambda m, **o: {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}       # noqa: E731
        return intake.analyze(text, ["data:image/jpeg;base64,AAAA"], chat=chat, order=["liner"])

    def test_그림에만_적힌_25fps_가_초안에_남는다(self):
        r = self.read("", 25)
        self.assertEqual(r["draft"]["output"]["fps"], 25)
        self.assertEqual(audit.read_values(r["draft"], r["missing"])["fps"], 25)
        self.assertIn("fps", intake.SCHEMA["required"])

    def test_글과_그림의_fps_가_다르면_두_값을_다_알린다(self):
        r = self.read("서울역에서 DDP로 30fps", 25)
        self.assertEqual(r["draft"]["output"]["fps"], 30)
        self.assertTrue(any("30fps" in n and "25fps" in n for n in r["notes"]), r["notes"])

    def test_감사가_fps_를_어긋남으로_가린다(self):
        r = self.read("", 30)
        values = audit.read_values(r["draft"], r["missing"])
        checks = audit.settle([{"field": "fps", "verdict": "mismatch", "evidence": "25fps", "found": "25"}], values, "", True)
        c = next(c for c in checks if c["field"] == "fps")
        self.assertEqual((c["verdict"], c["value"], c["found"]), ("mismatch", "30fps", "25fps"))


class MapBudget(unittest.TestCase):
    """5·6·경계 조건 — 받는 도중의 몫 초과, 캐시 총량, 예전에 저장된 '중간에 그만둔 답'."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.opened = []
        p = mock.patch.dict(geo._rested, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def serve(self, make):
        def fake(req, timeout):
            self.opened.append(req.full_url)
            return make()
        return mock.patch.object(geo, "_open", fake)

    def saved(self):
        return sorted(p.name for p in self.dir.glob("osm_*.json") if p.name != "osm_index.json")

    def test_남은_양보다_큰_응답은_받는_도중에_멈추고_저장하지_않는다(self):
        g = geo.Geo(self.dir)
        g.budget = geo.Budget(seconds=3, size=100, clock=lambda: 0.0)
        g.budget.got, g.budget.waited = 95, 2.0
        with self.serve(lambda: Reply(b'{"elements": []' + b" " * 36 + b"}")):       # 52바이트 · 남은 양 5바이트
            data = g.osm(BOX, "major")
        self.assertEqual((data["_source"], self.saved(), len(self.opened)), ("blank", [], 1))
        self.assertIn("몫", data["_note"])

    def test_남은_시간을_받는_도중에_넘기면_멈추고_저장하지_않는다(self):
        now = [0.0]
        g = geo.Geo(self.dir)
        g.budget = geo.Budget(seconds=3, clock=lambda: now[0])
        g.budget.waited = 2.0           # 남은 시간 1초
        with self.serve(lambda: Reply(SMALL, chunk=20, tick=lambda: now.__setitem__(0, now[0] + 2.5))):
            data = g.osm(BOX, "major")
        self.assertEqual((data["_source"], self.saved(), len(self.opened)), ("blank", [], 1))
        self.assertLessEqual(g.budget.waited, 3 + 2.5)      # 조각 하나를 받는 동안을 넘지 않는다

    def test_한도보다_큰_예전_파일도_디스크_사용량에_넣는다(self):
        (self.dir / "osm_major_big_000000000000.json").write_text(" " * 3000, encoding="utf-8")
        g = geo.Geo(self.dir)
        with mock.patch.object(geo, "MAX_OSM_BYTES", 1000), mock.patch.object(geo, "CACHE_MAX_BYTES", 2000):
            self.assertEqual((g._usage(), g._usage(readable=True)), (3000, 0))
            with self.serve(lambda: Reply(SMALL)):
                data = g.osm(BOX, "major")
        self.assertEqual((data["_source"], self.saved()), ("server", ["osm_major_big_000000000000.json"]))     # 새 것은 쓰기만 하고 저장하지 않는다

    def test_예전에_저장된_중간에_그만둔_답은_캐시로_쓰지_않는다(self):
        with self.serve(lambda: Reply(SMALL)):
            geo.Geo(self.dir).osm(BOX, "major")
        name = self.saved()[0]
        (self.dir / name).write_text(json.dumps({"elements": [], "remark": "runtime error: Query timed out"}), encoding="utf-8")
        with self.assertRaises(geo.GeoError):       # 오프라인: 빈 도형을 정상 캐시로 내지 않는다(대체 캐시 경로에서도)
            geo.Geo(self.dir, offline=True).osm(BOX, "major")
        with self.serve(lambda: Reply(SMALL)):
            data = geo.Geo(self.dir).osm(BOX, "major")
        self.assertEqual((data["_source"], len(data["elements"])), ("server", 1))       # 다시 받는다


class TwoTables(unittest.TestCase):
    """7 — 1차 · 2차 순위표를 이어 붙여 넣으면 뒤 표를 다음 순위로 읽는다."""

    TEXT = "1차 미션 순위\n순위\t이름\t점수\n1\t가람\t120\n2\t나래\t95\n2차 미션 순위\n순위\t이름\t점수\n1\t나래\t130\n2\t가람\t110"

    def test_두_표를_나눠_다음_순위로_붙인다(self):
        r = board.read(self.TEXT)
        d = r["draft"]
        self.assertEqual([(x["name"], x["value_text"]) for x in d["rows"]], [("가람", "120"), ("나래", "95")])
        self.assertEqual((d["title"], d["next"]["title"]), ("1차 미션 순위", "2차 미션 순위"))
        self.assertEqual([(x["name"], x["value_text"]) for x in d["next"]["rows"]], [("나래", "130"), ("가람", "110")])
        req = board.normalize(d)        # 만들 때 '값이 비어 있음'으로 거절되지 않는다
        self.assertEqual(len(req["next"]["rows"]), 2)

    def test_표_하나는_그대로다(self):
        d = board.read("순위\t이름\t점수\n1\t가람\t120\n2\t나래\t95")["draft"]
        self.assertEqual((len(d["rows"]), "next" in d), (2, False))


class CardFlow(unittest.TestCase):
    """8 — 분할 배치에서 앞 컷에서 이어지는 컷은 카드가 다시 떠오르지 않는다."""

    def test_이어지는_컷은_카드가_첫_장부터_다_보인다(self):
        text = (ROOT / "mapcg" / "templates" / "stage.js").read_text(encoding="utf-8")
        self.assertIn("J.flow && (J.flow.in || J.flow.card) ? 1 : ease(clamp01(t / 0.4))", text)


if __name__ == "__main__":
    unittest.main()
