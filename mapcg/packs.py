"""지도 묶음 — 자주 쓰는 지역의 바탕 지도(도로·강·녹지·지명)를 미리 받아 앱에 넣어 둔 것.

공개 지도 서버(Overpass)는 사람이 직접 쓰는 도구용이라 자주 붐비고, 자동으로 계속 부르면 안 된다.
그래서 서울처럼 자주 그리는 지역은 한 번 받아 mapcg/packs/ 에 넣어 두고, 그 안의 지도는 서버를 부르지 않고 그린다.

  python -m mapcg pack seoul        서울 묶음을 (다시) 받아 만든다 — 지도 서버가 될 때 한 번
묶음에 든 것은 그릴 때 쓰는 도형뿐이다. 지명 찾기와 경로 계산은 여전히 서버에 묻는다(geo.py — 서버를 둘씩 둔다).
자료: © OpenStreetMap contributors (ODbL).
"""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent / "packs"
INDEX = PACK_DIR / "index.json"
# 받아 둘 지역: (남, 서, 북, 동) · 자세함(major = 큰길만 — 골목길까지 넣으면 수백 MB 가 된다)
AREAS = {"seoul": {"label": "서울", "bbox": (37.413, 126.734, 37.715, 127.270), "detail": "major"}}


def compact(data: dict) -> dict:
    """그릴 때 쓰지 않는 것(점 번호 목록·테두리 상자·바뀐 기록)을 빼고 좌표를 1m 쯤으로 반올림한다 — 파일이 몇 분의 일로 준다."""
    def geom(g):
        return [{"lat": round(p["lat"], 5), "lon": round(p["lon"], 5)} if p else None for p in g]

    out = []
    for e in data.get("elements", []):
        it = {"type": e.get("type"), "tags": e.get("tags") or {}}
        if e.get("type") == "node":
            if "lat" not in e:
                continue
            it.update(lat=round(e["lat"], 5), lon=round(e["lon"], 5))
        if e.get("geometry"):
            it["geometry"] = geom(e["geometry"])
        if e.get("members"):
            it["members"] = [{"type": m.get("type"), "role": m.get("role"), "geometry": geom(m["geometry"])}
                             for m in e["members"] if m.get("geometry")]
        out.append(it)
    return {"elements": out}


def _index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(name: str, bbox, detail: str, data: dict) -> Path:
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    path = PACK_DIR / f"{name}_{detail}.json.gz"
    # 옆에 다 쓴 뒤 한 번에 바꿔 넣는다 — 쓰다가 끊겨도 있던 묶음은 그대로 남아 계속 쓰인다
    tmp = path.with_name(path.name + ".새로받는중")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(compact(data), f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    index = _index()
    index[path.name] = {"name": name, "label": AREAS.get(name, {}).get("label", name), "bbox": list(bbox), "detail": detail,
                        "made": time.strftime("%Y-%m-%d"), "source": "© OpenStreetMap contributors (ODbL)"}
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def cover(bbox, detail: str, *, exact: bool = True) -> tuple[dict, dict] | None:
    """bbox(남, 서, 북, 동)를 통째로 덮는 묶음이 있으면 (묶음 정보, 지도 자료)를 돌려준다.

    exact=True 면 자세함이 같은 묶음만. False 면 큰길만 있는 묶음도 받는다(골목길까지 필요한 가까운 화면의 대비책).
    """
    s, w, n, e = bbox
    for file, it in _index().items():
        bs, bw, bn, be = it["bbox"]
        if bs <= s and bw <= w and bn >= n and be >= e and (it["detail"] == detail or (not exact and it["detail"] == "major")):
            path = PACK_DIR / file
            if path.is_file():
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return it, json.load(f)
    return None


def build(name: str, cache_dir: Path, log=print, rows: int = 3, cols: int = 4, passes: int = 4) -> Path:
    """지역 하나의 묶음을 지도 서버에서 받아 만든다.

    한 번에 받으면 요청이 커서 붐비는 서버가 거절한다. 그래서 지역을 rows×cols 조각으로 나눠 하나씩, 사이를 띄워 받는다.
    받은 조각은 cache_dir 에 남으므로 중간에 실패해도 다시 실행하면 못 받은 조각만 받는다.
    """
    from . import geo as geo_mod
    area = AREAS[name]
    s, w, n, e = area["bbox"]
    geo = geo_mod.Geo(cache_dir)
    geo.blank_ok = geo.use_packs = geo.use_local = False        # 묶음을 만드는 중에는 묶음 · 지역 자료 · 빈 지도로 대신하지 않는다(서버에서 받는다)
    geo.budget = None                           # 묶음 만들기는 사람이 일부러 돌리는 긴 작업이다 — 의뢰 하나의 몫을 적용하지 않는다(조각마다 크기 한도는 그대로)
    tiles = [(round(s + (n - s) * r / rows, 4), round(w + (e - w) * c / cols, 4), round(s + (n - s) * (r + 1) / rows, 4), round(w + (e - w) * (c + 1) / cols, 4))
             for r in range(rows) for c in range(cols)]
    log(f"{area['label']} 지도 묶음 받는 중 — {len(tiles)}조각으로 나눠 받습니다({area['detail']})")
    got: dict[tuple, dict] = {}
    for attempt in range(passes):
        left = [t for t in tiles if t not in got]
        if not left:
            break
        if attempt:
            log(f"못 받은 조각 {len(left)}개 — 90초 쉬고 다시 받습니다")
            time.sleep(90)
        for t in left:
            geo_mod._rested.clear()     # 조각마다 서버를 처음부터 다시 불러 본다
            try:
                got[t] = geo.osm(t, area["detail"], wait=150)
                log(f"  조각 {len(got)}/{len(tiles)} 받음(도형 {len(got[t].get('elements', []))}개)")
            except geo_mod.GeoError as err:
                log(f"  조각 {t} 실패: {str(err)[:90]}")
            time.sleep(4)               # 공개 서버에 잇달아 몰아 보내지 않는다
    if len(got) < len(tiles):
        raise geo_mod.GeoError(f"{len(tiles)}조각 가운데 {len(tiles) - len(got)}조각을 받지 못했습니다. 받은 조각은 남아 있으니 나중에 다시 실행하면 이어서 받습니다.")
    seen, merged = set(), []            # 조각의 경계에 걸친 도형은 여러 조각에 들어 있다 — 한 번만 넣는다
    for data in got.values():
        for el in data.get("elements", []):
            key = (el.get("type"), el.get("id"))
            if key not in seen:
                seen.add(key)
                merged.append(el)
    path = save(name, area["bbox"], area["detail"], {"elements": merged})
    log(f"묶음: {path} ({path.stat().st_size / 1e6:.1f}MB · 도형 {len(merged)}개)")
    return path
