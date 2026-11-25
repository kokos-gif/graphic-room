"""Geofabrik 국가 자료(.osm.pbf)에서 한 지역을 뽑아 앱이 바로 읽는 지역 지도 자료(data/osm/<지역>.sqlite)를 만든다.

공개 Overpass 서버가 꽉 차서 의뢰마다 지도 받기가 수 분씩 멈추는 일을 없앤다(mapcg/osmlocal.py — 앱은 표준 라이브러리로 읽는다).
고르는 규칙은 geo.py 가 Overpass 에 보내는 질의와 같다: 지도 도형(osm) · 건물(buildings) · 둘레 시설(features).

    python tools/osm_local.py data/osm/south-korea-261120.osm.pbf            서울(packs.AREAS['seoul'] 범위)
    python tools/osm_local.py <pbf> --area seoul --margin 0.03

만들 때만 pyosmium(pip install osmium)이 필요하다. 무거운 작업이다 — heavyq 로 돌린다. 앱(서버)이 켜져 있으면 끝에 바꿔 넣지 못한다(윈도우).
자료: © OpenStreetMap contributors (ODbL).
"""
import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mapcg import geo, osmlocal, packs  # noqa: E402

MAJOR = set(geo.ROADS_MAJOR.split("|"))
MINOR = set(geo.ROADS_MINOR.split("|")) - MAJOR
FOOT = set(geo.ROADS_FOOT.split("|")) - MAJOR - MINOR
PLACES = {"suburb", "quarter", "neighbourhood"}
KINDS = [(k, v) for pairs in geo.FEATURE_KINDS.values() for k, v in pairs]
NO_NAMED = ("highway", "route", "railway", "public_transport", "shop", "office", "cuisine")


def road_rank(tags: dict) -> int | None:
    h = tags.get("highway")
    return 0 if h in MAJOR else 1 if h in MINOR else 2 if h in FOOT else None


def base_rank(tags: dict, kind: str) -> int | None:
    """geo.osm 의 Overpass 질의와 같은 것을 고른다. 도로는 등급, 나머지는 0. 안 고르면 None."""
    if kind == "n":
        return 0 if tags.get("place") in PLACES or tags.get("railway") == "station" else None
    if kind == "r":
        return 0 if tags.get("natural") == "water" else None
    r = road_rank(tags)
    if r is not None:
        return r
    if tags.get("railway") == "rail" and tags.get("tunnel") != "yes":
        return 0
    if tags.get("natural") in ("water", "wood") or tags.get("leisure") == "park" or tags.get("landuse") == "forest":
        return 0
    return None


def is_feature(tags: dict) -> bool:
    """geo.features 의 Overpass 질의: 이름 붙은 시설(길 · 노선 · 가게 · 사무실 빼고) · 이름 붙은 보행 광장 · 종류로 찾는 시설."""
    if tags.get("name"):
        if not any(k in tags for k in NO_NAMED) and tags.get("type") != "route":
            return True
        if tags.get("highway") == "pedestrian":
            return True
    return any((k in tags) if v == "*" else tags.get(k) == v for k, v in KINDS)


def bounds_of(pts) -> tuple:
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return min(lats), min(lons), max(lats), max(lons)


def hits(b, box) -> bool:
    return b[2] >= box[0] and b[0] <= box[2] and b[3] >= box[1] and b[1] <= box[3]


def geom(pts) -> list:
    return [{"lat": round(a, 6), "lon": round(o, 6)} for a, o in pts]


def openable(p: Path) -> str:
    """libosmium 은 윈도우에서 한글이 든 경로를 열지 못한다(2026-11-22 실측: 'Open failed … unknown error') — 짧은 경로(8.3)로 바꿔 준다."""
    s = str(p)
    if s.isascii() or os.name != "nt":
        return s
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.kernel32.GetShortPathNameW(s, buf, len(buf)) and buf.value.isascii():
        return buf.value
    raise RuntimeError(f"지도 원본 경로에 한글 등이 있어 pyosmium 이 열지 못합니다 — 영문만 있는 폴더로 옮겨 주세요: {s}")


def build(pbf: Path, area: str, margin: float, out: Path, log=print) -> Path:
    import osmium
    src = openable(pbf)
    s, w, n, e = packs.AREAS[area]["bbox"]
    box = (s - margin, w - margin, n + margin, e + margin)     # 가장자리 화면의 길이 끊기지 않게 조금 넓게 담는다(덮는 범위는 원래 상자로 알린다)
    t0 = time.perf_counter()

    # 1) 관계: 물(multipolygon)과 시설 — 구성 길의 번호만 먼저 모은다(좌표는 2단계에서)
    rels, need = [], set()
    for r in osmium.FileProcessor(src, osmium.osm.RELATION):
        tags = dict(r.tags)
        water, feat = base_rank(tags, "r") is not None, is_feature(tags)
        if not (water or feat):
            continue
        members = [(m.ref, m.role) for m in r.members if m.type == "w"]
        if not members:
            continue
        rels.append((r.id, tags, members, water, feat))
        need.update(ref for ref, _ in members)
    log(f"관계 {len(rels)}개(구성 길 {len(need)}개) — {time.perf_counter() - t0:.0f}초")

    wr = osmlocal.Writer(out, (s, w, n, e), pbf.name, time.strftime("%Y-%m-%d"))
    try:
        return _fill(osmium, src, wr, box, rels, need, t0, log)
    except BaseException:
        wr.abort()
        raise


def _fill(osmium, src, wr, box, rels, need, t0, log) -> Path:
    counts = {"base": 0, "building": 0, "feature": 0}
    member_geom: dict[int, list] = {}
    for o in osmium.FileProcessor(src, osmium.osm.NODE | osmium.osm.WAY).with_locations():
        if o.is_node():
            if not o.tags:
                continue
            tags = dict(o.tags)
            lat, lon = o.location.lat, o.location.lon
            b = (lat, lon, lat, lon)
            if not hits(b, box):
                continue
            rank = base_rank(tags, "n")
            if rank is not None:
                wr.add("base", rank, {"type": "node", "id": o.id, "lat": round(lat, 6), "lon": round(lon, 6), "tags": osmlocal.slim_tags(tags)}, b)
                counts["base"] += 1
            if is_feature(tags):
                wr.add("feature", 0, {"name": tags.get("name", ""), "tags": tags, "lat": round(lat, 6), "lon": round(lon, 6)}, b)
                counts["feature"] += 1
            continue
        tags = dict(o.tags)
        rank = base_rank(tags, "w") if tags else None
        bld, feat, member = "building" in tags, bool(tags) and is_feature(tags), o.id in need
        if rank is None and not bld and not feat and not member:
            continue
        pts = [(nd.lat, nd.lon) for nd in o.nodes if nd.location.valid()]
        if len(pts) < 2:
            continue
        if member:
            member_geom[o.id] = pts
        b = bounds_of(pts)
        if not hits(b, box):
            continue
        body = {"type": "way", "id": o.id, "tags": osmlocal.slim_tags(tags), "geometry": geom(pts)}
        if rank is not None:
            wr.add("base", rank, body, b)
            counts["base"] += 1
        if bld:
            wr.add("building", 0, body, b)
            counts["building"] += 1
        if feat:
            f = {"name": tags.get("name", ""), "tags": tags, "lat": round((b[0] + b[2]) / 2, 6), "lon": round((b[1] + b[3]) / 2, 6),
                 "bounds": [round(v, 7) for v in b]}
            if tags.get("leisure") == "park" and tags.get("name"):
                f["ring"] = [[round(a, 6), round(c, 6)] for a, c in pts]       # 공원 경계선 — 종류로 찾은 시설이 이 공원 안인지 본다
            wr.add("feature", 0, f, b)
            counts["feature"] += 1
    log(f"노드 · 길 — {time.perf_counter() - t0:.0f}초 · {counts}")

    for rid, tags, members, water, feat in rels:
        parts = [(ref, role, member_geom[ref]) for ref, role in members if ref in member_geom]
        if not parts:
            continue
        b = bounds_of([p for _, _, pts in parts for p in pts])
        if not hits(b, box):
            continue
        if water:
            wr.add("base", 0, {"type": "relation", "id": rid, "tags": osmlocal.slim_tags(tags),
                               "members": [{"type": "way", "ref": ref, "role": role, "geometry": geom(pts)} for ref, role, pts in parts]}, b)
            counts["base"] += 1
        if feat:
            wr.add("feature", 0, {"name": tags.get("name", ""), "tags": tags, "lat": round((b[0] + b[2]) / 2, 6),
                                  "lon": round((b[1] + b[3]) / 2, 6), "bounds": [round(v, 7) for v in b]}, b)
            counts["feature"] += 1
    path = wr.close()
    log(f"완성 {path} · {path.stat().st_size / 1e6:.0f}MB · {counts} · {time.perf_counter() - t0:.0f}초")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pbf", type=Path)
    ap.add_argument("--area", default="seoul", choices=sorted(packs.AREAS))
    ap.add_argument("--margin", type=float, default=0.03)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    out = a.out or a.pbf.resolve().parent / f"{a.area}.sqlite"
    build(a.pbf.resolve(), a.area, a.margin, out)


if __name__ == "__main__":
    main()
