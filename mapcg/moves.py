"""각자 이동 — 컷마다 다른 사람·팀이 따로 움직이는 의뢰("A 는 문화의 마당에서 놀이터로, B 는 잔디마당에서 생태연못으로 …").

컷 나눔(cuts.py)은 한 번의 이동을 구간마다 잘라 보여 준다 — 경로를 한 번만 받고 컷의 시작 이름은 이름표일 뿐이다.
각자 이동은 컷마다 출발·도착이 따로 있다. 그래서 컷마다 지명을 찾고 경로를 따로 받아 그 컷의 지도를 그리고, 한 영상으로 잇는다.
결과는 컷 나눔과 같은 모양(묶음 폴더 + 컷1·컷2 …)이라 영상 내보내기·검수판·메신저의 결과 카드를 그대로 쓴다.

지명 찾기 차례(한 장소 안의 이동 — 공원 등 — 이면 그 장소 안에서):
  ① 확인 카드에서 지도로 찍은 지점(from_pin · to_pin · 들르는 곳의 lat·lon)
  ② 그 장소 안의 시설 목록(OSM)에서 이름·종류로 — '여의도공원 문화의마당'처럼 공원 이름이 붙어 실린 곳
  ③ 지명 서버(그 장소 둘레에서) — 적힌 이름, Liner 가 알려 준 정식 이름·오타를 고친 표기
  ④ Liner 웹 검색으로 확인한 정식 이름
  ⑤ Liner 가 웹 검색 결과를 근거로 그 장소 안의 시설 가운데 하나를 고른다(좌표는 지도에서 — 모델은 고르기만 한다). 확인이 필요한 곳으로 알린다
  다 안 되면 지어내지 않는다 — 컷의 시작·끝이면 멈추고 확인 카드에서 지도로 찍어 달라고 알린다(들르는 곳은 앞뒤 사이에 어림으로 둔다).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import build, request
from .cuts import KIND, check_legs, route_length, write_preview
from .geo import ATTRIBUTION, Geo, GeoError, GeoNotFound, meters

PIN_HINT = "확인 카드의 컷 나눔에서 그 칸 옆의 📍 로 지도에서 지점을 찍어 주세요"
AREA_REACH_M = 3000     # 한 장소(공원 등) 안의 이동에서 지명이 장소에서 이만큼 넘게 떨어져 잡히면 같은 이름의 다른 곳으로 본다


def make(spec: dict, *, project_dir: Path, out_root: Path, request_dir: Path | None = None, offline: bool = False, log=print,
         namer=None, searcher=None, picker=None) -> dict:
    """컷마다 작업 폴더(컷1, 컷2 …)를 만든다. 돌려주는 것은 cuts.make 와 같은 모양.

    namer = 적힌 지명들을 지도에서 찾을 이름으로(intake.official_names), searcher = 못 찾은 지명을 웹 검색으로(intake.searched_name),
    picker = 웹 검색 근거로 장소 안의 시설 가운데 고르기(intake.picked_place). 없으면 그 단계를 건너뛴다."""
    t0 = time.perf_counter()
    base, cuts = spec["base"], spec["cuts"]
    geo = Geo(project_dir / "data" / "geo", offline=offline)
    walk = base["vehicle"] == "walk"
    area_name = (base.get("area") or {}).get("name", "")
    warnings: list[str] = []
    key = lambda s: re.sub(r"\s+", "", s or "")      # noqa: E731

    # 지명 묻기(Liner 한 번): 지도에서 찾을 정식 이름과 오타를 고친 표기
    wanted = [n for c in cuts for n in ([c["from"]] * ("from_pin" not in c) + [c["to"]] * ("to_pin" not in c)
                                        + [s["name"] for s in c.get("stops", []) if "lat" not in s])]
    named: dict = {}
    if namer and wanted:
        try:
            try:
                named = namer(wanted, area=area_name) or {}
            except TypeError:       # 장소를 받지 않는 예전 모양
                named = namer(wanted) or {}
        except Exception as e:      # 이름을 못 물어봐도 적힌 이름으로 이어 간다
            warnings.append(f"지명을 지도용 이름으로 바꾸지 못했습니다({type(e).__name__}) — 적힌 이름으로만 찾았습니다.")
    relabel: dict[str, str] = {}

    def mark(written: str, label: str | None, how: str) -> None:
        if label and key(label) != key(written) and key(written) not in relabel:
            relabel[key(written)] = label
            warnings.append(f"'{written}' 은(는) 오타로 보여 화면에는 '{label}'(으)로 적었습니다({how}). 다르면 확인 카드에서 고쳐 주세요.")
    show = lambda name: relabel.get(key(name), name)      # noqa: E731
    for written, info in named.items():
        mark(written, info.get("label"), "Liner 지명 확인")

    # 장소(공원 등)의 자리 — 지명을 그 안에서 찾는다
    anchor = None
    if area_name:
        log(f"장소 찾기: {area_name}")
        down = False        # 지명 서버가 안 됐다(그 알림은 따로 냈다) — 앞의 오타 알림 유무와 상관없이 이것으로 가른다
        for t in dict.fromkeys((area_name, re.sub(r"\s+", "", area_name))):      # '여의도 공원'은 없고 '여의도공원'은 있다
            try:
                anchor = geo.geocode_loose(t)
                break
            except GeoNotFound:
                continue
            except GeoError as e:
                warnings.append(f"장소 '{area_name}' 을(를) 지금 찾아볼 수 없습니다({str(e)[:60]}).")
                down = True
                break
        if anchor is None and not down:
            warnings.append(f"장소 '{area_name}' 을(를) 지도에서 찾지 못해 지명을 그 안으로 좁히지 못했습니다.")
    # 한 장소 안의 이동: 장소에서 이보다 먼 곳으로 잡힌 지명은 같은 이름의 다른 곳이다(전국의 '잔디마당') — 받지 않는다
    reach_m = AREA_REACH_M if area_name else None
    features_ok = [anchor is not None]
    found: dict[str, dict] = {}
    lost: set[str] = set()      # 못 찾은 이름 — 팀(컷)마다 같은 이름을 Liner 검색 · 고르기로 다시 묻지 않는다

    def find(name: str) -> dict:
        """적힌 이름 하나의 지점. 못 찾으면 GeoNotFound(지어내지 않는다)."""
        if key(name) in found:
            return found[key(name)]
        if key(name) in lost:
            raise GeoNotFound(f"'{name}' 을(를) 지도에서 찾지 못했습니다.")
        info = named.get(name) or {}
        tries = list(dict.fromkeys(t for t in (name, info.get("label"), info.get("search_name")) if t))
        hit = None
        if anchor and features_ok[0]:       # ② 그 장소 안의 시설
            for t in tries:
                try:
                    hit = geo.find_feature(t, anchor["lat"], anchor["lon"], area=area_name)
                    if hit["how"] == "kind":
                        warnings.append(f"'{name}' 은(는) 지도에 이름이 없어 {area_name} 안의 같은 종류 시설('{hit['display']}')로 잡았습니다. 지점이 맞는지 확인해 주세요.")
                    break
                except GeoNotFound:
                    continue
                except GeoError as e:       # 시설 목록을 못 받았다 — 이 의뢰에서는 다시 부르지 않고 지명 서버로 간다
                    features_ok[0] = False
                    warnings.append(f"{area_name} 안의 시설 목록을 받지 못해 지명 서버로만 찾았습니다({str(e)[:60]}).")
                    break
        if hit is None:         # ③ 지명 서버(장소 둘레에서)
            near, km = ((anchor["lat"], anchor["lon"]), 3) if anchor else (None, 30)
            for t in tries + ([f"{area_name} {name}"] if area_name else []):
                try:
                    hit = geo.geocode_loose(t, near=near, km=km)
                    if t != name:
                        warnings.append(f"'{name}' 은(는) '{t}'(으)로 찾았습니다. 지점이 맞는지 확인해 주세요.")
                    break
                except GeoNotFound:
                    continue
        if hit is None and searcher:        # ④ Liner 웹 검색으로 정식 이름을 확인
            try:
                got = searcher(name, near=area_name)
                if got and got["search_name"] != name:
                    try:
                        hit = (geo.find_feature(got["search_name"], anchor["lat"], anchor["lon"], area=area_name) if anchor and features_ok[0]
                               else geo.geocode_loose(got["search_name"], near=(anchor["lat"], anchor["lon"]) if anchor else None, km=3 if anchor else 30))
                        src = f" 근거: {', '.join(got['sources'])}" if got.get("sources") else ""
                        warnings.append(f"'{name}' 은(는) Liner 검색으로 확인한 이름 '{got['search_name']}'(으)로 찾았습니다.{src}")
                        mark(name, got.get("label"), "Liner 검색 확인")
                    except GeoNotFound:
                        pass
                if hit is None and got and got.get("address"):      # 정식 이름도 지도에 없다 — 검색 결과의 도로명주소로 지점을 잡는다
                    try:
                        hit = geo.geocode_loose(got["address"], near=(anchor["lat"], anchor["lon"]) if anchor else None, km=3 if anchor else 30)
                        src = f" 근거: {', '.join(got['sources'])}" if got.get("sources") else ""
                        warnings.append(f"'{name}' 은(는) 지도에 그런 이름이 없어, Liner 검색으로 확인한 주소 '{got['address']}'(으)로 지점을 잡았습니다. "
                                        f"지점이 맞는지 확인해 주세요.{src}")
                    except GeoNotFound:
                        pass
            except GeoError:
                raise
            except Exception:       # 검색이 안 된다 — 다음 단계로
                pass
        if hit is None and picker and anchor and features_ok[0]:     # ⑤ 검색 근거로 장소 안의 시설 가운데 고르기
            try:
                cands = geo.candidates(anchor["lat"], anchor["lon"], area=area_name)
                got = picker(name, area_name, cands)
                if got:
                    c = cands[got["index"]]
                    hit = {"name": name, "lat": c["lat"], "lon": c["lon"], "display": c["display"], "candidates": [], "approx": True}
                    warnings.append(f"'{name}' 은(는) 지도에 그 이름이 없어, Liner 가 웹 검색을 근거로 '{c['label']}'(으)로 골랐습니다"
                                    f"({got['why']} · 근거: {', '.join(got['sources'])}). 확인이 필요한 지점입니다 — 다르면 {PIN_HINT}.")
            except GeoError:
                pass
            except Exception:
                pass
        if hit is not None and reach_m and (anchor or found):
            ref = anchor or next(iter(found.values()))
            far = meters([hit["lon"], hit["lat"]], [ref["lon"], ref["lat"]])
            if far > reach_m:
                warnings.append(f"'{name}' 은(는) {'장소 ' + repr(area_name) if anchor else '다른 지점들'}에서 {far / 1000:.1f}km 떨어진 곳('{str(hit.get('display', ''))[:30]}')으로 잡혀 쓰지 않았습니다(같은 이름의 다른 곳).")
                hit = None
        if hit is None:
            lost.add(key(name))
            raise GeoNotFound(f"'{name}' 을(를) 지도에서 찾지 못했습니다.")
        hit = {**hit, "name": name}
        found[key(name)] = hit
        return hit

    # 컷마다의 출발·도착(먼저 다 찾는다 — 못 찾은 곳이 있으면 작업 폴더를 만들기 전에 한 번에 알린다)
    ends, missing = [], []
    for n, c in enumerate(cuts, 1):
        pair = []
        for end, label in (("from", "시작"), ("to", "끝")):
            pin = c.get(f"{end}_pin")
            if pin:
                pair.append({"name": c[end], "lat": pin["lat"], "lon": pin["lon"], "display": "지도에서 찍은 지점", "candidates": []})
                continue
            try:
                pair.append(find(c[end]))
            except GeoNotFound:
                pair.append(None)
                missing.append(f"컷 {n}{' · ' + c['who'] if c.get('who') else ''}의 {label} '{c[end]}'")
            except GeoError as e:
                raise GeoError(f"컷 {n}의 {label} '{c[end]}' 을(를) 지금 찾아볼 수 없습니다: {e}") from None
        ends.append(pair)
    if missing:     # 찾는 도중의 알림(시설 목록을 못 받음 등)도 같이 — 왜 못 찾았는지 보이게
        raise request.RequestError([f"지도에서 찾지 못한 곳: {', '.join(missing)} — 지도(OpenStreetMap)와 웹 검색에 그 지점이 없습니다. {PIN_HINT}.",
                                    *warnings])
    # 컷마다 들르는 곳(경유지) 찾기와 입력 검사(같은 자리 · 너무 먼 지점)도 작업 폴더를 만들기 전에 — 고칠 곳이 있으면 빈 폴더를 남기지 않는다
    routes = []
    for n, (c, (a, b)) in enumerate(zip(cuts, ends), 1):
        # 들르는 곳: 찾은 곳은 경로가 지나게(경유지), 못 찾은 곳은 앞뒤 사이에 어림으로 둔다
        stops, via = [], []
        for st in c.get("stops", []):
            if key(st["name"]) in (key(c["from"]), key(c["to"])):
                continue        # 컷의 시작·끝에 붙은 설명은 이름표에 같이 적는다(아래)
            try:
                p = ({"name": st["name"], "lat": st["lat"], "lon": st["lon"], "display": "지도에서 찍은 지점", "candidates": []} if "lat" in st else find(st["name"]))
                via.append(p)
                stops.append((st, p))
            except GeoNotFound:
                warnings.append(f"컷 {n}: 들르는 곳 '{st['name']}' 을(를) 지도에서 찾지 못해 앞뒤 지점 사이에 어림으로 잡았습니다. 정확한 자리가 필요하면 {PIN_HINT}.")
                stops.append((st, None))
        pts = [a, *via, b]
        if meters([a["lon"], a["lat"]], [b["lon"], b["lat"]]) < 15 and not via:
            raise request.RequestError([f"컷 {n}: 시작 '{c['from']}' 과(와) 끝 '{c['to']}' 이(가) 같은 자리로 잡혔습니다. 지점을 확인해 주세요."])
        check_legs(pts)
        routes.append((stops, via, pts))

    name = re.sub(r"[^0-9A-Za-z가-힣]+", "", area_name or f"{cuts[0]['from']}_{cuts[-1]['to']}") or "job"
    art_style = base.get("art_style", request.DEFAULT_ART_STYLE)
    suffix = "" if art_style == request.DEFAULT_ART_STYLE else f"_{art_style}"
    root = out_root / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}_각자{len(cuts)}컷{suffix}"
    root.mkdir(parents=True, exist_ok=False)
    resolved = {**base, "kind": KIND, "separate": True, "via": [], "cuts": []}
    made: list[dict] = []
    total_m = 0.0

    def save(state: str) -> dict:
        record = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": KIND, "state": state, "separate": True, "request": request.private(resolved),
                  "route": {"distance_m": round(total_m)}, "cuts": [{**m, "dir": m["dir"].name} for m in made], "cuts_wanted": len(cuts),
                  "warnings": warnings, "attribution": ATTRIBUTION, "timing": {"build_seconds": round(time.perf_counter() - t0, 1)}}
        (root / "job.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    save("making")
    show_credit = 0.25 * sum(c["duration_seconds"] for c in cuts)
    began = 0.0
    for n, (c, (a, b), (stops, via, pts)) in enumerate(zip(cuts, ends, routes), 1):
        boat = base["vehicle"] == "boat"
        log(f"컷 {n}/{len(cuts)}: {c.get('who', '')} {c['from']} → {c['to']} 경로 받기({'걸어서' if walk else '뱃길' if boat else '도로'})")
        osrm = geo.water_route_via(pts) if boat else geo.route_via(pts, foot=walk) if via else geo.route(a, b, foot=walk)
        coords = osrm["routes"][0]["geometry"]["coordinates"]
        dist = route_length(coords)
        total_m += dist
        # 경유지의 자리(경로의 처음부터 잰 비율): 경로 서버가 알려 준 구간 길이로
        legs = [float(leg.get("distance", 0)) for leg in osrm["routes"][0].get("legs", [])]
        k, run, at_via = dist / (sum(legs) or 1), 0.0, []
        for d in legs[:-1]:
            run += d
            at_via.append(run * k / (dist or 1))
        marks, vi = [], 0
        for st, p in stops:
            if p is not None:
                marks.append([st, at_via[vi] if vi < len(at_via) else None, False])
                vi += 1
            else:
                marks.append([st, None, True])
        j = 0
        while j < len(marks):       # 어림: 자리가 없는 것들은 앞뒤 자리 사이를 똑같이 나눈다
            if marks[j][1] is not None:
                j += 1
                continue
            e = j
            while e < len(marks) and marks[e][1] is None:
                e += 1
            lo = marks[j - 1][1] if j else 0.0
            hi = marks[e][1] if e < len(marks) else 1.0
            for i in range(j, e):
                marks[i][1] = lo + (hi - lo) * (i - j + 1) / (e - j + 1)
            j = e
        extra = {"flow": {"in": False, "out": False, "card": n > 1}, "cut": {"n": n, "of": len(cuts)},
                 "credit_window": [0, max(0.0, show_credit - began)]}
        began += c["duration_seconds"]
        if marks:
            extra["stops"] = [{"name": st.get("label") or show(st["name"]), "caption": st["caption"], "pass": st["pass"], "at": max(0.0, min(1.0, at)),
                               **({"approx": True} if rough else {})} for st, at, rough in marks]
        for st in c.get("stops", []):       # 컷의 시작·끝에 붙은 설명은 이름표 안에 같이('발견', '발견 못함')
            if st["caption"] and key(st["name"]) == key(c["from"]):
                extra["from_caption"] = st["caption"]
            elif st["caption"] and key(st["name"]) == key(c["to"]):
                extra["to_caption"] = st["caption"]
        req = {**base, "from": {"name": show(c["from"]), "lat": a["lat"], "lon": a["lon"]},
               "to": {"name": show(c["to"]), "lat": b["lat"], "lon": b["lon"]}, "duration_seconds": c["duration_seconds"]}
        if c.get("who"):
            req["who"] = c["who"]
        if "vehicle_scale" in c:
            req["vehicle_scale"] = c["vehicle_scale"]
        job = build.make_job(req, project_dir=project_dir, out_root=out_root, request_dir=request_dir, offline=offline,
                             log=lambda s: log(f"  {s}"), osrm=osrm, job_dir=root / f"컷{n}", extra=extra, geo=geo)
        warnings.extend(f"컷 {n}: {w}" for w in job["warnings"])
        made.append({"dir": job["dir"], "duration_seconds": c["duration_seconds"], "from_m": 0, "to_m": round(dist), "distance_m": round(dist),
                     "from": show(c["from"]), "to": show(c["to"]), "who": c.get("who", ""), "map": job["map"],
                     "approx": [st["name"] for st, _, rough in marks if rough] + [p["name"] for p in (a, b, *via) if p.get("approx")]})
        # 다시 만들 봉투: 찾은 지점을 좌표로 적는다(다시 낼 때 지명 묻기·검색을 하지 않는다)
        cut_out = {**c, "from_pin": {"lat": a["lat"], "lon": a["lon"]}, "to_pin": {"lat": b["lat"], "lon": b["lon"]}}
        if c.get("stops"):
            placed = {id(st): p for st, p in stops}
            cut_out["stops"] = [{**st, **({"lat": placed[id(st)]["lat"], "lon": placed[id(st)]["lon"]} if placed.get(id(st)) else {})} for st in c["stops"]]
        resolved["cuts"].append(cut_out)
        save("making")
    resolved["from"] = {"name": show(cuts[0]["from"]), "lat": ends[0][0]["lat"], "lon": ends[0][0]["lon"]}
    resolved["to"] = {"name": show(cuts[-1]["to"]), "lat": ends[-1][1]["lat"], "lon": ends[-1][1]["lon"]}
    record = save("done")
    write_preview(root, made)
    return {"dir": root, **record, "cuts": made, "places": {"from": ends[0][0], "to": ends[-1][1]}}
