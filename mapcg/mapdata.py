"""OSM 도형 + OSRM 경로 → 화면용 가벼운 데이터.

좌표는 경로 중심을 원점으로 한 미터(x=동, y=남).
투영 · 외곽선 이어붙이기 · U턴 가지 제거는 2026-11-06 에 만든 build-data.js 의 논리를 파이썬으로 옮긴 것이다(기존 부분).
보기 영역 맞춤 · 사각형으로 잘라내기 · 선 단순화 · 등급 분류는 새로 썼다.
"""
from __future__ import annotations

import math
import re

# 도로 등급 → 그리는 굵기 단계(0 = 가장 굵음)
ROAD_CLASS = {
    "motorway": 0, "trunk": 0, "primary": 1, "secondary": 2, "tertiary": 3,
    "motorway_link": 2, "trunk_link": 2, "primary_link": 3, "secondary_link": 4, "tertiary_link": 4,
    "unclassified": 4, "residential": 4, "living_street": 4,
    # 걸어서 가는 지도에만 받는 길(공원 산책로·광장·계단·자전거길) — 가장 가는 단계
    "pedestrian": 5, "footway": 5, "path": 5, "steps": 5, "cycleway": 5,
}
PLACE_RANK = {"suburb": 0, "quarter": 1, "neighbourhood": 2}


# ---------- 좌표 ----------
def projector(lat0: float, lon0: float):
    kx = 111320 * math.cos(math.radians(lat0))
    ky = 110540

    def to_xy(lon: float, lat: float) -> tuple[float, float]:
        return (lon - lon0) * kx, -(lat - lat0) * ky

    def to_lonlat(x: float, y: float) -> tuple[float, float]:
        return lon0 + x / kx, lat0 - y / ky

    return to_xy, to_lonlat


def length(pts) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _area(ring) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1])) / 2


# ---------- 경로 정리 ----------
def clean_route(pts, max_spur: float = 400.0) -> list[tuple[float, float]]:
    """촘촘히 다시 찍은 뒤, 갔다가 되돌아오는 짧은 가지(출발·도착 지점이 도로에 붙으며 생긴 것)를 잘라낸다."""
    pts = [tuple(p) for p in pts]
    total = length(pts)
    if len(pts) < 2 or total == 0:
        return pts
    step = max(5.0, total / 1500)
    dense = []
    for a, b in zip(pts, pts[1:]):
        n = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / step))
        for k in range(n):
            dense.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
    dense.append(pts[-1])
    near, limit = step * 3.6, int(max_spur / step)
    i = 0
    while i < len(dense):
        for j in range(min(len(dense) - 1, i + limit), i + 8, -1):
            if math.hypot(dense[j][0] - dense[i][0], dense[j][1] - dense[i][1]) < near:
                del dense[i + 1:j]
                break
        i += 1
    return dense


def trim_hook(pts, window_ratio: float = 0.25, max_window: float = 1500.0, min_run: float = 150.0) -> list:
    """출발 직후 제자리 근처로 되돌아오는 고리를 잘라낸다.

    넓은 지명('홍대')의 대표 지점은 건물 안이나 일방통행 안쪽에 찍히기 쉬워, 큰길로 나오느라 한 바퀴 돈다.
    달린 거리가 직선 거리의 3배를 넘는 구간 가운데 출발점에 가장 가까이 돌아온 곳에서 새로 출발한다.
    """
    pts = list(pts)
    window = min(max_window, length(pts) * window_ratio)
    run, best, best_d = 0.0, 0, None
    for k in range(1, len(pts)):
        run += math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1])
        if run > window:
            break
        d = math.hypot(pts[k][0] - pts[0][0], pts[k][1] - pts[0][1])
        if run >= min_run and run >= 3 * d and (best_d is None or d < best_d):
            best, best_d = k, d
    return pts[best:]


def simplify(pts, tol: float) -> list:
    """선에서 tol(미터)보다 덜 튀는 점을 뺀다(Douglas–Peucker)."""
    pts = list(pts)
    if len(pts) < 3 or tol <= 0:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        dx, dy = pts[b][0] - ax, pts[b][1] - ay
        seg = dx * dx + dy * dy
        best, far = -1, tol * tol
        for i in range(a + 1, b):
            px, py = pts[i][0] - ax, pts[i][1] - ay
            if seg:
                t = min(1.0, max(0.0, (px * dx + py * dy) / seg))
                px, py = px - t * dx, py - t * dy
            d = px * px + py * py
            if d > far:
                far, best = d, i
        if best >= 0:
            keep[best] = True
            stack.append((a, best))
            stack.append((best, b))
    return [p for p, k in zip(pts, keep) if k]


# ---------- 보기 영역 ----------
def fit_view(pts, aspect: float, pad_side: float = 0.16, pad_top: float = 0.30, pad_bottom: float = 0.16,
             min_width: float = 1500.0) -> tuple[float, float, float, float]:
    """경로가 다 들어오면서 화면 비율(aspect = 가로/세로)에 맞는 사각형 (x0, y0, x1, y1).

    위쪽 여유를 더 둔다 — 이동 아이콘 위에 얼굴이 올라가기 때문이다.
    """
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    w = max(dx / (1 - 2 * pad_side), min_width)
    h = dy / (1 - pad_top - pad_bottom)
    if w / aspect >= h:
        h = w / aspect
    else:
        w = h * aspect
    x0 = (min(xs) + max(xs)) / 2 - w / 2
    y0 = min(ys) - (h - dy) * pad_top / (pad_top + pad_bottom)
    return x0, y0, x0 + w, y0 + h


def plan_view(osrm: dict, aspect: float, tidy_start: bool = True, tidy_end: bool = True) -> dict:
    """경로에서 원점·투영·보기 영역을 정한다. 지도 도형을 받기 전에 부른다.

    tidy_* = 그 끝의 꼬인 고리를 잘라낼지. 좌표를 직접 지정한 지점은 False 로 넘겨 그대로 둔다.
    """
    coords = osrm["routes"][0]["geometry"]["coordinates"]
    lons, lats = [c[0] for c in coords], [c[1] for c in coords]
    to_xy, to_lonlat = projector((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
    route = clean_route([to_xy(lon, lat) for lon, lat in coords])
    first, last = route[0], route[-1]
    if tidy_start:
        route = trim_hook(route)
    if tidy_end:
        route = trim_hook(route[::-1])[::-1]
    moved = (round(math.hypot(route[0][0] - first[0], route[0][1] - first[1])),
             round(math.hypot(route[-1][0] - last[0], route[-1][1] - last[1])))
    return {"to_xy": to_xy, "to_lonlat": to_lonlat, "route": route, "view": fit_view(route, aspect),
            "total": length(route), "moved": moved}


def _grow(rect, margin: float):
    x0, y0, x1, y1 = rect
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    return x0 - mx, y0 - my, x1 + mx, y1 + my


def view_bbox(plan: dict, margin: float = 0.06) -> tuple[float, float, float, float]:
    """보기 영역(여유 포함)의 위경도 사각형 (남, 서, 북, 동)."""
    x0, y0, x1, y1 = _grow(plan["view"], margin)
    west, north = plan["to_lonlat"](x0, y0)
    east, south = plan["to_lonlat"](x1, y1)
    return south, west, north, east


def plan_area(lat: float, lon: float, width: float, height: float) -> dict:
    """경로 없이 한 지점 둘레만 보는 계획(지점 고르기 창용). width·height = 보는 범위(미터).

    kx·ky 는 화면에서 미터를 다시 위경도로 바꿀 때 쓰는 값이다(1도가 몇 미터인지).
    """
    to_xy, to_lonlat = projector(lat, lon)
    return {"to_xy": to_xy, "to_lonlat": to_lonlat, "route": [], "total": 0.0, "moved": (0, 0),
            "view": (-width / 2, -height / 2, width / 2, height / 2),
            "kx": 111320 * math.cos(math.radians(lat)), "ky": 110540}


# ---------- 도형 다듬기 ----------
def stitch(ways) -> list[list]:
    """끝점이 같은 선들을 이어 붙여 고리(외곽선)로 만든다."""
    segs = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while segs:
        r, grown = segs.pop(0), True
        while grown and not (len(r) > 2 and r[0] == r[-1]):
            grown = False
            end = r[-1]
            for i, s in enumerate(segs):
                if s[0] == end:
                    r = r + s[1:]
                elif s[-1] == end:
                    r = r + s[::-1][1:]
                else:
                    continue
                segs.pop(i)
                grown = True
                break
        rings.append(r)
    return rings


def clip_ring(ring, rect) -> list:
    """다각형을 사각형으로 잘라낸다(Sutherland–Hodgman). 화면 밖으로 멀리 뻗은 강·공원을 줄이는 용도."""
    x0, y0, x1, y1 = rect
    pts = list(ring)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]

    def cut(pts, inside, cross):
        out = []
        if not pts:
            return out
        prev, pin = pts[-1], inside(pts[-1])
        for cur in pts:
            cin = inside(cur)
            if cin != pin:
                out.append(cross(prev, cur))
            if cin:
                out.append(cur)
            prev, pin = cur, cin
        return out

    def at_x(xc):
        return lambda a, b: (xc, a[1] + (b[1] - a[1]) * (xc - a[0]) / (b[0] - a[0]))

    def at_y(yc):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (yc - a[1]) / (b[1] - a[1]), yc)

    pts = cut(pts, lambda p: p[0] >= x0, at_x(x0))
    pts = cut(pts, lambda p: p[0] <= x1, at_x(x1))
    pts = cut(pts, lambda p: p[1] >= y0, at_y(y0))
    pts = cut(pts, lambda p: p[1] <= y1, at_y(y1))
    return pts


def _touches(pts, rect) -> bool:
    x0, y0, x1, y1 = rect
    return (max(p[0] for p in pts) >= x0 and min(p[0] for p in pts) <= x1
            and max(p[1] for p in pts) >= y0 and min(p[1] for p in pts) <= y1)


def _inside(p, rect) -> bool:
    return rect[0] <= p[0] <= rect[2] and rect[1] <= p[1] <= rect[3]


def _in_rings(p, rings) -> bool:
    """점이 도형 안인가(고리 여러 개, 짝홀 규칙 — 안쪽 고리는 구멍)."""
    x, y = p
    inside = False
    for r in rings:
        for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
            if (y1 > y) != (y2 > y) and x < x1 + (x2 - x1) * (y - y1) / (y2 - y1):
                inside = not inside
    return inside


def _seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg = dx * dx + dy * dy
    t = min(1.0, max(0.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg)) if seg else 0.0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def roomiest_point(rings, view, avoid=(), nx: int = 30, ny: int = 18):
    """도형 안에서 가장 넉넉한 자리(가장자리에서 멀고, avoid 점들에서도 떨어진 곳)와 그 여유(미터)."""
    x0, y0, x1, y1 = _grow(view, -0.08)
    best, room = None, 0.0
    for i in range(nx):
        for j in range(ny):
            p = (x0 + (i + 0.5) * (x1 - x0) / nx, y0 + (j + 0.5) * (y1 - y0) / ny)
            if not _in_rings(p, rings):
                continue
            d = min(_seg_dist(p, a, b) for r in rings for a, b in zip(r, r[1:] + r[:1]))
            if avoid:
                d = min(d, 0.5 * min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in avoid))
            if d > room:
                best, room = p, d
    return best, room


def _ints(pts) -> list[list[int]]:
    return [[round(x), round(y)] for x, y in pts]


# ---------- 조립 ----------
def build(osm: dict, plan: dict, from_name: str, to_name: str, ppm: float) -> dict:
    """ppm = 지도 자리에서 1미터가 몇 픽셀인지. 단순화 정도와 너무 작은 도형 버리기에 쓴다."""
    to_xy, view = plan["to_xy"], plan["view"]
    clip = _grow(view, 0.08)
    tol = 0.35 / ppm
    min_area = (2.0 / ppm) ** 2

    def xy(geom):
        return [to_xy(p["lon"], p["lat"]) for p in geom if p]

    def ring(pts):
        r = clip_ring(pts, clip)
        if len(r) < 3:
            return None
        r = simplify(r + [r[0]], tol)[:-1]
        if len(r) < 3 or abs(_area(r)) < min_area:
            return None
        return _ints(r)

    def member_rings(e, role):
        ways = [[(p["lon"], p["lat"]) for p in m["geometry"] if p]
                for m in e.get("members", []) if m.get("type") == "way" and m.get("role") == role and m.get("geometry")]
        return [r for r in (ring([to_xy(lon, lat) for lon, lat in w]) for w in stitch(ways)) if r]

    out = {"roads": [], "bridges": [], "water": [], "green": [], "rail": [], "labels": []}
    seen: set[str] = set()
    bridges: dict[str, list] = {}          # 이름 → [전체 길이, 가장 긴 조각 길이, 그 조각의 가운데 점]
    named_water: list[tuple[str, list]] = []

    def label(name, p, kind, rank=0):
        name = re.sub(r"\s*\(.*?\)\s*", "", name).strip()
        if name and name not in seen and _inside(p, view) and not re.search(r"\d가$", name):   # '○○동3가' 같은 잔 이름은 뺀다
            seen.add(name)
            out["labels"].append({"n": name, "p": [round(p[0]), round(p[1])], "k": kind, "r": rank})

    for e in osm.get("elements", []):
        t = e.get("tags") or {}
        kind = e.get("type")
        if kind == "node":
            if "lat" not in e:
                continue
            p = to_xy(e["lon"], e["lat"])
            if t.get("railway") == "station" and t.get("name"):
                nm = re.sub(r"\s*\(.*?\)\s*", "", t["name"]).strip()
                label(nm if nm.endswith("역") else nm + "역", p, "st")
            elif t.get("place") in PLACE_RANK and t.get("name"):
                label(t["name"], p, "pl", PLACE_RANK[t["place"]])
            continue
        if kind == "relation":
            if t.get("natural") == "water":
                group = member_rings(e, "outer") + member_rings(e, "inner")   # 섬은 안쪽 고리 → evenodd 로 뚫는다
                if group:
                    out["water"].append(group)
                    if t.get("name"):
                        named_water.append((t["name"], group))
            continue
        g = xy(e.get("geometry") or [])
        if len(g) < 2 or not _touches(g, clip):
            continue
        if "highway" in t:
            cls = ROAD_CLASS.get(t["highway"])
            if cls is None or t.get("area") == "yes" or t.get("tunnel") not in (None, "no"):
                continue
            is_bridge = t.get("bridge") not in (None, "no")
            (out["bridges"] if is_bridge else out["roads"]).append({"c": cls, "g": _ints(simplify(g, tol))})
            # 다리 이름은 bridge:name 에 따로 있고, name 은 그 위를 지나는 도로 이름인 경우가 많다
            nm = t.get("bridge:name") or (t.get("name", "") if t.get("name", "").endswith("교") else "")
            nm = re.sub(r"\s*\(.*?\)\s*", "", nm).strip()
            if is_bridge and nm:
                ln, b = length(g), bridges.setdefault(nm, [0.0, 0.0, None])
                b[0] += ln
                if ln > b[1]:
                    b[1], b[2] = ln, g[len(g) // 2]
        elif t.get("natural") == "water":
            r = ring(g)
            if r:
                out["water"].append([r])
                if t.get("name"):
                    named_water.append((t["name"], [r]))
        elif t.get("railway"):
            out["rail"].append(_ints(simplify(g, tol)))
        elif t.get("leisure") == "park" or t.get("natural") == "wood" or t.get("landuse") == "forest":
            r = ring(g)
            if r:
                out["green"].append(r)

    for nm, (total, _, mid) in bridges.items():
        if nm.endswith("대교") or total >= 300:
            label(nm, mid, "br")
    # 강·호수 이름은 물 위에서 가장 넉넉하고 경로와 겹치지 않는 자리에 둔다(글자가 들어갈 만큼 넓을 때만)
    for nm, group in named_water:
        p, room = roomiest_point(group, view, avoid=plan["route"][::10])
        if p and room * ppm >= 14:
            label(nm, p, "wt")

    route = [[round(x, 1), round(y, 1)] for x, y in simplify(plan["route"], 0.25 / ppm)]
    out.update({
        "view": [round(v, 1) for v in view],
        "route": route,
        "total": round(plan["total"]),
        "from": {"n": from_name, "p": route[0]} if route else None,       # 경로가 없는 계획(plan_area)도 받는다
        "to": {"n": to_name, "p": route[-1]} if route else None,
    })
    return out
