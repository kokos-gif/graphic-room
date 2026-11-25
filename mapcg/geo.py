"""공개 지도 데이터 받기 — 지명→좌표(Nominatim) · 주행 경로(OSRM) · 지도 도형(Overpass).

전부 키 없이 받는 OpenStreetMap 계열 공개 서비스다. 받은 것은 data/geo 에 저장하고 같은 요청은 다시 받지 않는다.
결과물에는 출처를 표기해야 한다: © OpenStreetMap contributors (ODbL).
"""
from __future__ import annotations

import difflib
import hashlib
import http.client
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import osmlocal, packs

USER_AGENT = "mapcg/0.1 (broadcast map graphics demo)"
ATTRIBUTION = "© OpenStreetMap contributors (ODbL) · 경로 계산 OSRM"

# 공개 서버는 언제든 느려지거나 우리 요청을 막을 수 있다. 일마다 서버를 둘 이상 두고, 안 되면 다음 것으로 넘어간다.
NOMINATIM = "https://nominatim.openstreetmap.org/search"
PHOTON = "https://photon.komoot.io/api/"                                  # 지명 찾기의 대체 서버(같은 OpenStreetMap 자료)
ROUTERS = ["https://router.project-osrm.org/route/v1/driving/", "https://routing.openstreetmap.de/routed-car/route/v1/driving/"]
# 걸어서 가는 길(공원 산책로·광장·계단). 차 경로는 공원 안을 지나지 못해 바깥 큰길로 돌아간다
ROUTERS_FOOT = ["https://routing.openstreetmap.de/routed-foot/route/v1/driving/"]
OSRM = ROUTERS[0]
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
COOLDOWN = 600          # 안 된 서버는 이만큼(초) 뒤로 미룬다 — 막힌 서버를 요청마다 다시 기다리지 않게
_rested: dict[str, float] = {}      # 서버 주소 → 다시 앞에 세울 시각
_rest_lock = threading.Lock()


def _in_order(urls: list[str]) -> list[str]:
    """되는 서버부터: 쉬게 한 서버는 뒤로 보낸다(그래도 끝에는 불러 본다 — 다 쉬는 중이면 하나도 못 부르는 일이 없게)."""
    now = time.monotonic()
    with _rest_lock:
        return sorted(urls, key=lambda u: (_rested.get(u, 0) > now, urls.index(u)))


def _rest(url: str) -> None:
    with _rest_lock:
        _rested[url] = time.monotonic() + COOLDOWN

def _in_ring(lat: float, lon: float, ring: list) -> bool:
    """점이 [위도, 경도] 다각형 안에 있는가(짧은 거리라 평면으로 본다)."""
    hit = False
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / ((y2 - y1) or 1e-12):
            hit = not hit
    return hit


def _inside_area(feats: list[dict], areas: set[str]):
    """그 장소(공원)의 경계 안인지 보는 함수. 경계선이 있으면 선으로(경계선에서 30m 안까지 — 출입구는 경계선 위에 있다),
    없으면 범위 상자로, 그 장소가 시설 목록에 없으면 늘 참."""
    key = lambda s: re.sub(r"[\s·.()\-]+", "", s or "")      # noqa: E731
    region = next((f for f in sorted(feats, key=lambda f: "ring" not in f) if key(f["name"]) in areas and (f.get("ring") or f.get("bounds"))), None)

    def inside(f: dict) -> bool:
        if region is None:
            return True
        if region.get("ring"):
            return _in_ring(f["lat"], f["lon"], region["ring"]) or _ring_dist(f["lat"], f["lon"], region["ring"]) <= 30
        b, pad = region["bounds"], 0.0003
        return b[0] - pad <= f["lat"] <= b[2] + pad and b[1] - pad <= f["lon"] <= b[3] + pad
    return inside


def _ring_dist(lat: float, lon: float, ring: list) -> float:
    """점에서 다각형의 경계선까지 m."""
    kx, ky = 111320 * math.cos(math.radians(lat)), 110540
    best = float("inf")
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        ax, ay, dx, dy = (x1 - lon) * kx, (y1 - lat) * ky, (x2 - x1) * kx, (y2 - y1) * ky
        span = dx * dx + dy * dy
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / span)) if span else 0.0
        best = min(best, math.hypot(ax + dx * t, ay + dy * t))
    return best


# 화면이 넓게 잡히면(1픽셀이 여러 미터) 골목길은 받지 않는다.
ROADS_MAJOR = "motorway|trunk|primary|secondary|tertiary|motorway_link|trunk_link|primary_link"
ROADS_MINOR = ROADS_MAJOR + "|secondary_link|tertiary_link|unclassified|residential|living_street"
# 걸어서 가는 지도: 골목길에 더해 산책로·광장·계단·자전거길까지(공원 안의 길이 보여야 한다)
ROADS_FOOT = ROADS_MINOR + "|pedestrian|footway|path|steps|cycleway"
ROADS = {"major": ROADS_MAJOR, "minor": ROADS_MINOR, "foot": ROADS_FOOT}
# 공원·광장 안의 시설: 지명 서버는 '여의도공원 문화의마당'처럼 공원 이름이 붙은 정식 이름이 아니면 못 찾는다.
# 그 둘레의 시설을 한 번에 받아 이름을 맞춰 본다. 이름 없이 종류로만 있는 것(놀이터 등)은 종류로 찾는다
FEATURE_KINDS = {       # 적힌 이름에 든 말 → 그 종류의 OSM 표시
    "놀이터": [("leisure", "playground")], "게양대": [("man_made", "flagpole")], "깃대": [("man_made", "flagpole")],
    "연못": [("water", "pond"), ("natural", "water")], "호수": [("water", "lake"), ("natural", "water")],
    "분수": [("amenity", "fountain")], "화장실": [("amenity", "toilets")], "주차장": [("amenity", "parking")],
    "동상": [("tourism", "artwork"), ("historic", "memorial")], "출입구": [("entrance", "*")], "입구": [("entrance", "*")],
    "정자": [("building", "pavilion"), ("amenity", "shelter")], "운동장": [("leisure", "pitch")], "광장": [("highway", "pedestrian"), ("place", "square")],
    "잔디": [("landuse", "grass")], "정원": [("leisure", "garden")], "숲": [("natural", "wood"), ("landuse", "forest")],
}
KIND_LABELS = [(("leisure", "playground"), "놀이터"), (("water", "pond"), "연못"), (("natural", "water"), "물"), (("natural", "wood"), "숲"),
               (("landuse", "forest"), "숲"), (("landuse", "grass"), "잔디밭"), (("leisure", "garden"), "정원"), (("leisure", "pitch"), "운동장"),
               (("man_made", "flagpole"), "게양대"), (("entrance", "*"), "출입구"), (("amenity", "fountain"), "분수"), (("amenity", "toilets"), "화장실"),
               (("amenity", "parking"), "주차장"), (("tourism", "artwork"), "조형물"), (("historic", "memorial"), "기념물"), (("highway", "pedestrian"), "광장")]


HOME, HOME_KM = (37.565, 126.975), 20.0     # 지명을 먼저 찾는 범위: 서울시청 둘레 20km(서울 전역과 맞닿은 곳 조금)


# 의뢰서에 흔히 적히지만 지도에는 다른 이름으로 실린 서울의 장소(띄어쓰기를 뺀 이름 → 지도에 실린 이름)
SEOUL_NAMES = {"시청광장": "서울광장", "서울시청광장": "서울광장", "시청앞광장": "서울광장", "서울시청앞광장": "서울광장"}


def home_on() -> bool:
    """지명을 서울 안에서 먼저 찾을지. 환경 변수 MAPCG_REGION=all 이면 처음부터 전국에서 찾는다(서울 밖 의뢰를 다룰 때)."""
    return os.environ.get("MAPCG_REGION", "").strip().lower() not in ("all", "off", "kr")


class GeoError(RuntimeError):
    pass


class GeoNotFound(GeoError):
    """지명 서버는 답했는데 그런 이름이 없다(서버가 안 되는 것과 다르다 — 이때만 다른 이름으로 다시 찾는다)."""


class GeoTooBig(GeoError):
    """받을 지도가 한도보다 크다. 같은 요청을 다른 서버에 다시 보내도 똑같이 크므로 다시 보내지 않는다."""


class GeoBudget(GeoError):
    """이 의뢰의 몫(기다릴 시간·받을 양)이 받는 도중에 다 찼다. 서버 탓이 아니므로 서버를 쉬게 하지 않고, 다른 서버에도 묻지 않는다."""


def _bad_remark(data) -> str:
    """지도 서버가 응답 200 으로 보냈지만 중간에 그만둔 답이면 그 말(remark), 아니면 빈 문자열."""
    remark = str(data.get("remark") or "") if isinstance(data, dict) else ""
    return remark if re.search(r"timed out|runtime error|out of memory", remark, re.I) else ""


def _stale(path: Path) -> bool:
    """예전 버전이 저장해 둔 '서버가 중간에 그만둔 답' 파일인가. 한도보다 큰 파일은 열지 않는다(아니라고 본다)."""
    try:
        if path.stat().st_size > MAX_OSM_BYTES:
            return False
        return bool(_bad_remark(json.loads(path.read_text(encoding="utf-8"))))
    except (OSError, ValueError):
        return False


# ---- 받는 양의 한도 ----
# 2026-11-20 실측: 정상으로 받은 지도 도형 40개(서울 컷·서울 묶음 조각)는 가장 큰 것이 5.1MB 였다.
# 같은 날 지명이 다른 도시로 잡혀 받은 7개는 63~308MB 였다. 그 사이에 선을 긋는다.
MAX_OSM_BYTES = int(float(os.environ.get("MAPCG_MAX_MAP_MB") or 40) * 1024 * 1024)      # 지도 도형 응답 하나(정상 최대의 약 8배)
AREA_LIMIT = {"foot": 0.004, "minor": 0.02, "major": 0.6}      # 한 번에 받는 범위(위도 폭 × 경도 폭, 도²). 서울 전체가 0.16, 골목길 화면은 0.005 안쪽이다
CACHE_MAX_BYTES = 500 * 1024 * 1024             # data/geo 에 쌓아 두는 지도 도형의 합. 넘으면 새로 받은 것은 쓰기만 하고 저장하지 않는다
OSM_BUDGET_SECONDS = 120        # 한 의뢰가 지도 도형 서버를 기다리는 시간의 합(컷·대체 서버를 통틀어). 정상 응답은 조각 하나에 2~20초였다
OSM_BUDGET_BYTES = 80 * 1024 * 1024
OSM_BUDGET_TRIES = 6
_received = threading.local()   # 방금 받은 응답의 크기(바이트) — 의뢰의 몫에서 뺀다


PLACE_REST = 120        # 지명 서버가 안 되면 이만큼(초) 쉬게 두고 대체 서버로 바로 간다. 그동안은 대체 서버의 답을 기억해 다시 묻지 않는다
_place_state: dict[str, dict] = {}


class Budget:
    """한 의뢰가 지도 도형 서버에 쓸 수 있는 몫 — 기다린 시간·받은 양·실패한 시도 수. 여러 컷과 대체 서버가 같이 쓴다.

    몫을 다 쓰면 서버를 더 부르지 않고 대비책(받아 둔 지도 · 지도 묶음 · 바탕 없는 지도)으로 간다.
    """

    def __init__(self, seconds: float = OSM_BUDGET_SECONDS, size: int = OSM_BUDGET_BYTES, tries: int = OSM_BUDGET_TRIES, clock=time.monotonic):
        self.seconds, self.size, self.tries, self.clock = seconds, size, tries, clock
        self.waited, self.got, self.tried = 0.0, 0, 0

    def spent(self) -> str | None:
        """몫을 다 썼으면 그 까닭, 남았으면 None."""
        if self.waited >= self.seconds:
            return f"이 의뢰에서 지도 서버를 기다릴 시간({self.seconds:g}초)을 다 썼습니다"
        if self.got >= self.size:
            return f"이 의뢰에서 받을 수 있는 지도의 양({self.size // 1024 // 1024}MB)을 다 받았습니다"
        if self.tried >= self.tries:
            return f"이 의뢰에서 지도 서버가 {self.tries}번 실패해 더 부르지 않습니다"
        return None


def meters(a, b) -> float:
    """두 [경도, 위도] 사이의 거리(m)."""
    la, lb = math.radians(a[1]), math.radians(b[1])
    h = math.sin((lb - la) / 2) ** 2 + math.cos(la) * math.cos(lb) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


BOAT_MS = 4.7           # 배의 운항 속도(m/s, 약 17km/h — 한강버스 정도). 걸리는 시간 기록에만 쓴다(화면은 거리를 센다)
WATER_REACH_M = 2500    # 출발·도착 지점에서 강 중심선까지 이보다 멀면 배로 가는 곳이 아니라고 본다


def water_path(ways: list[dict], a: dict, b: dict, reach_m: float = WATER_REACH_M) -> dict:
    """뱃길: 강 중심선 조각들(ways = [{'name', 'pts': [[위도, 경도], ...]}])을 이은 그물에서 a → b 가장 짧은 물길.

    - 두 지점은 가장 가까운 강 중심선 위의 점(수선의 발)에 짧은 선으로 붙는다(선착장은 강가에 있고 중심선은 강 가운데다).
    - 두 지점 모두 가까이 있는 같은 이름의 강을 먼저 쓴다 — 뚝섬 옆 중랑천처럼 한쪽에만 가까운 지류로 들어가지 않게.
      그 강만으로 이어지지 않으면 둘레의 물길 전체로 다시 찾는다.
    - OSRM 응답과 같은 모양({'code', 'routes': [{'geometry', 'distance', 'duration'}], 'waypoints'})으로 돌려준다. 'water' = 쓴 강 이름.
    """
    import heapq
    A, Z = (float(a["lat"]), float(a["lon"])), (float(b["lat"]), float(b["lon"]))
    kx, ky = 111320 * math.cos(math.radians((A[0] + Z[0]) / 2)), 110540      # 좁은 범위의 평면 근사(1도 = 몇 m)
    segs = [(w.get("name") or "", tuple(p), tuple(q)) for w in ways for p, q in zip(w.get("pts") or [], (w.get("pts") or [])[1:]) if p != q]
    if not segs:
        raise GeoNotFound("둘레에 강 물길이 없습니다 — 배로 갈 수 있는 곳인지 확인해 주세요.")

    def foot(pt, seg):
        """pt 에서 조각 seg 로 내린 수선의 발: (거리 m, 발의 [위도, 경도])."""
        (ax, ay), (bx, by), (px, py) = ((p[1] * kx, p[0] * ky) for p in (seg[1], seg[2], pt))
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
        fx, fy = ax + t * dx, ay + t * dy
        return math.hypot(px - fx, py - fy), (fy / ky, fx / kx)

    def nearest(pt, pool):
        return min(((foot(pt, s), s) for s in pool), key=lambda r: r[0][0])

    def dist(p, q):
        return meters([p[1], p[0]], [q[1], q[0]])

    def solve(pool):
        (da, fa), sa = nearest(A, pool)
        (dz, fz), sz = nearest(Z, pool)
        if da > reach_m or dz > reach_m:
            return None
        if sa is sz:        # 같은 조각 위 — 곧장 간다
            return [fa, fz]
        adj: dict = {}
        for _, p, q in pool:
            d = dist(p, q)
            adj.setdefault(p, []).append((q, d))
            adj.setdefault(q, []).append((p, d))
        START, END = ("출발",), ("도착",)
        adj[START] = [(sa[1], dist(fa, sa[1])), (sa[2], dist(fa, sa[2]))]
        for node in (sz[1], sz[2]):
            adj[node] = [*adj[node], (END, dist(fz, node))]
        best, prev, heap, n = {START: 0.0}, {}, [(0.0, 0, START)], 0
        while heap:
            d, _, u = heapq.heappop(heap)
            if u == END:
                break
            if d > best.get(u, math.inf):
                continue
            for v, w in adj.get(u, ()):
                if d + w < best.get(v, math.inf):
                    best[v], prev[v] = d + w, u
                    n += 1
                    heapq.heappush(heap, (d + w, n, v))
        if END not in prev:
            return None
        path, u = [], prev[END]
        while u != START:
            path.append(u)
            u = prev[u]
        return [fa, *path[::-1], fz]

    # 두 지점 모두 가까운 강(이름 있는 강 먼저, 두 거리의 합이 작은 순서) → 그래도 안 되면 물길 전체
    ranked = []
    for name in {s[0] for s in segs}:
        pool = [s for s in segs if s[0] == name]
        (da, _), _s = nearest(A, pool)
        (dz, _), _s = nearest(Z, pool)
        if da <= reach_m and dz <= reach_m:
            ranked.append((name == "", da + dz, name, pool))
    line, used = None, ""
    for _, _, name, pool in sorted(ranked, key=lambda r: r[:2]):
        line = solve(pool)
        if line:
            used = name
            break
    if not line:
        line = solve(segs)
    if not line:
        raise GeoNotFound(f"두 지점을 잇는 물길을 찾지 못했습니다(강 중심선까지 {reach_m / 1000:.1f}km 안에 있어야 합니다). 선착장 이름이나 지점을 확인해 주세요.")
    pts = [A, *line, Z]

    def sharp(p, q, r):
        """q 에서 75도보다 크게 꺾이는가(되돌아가는 갈고리)."""
        v1, v2 = ((q[1] - p[1]) * kx, (q[0] - p[0]) * ky), ((r[1] - q[1]) * kx, (r[0] - q[0]) * ky)
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        return n1 < 1 or n2 < 1 or (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2) < math.cos(math.radians(75))
    # 강 중심선은 점이 성기다(한강은 수백 m 마다 한 점) — 선착장에서 가장 가까운 점이 비스듬히 앞쪽에 있으면 갔다가 되돌아오는 갈고리가 된다.
    # 양 끝에서 크게 꺾이는 이음점은 건너뛰고 곧장 잇는다(넓은 강이라 물 위에 머문다). 끝마다 두 점까지만 — 굽이를 가로질러 뭍을 건너지 않게
    for _ in range(2):
        if len(pts) > 2 and sharp(pts[0], pts[1], pts[2]):
            del pts[1]
        if len(pts) > 2 and sharp(pts[-3], pts[-2], pts[-1]):
            del pts[-2]
    coords = [[round(p[1], 6), round(p[0], 6)] for i, p in enumerate(pts) if i == 0 or dist(p, pts[i - 1]) > 0.5]
    total = sum(meters(coords[i - 1], coords[i]) for i in range(1, len(coords)))
    return {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": coords}, "distance": total, "duration": total / BOAT_MS}],
            "waypoints": [{"location": coords[0], "name": ""}, {"location": coords[-1], "name": ""}], "water": used}


def together(places: list[dict]) -> list[dict]:
    """같은 이름이 여러 곳에 있을 때(다른 도시의 '월드컵경기장'), 지점마다 다른 지점들과 가장 가까운 후보를 고른다.

    한 의뢰의 지점들은 한 동네에 모여 있기 마련이다. 좌표를 직접 준 지점(후보 없음)은 그대로 둔다.
    """
    def spread(c: dict, others: list[dict]) -> float:
        return sum(min(meters([c["lon"], c["lat"]], [o["lon"], o["lat"]]) for o in (p.get("candidates") or [p])) for p in others)

    out = []
    for i, p in enumerate(places):
        cands = p.get("candidates") or []
        if len(cands) > 1:
            best = min(cands, key=lambda c: spread(c, places[:i] + places[i + 1:]))
            p = {**p, "lat": best["lat"], "lon": best["lon"], "display": best["display"]}
        out.append(p)
    return out


def _open(req, timeout: float):
    """서버에 접속한다(시험에서 가짜 응답으로 바꿔 끼우는 자리)."""
    return urllib.request.urlopen(req, timeout=timeout)


# 지명 서버 사용 규칙: 초당 1건. 메신저에서는 여러 작업이 같이 돌 수 있어 프로그램 전체에서 하나로 센다.
_nominatim_lock = threading.Lock()
_nominatim_last = 0.0


def write_atomic(path: Path, text: str) -> None:
    """임시 파일에 다 쓴 뒤 한 번에 바꿔 넣는다 — 쓰다 만 파일이 남거나 읽히지 않는다."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(8):        # 윈도우에서는 다른 쪽이 읽는 중이거나 백신이 잡고 있으면 잠깐 막힌다
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05)


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", text).strip("_")[:30] or "x"


# 지명 뒤에 붙는, 자리를 가리키는 꼬리말
_TAIL = re.compile(r"\s*(앞|뒤|옆|근처|부근|인근|일대|방향|방면|쪽|진입로|진입|입구|북단|남단|동단|서단)$")


class Geo:
    def __init__(self, cache_dir: str | Path, offline: bool = False):
        self.dir = Path(cache_dir)
        self.offline = offline
        self.use_packs = True       # 앱에 넣어 둔 지도 묶음(packs.py)을 쓸지
        self.blank_ok = True        # 지도 서버가 다 안 될 때 바탕 지도 없이라도 그릴지(그렸으면 결과의 '_note' 에 그 사실이 적힌다)
        self.budget: Budget | None = Budget()      # 이 의뢰가 지도 도형 서버에 쓸 몫. None 이면 한도 없이(지도 묶음을 만들 때)
        self.use_local = True       # 내려받아 둔 지역 지도 자료(osmlocal — data/osm/*.sqlite)를 서버보다 먼저 쓸지
        self._locals: list | None = None

    def _local(self, bbox) -> "osmlocal.Local | None":
        """bbox(남, 서, 북, 동)를 덮는 지역 지도 자료. 있으면 지도 도형 · 건물 · 둘레 시설을 공개 서버(Overpass)에 묻지 않는다."""
        if not self.use_local:
            return None
        if self._locals is None:
            self._locals = osmlocal.find(self.dir)
        return next((loc for loc in self._locals if loc.covers(bbox)), None)

    # ---- 저장해 두고 다시 쓰기 ----
    def _usage(self, readable: bool = False) -> int:
        """쌓아 둔 지도 도형이 디스크에서 차지하는 크기 합. 파일 이름·크기만 본다(열지 않는다 · 목록에 없는 파일도 센다).

        한도보다 커서 앱이 읽지 않는 예전 파일도 디스크는 차지하므로 센다 — 새 지도를 저장할지는 이 합으로 정한다.
        한도를 보고 저장하는 것(room=True)은 지도 도형(osm_)과 네비의 건물 도형(buildings_) 둘이라 둘 다 센다.
        readable=True 면 앱이 읽어 쓰는 파일(한도 이하)만 센다.
        """
        total = 0
        try:
            with os.scandir(self.dir) as it:
                for f in it:
                    if f.name.startswith(("osm_", "buildings_")) and f.name.endswith(".json") and f.name != "osm_index.json":
                        size = f.stat().st_size
                        if not readable or size <= MAX_OSM_BYTES:
                            total += size
        except OSError:
            pass
        return total

    def _cached(self, kind: str, label: str, key, fetch, *, room: bool = False, bad=None, keep=None):
        """room=True 면 쌓아 둔 양이 한도(CACHE_MAX_BYTES)를 넘을 때 새 것을 저장하지 않는다(있던 파일은 지우지 않는다).
        bad(data) 가 참이면 저장된 것을 없는 것으로 친다(예전 버전이 저장한, 서버가 중간에 그만둔 답).
        keep(data) 가 거짓이면 이번 답을 저장하지 않는다(대체 서버의 답처럼 다음에 다시 물어야 하는 것)."""
        h = hashlib.sha1(json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
        path = self.dir / f"{kind}_{_slug(label)}_{h}.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not (bad and bad(data)):
                    return data
            except ValueError:
                pass        # 예전에 쓰다 만 파일 — 없는 것으로 치고 다시 받는다
        if self.offline:
            raise GeoError(f"저장된 지도 데이터가 없습니다({path.name}). 인터넷에 연결해 한 번 받아야 합니다.")
        data = fetch()
        if keep is not None and not keep(data):
            return data
        try:
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if not room or self._usage() + len(text.encode("utf-8")) <= CACHE_MAX_BYTES:
                self.dir.mkdir(parents=True, exist_ok=True)
                write_atomic(path, text)
        except OSError:
            pass            # 저장은 다음에 덜 받으려는 것일 뿐 — 못 써도 이번 결과는 쓴다(쓰다 만 파일은 남지 않는다)
        return data

    @staticmethod
    def _http(url: str, data: bytes | None = None, timeout: float = 60, limit: int | None = None, deadline=None):
        """limit(바이트)를 주면 그보다 큰 응답은 끝까지 받지 않는다 — 서버가 알려 준 크기와 실제로 받은 양을 둘 다 본다.
        deadline=(시계 함수, 그 시계의 마감 시각)을 주면 받는 도중에도 시간을 보고, 넘으면 GeoBudget 으로 멈춘다."""
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept-Language": "ko"})
        _received.n = 0
        try:
            with _open(req, timeout) as resp:
                if limit is None:
                    raw = resp.read()
                    _received.n = len(raw)
                    return json.loads(raw.decode("utf-8"))
                told = resp.headers.get("Content-Length") if getattr(resp, "headers", None) is not None else None
                if told and str(told).isdigit() and int(told) > limit:
                    raise GeoTooBig(f"지도 서버가 보내려는 지도가 너무 큽니다({int(told) // 1024 // 1024}MB · 한도 {limit // 1024 // 1024}MB).")
                parts, got = [], 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
                    _received.n = got
                    if got > limit:
                        raise GeoTooBig(f"받는 지도가 한도({limit // 1024 // 1024}MB)를 넘어 받기를 멈췄습니다.")
                    if deadline and deadline[0]() > deadline[1]:
                        raise GeoBudget("이 의뢰에서 지도 서버를 기다릴 시간을 받는 도중에 다 써서 받기를 멈췄습니다.")
                    parts.append(chunk)
                return json.loads(b"".join(parts).decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = GeoError(f"지도 서버 응답 {e.code}: {url.split('?')[0]}")
            err.status = e.code         # 부른 쪽이 '요청이 틀렸다(400)'와 '서버가 안 된다'를 가를 수 있게
            raise err from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GeoError(f"지도 서버 연결 실패: {url.split('?')[0]} ({getattr(e, 'reason', e)})") from None
        except (http.client.HTTPException, UnicodeDecodeError) as e:
            # 답이 오다가 끊겼다(IncompleteRead 등). 이것도 '이 서버에서는 못 받았다'로 돌려야 다음 서버 · 받아 둔 지도 · 빈 지도로 넘어간다
            raise GeoError(f"지도 서버의 답이 중간에 끊겼습니다: {url.split('?')[0]} ({type(e).__name__})") from None
        except json.JSONDecodeError:
            raise GeoError(f"지도 서버가 JSON 이 아닌 답을 보냈습니다: {url.split('?')[0]}") from None

    # ---- 지명 → 좌표 ----
    def geocode(self, name: str, country: str = "kr", near: tuple[float, float] | None = None, km: float = 30) -> dict:
        """지명 하나를 좌표로. 첫 후보를 쓰고, 나머지 후보도 같이 돌려준다(나중에 사람이 고를 수 있게).

        near=(위도, 경도) 를 주면 그 둘레 km 안에서만 찾는다 — 다른 지역의 같은 이름('월드컵대로')이 후보에 오르지 않는다.
        지명 서버가 안 되면 대체 서버(Photon)에서 찾는다.
        """
        box = None      # (서, 남, 동, 북)
        if near:
            dlat, dlon = km / 111.0, km / (111.0 * max(0.2, abs(math.cos(math.radians(near[0])))))
            box = tuple(round(v, 3) for v in (near[1] - dlon, near[0] - dlat, near[1] + dlon, near[0] + dlat))

        came = {"fallback": False}      # 이번 답이 대체 서버에서 왔나(그러면 저장하지 않는다)
        # 지명 서버가 안 될 때의 짧은 기억(보관 폴더별): 언제까지 쉬게 둘지, 그동안 대체 서버가 준 답
        state = _place_state.setdefault(str(self.dir), {"until": 0.0, "memo": {}})
        memo_key = json.dumps([name, country, list(box) if box else None], ensure_ascii=False)

        def fetch():
            global _nominatim_last
            args = {"q": name, "format": "jsonv2", "limit": 5, "countrycodes": country, "accept-language": "ko"}
            if box:
                args.update(viewbox=f"{box[0]},{box[3]},{box[2]},{box[1]}", bounded=1)
            with _nominatim_lock:
                if time.monotonic() >= state["until"]:      # 방금 안 됐던 지명 서버는 잠깐 쉬게 둔다(지명마다 30초씩 다시 기다리지 않게)
                    wait = 1.1 - (time.monotonic() - _nominatim_last)
                    if wait > 0:
                        time.sleep(wait)
                    try:
                        return self._http(f"{NOMINATIM}?{urllib.parse.urlencode(args)}", timeout=30)
                    except GeoError:
                        state["until"] = time.monotonic() + PLACE_REST
                    finally:
                        _nominatim_last = time.monotonic()
                # 지명 서버가 안 된다 — 대체 서버에서 찾아 같은 모양으로 바꾼다. 이 답은 디스크에 두지 않고 쉬는 동안만 기억한다
                came["fallback"] = True
                kept = state["memo"].get(memo_key)
                if kept and kept[0] > time.monotonic():
                    return kept[1]
                args = {"q": name, "limit": 5, "lang": "default"}
                if box:
                    args.update(bbox=",".join(map(str, box)), lat=near[0], lon=near[1])
                got = self._http(f"{PHOTON}?{urllib.parse.urlencode(args)}", timeout=30)
                out = []
                for f in got.get("features") or []:
                    pr, (lon, lat) = f.get("properties") or {}, f["geometry"]["coordinates"][:2]
                    if country and str(pr.get("countrycode", "")).lower() not in ("", country):
                        continue
                    label = ", ".join(str(pr[k]) for k in ("name", "street", "district", "city", "state", "country") if pr.get(k))
                    out.append({"display_name": label, "lat": lat, "lon": lon, "category": pr.get("osm_key", ""), "type": pr.get("osm_value", "")})
                state["memo"][memo_key] = (time.monotonic() + PLACE_REST, out)
                return out

        # 대체 서버의 답은 저장하지 않는다. 지명 서버가 잠깐 안 됐을 때의 답(특히 '없음')이 기한 없이 남으면,
        # 서버가 돌아온 뒤에도 그 지명은 다시 묻지 않고 계속 '찾지 못했습니다'가 된다
        hits = self._cached("place", name, {"q": name, "country": country, **({"box": list(box)} if box else {})}, fetch,
                            keep=lambda _: not came["fallback"])
        if not hits:
            raise GeoNotFound(f"'{name}' 을(를) 지도에서 찾지 못했습니다. 더 구체적인 이름이나 좌표를 넣어 주세요.")
        cands = [{"display": h.get("display_name", ""), "lat": float(h["lat"]), "lon": float(h["lon"]),
                  "type": f"{h.get('category', '')}/{h.get('type', '')}"} for h in hits]
        return {"name": name, "lat": cands[0]["lat"], "lon": cands[0]["lon"], "display": cands[0]["display"], "candidates": cands}

    def geocode_loose(self, name: str, country: str = "kr", near: tuple[float, float] | None = None, km: float = 30) -> dict:
        """지명을 찾되, 못 찾으면 자리를 가리키는 꼬리말('앞', '방향', '북단' …)을 떼고 다시 찾는다.

        의뢰서에는 '월드컵경기장 앞', '월드컵대교 방향'처럼 적혀 오는데 지도에는 그런 이름이 없다.
        꼬리말을 떼고 찾았으면 결과의 'searched' 에 실제로 찾은 이름을 남긴다(사람이 확인할 수 있게).
        다시 찾는 것은 '그런 이름이 없다'는 답을 받았을 때뿐이다 — 서버가 안 되거나 저장된 것이 없을 때는 그 까닭을 그대로 올린다.
        """
        def loose(near, km) -> dict:
            tried = name.strip()
            while True:
                try:
                    hit = self.geocode(tried, country, near, km)
                    return hit if tried == name.strip() else {**hit, "name": name, "searched": tried}
                except GeoNotFound:
                    shorter = _TAIL.sub("", tried).strip()
                    if not shorter or shorter == tried:
                        raise GeoNotFound(f"'{name}' 을(를) 지도에서 찾지 못했습니다. 더 구체적인 이름이나 좌표를 넣어 주세요.") from None
                    tried = shorter

        # 이 앱이 다루는 곳은 서울이다 — 둘레를 따로 주지 않은 지명은 먼저 서울 안에서 찾는다('시청' · '시청광장' · '월드컵경기장'이 다른 도시로 잡히지 않게).
        # 서울에 그런 이름이 없을 때만 전국에서 찾고, 그렇게 잡힌 지점에는 표시(outside)를 남겨 부른 쪽이 알린다
        if country == "kr" and home_on():
            alias = SEOUL_NAMES.get(re.sub(r"\s+", "", name))
            if alias:       # 지도에는 다른 이름으로 실린 서울의 장소 — 실린 이름으로 찾고, 무엇으로 찾았는지 남긴다
                try:
                    return {**self.geocode(alias, country, near or HOME, km if near else HOME_KM), "name": name, "searched": alias}
                except GeoNotFound:
                    pass
        if near is None and country == "kr" and home_on():
            try:
                return loose(HOME, HOME_KM)
            except GeoNotFound:
                return {**loose(None, km), "outside": True}
        return loose(near, km)

    # ---- 공원·광장 안의 시설 ----
    def features(self, lat: float, lon: float, radius_m: float = 700) -> list[dict]:
        """(lat, lon) 둘레의 시설: 이름이 붙은 것과, 놀이터·게양대·출입구처럼 이름 없이 종류로만 실린 것.
        돌려주는 것: [{'name', 'tags', 'lat', 'lon'}]. 받은 것은 저장해 두고 다시 쓴다(지도 서버를 한 번만 부른다)."""
        dlat, dlon = radius_m / 111000, radius_m / (111000 * max(0.2, abs(math.cos(math.radians(lat)))))
        s, w, n, e = (round(v, 4) for v in (lat - dlat, lon - dlon, lat + dlat, lon + dlon))
        local = self._local((s, w, n, e))
        if local:
            return local.features((s, w, n, e))
        kinds = sorted({f'nwr["{k}"]' if v == "*" else f'nwr["{k}"="{v}"]' for pairs in FEATURE_KINDS.values() for k, v in pairs})
        # 이름이 붙은 것 — 길·노선·가게·사무실은 뺀다(시설 이름만 남게). 광장처럼 이름 붙은 보행로는 따로 넣는다
        named = 'nwr["name"][!"highway"][!"route"][!"railway"][!"public_transport"][!"shop"][!"office"][!"cuisine"]["type"!="route"];'
        query = (f"[out:json][timeout:30][bbox:{s},{w},{n},{e}];\n(" + named + 'nwr["name"]["highway"="pedestrian"];'
                 + "".join(k + ";" for k in kinds) + ")->.f;\n.f out bb tags;\n"      # 면은 범위(bounds)만 온다 — 가운데는 그 범위의 가운데(서버의 center 와 같은 값)
                 + 'way.f["leisure"="park"]["name"];out geom;')     # 공원의 경계선 — 종류로 찾은 시설이 그 공원 안인지 본다(공원이 비스듬하면 범위 상자는 옆 건물까지 덮는다)

        def fetch():
            body = urllib.parse.urlencode({"data": query}).encode("utf-8")
            last: GeoError | None = None
            for url in _in_order(OVERPASS):
                try:
                    data = self._http(url, data=body, timeout=40)
                except GeoError as err:
                    last = err
                    _rest(url)
                    continue
                if not isinstance(data, dict) or "elements" not in data or _bad_remark(data):
                    _rest(url)
                    continue
                out, seen = [], {}
                for el in data["elements"]:
                    if el.get("geometry"):      # 같은 공원의 경계선(두 번째로 온 것) — 앞에 담은 항목에 붙인다
                        hit = seen.get((el.get("type"), el.get("id")))
                        if hit is not None:
                            hit["ring"] = [[round(g["lat"], 6), round(g["lon"], 6)] for g in el["geometry"] if g]
                        continue
                    bb = el.get("bounds")       # 면(공원·숲)의 범위 — 종류로 찾은 시설이 그 공원 안에 있는지 볼 때 쓴다
                    c = el if "lat" in el else ({"lat": (bb["minlat"] + bb["maxlat"]) / 2, "lon": (bb["minlon"] + bb["maxlon"]) / 2} if bb else {})
                    if "lat" in c:
                        out.append({"name": (el.get("tags") or {}).get("name", ""), "tags": el.get("tags") or {},
                                    "lat": round(float(c["lat"]), 6), "lon": round(float(c["lon"]), 6),
                                    **({"bounds": [bb["minlat"], bb["minlon"], bb["maxlat"], bb["maxlon"]]} if bb else {})})
                        seen[(el.get("type"), el.get("id"))] = out[-1]
                return out
            raise last or GeoError("둘레의 시설을 받지 못했습니다.")

        try:
            return self._cached("features", f"{s}_{w}", {"bbox": [s, w, n, e], "q": query}, fetch)
        except GeoError:        # 서버가 안 된다 — 전에 받아 둔 같은 범위의 시설 목록(예전 모양의 질의로 받은 것)이 있으면 그것을 쓴다
            for old in sorted(self.dir.glob(f"features_{_slug(f'{s}_{w}')}_*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(old.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(data, list) and data:
                    return data
            raise

    def find_feature(self, name: str, lat: float, lon: float, area: str = "", radius_m: float = 700) -> dict:
        """둘레의 시설에서 적힌 이름을 찾는다. 이름이 맞는 것이 먼저, 없으면 종류('놀이터')가 맞는 것이 하나뿐일 때 그것.
        area = 이 의뢰의 지역 이름(예: '여의도공원'). 시설 이름 앞에 붙어 있으면('여의도공원 문화의마당') 떼고 견준다.
        돌려주는 것: 지명 찾기와 같은 모양에 'how'(name 이름으로 · kind 종류로) 를 더한 것. 못 찾으면 GeoNotFound."""
        feats = self.features(lat, lon, radius_m)
        key = lambda s: re.sub(r"[\s·.()\-]+", "", s or "")      # noqa: E731
        areas = {key(area), key(re.sub(r"\s*(공원|광장)$", "", area or ""))} - {""}

        def bare(s: str) -> str:
            s = key(s)
            for a in sorted(areas, key=len, reverse=True):
                if s.startswith(a) and len(s) > len(a):
                    return s[len(a):]
            return s
        want = bare(_TAIL.sub("", name.strip()) or name)
        want = re.sub(r"(중앙|가운데|인근|근처|부근|옆|앞|뒤|쪽)$", "", want) or want

        def place(f: dict, how: str) -> dict:
            kind = next((label for (k, v), label in KIND_LABELS if f["tags"].get(k) and (v == "*" or f["tags"].get(k) == v)), "시설")
            return {"name": name, "lat": f["lat"], "lon": f["lon"], "display": f["name"] or f"이름 없는 {kind}", "candidates": [], "how": how}
        num = re.search(r"(\d+)\s*번", name)
        # 지역(공원)의 범위: 종류로만 찾는 것(이름 없는 출입구·놀이터)은 그 안에 있는 것만 받는다 — 옆 건물의 출입구를 집지 않게
        inside = _inside_area(feats, areas)
        # 지역 그 자체('여의도공원')와 번호가 다른 것('1번출입구' ↔ '5번 출입구')은 후보에서 뺀다
        feats = [f for f in feats if key(f["name"]) not in areas and bare(f["name"]) not in ("공원", "광장")
                 and (not num or re.search(rf"(^|\D){num.group(1)}(\D|$)", f["tags"].get("ref", "") + " " + f["name"]))]
        if want:
            exact = [f for f in feats if f["name"] and bare(f["name"]) == want]
            if not exact:       # 적힌 이름이 시설 이름 안에 들었거나, 시설 이름이 적힌 이름의 대부분일 때('한국전통의 숲' ↔ '여의도공원 한국전통의숲')
                exact = [f for f in feats if f["name"] and (want in bare(f["name"]) or (bare(f["name"]) in want and len(bare(f["name"])) >= max(3, 0.6 * len(want))))]
            if not exact and len(want) >= 4:      # 한두 글자 틀린 이름('태극기 계양대' ↔ '태극기게양대')
                scored = [(difflib.SequenceMatcher(None, want, bare(f["name"])).ratio(), f) for f in feats if f["name"]]
                best = max(scored, key=lambda x: x[0], default=(0, None))
                if best[0] >= 0.8 and sum(1 for r, _ in scored if r >= best[0] - 0.02) == 1:
                    exact = [best[1]]
            if exact:
                return place(min(exact, key=lambda f: abs(len(bare(f["name"])) - len(want))), "name")
            # 종류: 적힌 이름에 '놀이터'·'게양대' 같은 말이 있으면 그 종류의 시설 — 하나뿐일 때만(여럿이면 어느 것인지 모른다)
            # 같은 말이 든 고유한 이름('생태연못')이 다른 이름으로 실린 곳('지당')을 가리키는지는 알 수 없다 — 이름이 그 말로 끝나는 것만 본다
            for word, pairs in FEATURE_KINDS.items():
                if word not in want:
                    continue
                if want.endswith(word) and len(want) > len(word) + 1 and not want.startswith(("공원", "광장")):
                    continue        # '생태연못' · '잔디마당'처럼 앞에 고유한 말이 붙었다 — 종류만으로 고르지 않는다
                hits = [f for f in feats if inside(f) and any(f["tags"].get(k) and (v == "*" or f["tags"].get(k) == v) for k, v in pairs)]
                if len(hits) == 1:
                    return place(hits[0], "kind")
        raise GeoNotFound(f"'{name}' 을(를) 둘레의 시설에서 찾지 못했습니다.")

    def candidates(self, lat: float, lon: float, area: str = "", radius_m: float = 700, limit: int = 60) -> list[dict]:
        """그 장소(공원) 안의 시설 목록 — 지도에 그 이름으로 없는 곳을 Liner 가 검색 근거로 고를 때 보여 준다.
        [{'label' 예: "이름 없는 연못 — '여의도공원 자연생태의숲' 안", 'lat', 'lon', 'display'}]"""
        feats = self.features(lat, lon, radius_m)
        key = lambda s: re.sub(r"[\s·.()\-]+", "", s or "")      # noqa: E731
        areas = {key(area), key(re.sub(r"\s*(공원|광장)$", "", area or ""))} - {""}
        inside = _inside_area(feats, areas)

        def kind(f: dict) -> str:
            return next((label for (k, v), label in KIND_LABELS if f["tags"].get(k) and (v == "*" or f["tags"].get(k) == v)), "")
        hosts = [f for f in feats if f["name"] and f.get("bounds") and key(f["name"]) not in areas and kind(f) in ("숲", "광장", "잔디밭", "정원", "운동장")]
        out = []
        for f in feats:
            if key(f["name"]) in areas or f["tags"].get("natural") == "tree" or not inside(f):
                continue
            k = kind(f)
            if not f["name"] and not k:
                continue
            ref = f["tags"].get("ref", "")
            label = f["name"] + (f" ({k})" if k else "") if f["name"] else f"이름 없는 {k}" + (f"(번호 {ref})" if ref else "")
            host = min((h for h in hosts if h is not f and h["bounds"][0] <= f["lat"] <= h["bounds"][2] and h["bounds"][1] <= f["lon"] <= h["bounds"][3]),
                       key=lambda h: (h["bounds"][2] - h["bounds"][0]) * (h["bounds"][3] - h["bounds"][1]), default=None)
            if host:
                label += f" — '{host['name']}' 안"
            out.append({"label": label, "lat": f["lat"], "lon": f["lon"], "display": f["name"] or label})
        return out[:limit]

    # ---- 주행 경로 ----
    def _route(self, tail: str, foot: bool = False) -> dict:
        """경로 서버에 묻는다. 한 서버가 안 되면 대체 서버에 같은 것을 묻는다. foot = 걸어서 가는 길(산책로·광장·계단)."""
        last: GeoError | None = None
        for url in _in_order(ROUTERS_FOOT if foot else ROUTERS):
            try:
                data = self._http(url + tail, timeout=40)
            except GeoError as err:
                if getattr(err, "status", None) == 400:
                    # 경로 서버는 '길이 없다 · 지점이 도로에서 멀다'를 400 으로 답한다 — 서버 고장이 아니므로 쉬게 하지 않고, 다른 서버에도 묻지 않는다
                    raise GeoError("경로를 찾지 못했습니다 — 두 지점을 잇는 길이 없거나 지점이 길에서 너무 멉니다.") from None
                last = err
                _rest(url)
                continue
            if data.get("code") != "Ok" or not data.get("routes"):
                raise GeoError(f"경로를 찾지 못했습니다({data.get('code')}).")
            return data
        raise last or GeoError("경로 서버가 응답하지 않습니다.")

    def route(self, a: dict, b: dict, foot: bool = False, steps: bool = False) -> dict:
        """두 지점 사이 실제 도로를 따라가는 경로(OSRM 응답 그대로). 가까운 도로에 자동으로 붙는다. foot = 걸어서 가는 길.
        steps = 회전 안내(어디서 어느 쪽으로 · 길 이름)까지 — 네비 화면의 안내판이 쓴다."""
        pts = [[round(a["lon"], 5), round(a["lat"], 5)], [round(b["lon"], 5), round(b["lat"], 5)]]

        def fetch():
            coords = ";".join(f"{lon},{lat}" for lon, lat in pts)
            return self._route(f"{coords}?overview=full&geometries=geojson&steps={'true' if steps else 'false'}", foot)

        return self._cached("route", f"{a.get('name', '')}_{b.get('name', '')}",
                            {"pts": pts, **({"foot": True} if foot else {}), **({"steps": True} if steps else {})}, fetch)

    def buildings(self, bbox: tuple[float, float, float, float]) -> dict:
        """bbox(남, 서, 북, 동) 안의 건물 도형(Overpass 응답 그대로). 네비 화면의 흰 건물 덩어리. 범위가 넓으면 GeoError."""
        s, w, n, e = (round(v, 4) for v in bbox)
        if (n - s) * (e - w) > 0.004:
            raise GeoError("건물을 받을 범위가 너무 넓습니다")
        local = self._local((s, w, n, e))
        if local:
            return local.buildings((s, w, n, e))
        query = f'[out:json][timeout:60][bbox:{s},{w},{n},{e}];way["building"];out geom;'

        def fetch():
            # 지도 도형(osm)과 같은 규칙: 쉬는 서버는 부르지 않고, 이 의뢰의 몫(시간 · 용량 · 실패 횟수, self.budget)을 같이 쓴다
            body = urllib.parse.urlencode({"data": query}).encode("utf-8")
            last: GeoError | None = None
            now = time.monotonic()
            awake = [u for u in OVERPASS if _rested.get(u, 0) <= now]
            if not awake:       # 방금 다 안 됐다 — 다시 기다리지 않고 건물 없이 간다
                raise GeoError("지도 서버가 모두 응답하지 않습니다(조금 전에 확인함)")
            bud = self.budget
            for url in awake:
                why = bud.spent() if bud else None
                if why:         # 앞에서 지도 도형을 받느라 이 의뢰의 몫을 다 썼다 — 더 부르지 않는다
                    raise GeoError(why)
                t0 = bud.clock() if bud else 0.0
                _received.n = 0
                room_bytes = min(MAX_OSM_BYTES, bud.size - bud.got) if bud else MAX_OSM_BYTES
                room_secs = bud.seconds - bud.waited if bud else 70.0
                failed = True
                try:
                    data = self._http(url, data=body, timeout=max(1.0, min(70.0, room_secs)), limit=room_bytes,
                                      deadline=(bud.clock, t0 + room_secs) if bud else None)
                    failed = False
                except (GeoTooBig, GeoBudget):      # 크기 · 이 의뢰의 몫이 찬 것은 서버 탓이 아니다 — 서버를 쉬게 하지 않는다
                    raise
                except GeoError as err:
                    last = err
                    _rest(url)
                    continue
                finally:
                    if bud:     # 지도 도형과 같이: 횟수는 실패한 시도만, 시간 · 용량은 다 센다
                        bud.tried += failed
                        bud.waited += bud.clock() - t0
                        bud.got += getattr(_received, "n", 0)
                if not isinstance(data, dict) or "elements" not in data or _bad_remark(data):
                    if bud:
                        bud.tried += 1
                    _rest(url)
                    continue
                return data
            raise last or GeoError("건물 도형을 받지 못했습니다.")

        return self._cached("buildings", f"{s}_{w}", {"bbox": [s, w, n, e], "q": query}, fetch, room=True)

    def route_via(self, points: list[dict], foot: bool = False) -> dict:
        """여러 지점을 차례로 지나는 경로 한 줄(OSRM 응답 그대로). 컷 나눔 지도가 쓴다.

        지점에 bearing(지나는 방향, 북 0·동 90)을 주면 그 방향으로 가는 차로에 붙는다 —
        다리나 큰길 한가운데를 찍으면 반대 방향 차로에 붙어 유턴하는 경로가 나오기 때문이다.
        """
        pts = [[round(p["lon"], 5), round(p["lat"], 5)] for p in points]
        bearings = [f"{round(p['bearing'])},45" if p.get("bearing") is not None else "" for p in points]

        def fetch():
            coords = ";".join(f"{lon},{lat}" for lon, lat in pts)
            extra = f"&bearings={';'.join(bearings)}" if any(bearings) else ""
            return self._route(f"{coords}?overview=full&geometries=geojson&steps=false{extra}", foot)

        return self._cached("route", f"{points[0].get('name', '')}_{points[-1].get('name', '')}_via",
                            {"pts": pts, "bearings": bearings, **({"foot": True} if foot else {})}, fetch)

    # ---- 뱃길(한강버스·유람선) ----
    def water_route(self, a: dict, b: dict) -> dict:
        """두 지점 사이 강 위로 가는 뱃길(OSRM 응답과 같은 모양). 도로 경로 서버는 물 위를 모르므로,
        지도 데이터의 강 중심선(waterway=river·canal)을 받아 이어 붙인 그물에서 가장 짧은 길을 찾는다(water_path)."""
        pad = 0.03      # 약 3km — 두 지점이 같은 강가에 있어도 강 중심선이 범위 안에 들게
        s, w = round(min(a["lat"], b["lat"]) - pad, 4), round(min(a["lon"], b["lon"]) - pad, 4)
        n, e = round(max(a["lat"], b["lat"]) + pad, 4), round(max(a["lon"], b["lon"]) + pad, 4)
        if n - s > 1.0 or e - w > 1.2:
            raise GeoError("뱃길을 찾을 범위가 너무 넓습니다(약 100km 이상). 출발지·도착지 이름을 더 구체적으로 적어 주세요.")
        query = f'[out:json][timeout:40][bbox:{s},{w},{n},{e}];way["waterway"~"^(river|canal)$"];out geom;'

        def fetch():
            body = urllib.parse.urlencode({"data": query}).encode("utf-8")
            last: GeoError | None = None
            for url in _in_order(OVERPASS):
                try:
                    data = self._http(url, data=body, timeout=45)
                except GeoError as err:
                    last = err
                    _rest(url)
                    continue
                if not isinstance(data, dict) or "elements" not in data or _bad_remark(data):
                    _rest(url)
                    continue
                return {"ways": [{"name": (el.get("tags") or {}).get("name", ""),
                                  "pts": [[round(g["lat"], 6), round(g["lon"], 6)] for g in el["geometry"] if g]}
                                 for el in data["elements"] if el.get("type") == "way" and el.get("geometry")]}
            raise last or GeoError("강 물길 데이터를 받지 못했습니다.")

        ways = self._cached("water", f"{s}_{w}", {"bbox": [s, w, n, e], "q": query}, fetch)["ways"]
        return water_path(ways, a, b)

    def water_route_via(self, points: list[dict]) -> dict:
        """여러 지점을 차례로 지나는 뱃길 한 줄(컷 나눔·각자 이동이 쓴다). 구간마다 water_route 를 잇는다.
        도로 경로처럼 구간마다의 길이(legs)를 같이 낸다 — 컷 나눔이 경유지 자리 · 구간 이름 컷의 경계를 이것으로 잡는다."""
        coords: list = []
        legs: list[dict] = []
        for a, b in zip(points, points[1:]):
            leg = self.water_route(a, b)["routes"][0]
            coords += leg["geometry"]["coordinates"][1 if coords else 0:]
            legs.append({"distance": float(leg["distance"]), "duration": float(leg["distance"]) / BOAT_MS})
        total = sum(leg["distance"] for leg in legs)
        return {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": coords}, "distance": total, "duration": total / BOAT_MS, "legs": legs}],
                "waypoints": [{"location": coords[0], "name": ""}, {"location": coords[-1], "name": ""}]}

    # ---- 지도 도형 ----
    def osm(self, bbox: tuple[float, float, float, float], detail: str = "major", wait: float | None = None) -> dict:
        """bbox = (남, 서, 북, 동). 도로·물·녹지·철도·지명·역을 도형과 함께 받는다.

        차례: 내려받아 둔 지역 지도 자료(osmlocal — 덮으면 여기서 끝) → 전에 받아 둔 같은 범위 → 앱에 넣어 둔 지도 묶음(packs.py — 서버를 부르지 않는다) → 지도 서버들
        → (다 안 되면) 받아 둔 더 넓은 지도 · 큰길만 있는 묶음 · 바탕 없는 지도. 대신 쓴 경우는 결과의 '_note' 에 적힌다.
        """
        s, w, n, e = (round(v, 4) for v in bbox)
        if n - s > 2.0 or e - w > 2.5:      # 대략 200km — 이보다 넓으면 수백 MB 를 받게 된다. 지명이 잘못 잡힌 경우다
            raise GeoError("지도 범위가 너무 넓습니다(200km 이상). 출발지·도착지가 다른 지역의 같은 이름으로 잡힌 것 같습니다 — 이름을 더 구체적으로 적어 주세요.")
        local = self._local((s, w, n, e))
        if local:
            if detail != "major" and (n - s) * (e - w) > AREA_LIMIT.get(detail, AREA_LIMIT["major"]):
                # 서버로 받을 때와 같은 한도: 이 넓이에 골목길까지 그리면 너무 무겁다 — 큰길만(서버 경로의 '큰길만 있는 묶음'과 같은 결과)
                return {**local.osm((s, w, n, e), "major"), "_source": "local_major",
                        "_note": "지도 범위가 넓어 큰길만 그렸습니다(내려받아 둔 지역 지도)."}
            return {**local.osm((s, w, n, e), detail), "_source": "local"}
        roads = ROADS.get(detail, ROADS_MAJOR)
        query = f"""[out:json][timeout:{max(90, int(wait or 0))}][bbox:{s},{w},{n},{e}];
(
  way["highway"~"^({roads})$"];
  way["railway"="rail"]["tunnel"!="yes"];
  way["natural"="water"];
  relation["natural"="water"];
  way["leisure"="park"];
  way["natural"="wood"];
  way["landuse"="forest"];
  node["place"~"^(suburb|quarter|neighbourhood)$"];
  node["railway"="station"];
);
out geom;"""

        wait = wait or (45 if (n - s) * (e - w) < 0.02 else 100)       # 좁은 범위는 금방 온다 — 오래 걸리면 막힌 것으로 보고 다음 서버로 간다
        came = {"from": "cache"}      # 이 지도가 어디서 왔는지(결과의 '_source' — 작업 기록에 남긴다)

        def fetch():
            body = urllib.parse.urlencode({"data": query}).encode("utf-8")
            last: GeoError | None = None
            now = time.monotonic()
            awake = [u for u in OVERPASS if _rested.get(u, 0) <= now]
            if not awake:       # 방금 다 안 됐다 — 요청마다 몇 분씩 다시 기다리지 않고 바로 대비책으로 간다
                raise GeoError("지도 서버가 모두 응답하지 않습니다(조금 전에 확인함).")
            bud = self.budget
            for url in awake:
                why = bud.spent() if bud else None
                if why:         # 앞의 컷·앞의 서버에서 이 의뢰의 몫을 다 썼다 — 더 부르지 않는다
                    raise GeoError(f"{why}.")
                t0 = bud.clock() if bud else 0.0
                _received.n = 0
                # 이번 요청에 쓸 수 있는 것: 응답 하나의 한도와 이 의뢰의 남은 몫 가운데 작은 쪽. 받는 도중에도 남은 시간을 본다
                room_bytes = min(MAX_OSM_BYTES, bud.size - bud.got) if bud else MAX_OSM_BYTES
                room_secs = bud.seconds - bud.waited if bud else wait
                failed = True
                try:
                    try:
                        data = self._http(url, data=body, timeout=max(1.0, min(wait, room_secs)), limit=room_bytes,
                                          deadline=(bud.clock, t0 + room_secs) if bud else None)
                        failed = False
                    except GeoTooBig:
                        if room_bytes < MAX_OSM_BYTES:      # 응답 하나의 한도가 아니라 이 의뢰의 남은 몫을 넘었다
                            raise GeoBudget(f"이 의뢰에서 받을 수 있는 지도의 양({bud.size // 1024 // 1024}MB)을 받는 도중에 다 써서 받기를 멈췄습니다.") from None
                        raise
                except (GeoTooBig, GeoBudget):      # 같은 요청은 어느 서버에서 받아도 똑같이 크다 · 몫은 서버를 바꿔도 같다 — 다른 서버에 다시 보내지 않는다
                    raise
                except GeoError as err:
                    last = err
                    _rest(url)
                    continue
                finally:
                    if bud:
                        # 횟수 한도는 '실패한 시도'만 센다. 잘 받은 것까지 세면 컷이 일곱 · 여덟 개인 의뢰는
                        # 여섯 컷을 잘 받고도 뒤의 컷이 '몫을 다 썼다'며 바탕 없는 지도가 된다(시간 · 용량 한도는 그대로 다 센다)
                        bud.tried += failed
                        bud.waited += bud.clock() - t0
                        bud.got += getattr(_received, "n", 0)
                remark = _bad_remark(data)
                if remark:
                    # 응답 200 이어도 서버가 중간에 그만둔 것이다(도형이 비거나 일부만 온다) — 정상 지도로 저장하지 않는다
                    if bud:
                        bud.tried += 1
                    last = GeoError(f"지도 서버가 지도를 다 보내지 못했습니다({remark[:60]}).")
                    _rest(url)
                    continue
                if isinstance(data, dict) and "elements" in data:
                    self._remember(key_name, [s, w, n, e], detail)
                    came["from"] = "server"
                    return data
                _rest(url)
            raise last or GeoError("지도 도형을 받지 못했습니다.")

        label, key = f"{detail}_{s}_{w}", {"bbox": [s, w, n, e], "detail": detail}
        key_name = f"osm_{_slug(label)}_{hashlib.sha1(json.dumps(key, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:12]}.json"
        exact = self.dir / key_name
        if self.use_packs and (not exact.is_file() or _stale(exact)):      # 받아 둔 것이 없거나, 예전에 저장된 '중간에 그만둔 답'이면 묶음부터
            packed = packs.cover([s, w, n, e], detail)
            if packed:
                return {**packed[1], "_source": "pack"}
        try:
            if exact.is_file() and exact.stat().st_size > MAX_OSM_BYTES:      # 전에 잘못 받아 둔 큰 파일 — 열지 않는다(다시 받지도 않는다)
                raise GeoTooBig(f"전에 받아 둔 이 범위의 지도 파일이 한도보다 큽니다({exact.stat().st_size // 1024 // 1024}MB).")
            area = (n - s) * (e - w)
            if not exact.is_file() and area > AREA_LIMIT.get(detail, AREA_LIMIT["major"]):      # 받기 전에: 이 넓이면 한도를 넘는다
                raise GeoTooBig(f"지도 범위가 넓어({'골목길까지' if detail == 'minor' else '큰길만'} 가로 {(e - w) * 88:.0f}km × 세로 {(n - s) * 111:.0f}km) 받지 않았습니다.")
            data = self._cached("osm", label, key, fetch, room=True, bad=_bad_remark)
            return {**data, "_source": came["from"]}
        except GeoError as err:
            # 서버가 안 되거나, 받을 것이 너무 크거나, 이 의뢰의 몫을 다 썼다. 전에 받아 둔 지도 가운데 이 범위를 덮는 것이 있으면 그것으로 그린다
            why = ("지도가 너무 커서 받지 않고" if isinstance(err, GeoTooBig)
                   else "이 의뢰의 지도 받기 몫(시간·양)을 다 써서" if isinstance(err, GeoBudget) else "지도 서버가 응답하지 않아")
            for cover in self._covers([s, w, n, e], detail):
                try:
                    data = json.loads((self.dir / cover["file"]).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if _bad_remark(data):       # 예전 버전이 저장한, 서버가 중간에 그만둔 답 — 정상 지도로 쓰지 않는다
                    continue
                data["_note"] = (f"{why}, 전에 받아 둔 이 지역의 지도로 그렸습니다"
                                 + ("(큰길만 있고 골목길은 없습니다)." if cover["detail"] != detail else "."))
                return {**data, "_source": "cover" if cover["detail"] == detail else "cover_major"}
            packed = packs.cover([s, w, n, e], detail, exact=False) if self.use_packs else None
            if packed:
                return {**packed[1], "_source": "pack_major",
                        "_note": f"{why}, 앱에 넣어 둔 {packed[0]['label']} 지도 묶음으로 그렸습니다(큰길만 있고 골목길은 없습니다)."}
            if self.blank_ok and not self.offline:
                tail = "범위를 좁히거나 지점을 직접 지정해 주세요." if isinstance(err, GeoTooBig) else "잠시 뒤 다시 만들어 주세요."
                return {"elements": [], "_source": "blank",
                        "_note": f"{why} 바탕 지도(도로·강) 없이 경로와 이름표만 그렸습니다. {tail} ({err})"}
            raise

    # ---- 받아 둔 지도의 목록(어느 파일이 어느 범위인지) ----
    def _index(self) -> dict:
        try:
            return json.loads((self.dir / "osm_index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, file: str, bbox: list[float], detail: str) -> None:
        try:
            index = self._index()
            index[file] = {"bbox": bbox, "detail": detail}
            self.dir.mkdir(parents=True, exist_ok=True)
            write_atomic(self.dir / "osm_index.json", json.dumps(index, ensure_ascii=False))
        except OSError:
            pass

    def _covers(self, bbox: list[float], detail: str) -> list[dict]:
        """받아 둔 지도 가운데 bbox 를 통째로 덮는 것들을 쓸 차례대로: 같은 자세함이 먼저, 그 안에서는 좁은 것부터(없으면 큰길만 있는 것)."""
        s, w, n, e = bbox
        hits = []
        for file, it in self._index().items():
            bs, bw, bn, be = it["bbox"]
            path = self.dir / file
            if bs <= s and bw <= w and bn >= n and be >= e and (it["detail"] == detail or it["detail"] == "major") and path.is_file() \
                    and path.stat().st_size <= MAX_OSM_BYTES:
                hits.append((it["detail"] != detail, (bn - bs) * (be - bw), {"file": file, **it}))
        return [h[2] for h in sorted(hits, key=lambda h: h[:2])]

    def _cover(self, bbox: list[float], detail: str) -> dict | None:
        """받아 둔 지도 가운데 bbox 를 통째로 덮는 가장 좁은 것(파일을 열어 보지는 않는다)."""
        hits = self._covers(bbox, detail)
        return hits[0] if hits else None
