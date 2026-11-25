"""네비 화면(map_style 'navi_screen') — 휴대폰 길안내 화면 모양의 CG.

초록 안내판(다음 회전 · 거리 · 길 이름) · 파란 경로와 차량 화살표 · 아래 목적지 카드(주소 · 남은 시간 · 남은 거리)를
세로 휴대폰 화면(검은 방송 화면 가운데)에 그린다. 기준은 '기존 네비 양식'(여의도→노량진 7초 v1)을 김갑PD 1번(12초)에서
핸드폰으로 봐도 읽히게 다듬은 것이다.

지도 화면(route_move.html)과 달리 브라우저를 쓰지 않고 Pillow 로 한 장씩 그려 바로 영상으로 만든다.
지명 찾기 · 경로(OSRM, 회전 안내 포함) · 지도 도형(OSM)은 앱의 Geo 를 같이 쓴다(받아 둔 것은 다시 받지 않는다).
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import build, request
from . import export as video      # 아래 export() 와 이름이 겹치지 않게
from . import navi_ui as UI
from .geo import ATTRIBUTION, Geo, GeoError

STYLE = "navi_screen"
LABEL = "네비 화면"
W, H, S = 520, 850, 2           # 설계 단위(세로 화면) · 그리는 배율(2배로 그려 줄인다)
LAND, WATER = '#c8e1b5', '#9fcee3'
LABEL_SCALE = 1.55              # 지명 글자 배율 — 핸드폰으로 봐도 읽히게
HALFTONE = 0.0                  # 망점 세기(기존 양식 .19). 팀 디자인 규칙(하프톤 금지)으로 끈다
GRAIN = .007                    # 종이 입자
BASE_MAX_PX = 9000              # 바탕 지도 한 장의 긴 변 한도(픽셀) — 경로가 길면 바탕을 덜 촘촘히 그린다
REF_SPEED = 348.0               # 기준 화면 속도(영상 1초에 지나는 실제 m: 3.65km / 10.5초). 더 빠르면 더 넓게 본다
TRIM_START_M, TRIM_UTURN_M = 80.0, 80.0
# 맑은 고딕(Windows). 없는 컴퓨터(맥 · 리눅스)는 같이 싣는 프리텐다드로 — Windows 에서 그린 화면은 예전과 같다
_BUNDLED = Path(__file__).resolve().parent / "templates" / "fonts" / "Pretendard-Bold.otf"
FONT_BOLD = next((p for p in (r"C:\Windows\Fonts\malgunbd.ttf",) if Path(p).is_file()), str(_BUNDLED))
FONT_REG = next((p for p in (r"C:\Windows\Fonts\malgun.ttf",) if Path(p).is_file()), str(_BUNDLED))
ROAD_W = {'motorway': 31, 'motorway_link': 22, 'trunk': 29, 'trunk_link': 22, 'primary': 29, 'primary_link': 22,
          'secondary': 26, 'secondary_link': 22, 'tertiary': 23, 'tertiary_link': 18,
          'residential': 16, 'unclassified': 17, 'living_street': 13}       # 보행로 · 계단 · 주차장 안길은 그리지 않는다(자글자글해진다)
BUILDING_MAX_DEG2 = 0.003       # 건물을 받을 범위의 한도(위도 × 경도, 약 5km × 6km) — 넘으면 건물 없이


def panel_box(width: int, height: int) -> tuple[int, int, int, int]:
    """방송 화면 안의 세로 화면 자리 (x, y, 너비, 높이). 1920×1080 → (654, 40, 612, 1000) — 위아래 40 여백."""
    ph = round(height * 0.926)
    pw = round(ph * W / H)
    return (width - pw) // 2, (height - ph) // 2, pw, ph


def timing(duration: float) -> tuple[float, float]:
    """(출발 유지, 이동) 초. 나머지는 도착 유지(0.75~1.2초)."""
    hold = min(.5, duration * .05)
    tail = max(.75, min(1.2, duration * .09))
    return hold, max(.5, duration - hold - tail)


# ---------- 좌표 ----------
def projector(lat0: float, lon0: float):
    k = 111195 * math.cos(math.radians(lat0))
    return lambda lon, lat: ((lon - lon0) * k, (lat0 - lat) * 111195)       # x 동쪽 · y 남쪽(화면 아래) 미터


def _cum(xy: list) -> list[float]:
    out = [0.0]
    for p, q in zip(xy, xy[1:]):
        out.append(out[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
    return out


def _cut(coords: list, start_m: float, end_m: float) -> list:
    """경로 선(경도 · 위도)을 앞 start_m · 뒤 end_m 만큼 잘라 낸다(끝점은 선 위에서 보간)."""
    pr = projector(coords[0][1], coords[0][0])
    xy = [pr(*c) for c in coords]
    cum = _cum(xy)
    lo, hi = start_m, cum[-1] - end_m
    if hi - lo < 1:
        return coords

    def at(d):
        i = max(0, min(len(cum) - 2, next((j for j in range(len(cum) - 1) if cum[j + 1] >= d), len(cum) - 2)))
        f = (d - cum[i]) / max(1e-6, cum[i + 1] - cum[i])
        return [coords[i][0] + (coords[i + 1][0] - coords[i][0]) * f, coords[i][1] + (coords[i + 1][1] - coords[i][1]) * f]
    return [at(lo)] + [c for c, d in zip(coords, cum) if lo < d < hi] + [at(hi)]


def _heading(xy, cum, d0, d1) -> float:
    def at(d):
        i = max(0, min(len(cum) - 2, next((j for j in range(len(cum) - 1) if cum[j + 1] >= d), len(cum) - 2)))
        f = (d - cum[i]) / max(1e-6, cum[i + 1] - cum[i])
        return xy[i][0] + (xy[i + 1][0] - xy[i][0]) * f, xy[i][1] + (xy[i + 1][1] - xy[i][1]) * f
    a, b = at(d0), at(d1)
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _off(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def steps_of(osrm: dict) -> list[dict]:
    """OSRM 회전 안내를 경로 위의 구간 [{start, end, name, modifier, type}](실제 m)으로."""
    out, pos = [], 0.0
    for leg in osrm["routes"][0].get("legs", []):
        for st in leg.get("steps", []):
            out.append({"start": pos, "end": pos + st["distance"], "name": st.get("name") or "",
                        "modifier": st["maneuver"].get("modifier", "straight"), "type": st["maneuver"]["type"]})
            pos += st["distance"]
    return out


def tidy(osrm: dict) -> tuple[list, list[dict], float, float, float, list[str]]:
    """12초 안팎의 압축 연출에서 어색한 것을 뺀다. 돌려주는 것: (경로 선, 회전 안내, 실제 거리, 예상 주행 초, 뺀 앞 m, 알림).
    - 출발 직후 반대쪽으로 갔다 꺾는 갈고리 · 꺾여 드는 곡선(앞 80m 안): 차가 처음부터 갈 방향을 보게
    - 도착 직전의 짧은 U턴(80m 미만): 차가 제자리에서 도는 것처럼 보이지 않게"""
    route = osrm["routes"][0]
    coords = [list(c) for c in route["geometry"]["coordinates"]]
    steps = steps_of(osrm)
    dist, secs = float(route.get("distance", 0)), float(route.get("duration", 0))
    notes = []
    pr = projector(coords[0][1], coords[0][0])
    xy = [pr(*c) for c in coords]
    cum = _cum(xy)
    total = cum[-1]
    head = 0.0
    if total > 300:
        ref = _heading(xy, cum, 80, 160)
        for k, d in enumerate(cum):
            if d > TRIM_START_M:
                break
            if _off(_heading(xy, cum, d, min(total, d + 40)), ref) <= 25:
                head = d
                break
    tail = 0.0
    real = [s for s in steps if s["type"] != "arrive"]
    if len(real) >= 2 and real[-1]["modifier"] == "uturn" and real[-1]["end"] - real[-1]["start"] < TRIM_UTURN_M:
        tail = (real[-1]["end"] - real[-1]["start"]) * total / max(1.0, dist)
    if head or tail:
        coords = _cut(coords, head, tail)
        scale = dist / max(1.0, total)
        h_m, t_m = head * scale, tail * scale
        end = dist - t_m
        kept = []
        for s in steps:
            if s["type"] == "arrive" or s["start"] >= end - 1 or s["end"] <= h_m + 1:
                continue
            kept.append({**s, "start": max(0.0, s["start"] - h_m), "end": min(end, s["end"]) - h_m})
        if kept:
            kept[0]["type"] = "depart"
        steps = kept + [{"start": end - h_m, "end": end - h_m, "name": steps[-1]["name"] if steps else "", "modifier": "straight", "type": "arrive"}]
        secs = secs * (dist - h_m - t_m) / max(1.0, dist)
        dist = dist - h_m - t_m
        if head:
            notes.append(f"출발 직후 {h_m:.0f}m(반대쪽으로 갔다 꺾는 갈고리 · 꺾여 드는 곡선)는 연출상 빼고 그렸습니다.")
        if tail:
            notes.append(f"도착 직전 {t_m:.0f}m U턴은 연출상 빼고 그렸습니다.")
    return coords, steps, dist, secs, head, notes


# ---------- 지도 도형 → 네비 그림 자료 ----------
def _coords(geometry) -> list:
    return [[round(g["lon"], 7), round(g["lat"], 7)] for g in geometry or [] if g]


def _rings(parts: list[list]) -> list[list]:
    """관계(relation)의 조각들을 닫힌 고리로 잇는다. 닫지 못한 조각은 버린다(없는 땅을 지어 닫지 않는다)."""
    parts = [p[:] for p in parts if len(p) > 1]
    rings = []
    while parts:
        ring = parts.pop(0)
        while ring[0] != ring[-1]:
            for i, p in enumerate(parts):
                if ring[-1] == p[0]:
                    ring += p[1:]
                elif ring[-1] == p[-1]:
                    ring += p[-2::-1]
                elif ring[0] == p[-1]:
                    ring = p[:-1] + ring
                elif ring[0] == p[0]:
                    ring = p[:0:-1] + ring
                else:
                    continue
                parts.pop(i)
                break
            else:
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def _area_m2(ring: list, pr) -> float:
    xy = [pr(*c) for c in ring]
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(xy, xy[1:]))) / 2


def shapes(osm: dict, buildings: dict | None, pr) -> dict:
    """geo.osm(+ 건물)의 Overpass 응답을 네비 그림 자료로: roads · water · parks · buildings · railways · labels."""
    data = {"roads": [], "water": [], "parks": [], "buildings": [], "railways": [], "labels": []}
    seen = set()

    def label(text, lon, lat, size, color):
        key = re.sub(r"\s+", "", text)
        if text and key not in seen:
            seen.add(key)
            data["labels"].append([text, lon, lat, size, color])

    for el in [*(osm or {}).get("elements", []), *(buildings or {}).get("elements", [])]:
        tags = el.get("tags") or {}
        name = tags.get("name:ko") or tags.get("name") or ""
        if el.get("type") == "node":
            if tags.get("railway") == "station" and name:
                label(name if name.endswith("역") else name + "역", el["lon"], el["lat"], 28, '#121811')
            elif tags.get("place") in ("suburb", "quarter", "neighbourhood") and name and not re.search(r"\d", name):      # '영등포동1가' 같은 번호 동은 빼다
                label(name, el["lon"], el["lat"], 25 if tags.get("place") == "neighbourhood" else 27, '#26342a')
            continue
        cat = ("water" if tags.get("natural") == "water" else
               "parks" if tags.get("leisure") == "park" or tags.get("landuse") in ("forest", "grass", "recreation_ground") or tags.get("natural") == "wood" else
               "buildings" if "building" in tags else "roads" if tags.get("highway") in ROAD_W else
               "railways" if tags.get("railway") == "rail" else None)
        if not cat:
            continue
        keep = {k: tags[k] for k in ("highway", "bridge", "tunnel", "layer") if k in tags}
        if el.get("type") == "relation":
            members = el.get("members") or []
            outer = _rings([_coords(m.get("geometry")) for m in members if m.get("type") == "way" and m.get("role") in ("outer", "")])
            inner = _rings([_coords(m.get("geometry")) for m in members if m.get("type") == "way" and m.get("role") == "inner"])
            if outer:
                data[cat].append({"outer": outer, "inner": inner, "tags": keep})
            continue
        pts = _coords(el.get("geometry"))
        if len(pts) < 2:
            continue
        if cat in ("water", "parks", "buildings"):
            if pts[0] != pts[-1] or len(pts) < 4:
                continue
            data[cat].append({"outer": [pts], "inner": [], "tags": keep})
            if cat == "parks" and tags.get("leisure") == "park" and name and _area_m2(pts, pr) >= 40000:
                lon = sum(p[0] for p in pts[:-1]) / (len(pts) - 1)
                lat = sum(p[1] for p in pts[:-1]) / (len(pts) - 1)
                label(name, lon, lat, 30, '#2f5a28')
        else:
            data[cat].append({"line": pts, "tags": keep})
    return data


def address(place: dict, name: str) -> str:
    """목적지 카드의 주소 줄: '서울특별시 동작구 노량진역'. 지명 서버의 긴 주소(…, 동작구, 서울특별시, …)에서 시 · 도와 구 · 군을 뽑는다."""
    parts = [p.strip() for p in str(place.get("display") or "").split(",") if p.strip()]
    city = next((p for p in parts if p.endswith(("특별시", "광역시", "특별자치시", "특별자치도")) or (p.endswith("도") and len(p) <= 5)), "")
    gu = next((p for p in parts if p != city and (p.endswith(("구", "군")) or (p.endswith("시") and len(p) <= 5))), "")
    return " ".join(x for x in (city, gu, name) if x)


def short_name(name: str) -> str:
    """방향판의 짧은 이름: '노량진역' → '노량진'."""
    name = name.strip()
    return name[:-1] if name.endswith("역") and len(name) > 2 else name


# ---------- 경로 고르기 ----------
START_NUDGE = [(0, 0), (0, -150), (150, 0), (0, 150), (-150, 0), (0, -300), (300, 0), (0, 300), (-300, 0)]      # 동 · 남(+) m
UTURN_COST, TUNNEL_COST = 600.0, 1500.0
_TUNNEL = re.compile(r"지하차도|터널|tunnel", re.I)


def route_cost(osrm: dict, nudge_m: float = 0.0) -> float:
    """경로의 연출 비용: 실제 거리 + U턴마다 600m + 지하차도 · 터널에서 출발 1500m + 옮긴 거리의 절반.
    (네비 화면은 차를 따라가므로 처음에 반대쪽으로 갔다 돌아오거나 땅 밑에서 출발하면 어색하다)"""
    return float(osrm["routes"][0].get("distance", 0)) + route_penalty(osrm) + nudge_m / 2


START_SIDE_M = 1000.0      # 출발을 150 · 300m 옮겨서 고칠 수 있는 U턴은 경로 앞쪽(1km 안 · 앞 절반)의 것뿐이다


def route_penalty(osrm: dict, start_only: bool = False) -> float:
    """출발이 어색한 정도(m 로 친 벌점): U턴마다 600, 지하차도 · 터널에서 출발하면 1500. 0 이면 손댈 것 없는 경로다.
    start_only=True 면 경로 앞쪽의 U턴만 센다(best_route — 도착 쪽 U턴은 출발을 옮겨도 그대로라 둘레를 물어도 소용없다)."""
    steps = steps_of(osrm)
    reach = min(START_SIDE_M, steps[-1]["end"] / 2) if start_only and steps else math.inf
    uturns = sum(1 for s in steps if s["modifier"] == "uturn" and s["type"] != "arrive" and s["start"] <= reach)
    tunnel = bool(steps) and bool(_TUNNEL.search(steps[0]["name"]))
    return UTURN_COST * uturns + TUNNEL_COST * tunnel


def best_route(geo: Geo, a: dict, b: dict, pinned: bool, log=print) -> tuple[dict, dict, float]:
    """출발을 지명으로 찾았는데 그 자리에서 받은 경로의 출발이 어색하면(지하차도 위 · 갔다가 되돌아옴 — 공원처럼 넓은 곳의 가운데가 그렇다)
    둘레 150 · 300m 의 8곳에서도 경로를 받아, 어색함이 가장 많이 줄어드는 곳으로 옮긴다. 지점을 직접 정했으면 그대로.
    어색하지 않으면 옮기지 않고 둘레도 묻지 않는다. 돌려주는 것: (경로, 실제 출발 지점, 옮긴 m).

    견주는 것은 '어색함(벌점) + 옮긴 거리의 절반'이다. 경로 길이는 넣지 않는다 — 넣으면 도착지 쪽으로 옮긴 출발이 길이 짧아졌다는
    이유만으로 늘 이겨서, 멀쩡한 출발도 거의 매번 300m 를 옮겼다(그리고 의뢰마다 경로 서버를 9번 불렀다)."""
    first = geo.route(a, b, steps=True)
    base = route_penalty(first, start_only=True)
    if pinned or base <= 0:
        return first, a, 0.0
    k = 111195 * math.cos(math.radians(a["lat"]))
    best = (base, first, a, 0.0)
    for dx, dy in START_NUDGE[1:]:
        c = {**a, "lat": a["lat"] - dy / 111195, "lon": a["lon"] + dx / k}
        try:
            r = geo.route(c, b, steps=True)
        except GeoError:
            continue
        moved = math.hypot(dx, dy)
        cost = route_penalty(r, start_only=True) + moved / 2
        if cost < best[0]:
            best = (cost, r, c, moved)
    return best[1], best[2], best[3]


# ---------- 작업 만들기 ----------
def make_job(req: dict, *, project_dir: Path, out_root: Path, offline: bool = False, log=print, geo: Geo | None = None,
             searcher=None) -> dict:
    """지명 찾기 · 경로(회전 안내 포함) · 지도 도형을 받아 네비 화면 작업 폴더를 만든다(미리보기까지). 영상은 export 가 만든다.
    searcher = 지도에 없는 지명을 Liner 웹 검색으로 확인하는 함수(intake.searched_name) — 한 화면 지도와 같은 길(build.resolve_or_search)."""
    t0 = time.perf_counter()
    geo = geo or Geo(project_dir / "data" / "geo", offline=offline)
    warnings: list[str] = []
    log(f"지명 찾기: {req['from']['name']} → {req['to']['name']}")
    found: list[str] = []
    a = build.resolve_or_search(geo, req["from"], build._resolve, searcher, (req["to"].get("name", ""),), found)
    b = build.resolve_or_search(geo, req["to"], build._resolve, searcher, (req["from"].get("name", ""),), found)
    a, b, picked = build.near_pair(a, b)
    if picked:
        warnings.append(picked)
    warnings += [f"'{p['name']}' 은(는) 지도에 그런 이름이 없어 '{p['searched']}'(으)로 찾았습니다. 지점이 맞는지 확인해 주세요."
                 for p in (a, b) if p.get("searched")]
    warnings += found
    log("경로 받기(실제 도로 · 회전 안내)")
    osrm, a, moved = best_route(geo, a, b, "lat" in req["from"], log)
    if moved:
        warnings.append(f"출발 지점을 '{req['from']['name']}' 안에서 약 {moved:.0f}m 옮겼습니다(지하차도 위 · 반대쪽으로 갔다 돌아오는 출발을 피해). 정확한 지점이 필요하면 지점을 직접 지정해 주세요.")
    coords, steps, dist, secs, _, notes = tidy(osrm)
    warnings += notes
    if req.get("vehicle") in ("walk", "boat"):
        warnings.append("네비 화면은 자동차 길로 그렸습니다(걷기 · 배 이동은 경로 이동 지도로 만들어 주세요).")
    if req.get("faces"):
        warnings.append("네비 화면에는 출연진 얼굴을 넣지 않습니다.")
    if req.get("stops"):
        warnings.append("네비 화면에는 들르는 곳 · 정차를 그리지 않습니다(경로 이동 지도로 만들면 지도에 표시하고 멈춥니다).")
    if req.get("layout", "full") != "full":
        warnings.append("네비 화면은 전체 화면으로 만듭니다(화면 배치 설정은 쓰지 않음).")

    lons, lats = [c[0] for c in coords], [c[1] for c in coords]
    pad_lat, pad_lon = 0.02, 0.022      # 약 2km — 화면이 경로 둘레 1~2km 를 보여 준다
    bbox = (min(lats) - pad_lat, min(lons) - pad_lon, max(lats) + pad_lat, max(lons) + pad_lon)
    span = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    detail = "minor" if span < 0.02 else "major"
    log(f"지도 도형 받기({'골목길 포함' if detail == 'minor' else '큰길만'})")
    osm = geo.osm(bbox, detail)
    if osm.get("_note"):
        warnings.append(osm["_note"])
    bld = None
    near = (min(lats) - .012, min(lons) - .014, max(lats) + .012, max(lons) + .014)
    if (near[2] - near[0]) * (near[3] - near[1]) <= BUILDING_MAX_DEG2:
        log("건물 도형 받기")
        try:
            bld = geo.buildings(near)
        except GeoError as e:
            warnings.append(f"건물 도형을 받지 못해 건물 없이 그렸습니다({e}).")
    else:
        warnings.append("경로가 길어 건물은 그리지 않았습니다(길 · 물 · 녹지만).")
    pr = projector(coords[0][1], coords[0][0])
    data = shapes(osm, bld, pr)
    to_name = req["to"]["name"].strip()
    navi = {
        "from": req["from"]["name"], "to": to_name,
        "dest": {"name": to_name, "address": address(b, to_name), "short": short_name(to_name)},
        "route": coords, "steps": steps, "distance_m": round(dist, 1), "drive_seconds": round(secs, 1),
        "duration_seconds": req["duration_seconds"], "output": req["output"], "attribution": ATTRIBUTION.split(" · ")[0],
        **data,
    }
    name = re.sub(r"[^0-9A-Za-z가-힣]+", "", f"{req['from']['name']}_{req['to']['name']}") or "job"
    job_dir = out_root / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}_네비화면"
    job_dir.mkdir(parents=True, exist_ok=False)
    (job_dir / "navi.json").write_text(json.dumps(navi, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log("미리보기 그리기")
    preview(job_dir, navi)
    record = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": request.private(req),      # 전화번호는 가린다(작업 폴더는 넘겨진다)
        "places": {"from": a, "to": b},
        "route": {"distance_m": round(dist), "drive_seconds": round(secs), "drawn_m": round(dist)},
        "map": {"style": STYLE, "detail": detail, "source": osm.get("_source", ""), "buildings": bool(bld),
                "counts": {k: len(data[k]) for k in ("roads", "water", "parks", "buildings", "railways", "labels")}},
        "warnings": warnings,
        "attribution": ATTRIBUTION,
        "timing": {"build_seconds": round(time.perf_counter() - t0, 1)},
    }
    (job_dir / "job.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dir": job_dir, **record}


# ---------- 그리기 ----------
class Scene:
    """navi.json 한 벌로 세로 네비 화면을 시각마다 그린다(1040 × 1700 → 방송 화면 크기로 줄여 얹는다)."""

    def __init__(self, navi: dict):
        self.n = navi
        lon0, lat0 = navi["route"][0]
        self.pr = projector(lat0, lon0)
        self.route = np.array([self.pr(*c) for c in navi["route"]], dtype=np.float64)
        self.cumulative = np.r_[0, np.cumsum(np.linalg.norm(np.diff(self.route, axis=0), axis=1))]
        self.total = float(self.cumulative[-1])
        self.duration = float(navi["duration_seconds"])
        self.hold, self.move = timing(self.duration)
        self.arrive = self.hold + self.move
        speed = self.total / self.move
        self.mpp = 2.25 * float(np.clip((speed / REF_SPEED) ** .5, .8, 2.6))      # 1 설계 픽셀이 몇 m — 빠른(긴) 경로는 넓게
        self.fonts = {}
        self.texture = self._texture()
        self._base()

    def font(self, size, bold=True):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, max(1, round(size * S)))
        return self.fonts[key]

    def w(self, coords):
        return [self.pr(*c) for c in coords]

    def _base(self):
        lo = self.route.min(axis=0) - np.array([1700, 2100]) * self.mpp / 2.25
        hi = self.route.max(axis=0) + np.array([1700, 2100]) * self.mpp / 2.25
        self.ext_min = lo
        self.base_scale = min(1.35, BASE_MAX_PX / float(max(hi - lo)))
        size = tuple(np.ceil((hi - lo) * self.base_scale).astype(int))
        im = Image.new('RGB', size, LAND)
        d = ImageDraw.Draw(im)
        k = self.base_scale

        def pts(coords):
            return [((x - lo[0]) * k, (y - lo[1]) * k) for x, y in self.w(coords)]

        def polygon(f, fill, outline=None):
            for ring in f["outer"]:
                if len(ring) >= 3:
                    d.polygon(pts(ring), fill=fill, outline=outline)
            for ring in f.get("inner", []):
                if len(ring) >= 3:
                    d.polygon(pts(ring), fill=LAND)

        for f in self.n["parks"]:
            polygon(f, '#b2d99b')
        for f in sorted(self.n["water"], key=lambda f: len(f["outer"]) == 1):     # 큰 관계(강)를 먼저, 작은 연못을 위에
            polygon(f, WATER)
        for f in self.n["buildings"]:
            polygon(f, '#edf0e1', '#c1d6ae')
        roads = []
        for f in self.n["roads"]:
            t = f["tags"]
            if t.get("tunnel") == "yes":
                continue
            layer = int(t.get("layer", "0")) if str(t.get("layer", "0")).lstrip('-').isdigit() else 0
            roads.append((layer, ROAD_W[t["highway"]], f))
        for layer in sorted({r[0] for r in roads}):      # 같은 층의 테두리를 먼저 다 그려 교차로가 막히지 않게
            group = sorted((r for r in roads if r[0] == layer), key=lambda r: r[1])
            for casing in (True, False):
                for _, width, f in group:
                    p = pts(f["line"])
                    shade = '#d5dec8' if casing else '#f7f6ec'
                    if f["tags"].get("bridge") == "yes" and casing:
                        shade = '#bacbb8'
                    d.line(p, fill=shade, width=max(1, round((width + (3 if casing else 0)) * k)), joint='curve')
        for f in self.n["railways"]:
            if f["tags"].get("tunnel") == "yes":
                continue
            p = pts(f["line"])
            d.line(p, fill='#c0c8b4', width=max(1, round(3 * k)))
            d.line(p, fill='#e2e7d5', width=max(1, round(k)))
        self.base = im

    def _texture(self):
        yy, xx = np.mgrid[0:H * S, 0:W * S].astype(np.float32)
        rng = np.random.default_rng(63701)
        grain = rng.normal(0, GRAIN, (H * S, W * S)).astype(np.float32)
        screens = []
        for angle, offset in [(22.5, 0), (52.5, 1.9), (7.5, 3.5)]:
            th = math.radians(angle)
            u = (xx * math.cos(th) + yy * math.sin(th) + offset) % 5.8 - 2.9
            v = (-xx * math.sin(th) + yy * math.cos(th) + offset) % 5.8 - 2.9
            screens.append(1 - HALFTONE * np.exp(-(u * u + v * v) / .58) + grain)
        return np.stack(screens, axis=2).astype(np.float32)

    def sample(self, distance):
        distance = float(np.clip(distance, 0, self.total))
        i = min(len(self.route) - 2, max(0, int(np.searchsorted(self.cumulative, distance, side='right') - 1)))
        frac = (distance - self.cumulative[i]) / max(.001, self.cumulative[i + 1] - self.cumulative[i])
        return self.route[i] * (1 - frac) + self.route[i + 1] * frac

    def guidance(self, travelled):
        dest = self.n["dest"]["name"]
        distance = travelled / max(1e-6, self.total) * self.n["distance_m"]
        steps = self.n["steps"]
        active = next((i for i, s in enumerate(steps) if s["end"] > distance), len(steps) - 1)
        step, nxt = steps[active], steps[min(active + 1, len(steps) - 1)]
        mod = nxt["modifier"]
        turn = 'uturn' if mod == 'uturn' else 'left' if 'left' in mod else 'right' if 'right' in mod else 'straight'
        name = nxt["name"]
        if nxt["type"] == 'arrive':
            turn, name = 'straight', f'{dest} 방면'
        return name or f'{dest} 방면', turn, max(0, step["end"] - distance)

    def frame(self, t: float) -> Image.Image:
        p = float(np.clip((t - self.hold) / self.move, 0, 1))
        p = p * p * (3 - 2 * p)
        travelled = p * self.total
        car = self.sample(travelled)
        end_zoom = np.clip((t - (self.arrive - .75)) / .8, 0, 1)
        mpp = self.mpp * (1 - .14 * float(end_zoom * end_zoom * (3 - 2 * end_zoom)))      # 도착 직전에 조금 다가간다
        origin = car - np.array([250, 410]) * mpp
        factor = self.base_scale * mpp / S
        offset = (origin - self.ext_min) * self.base_scale
        im = self.base.transform((W * S, H * S), Image.Transform.AFFINE, (factor, 0, offset[0], 0, factor, offset[1]),
                                 Image.Resampling.BICUBIC).convert('RGBA')
        d = ImageDraw.Draw(im)

        def xy(pt):
            return (np.asarray(pt) - origin) / mpp * S

        def line(points, color, width):
            pxy = [tuple(v) for v in xy(np.asarray(points))]
            if len(pxy) > 1:
                d.line(pxy, fill=color, width=round(width * S), joint='curve')
                r = width * S / 2
                for x, y in pxy:
                    if -r < x < W * S + r and -r < y < H * S + r:
                        d.ellipse((x - r, y - r, x + r, y + r), fill=color)

        i = min(len(self.route) - 2, max(0, int(np.searchsorted(self.cumulative, travelled, side='right') - 1)))
        past, remaining = np.vstack([self.route[:i + 1], car]), np.vstack([car, self.route[i + 1:]])
        line(past, '#dce9e1', 30)
        line(past, '#769fd0', 21)
        line(remaining, '#143bd0', 33)
        line(remaining, '#153eff', 27)
        gap = 175 * mpp / 2.25
        for dist in np.arange(0, self.total, gap):      # 경로 위 꺾쇠: 길 위 거리에 붙어 있어 화면과 함께 움직인다
            if dist < travelled + 45:
                continue
            pt = self.sample(dist)
            a, b = self.sample(max(0, dist - 8)), self.sample(min(self.total, dist + 8))
            tg = (b - a) / max(.001, np.linalg.norm(b - a))
            nm = np.array([-tg[1], tg[0]])
            x, y = xy(pt)
            if -30 < x < W * S + 30 and -30 < y < H * S + 30:
                nodes = [pt - tg * 14 + nm * 14, pt + tg * 6, pt - tg * 14 - nm * 14]
                d.line([tuple(v) for v in xy(nodes)], fill='#e6f2ec', width=round(3.6 * S), joint='curve')

        # 지명: 똑바로 세워 쓰고, 안내판 · 버튼 · 경로 선 · 차 · 도착 깃발 자리와 화면 끝에 걸리면 그 순간은 그리지 않는다
        occupied = []
        carxy = xy(car)
        for dist in np.arange(0, self.total, 20):
            rx, ry = xy(self.sample(dist))
            if -40 * S < rx < W * S + 40 * S and -40 * S < ry < H * S + 40 * S:
                occupied.append((rx - 18 * S, ry - 18 * S, rx + 18 * S, ry + 18 * S))
        ex, ey = xy(self.route[-1])
        if p < .985:
            fx = min(max(ex, 62 * S), 420 * S)
            occupied.append((min(ex, fx) - 58 * S, ey - 90 * S, max(ex, fx) + 58 * S, ey + 16 * S))
        for text, lon, lat, size, color in sorted(self.n["labels"], key=lambda x: -x[3]):
            pos = xy(self.pr(lon, lat))
            x, y = pos
            if not (20 * S < x < 475 * S and UI.MAP_TOP * S < y < UI.MAP_BOTTOM * S) or np.linalg.norm(pos - carxy) < 100 * S:
                continue
            font = self.font(round(size * LABEL_SCALE))
            box = d.multiline_textbbox((x, y), text, font=font, anchor='mm', spacing=4 * S, stroke_width=3 * S)
            if box[0] < 10 * S or box[2] > 480 * S or box[1] < UI.MAP_TOP * S or box[3] > UI.MAP_BOTTOM * S:
                continue
            if any(not (box[2] < o[0] or box[0] > o[2] or box[3] < o[1] or box[1] > o[3]) for o in occupied):
                continue
            occupied.append(box)
            d.multiline_text((x, y), text, font=font, fill=color, anchor='mm', align='center', spacing=4 * S,
                             stroke_width=3 * S, stroke_fill='#f2f4e5')

        if -100 < ex < W * S + 100 and UI.MAP_TOP * S < ey < (UI.MAP_BOTTOM + 60) * S:      # 도착 점과 깃발
            d.ellipse((ex - 12 * S, ey - 12 * S, ex + 12 * S, ey + 12 * S), fill='#f9f9e9', outline='#203ca9', width=4 * S)
            if p < .985:
                fx = min(max(ex, 62 * S), 420 * S)      # 깃발 몸통은 화면 안(오른쪽 교통 막대에 가리지 않게), 꼬리만 점을 가리킨다
                d.rounded_rectangle((fx - 54 * S, ey - 84 * S, fx + 54 * S, ey - 30 * S), radius=14 * S, fill='#e2493e', outline='#f4efd9', width=3 * S)
                tx = min(max(ex, fx - 40 * S), fx + 40 * S)
                d.polygon([(tx - 11 * S, ey - 31 * S), (ex, ey - 16 * S), (tx + 11 * S, ey - 31 * S)], fill='#e2493e')
                d.text((fx, ey - 57 * S), '도착', font=self.font(34), fill='#fff9e9', anchor='mm')

        ahead, behind = self.sample(min(self.total, travelled + 28)), self.sample(max(0, travelled - 28))
        tg = ahead - behind
        angle = math.degrees(math.atan2(tg[1], tg[0]))      # 50m 남짓 평균한 방향 — 꺾이는 점에서 떨지 않게
        vehicle = UI.draw_vehicle().rotate(-angle, Image.Resampling.BICUBIC, expand=True)
        cx, cy = xy(car)
        im.alpha_composite(vehicle, (round(cx - vehicle.width / 2), round(cy - vehicle.height / 2)))

        name, turn, turn_dist = self.guidance(travelled)
        total = self.n["distance_m"]
        im.alpha_composite(UI.draw_ui(t, (1 - p) * total, total, name, turn, t >= self.arrive, turn_distance_m=turn_dist,
                                      estimated_seconds=(1 - p) * self.n["drive_seconds"], dest=self.n["dest"]))
        d = ImageDraw.Draw(im)
        d.text((262 * S, 842 * S), self.n["attribution"], font=self.font(10, False), fill='#6c7367', anchor='ms')
        arr = np.asarray(im.convert('RGB'), dtype=np.float32) * self.texture
        arr = arr * .98 + np.array([249, 247, 231], dtype=np.float32) * .02
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    def broadcast(self, t: float) -> Image.Image:
        """방송 화면(의뢰 크기) 한 장: 검은 바탕 가운데에 세로 네비 화면."""
        out = self.n["output"]
        x, y, pw, ph = panel_box(out["width"], out["height"])
        frame = Image.new('RGB', (out["width"], out["height"]), 'black')
        frame.paste(self.frame(t).resize((pw, ph), Image.Resampling.LANCZOS), (x, y))
        return frame


PREVIEW_PAGE = """<!doctype html><meta charset="utf-8"><title>네비 화면 미리보기</title>
<style>html,body{margin:0;height:100%;background:#000}img{display:block;width:100%;height:100%;object-fit:contain}</style>
<img id="v" alt="네비 화면 미리보기"><script>
const N=@N@,STEP=@STEP@,v=document.getElementById('v');let i=0;
const show=()=>{v.src='preview/'+String(i).padStart(3,'0')+'.jpg';i=(i+1)%N};show();setInterval(show,STEP);
</script>"""


def preview(job_dir: Path, navi: dict, every: float = .5, width: int = 960) -> None:
    """결과 카드에 보일 미리보기: 0.5초마다 한 장(작게)을 그려 돌려 보여 주는 index.html."""
    scene = Scene(navi)
    folder = job_dir / "preview"
    folder.mkdir(exist_ok=True)
    times = list(np.arange(0, scene.duration + 1e-6, every))
    out = navi["output"]
    size = (width, round(width * out["height"] / out["width"]))
    for n, t in enumerate(times):
        scene.broadcast(min(t, scene.duration - 1 / out["fps"])).resize(size, Image.Resampling.LANCZOS).save(folder / f"{n:03d}.jpg", quality=86)
    (job_dir / "index.html").write_text(PREVIEW_PAGE.replace("@N@", str(len(times))).replace("@STEP@", str(round(every * 1000))), encoding="utf-8")


EXPORT_WAIT = 300       # 프레임을 다 보낸 뒤 ffmpeg 가 영상을 맺기를 기다리는 한도(초)


def export(job_dir: Path, log=print, name: str = "네비") -> dict:
    """영상(MP4 · H.264)을 만든다. 돌려주는 것은 video.export(브라우저 내보내기)와 같은 모양: {mp4, frames, spec, seconds}."""
    t0 = time.perf_counter()
    job_dir = Path(job_dir)
    navi = json.loads((job_dir / "navi.json").read_text(encoding="utf-8"))
    scene = Scene(navi)
    out = navi["output"]
    fps, frames = int(out["fps"]), video.frame_count(scene.duration, out["fps"])      # 브라우저 내보내기와 같은 반올림(2.5초 × 25fps = 63장)
    target = job_dir / f"{name}.mp4"
    part = job_dir / f"{name}.쓰는중.mp4"      # 다 만들어진 뒤에만 제 이름으로 바꾼다 — 도중에 실패해도 앞서 만든 영상을 덮지 않고, 반쪽 영상이 남지 않는다
    cmd = [video.find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{out['width']}x{out['height']}", "-r", str(fps), "-i", "pipe:0", "-an",
           "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",       # 홀수 크기는 H.264 가 받지 않는다 — 한 줄을 덧대 짝수로(브라우저 내보내기와 같다)
           "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(part)]
    log(f"프레임 그리기 {frames}장(네비 화면)")
    errors = (job_dir / f"{name}.ffmpeg.log").open("w+b")      # ffmpeg 의 말은 파일로 받는다(파이프로 받으면 프레임을 보내는 동안 막힐 수 있다)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errors)
    except OSError as e:        # ffmpeg 를 실행하지도 못했다 — 열어 둔 기록 파일을 닫고 지운다(열린 채 남으면 다음 내보내기가 못 지운다)
        errors.close()
        (job_dir / f"{name}.ffmpeg.log").unlink(missing_ok=True)
        raise video.ExportError(f"ffmpeg 를 실행하지 못했습니다({type(e).__name__}) — ffmpeg 가 깔려 있는지 확인해 주세요.") from None
    try:
        try:
            for n in range(frames):
                proc.stdin.write(scene.broadcast(n / fps).tobytes())
                if n and n % (fps * 2) == 0:
                    log(f"{n}/{frames}장")
            proc.stdin.close()
            code = proc.wait(timeout=EXPORT_WAIT)
        except BaseException as e:      # 그리다 예외가 났거나, ffmpeg 가 먼저 죽어 더 받지 않거나, 끝내기를 너무 오래 기다렸다
            died = proc.poll() is not None      # ffmpeg 가 먼저 죽었나(그러면 프레임을 넘기다 OSError 가 난다)
            proc.kill()
            proc.wait()
            if isinstance(e, subprocess.TimeoutExpired) or (isinstance(e, OSError) and died):
                errors.seek(0)
                said = errors.read().decode("utf-8", errors="replace").strip()[-400:]
                raise video.ExportError(f"ffmpeg 가 영상을 만들지 못했습니다: {said or type(e).__name__}") from None
            raise
        if code or not part.is_file():
            errors.seek(0)
            said = errors.read().decode("utf-8", errors="replace").strip()[-400:]
            raise video.ExportError(f"ffmpeg 가 영상을 만들지 못했습니다(종료 코드 {code}): {said}")
        os.replace(part, target)
    finally:
        if proc.stdin and not proc.stdin.closed:
            with contextlib.suppress(OSError):
                proc.stdin.close()
        errors.close()
        for leftover in (part, job_dir / f"{name}.ffmpeg.log"):      # 반쪽 영상과 ffmpeg 의 말은 남기지 않는다
            with contextlib.suppress(OSError):
                leftover.unlink(missing_ok=True)
    spec = video.check_spec({"width": out["width"], "height": out["height"], "fps": fps, "frames": frames}, video.probe(target))
    log(video.spec_line(spec))
    result = {"mp4": target, "frames": frames, "spec": spec, "seconds": round(time.perf_counter() - t0, 1)}
    video._record(job_dir, result)
    return result
