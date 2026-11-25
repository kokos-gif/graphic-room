"""컷 나눔 지도 — 한 경로를 여러 컷으로 잘라, 컷마다 그 구간에 맞춘 지도를 그리고 한 영상으로 잇는다.

의뢰서의 콘티가 "출발 → (컷 전환) 다리 → (컷 전환) 분기점 → (컷 전환) 달리는 모습"처럼 구간마다 화면을 바꿀 때 쓴다.
경로는 한 번만 받는다(경유지를 지나게). 컷은 그 경로의 '몇 m 부터 몇 m 까지'로 자른다 — 컷마다 경로를 따로 받으면
구간이 서로 어긋나거나 다른 길로 돌아갈 수 있다.

의뢰 봉투(지도 의뢰와 같은 항목에 아래 둘을 더한다):
  "via":  [{"name", "lat", "lon", "bearing"(선택: 그 지점을 지나는 방향, 북 0·동 90)}]      출발과 도착 사이에 지나갈 곳
  "cuts": [{"from", "to"                    화면에 적을 구간 이름(출발·도착 이름표)
            "from_m", "to_m"                경로의 처음부터 잰 거리(m). 안 주면 경로를 컷 수로 똑같이 나눈다
            "duration_seconds"              이 컷의 길이
            "vehicle_scale"(선택)           이동 아이콘을 키운다(가까이서 보는 컷)
            "bridge"(선택) {"look": "cable", "from_m", "to_m", "pylon_m"}   그 구간을 사장교(주탑·케이블)로 그린다}]
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import build, export, request
from .geo import _TAIL, ATTRIBUTION, Geo, GeoError, GeoNotFound, meters as _meters, together      # noqa: F401 — together 는 예전 이름(cuts.together)으로도 쓰인다

KIND = "cuts"          # 작업 기록과 메신저의 결과에 남기는 종류 이름
MAX_CUTS = 8
BRIDGE_HALF_M = 700      # 다리의 '북단·남단'을 다리 지점에서 이만큼 앞뒤로 본다(다리 길이를 모를 때의 어림)
MAX_ROUTE_M = 150_000   # 이보다 긴 경로는 지명이 잘못 잡힌 것으로 본다(컷 나눔은 한 지역 안의 이동을 그린다)
DETOUR_M = 30_000       # 경유지 하나 때문에 돌아가는 거리가 이보다 크면(그리고 출발~도착 직선거리의 1.5배보다 크면) 잘못 잡힌 것으로 본다
BRIDGE_LOOKS = ("cable",)
OFF_ROUTE_M = 300       # 경유지로 넣지 않은 핀이 경로에서 이보다 멀면 그 사실을 알린다(경로 위의 가장 가까운 자리에 표시한다)


def _num(v, label: str, problems: list[str], lo: float, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        problems.append(f"{label}은(는) 숫자여야 합니다.")
        return None
    if not lo <= f <= hi:
        problems.append(f"{label}이(가) 범위({lo:g}~{hi:g})를 벗어났습니다.")
        return None
    return f


def normalize(raw: dict) -> dict:
    """컷 나눔 의뢰를 검사한다. 돌려주는 것: {'base' 공통 의뢰(지도 의뢰와 같은 모양), 'via', 'cuts'}. 고칠 곳은 모아서 RequestError."""
    if not isinstance(raw, dict):
        raise request.RequestError(["의뢰 봉투는 JSON 객체여야 합니다."])
    problems: list[str] = []
    cuts_raw = raw.get("cuts")
    if not isinstance(cuts_raw, list) or not 2 <= len(cuts_raw) <= MAX_CUTS or not all(isinstance(c, dict) for c in cuts_raw):
        raise request.RequestError([f"컷(cuts)은 2~{MAX_CUTS}개의 목록이어야 합니다."])
    total = 0.0
    cuts: list[dict] = []
    for n, c in enumerate(cuts_raw, 1):
        cut = {"from": str(c.get("from") or "").strip(), "to": str(c.get("to") or "").strip()}
        if not cut["from"] or not cut["to"]:
            problems.append(f"컷 {n}: 구간 이름(from · to)이 없습니다.")
        d = _num(c.get("duration_seconds"), f"컷 {n}의 길이(duration_seconds)", problems, 1, 60)
        cut["duration_seconds"] = d or 0.0
        total += d or 0.0
        if ("from_m" in c) != ("to_m" in c):
            problems.append(f"컷 {n}: from_m 과 to_m 은 둘 다 주거나 둘 다 비워야 합니다.")
        elif "from_m" in c:
            a, b = _num(c["from_m"], f"컷 {n}의 from_m", problems, 0, 1e6), _num(c["to_m"], f"컷 {n}의 to_m", problems, 0, 1e6)
            if a is not None and b is not None:
                if b - a < 50:
                    problems.append(f"컷 {n}: 구간이 너무 짧습니다(50m 이상이어야 합니다).")
                cut.update(from_m=a, to_m=b)
        if c.get("vehicle_scale") is not None:
            cut["vehicle_scale"] = _num(c["vehicle_scale"], f"컷 {n}의 아이콘 크기(vehicle_scale)", problems, 0.5, 3) or 1
        if str(c.get("who") or "").strip():        # 이 컷에서 움직이는 사람·팀(각자 이동)
            cut["who"] = str(c["who"]).strip()[:12]
        for end in ("from", "to"):      # 컷의 시작·끝 지점을 지도에서 직접 찍은 좌표(각자 이동: 지도에 없는 곳)
            pin = c.get(f"{end}_pin")
            if isinstance(pin, dict) and pin.get("lat") is not None:
                lat, lon = (_num(pin.get(k), f"컷 {n}의 {'시작' if end == 'from' else '끝'} 지점 {k}", problems, lo, hi) for k, lo, hi in (("lat", -90, 90), ("lon", -180, 180)))
                if lat is not None and lon is not None:
                    cut[f"{end}_pin"] = {"lat": lat, "lon": lon}
        br = {"look": "cable"} if c.get("bridge") is True else c.get("bridge")
        if br not in (None, False):
            if not isinstance(br, dict) or br.get("look") not in BRIDGE_LOOKS:
                problems.append(f"컷 {n}: 다리 모양(bridge.look)은 {', '.join(BRIDGE_LOOKS)} 중에서 골라 주세요.")
            else:
                cut["bridge"] = {"look": br["look"], **{k: _num(br[k], f"컷 {n}의 bridge.{k}", problems, 0, 1e6) for k in ("from_m", "to_m", "pylon_m") if k in br}}
        stops = []
        for s in (c.get("stops") if isinstance(c.get("stops"), list) else []):
            if not isinstance(s, dict) or not str(s.get("name") or "").strip():
                continue
            st = {"name": str(s["name"]).strip(), "caption": str(s.get("caption") or "").strip()[:30], "pass": s.get("pass") is True}
            if str(s.get("label") or "").strip():      # 화면 이름표(상호를 가릴 때 등). 지도에서는 name 으로 찾는다
                st["label"] = str(s["label"]).strip()[:30]
            if s.get("lat") is not None or s.get("lon") is not None:      # 지도에서 못 찾는 곳은 좌표를 직접 줄 수 있다(어림으로 두지 않게)
                lat, lon = _num(s.get("lat"), f"컷 {n}: '{st['name']}' 의 lat", problems, -90, 90), _num(s.get("lon"), f"컷 {n}: '{st['name']}' 의 lon", problems, -180, 180)
                if lat is not None and lon is not None:
                    st.update(lat=lat, lon=lon)
            if s.get("at_m") is not None:       # 앞서 만든 경로 위의 자리(결과 카드의 '다시 만들기'가 돌려준다)
                at = _num(s["at_m"], f"컷 {n}: '{st['name']}' 의 at_m", problems, 0, 1e6)
                if at is not None:
                    st["at_m"] = at
            if s.get("approx") is True:         # 그때 어림으로 놓은 곳이었다
                st["approx"] = True
            stops.append(st)
        if stops[:6]:
            cut["stops"] = stops[:6]
        cuts.append(cut)
    if total > 60:
        problems.append(f"컷 길이의 합({total:g}초)이 60초를 넘습니다.")
    via: list[dict] = []
    for n, v in enumerate(raw.get("via") or [], 1):
        lat, lon = _num((v or {}).get("lat"), f"경유지 {n}의 lat", problems, -90, 90), _num((v or {}).get("lon"), f"경유지 {n}의 lon", problems, -180, 180)
        p = {"name": str((v or {}).get("name") or f"경유지 {n}"), "lat": lat or 0.0, "lon": lon or 0.0}
        if (v or {}).get("bearing") is not None:
            p["bearing"] = _num(v["bearing"], f"경유지 {n}의 bearing", problems, 0, 360) or 0.0
        via.append(p)
    # 공통 항목(프로그램·출발·도착·화풍·규격·얼굴…)은 지도 의뢰와 같은 검사를 거친다. 길이는 컷 길이의 합이다
    try:
        # 한 화면 지도의 들르는 곳(stops)은 컷 나눔에서 쓰지 않는다 — 컷마다 들르는 곳(cuts[].stops)이 따로 있다
        base = request.normalize({**{k: v for k, v in raw.items() if k not in ("cuts", "via", "stops")}, "duration_seconds": min(60, max(1, total or 1))})
    except request.RequestError as e:
        problems = [*e.problems, *problems]
        base = {}
    if problems:
        raise request.RequestError(problems)
    spec = {"base": base, "via": via, "cuts": cuts}
    if raw.get("separate") is True:     # 각자 이동: 컷마다 다른 사람이 따로 움직인다 — 컷마다 경로를 따로 받는다(moves.py)
        spec["separate"] = True
    if isinstance(raw.get("to"), dict) and raw["to"].get("bearing") is not None:      # 도착 지점을 지나는 방향(반대 차로에 붙지 않게)
        try:
            spec["to_bearing"] = float(raw["to"]["bearing"]) % 360
        except (TypeError, ValueError):
            raise request.RequestError(["도착지의 bearing 은 숫자여야 합니다."]) from None
    return spec


def slice_route(coords: list, start_m: float, end_m: float) -> list:
    """경로([경도, 위도] 줄)에서 처음부터 start_m ~ end_m 인 구간만 잘라 낸다(양 끝은 선 위의 점을 새로 찍는다)."""
    cum = [0.0]
    for i in range(1, len(coords)):
        cum.append(cum[-1] + _meters(coords[i - 1], coords[i]))
    start_m, end_m = max(0.0, start_m), min(cum[-1], end_m)

    def point(s: float) -> list:
        i = next((k for k in range(1, len(cum)) if cum[k] >= s), len(cum) - 1)
        f = (s - cum[i - 1]) / ((cum[i] - cum[i - 1]) or 1)
        return [coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * f, coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * f]

    inner = [c for c, s in zip(coords, cum) if start_m < s < end_m]
    return [point(start_m), *inner, point(end_m)]


def route_length(coords: list) -> float:
    return sum(_meters(coords[i - 1], coords[i]) for i in range(1, len(coords)))


def check_legs(points: list[dict]) -> None:
    """경로를 묻기 전에 지점들의 자리를 본다 — 걸리면 RequestError 로 멈춘다(경로 서버·지도 서버를 부르지 않는다).

    ① 경유지 하나가 출발·도착에서 동떨어져 있다(서울 → 부산의 '월드컵대로' → 서울). ② 지점들을 직선으로 이은 길이가 이미 한도를 넘는다.
    출발과 도착이 같은 지역이어도 가운데 지점 하나가 잘못 잡히면 경로가 나라를 돌아온다 — 끝점끼리의 거리만 봐서는 못 잡는다.
    """
    xy = lambda p: [p["lon"], p["lat"]]      # noqa: E731
    direct = _meters(xy(points[0]), xy(points[-1]))
    for v in points[1:-1]:
        detour = _meters(xy(points[0]), xy(v)) + _meters(xy(v), xy(points[-1])) - direct
        if detour > max(DETOUR_M, direct * 1.5):
            raise request.RequestError([f"경유지 '{v.get('name', '')}' 이(가) 출발·도착에서 동떨어진 곳으로 잡혔습니다(이곳을 들르면 약 {detour / 1000:.0f}km 를 돌아갑니다). "
                                        "다른 지역의 같은 이름인 것 같습니다 — 이름을 더 구체적으로 적거나 그 지점을 빼 주세요."])
    legs = sum(_meters(xy(p), xy(q)) for p, q in zip(points, points[1:]))
    if legs > MAX_ROUTE_M:
        raise request.RequestError([f"지점들을 곧게 이어도 {legs / 1000:.0f}km 입니다 — 출발지나 도착지가 다른 지역의 같은 이름으로 잡힌 것 같습니다. "
                                    f"이름을 더 구체적으로 적거나 지도에서 지점을 직접 지정해 주세요(출발: {points[0].get('display', '')[:40]} / 도착: {points[-1].get('display', '')[:40]})."])


def spec_request(spec: dict) -> dict:
    """검사를 마친 컷 나눔 의뢰를 다시 낼 수 있는 의뢰 봉투로(작업 기록·결과 카드에 남긴다).

    '다른 화풍으로 다시 만들기'가 이것을 그대로 다시 낸다 — 경유지와 도착 방향이 빠지면 다른 길로 가는 지도가 나온다.
    """
    base = spec["base"]
    to = {**base["to"], "bearing": spec["to_bearing"]} if "to_bearing" in spec else base["to"]
    return {**base, "to": to, "kind": KIND, "via": spec["via"], "cuts": spec["cuts"], **({"separate": True} if spec.get("separate") else {})}


def locate(coords: list, pt: list) -> tuple[float, float]:
    """경로([경도, 위도] 줄) 위에서 pt([경도, 위도])에 가장 가까운 자리: (경로의 처음부터 잰 m, 경로에서 떨어진 거리 m)."""
    kx, ky = 111320 * math.cos(math.radians(pt[1])), 110540        # 이 둘레에서 경도·위도 1도의 m(짧은 거리라 평면으로 본다)
    best, run = (0.0, float("inf")), 0.0
    for p, q in zip(coords, coords[1:]):
        ax, ay = (p[0] - pt[0]) * kx, (p[1] - pt[1]) * ky
        dx, dy = (q[0] - p[0]) * kx, (q[1] - p[1]) * ky
        span = dx * dx + dy * dy
        f = max(0.0, min(1.0, -(ax * dx + ay * dy) / span)) if span else 0.0
        off, seg = math.hypot(ax + dx * f, ay + dy * f), _meters(p, q)
        if off < best[1]:
            best = (run + seg * f, off)
        run += seg
    return best


def fixed_request(spec: dict, a: dict, b: dict, via: list[dict], spans: list, spots: dict, coords: list) -> dict:
    """이번 제작에서 확정한 자리로 다시 낼 수 있는 의뢰 봉투(작업 기록·결과 카드에 남긴다).

    출발·도착은 좌표로, 경유지는 지명으로 찾아 넣은 것까지 다, 컷은 구간(from_m · to_m)으로, 들르는 곳은 경로 위의 자리(at_m)와 좌표로 적는다.
    이것을 다시 내면 지명 묻기·검색·지명 찾기를 하지 않고, 저장해 둔 같은 경로 응답으로 같은 경계와 핀을 그린다(spec_request 는 받은 그대로의 봉투).
    """
    base = spec["base"]
    point = lambda p, name: {"name": name, "lat": p["lat"], "lon": p["lon"], **({"display": p["display"]} if p.get("display") else {})}      # noqa: E731
    to = point(b, base["to"]["name"])
    if "to_bearing" in spec:
        to["bearing"] = spec["to_bearing"]
    where = {id(st): (m, rough) for found in spots.values() for st, m, rough in found}
    out = []
    for cut, (s0, s1) in zip(spec["cuts"], spans):
        c = {**cut, "from_m": s0, "to_m": s1}
        if cut.get("stops"):
            stops = []
            for st in cut["stops"]:
                if id(st) in where:
                    m, rough = where[id(st)]
                    lon, lat = slice_route(coords, m, m)[0]
                    st = {**{k: v for k, v in st.items() if k != "approx"}, "lat": lat, "lon": lon, "at_m": m, **({"approx": True} if rough else {})}
                stops.append(dict(st))      # 원래 의뢰의 객체를 봉투와 나눠 쓰지 않는다
            c["stops"] = stops
        out.append(c)
    return {**base, "from": point(a, base["from"]["name"]), "to": to, "kind": KIND,
            "via": [{k: v[k] for k in ("name", "lat", "lon", "bearing") if k in v} for v in via], "cuts": out}


def frame_plan(durations: list[float], fps: float) -> list[int]:
    """컷마다의 프레임 수. 컷의 경계는 영상 전체의 누적 시간으로 잡는다.

    컷마다 따로 반올림해 더하면 전체 장수와 어긋난다(2.5초 × 2컷 · 25fps 는 125장인데 63 + 63 = 126장이 된다).
    """
    marks, run = [0], 0.0
    for d in durations:
        run += d
        marks.append(export.frame_count(run, fps))
    return [b - a for a, b in zip(marks, marks[1:])]


def make(spec: dict, *, project_dir: Path, out_root: Path, request_dir: Path | None = None, offline: bool = False, log=print,
         namer=None, searcher=None, picker=None) -> dict:
    """컷마다 작업 폴더(컷1, 컷2 …)를 만든다. 돌려주는 것: {'dir' 묶음 폴더, 'cuts' [{dir, duration_seconds, job}], 'route_m', 'warnings'}.

    namer = 적힌 지명들을 지도에서 찾을 이름으로 바꿔 주는 함수(intake.official_names — Liner 모델). 없으면 적힌 이름으로만 찾는다.
    searcher = 못 찾은 지명 하나를 웹 검색으로 확인해 주는 함수(intake.searched_name — Liner 검색 + 모델). 있으면 이쪽을 먼저 쓴다.
    """
    if spec.get("separate"):        # 각자 이동 — 컷마다 경로를 따로 받는다
        from . import moves
        return moves.make(spec, project_dir=project_dir, out_root=out_root, request_dir=request_dir, offline=offline, log=log,
                          namer=namer, searcher=searcher, picker=picker)
    t0 = time.perf_counter()
    base, cuts = spec["base"], spec["cuts"]
    geo = Geo(project_dir / "data" / "geo", offline=offline)
    log(f"지명 찾기: {base['from']['name']} → {base['to']['name']}")
    via, warnings = list(spec["via"]), []
    named: dict = {}        # 적힌 지명 → {'search_name', 'kind' place·road·direction·generic}
    by_names = not via and not any("from_m" in c for c in cuts)
    if namer and by_names:
        wanted = [base["from"]["name"], base["to"]["name"], *[c["to"] for c in cuts[:-1]], *[s["name"] for c in cuts for s in c.get("stops", [])]]
        try:
            named = namer(wanted) or {}
        except Exception as e:      # 이름을 못 물어봐도 적힌 이름으로 이어 간다
            warnings.append(f"지명을 지도용 이름으로 바꾸지 못했습니다({type(e).__name__}) — 적힌 이름으로만 찾았습니다.")
    kind = lambda name: (named.get(name) or {}).get("kind") or "place"      # noqa: E731
    same = lambda x, y: re.sub(r"\s+", "", x) == re.sub(r"\s+", "", y)      # noqa: E731
    # 화면 이름표: 의뢰서에 적힌 그대로가 원칙이다. 다만 Liner 가 오타로 본 이름(지명 묻기·검색의 label)은 고친 표기로 적는다
    relabel: dict[str, str] = {}
    key = lambda name: re.sub(r"\s+", "", name or "")      # noqa: E731

    def mark(written: str, label: str | None, how: str) -> None:
        if label and key(label) != key(written) and key(written) not in relabel:
            relabel[key(written)] = label
            warnings.append(f"'{written}' 은(는) 오타로 보여 화면에는 '{label}'(으)로 적었습니다({how}). 다르면 확인 카드에서 고쳐 주세요.")
    show = lambda name: relabel.get(key(name), name)      # noqa: E731
    for written, info in named.items():
        mark(written, info.get("label"), "Liner 지명 확인")

    def find(name: str, near=None, km: float = 30) -> dict:
        """적힌 이름으로 찾고, 없으면 Liner 검색·Liner 가 알려 준 정식 이름으로 다시 찾는다(화면에는 적힌 이름을 그대로 쓴다).
        near=(위도, 경도) 를 주면 그 둘레 km 안에서만 찾는다."""
        try:
            return geo.geocode_loose(name, near=near, km=km)
        except GeoNotFound:     # 지명 서버가 '그런 이름 없음'이라고 답했을 때만 다른 이름을 알아본다(서버가 안 되는 것은 그대로 올린다)
            if searcher:        # 웹 검색 결과에 비춰 정식 이름을 확인한다(근거 주소를 알림에 남긴다)
                try:
                    hit = searcher(name, near=" ".join(n for n in (base["from"]["name"], base["to"]["name"]) if n != name))
                    if hit and hit["search_name"] != name:
                        place = geo.geocode_loose(hit["search_name"], near=near, km=km)
                        src = f" 근거: {', '.join(hit['sources'])}" if hit.get("sources") else ""
                        warnings.append(f"'{name}' 은(는) 지도에 그런 이름이 없어, Liner 검색으로 확인한 정식 이름 '{hit['search_name']}'(으)로 찾았습니다.{src}")
                        mark(name, hit.get("label"), "Liner 검색 확인")
                        return {**place, "name": name}
                except GeoNotFound:     # 그 이름도 지도에 없다 — 아래 길로 간다
                    pass
                except GeoError:        # 지도 서버가 안 된다 — 이름을 바꿔 가며 다시 묻지 않는다
                    raise
                except Exception:       # 검색이 안 된다 — 아래 길로 간다
                    pass
            alt = (named.get(name) or {}).get("search_name")
            if not alt or alt == name:
                raise
            hit = geo.geocode_loose(alt, near=near, km=km)
            warnings.append(f"'{name}' 은(는) 지도에 그런 이름이 없어, Liner 가 알려 준 정식 이름 '{alt}'(으)로 찾았습니다. 지점이 맞는지 확인해 주세요.")
            return {**hit, "name": name}

    # 출발지로 지역을 잡는다. 도착지는 먼저 그 둘레에서 찾고(같은 이름이 다른 지역에 있어도 가까운 쪽), 없을 때만 넓게 찾는다
    # 의뢰에 '장소'가 적혀 있으면 출발지를 먼저 그 둘레에서 찾는다('시청'처럼 어디에나 있는 이름이 다른 도시로 잡히지 않게)
    anchor, area_note = build.area_anchor(geo, base)
    if area_note:
        warnings.append(area_note)

    def first(name: str) -> dict:
        if anchor:
            try:
                return find(name, near=anchor, km=build.AREA_KM)
            except GeoNotFound:
                pass
        return find(name)

    a = base["from"] if "lat" in base["from"] else first(base["from"]["name"])
    if "lat" in base["to"]:
        b = base["to"]
    else:
        try:
            b = find(base["to"]["name"], near=(a["lat"], a["lon"]), km=40)
        except GeoNotFound:
            b = find(base["to"]["name"])
    a, b = together([{**p, "display": p.get("display", "직접 지정한 좌표"), "candidates": p.get("candidates", [])} for p in (a, b)])
    warnings += [f"'{p['name']}' 은(는) 지도에 그런 이름이 없어 '{p['searched']}'(으)로 찾았습니다. 지점이 맞는지 확인해 주세요." for p in (a, b) if p.get("searched")]
    warnings += [build.outside_note(p) for p in (a, b) if p.get("outside")]
    span = _meters([a["lon"], a["lat"]], [b["lon"], b["lat"]])
    center, reach = ((a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2), max(8.0, span / 1000 * 0.75 + 5)      # 이 의뢰의 지역: 출발·도착의 가운데와 그 둘레(km)

    # 경로가 지나는 차례대로: 컷 안에서 들르는 곳들, 그리고(지명으로 잡는 경로면) 컷이 끝나는 곳. place 가 없으면 앞뒤 지점 사이에 어림으로 둔다.
    # 경유지·거리 경계를 준 의뢰도 들르는 곳(핀)은 모은다 — 경로는 그대로 두고, 핀을 경로 위의 가장 가까운 자리에 놓는다
    seq: list[dict] = []
    for i, c in enumerate(cuts):
        for st in c.get("stops", []):
            if same(st["name"], c["from"]) or same(st["name"], c["to"]):
                continue        # 컷의 시작·끝에 붙은 설명은 이름표에 같이 적는다(아래)
            seq.append({"cut": i, "st": st, "name": st["name"], "what": f"컷 {i + 1}: 들르는 곳 '{st['name']}'",
                        **({"place": {"name": st["name"], "lat": st["lat"], "lon": st["lon"], "display": "직접 지정한 좌표", "candidates": []}} if "lat" in st else {}),
                        **({"at_m": st["at_m"]} if "at_m" in st else {})})
        if by_names and i < len(cuts) - 1:
            seq.append({"cut": i, "st": None, "name": c["to"], "what": f"컷 {i + 1}이 끝나는 곳 '{c['to']}'"})
    if seq:
        for it in seq:
            if "at_m" in it:        # 앞서 만든 자리를 그대로 쓴다(결과 카드의 '다시 만들기') — 다시 찾지 않는다
                continue
            k = kind(it["name"])
            if it.get("place"):     # 좌표를 직접 준 곳
                it["tail"] = None
            elif k == "generic":
                warnings.append(f"{it['what']} 은(는) 어느 곳인지 알 수 없는 이름이라 지도에서 찾지 않고, 앞뒤 지점 사이에 어림으로 잡았습니다. 정확한 상호나 주소를 적어 주세요.")
            elif k in ("road", "direction"):
                warnings.append(f"{it['what']} 은(는) {'길 이름' if k == 'road' else '방향을 가리키는 말'}이라 한 점으로 찾지 않고, 앞뒤 지점 사이에 어림으로 잡았습니다.")
            else:
                try:
                    it["place"] = find(it["name"], near=center, km=reach)
                    it["tail"] = (re.search(r"(북단|남단|동단|서단)$", it["name"].strip()) or [None, None])[1] if it["place"].get("searched") else None
                except GeoNotFound:
                    warnings.append(f"{it['what']} 을(를) 이 지역의 지도에서 찾지 못해 앞뒤 지점 사이에 어림으로 잡았습니다. 더 구체적인 이름을 적어 주세요.")
                except GeoError as err:      # 지명 서버가 안 되거나(오프라인이면 저장된 것이 없거나) — 못 찾은 것과 다르다
                    warnings.append(f"{it['what']} 을(를) 지금 찾아볼 수 없어 앞뒤 지점 사이에 어림으로 잡았습니다({str(err)[:70]}). 잠시 뒤 다시 만들어 주세요.")
        placed = [it for it in seq if it.get("place") and "at_m" not in it]
        chosen = together([a, *[it["place"] for it in placed], b])[1:-1]
        for it, place in zip(placed, chosen):
            it["place"] = place
        # 지명으로 잡는 경로면 찾은 곳을 경유지로 넣는다. 바로 앞 지점과 같은 곳이 잇달아 나올 때만 경로에 한 번만 넣는다 —
        # 다른 지점을 거쳐 다시 들르는 곳(가 → 나 → 다 → 나 → 라)은 차례대로 다시 넣는다(앞의 자리로 되감지 않는다)
        ends: dict[int, set] = {}       # 경유지 번호 → 그 지점을 가리킨 다리 끝(북단·남단…)
        at_b = lambda p: _meters([p["lon"], p["lat"]], [b["lon"], b["lat"]]) < 60      # noqa: E731
        for idx, it in enumerate(placed if by_names else []):
            pt = [it["place"]["lon"], it["place"]["lat"]]
            near = lambda n: _meters(pt, [via[n]["lon"], via[n]["lat"]]) < 60      # noqa: E731
            # 출발지·도착지와 같은 곳은 경유지로 넣지 않는다 — 넣으면 길이 0 인 구간이 생겨 컷의 경계가 무너진다
            # ('서부운전면허'를 찾으니 도착지 '서부운전면허시험장'과 같은 좌표). 자리는 아래에서 경로 위의 가장 가까운 곳(처음·끝)으로 잡힌다.
            # 다른 곳을 거쳐 다시 돌아오는 출발지·도착지는 그대로 경유지로 넣는다(가 → 나 → 가 → 다)
            if (not via and _meters(pt, [a["lon"], a["lat"]]) < 60) or (at_b(it["place"]) and all(at_b(x["place"]) for x in placed[idx + 1:])):
                continue
            hit = len(via) - 1 if via and near(len(via) - 1) else None
            if hit is None and it.get("tail"):      # 한 다리의 반대쪽 끝은 사이에 들르는 곳이 있어도 같은 다리 지점이다(다리를 두 번 건너지 않는다)
                hit = next((n for n, tails in ends.items() if near(n) and it["tail"] not in tails), None)
            if hit is None:
                via.append({k: it["place"][k] for k in ("name", "lat", "lon")})
                hit = len(via) - 1
            if it.get("tail"):
                ends.setdefault(hit, set()).add(it["tail"])
            it["wp"] = hit
        loose = [f"'{it['name']}' → '{it['place']['searched']}'" for it in placed if it["place"].get("searched")]
        if loose:
            warnings.append(f"지도에 그런 이름이 없어 꼬리말을 떼고 찾은 곳이 있습니다({', '.join(loose)}). 지점이 맞는지 확인해 주세요.")
    boat, walk = spec["base"].get("vehicle") == "boat", spec["base"].get("vehicle") == "walk"
    log(f"경로 받기({'강 위 뱃길' if boat else '걸어서 가는 길' if walk else '실제 도로'}, 경유지 {len(via)}곳)")
    if "to_bearing" in spec:
        b = {**b, "bearing": spec["to_bearing"]}
    check_legs([a, *via, b])        # 잘못 잡힌 지점으로는 경로 서버·지도 서버를 한 번도 부르지 않는다
    # 걸어서 가는 의뢰는 한 화면 지도(build.make_job)와 같은 기준으로 보행 경로를 받는다 — 차가 다니는 길로 돌아가지 않게
    osrm = geo.water_route_via([a, *via, b]) if boat else geo.route_via([a, *via, b], foot=True) if walk else geo.route_via([a, *via, b])
    coords = osrm["routes"][0]["geometry"]["coordinates"]
    total = route_length(coords)
    if total > MAX_ROUTE_M:        # 지도를 받기 전에 멈춘다 — 이대로 가면 나라 절반의 지도를 받게 된다
        raise request.RequestError([f"경로가 {total / 1000:.0f}km 로 나왔습니다 — 출발지나 도착지가 다른 지역의 같은 이름으로 잡힌 것 같습니다. "
                                    f"이름을 더 구체적으로 적거나 지도에서 지점을 직접 지정해 주세요(출발: {a.get('display', '')[:40]} / 도착: {b.get('display', '')[:40]})."])
    bounds = None       # 구간 이름으로 잡은 컷의 경계 [(시작 m, 끝 m)]
    spots: dict[int, list] = {}     # 컷 번호 → [(들르는 곳, 경로의 처음부터 잰 m, 어림인가)]

    def cut_range(i: int) -> tuple[float, float]:
        """컷 i(0부터)의 구간 [시작 m, 끝 m] — 거리 경계를 준 컷은 그 값, 아니면 경로를 컷 수로 똑같이 나눈 자리."""
        c = cuts[i]
        return c.get("from_m", total * i / len(cuts)), min(total, c.get("to_m", total * (i + 1) / len(cuts)))

    if seq:
        legs = [float(leg.get("distance", 0)) for leg in osrm["routes"][0].get("legs", [])]
        k, at_via, run = total / (sum(legs) or 1), [], 0.0
        for d in legs[:-1]:
            run += d
            at_via.append(run * k)
        for it in seq:      # 경유지로 넣지 않은 핀(경유지·거리 경계를 준 의뢰): 경로 위의 가장 가까운 자리
            if it.get("place") and "at_m" not in it and it.get("wp") is None:
                it["pos"], off = locate(coords, [it["place"]["lon"], it["place"]["lat"]])
                if off > OFF_ROUTE_M:
                    warnings.append(f"{it['what']} 은(는) 경로에서 약 {off:.0f}m 떨어져 있어 경로 위의 가장 가까운 자리에 표시했습니다. "
                                    "경로가 그곳을 지나야 하면 경유지에 넣어 주세요.")
        for it in seq:      # 앞서 만든 자리(다시 만들기) — 그대로 쓴다. 그때 어림이었으면 지금도 어림으로 표시한다
            if "at_m" in it:
                it["pos"] = max(0.0, min(total, float(it["at_m"])))
                if it["st"].get("approx"):
                    it["approx"] = True
        for it in seq:      # 찾은 곳의 자리(경로의 처음부터 잰 m)
            if it.get("wp") is not None and it["wp"] < len(at_via):
                pos = at_via[it["wp"]]
                if it.get("tail"):      # 다리의 북단·남단: 다리 지점에서 앞뒤로 700m 가운데 그 방향에 있는 쪽
                    lo, hi = max(0.0, pos - BRIDGE_HALF_M), min(total, pos + BRIDGE_HALF_M)
                    p_lo, p_hi = slice_route(coords, lo, lo + 1)[0], slice_route(coords, hi - 1, hi)[-1]
                    axis, want_big = (1, it["tail"] == "북단") if it["tail"] in ("북단", "남단") else (0, it["tail"] == "동단")
                    pos = lo if (p_lo[axis] > p_hi[axis]) == want_big else hi
                it["pos"] = pos
        last = 0.0
        for it in seq:      # 차례가 뒤집힌 자리는 버리고 어림으로 돌린다
            if it.get("pos") is not None:
                if it["pos"] < last - 1:
                    warnings.append(f"{it['what']} 이(가) 경로의 차례와 맞지 않는 곳으로 잡혀 앞뒤 지점 사이에 어림으로 잡았습니다.")
                    it["pos"] = None
                else:
                    last = it["pos"]
        j = 0
        while j < len(seq):         # 자리가 없는 것들은 앞뒤 자리 사이를 똑같이 나눠 놓는다(경로를 그대로 쓰는 의뢰는 그 컷의 구간 안에서)
            if seq[j].get("pos") is None:
                kin = (lambda it: True) if by_names else (lambda it, c=seq[j]["cut"]: it["cut"] == c)      # noqa: E731
                e = j
                while e < len(seq) and seq[e].get("pos") is None and kin(seq[e]):
                    e += 1
                w0, w1 = (0.0, total) if by_names else cut_range(seq[j]["cut"])
                lo = max(w0, seq[j - 1]["pos"]) if j and kin(seq[j - 1]) else w0
                hi = min(w1, seq[e]["pos"]) if e < len(seq) and kin(seq[e]) else w1
                for n_, it in enumerate(seq[j:e], 1):
                    it["pos"], it["approx"] = lo + (hi - lo) * n_ / (e - j + 1), True
                j = e
            else:
                j += 1
        if by_names:
            edges = [0.0, *[it["pos"] for it in seq if it["st"] is None], total]
            if all(e1 - e0 >= 50 for e0, e1 in zip(edges, edges[1:])):
                bounds = list(zip(edges, edges[1:]))
                if any(it["st"] is None and it.get("approx") for it in seq):
                    warnings.append("어림으로 잡은 컷의 경계가 있습니다 — 확인 카드의 미리보기에서 컷이 바뀌는 자리를 확인해 주세요.")
            else:
                # 경로를 길이로 똑같이 나누면 이름표와 실제 자리가 어긋난 채 '완성'된다(컷 1이 수색역 600m 앞에서 끝나는 식) — 만들지 않고 알린다
                problems = []
                for n, (e0, e1) in enumerate(zip(edges, edges[1:]), 1):
                    if e1 - e0 >= 50:
                        continue
                    c = cuts[n - 1]
                    left = cuts[n - 2]["to"] if n > 1 else base["from"]["name"]
                    right = c["to"] if n < len(cuts) else base["to"]["name"]
                    msg = (f"컷 {n}({c['from']} → {c['to']})이 들어갈 자리가 경로 위에 {max(0.0, e1 - e0):.0f}m 밖에 없습니다 — "
                           f"컷 {n}의 처음과 끝으로 잡힌 '{left}' 와(과) '{right}' 이(가) 거의 같은 곳입니다.")
                    if n > 1 and not same(cuts[n - 2]["to"], c["from"]):
                        msg += (f" 컷 {n}은 '{c['from']}'에서 시작해 앞 컷이 끝난 곳과 이어지지 않는 구간으로 보입니다. "
                                "컷 나눔은 아직 하나로 이어진 경로만 나눌 수 있으니, 이 컷은 빼고 따로 의뢰해 주세요.")
                    problems.append(msg)
                raise request.RequestError(problems + ["경로를 길이로 똑같이 나누면 컷의 이름표와 실제 자리가 어긋나서 만들지 않았습니다."])
        for it in seq:      # 들르는 곳은 어느 경우에도 버리지 않는다 — 컷의 구간이 어떻게 잡혔든 그 컷 안의 자리로 놓는다
            if it["st"] is not None:
                spots.setdefault(it["cut"], []).append((it["st"], it["pos"], bool(it.get("approx"))))
    # 컷마다의 구간 [시작 m, 끝 m] — 지도를 받기 전에 다 정하고 검사한다(짧은 컷이 있으면 빈 작업 폴더를 남기지 않고 멈춘다)
    spans = []
    for n, cut in enumerate(cuts, 1):
        even = bounds[n - 1] if bounds else cut_range(n - 1)
        s0, s1 = cut.get("from_m", even[0]), min(total, cut.get("to_m", even[1]))
        if s1 - s0 < 50:
            raise request.RequestError([f"컷 {n}: 구간({s0:g}~{s1:g}m)이 경로(전체 {total:.0f}m) 안에서 너무 짧습니다."])
        spans.append((s0, s1))
    # 컷의 이름표와 경로의 끝점이 다른 곳을 가리키면 알린다 — 이름만 바꾸면 자리는 그대로다(네비의 목표도 도착지 그대로).
    # '월드컵경기장 앞'(서울월드컵경기장) · '여의도 방향'처럼 같은 곳을 달리 적었거나 방향을 가리키는 이름표는 알리지 않는다
    def other_place(label: str, place: str) -> bool:
        if re.search(r"(방향|방면|쪽)$", label.strip()):
            return False
        x, y = (re.sub(r"\s+", "", s) for s in (_TAIL.sub("", label.strip()), place))
        return bool(x and y) and x not in y and y not in x
    if other_place(cuts[0]["from"], base["from"]["name"]):
        warnings.append(f"컷 1의 시작 이름 '{cuts[0]['from']}' 이(가) 출발지 '{base['from']['name']}' 와(과) 다릅니다 — 경로는 출발지에서 시작하고 "
                        f"'{cuts[0]['from']}' 은(는) 이름표에만 쓰입니다. 출발 지점을 바꾸려면 출발지를 고쳐 주세요.")
    if other_place(cuts[-1]["to"], base["to"]["name"]):
        warnings.append(f"마지막 컷의 끝 이름 '{cuts[-1]['to']}' 이(가) 도착지 '{base['to']['name']}' 와(과) 다릅니다 — 경로와 네비의 목표는 도착지이고 "
                        f"'{cuts[-1]['to']}' 은(는) 이름표에만 쓰입니다. 도착 지점을 바꾸려면 도착지를 고쳐 주세요.")
    # 다시 만들 봉투: 이번에 확정한 출발·도착 좌표, 경유지, 컷의 구간, 핀의 자리를 다 적는다 —
    # 결과 카드에서 화풍·배치만 바꿔 다시 낼 때 지명 묻기·검색·지명 찾기를 하지 않고 같은 도로선·경계·핀으로 그린다
    resolved = fixed_request(spec, a, b, via, spans, spots, coords)
    if relabel:     # 오타를 고친 이름은 결과 카드·작업 기록에도 고친 표기로(자리는 좌표로 정해져 있어 이름을 바꿔도 다시 찾지 않는다)
        for p in (resolved["from"], resolved["to"]):
            p["name"] = show(p["name"])
        for c in resolved["cuts"]:
            c["from"], c["to"] = show(c["from"]), show(c["to"])
            if c.get("stops"):      # 복사본을 고친다 — 원래 의뢰의 들르는 곳은 아래에서 이름으로 짝을 짓는다
                c["stops"] = [{**st, "name": show(st["name"])} for st in c["stops"]]
    name = re.sub(r"[^0-9A-Za-z가-힣]+", "", f"{base['from']['name']}_{base['to']['name']}") or "job"
    art_style = base.get("art_style", request.DEFAULT_ART_STYLE)
    suffix = "" if art_style == request.DEFAULT_ART_STYLE else f"_{art_style}"
    root = out_root / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}_{len(cuts)}컷{suffix}"
    root.mkdir(parents=True, exist_ok=False)
    for n, (prev, cut) in enumerate(zip(cuts, cuts[1:]), 2):       # 뒤 컷의 시작 이름은 이름표일 뿐, 자리는 앞 컷이 끝난 곳이다
        if by_names and not same(prev["to"], cut["from"]):       # 자리를 m 로 직접 준 의뢰는 이름이 처음부터 이름표일 뿐이다 — 알리지 않는다
            warnings.append(f"컷 {n}의 시작 이름 '{cut['from']}' 은(는) 이름표에만 쓰입니다 — 컷의 자리는 컷 {n - 1}이 끝나는 곳('{prev['to']}')에서 끊기지 않고 이어집니다.")
    made = []

    def save(state: str) -> dict:
        """작업 기록을 쓴다. state: making 만드는 중(중간에 끊기면 이 상태로 남아 몇 컷까지 됐는지 보인다) · done 다 만듦."""
        record = {
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": KIND, "state": state, "request": request.private(resolved),
            "route": {"distance_m": round(total)},
            "cuts": [{**m, "dir": m["dir"].name} for m in made], "cuts_wanted": len(cuts),
            "warnings": warnings, "attribution": ATTRIBUTION,
            "timing": {"build_seconds": round(time.perf_counter() - t0, 1)},
        }
        (root / "job.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    save("making")
    show_credit = 0.25 * sum(c["duration_seconds"] for c in cuts)        # 지도 출처는 영상 전체 길이의 앞 25% 동안만 보인다
    began = 0.0
    for n, cut in enumerate(cuts, 1):
        s0, s1 = spans[n - 1]
        part = slice_route(coords, s0, s1)
        dist = route_length(part)
        req = {**base, "from": {"name": show(cut["from"]), "lat": part[0][1], "lon": part[0][0]},
               "to": {"name": show(cut["to"]), "lat": part[-1][1], "lon": part[-1][0]}, "duration_seconds": cut["duration_seconds"]}
        if "vehicle_scale" in cut:
            req["vehicle_scale"] = cut["vehicle_scale"]
        extra = {"flow": {"in": n > 1, "out": n < len(cuts)}, "cut": {"n": n, "of": len(cuts)},
                 "remain_after_m": round(total - s1), "goal": show(base["to"]["name"]),       # 네비 지도의 남은 거리는 컷을 넘어 이어서 센다
                 "credit_window": [0, max(0.0, show_credit - began)]}                   # 이 컷 안에서 출처가 보이는 [시작, 끝] 초
        began += cut["duration_seconds"]      # 컷과 컷 사이에서는 멈추지 않고 달리는 채로 넘어간다
        # 들르는 곳: 이 컷 안에서의 자리(0~1). 지도에서 찾지 못한 곳은 앞뒤 지점 사이에 어림으로 놓여 있다(approx)
        if spots.get(n - 1):
            extra["stops"] = [{"name": st.get("label") or show(st["name"]), "caption": st["caption"], "pass": st["pass"], "at": max(0.0, min(1.0, (m - s0) / (s1 - s0))),
                               **({"approx": True} if rough else {})} for st, m, rough in spots[n - 1]]
            for st, m, _ in spots[n - 1]:       # 경로를 그대로 쓰는 의뢰에서 핀이 이 컷의 구간 밖에 있으면 — 컷 끝에 붙인 사실을 알린다
                if m < s0 - 30 or m > s1 + 30:
                    warnings.append(f"컷 {n}: 들르는 곳 '{st['name']}' 이(가) 이 컷의 구간({s0:.0f}~{s1:.0f}m) 밖(경로의 {m:.0f}m)에 있어 "
                                    f"컷의 {'처음' if m < s0 else '끝'}에 붙였습니다. 다른 컷의 들르는 곳이면 그 컷으로 옮겨 주세요.")
        for st in cut.get("stops", []):     # 컷의 시작·끝에 붙은 설명은 이름표 안에 같이 적는다(이름이 되풀이되면 뗀다: 'DDP 집결' → '집결')
            cap = st["caption"][len(st["name"]):].strip() or st["caption"] if st["caption"].startswith(st["name"]) else st["caption"]
            if cap and same(st["name"], cut["from"]):
                extra["from_caption"] = cap
            elif cap and same(st["name"], cut["to"]):
                extra["to_caption"] = cap
        if "bridge" in cut:     # 다리의 자리: 경로 전체의 m → 이 컷 안의 비율
            br = cut["bridge"]
            f0, f1 = (br.get("from_m", s0) - s0) / (s1 - s0), (br.get("to_m", s1) - s0) / (s1 - s0)
            f0, f1 = max(0.0, min(1.0, f0)), max(0.0, min(1.0, f1))
            extra["bridge"] = {"look": br["look"], "from": f0, "to": f1,
                               "pylon": max(f0, min(f1, (br["pylon_m"] - s0) / (s1 - s0))) if "pylon_m" in br else f0 + (f1 - f0) * 0.45}
        log(f"컷 {n}/{len(cuts)}: {cut['from']} → {cut['to']} · {s0:.0f}~{s1:.0f}m · {cut['duration_seconds']:g}초")
        part_osrm = {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": part}, "distance": dist,
                                                 "duration": osrm["routes"][0].get("duration", 0) * dist / (total or 1)}]}
        job = build.make_job(req, project_dir=project_dir, out_root=out_root, request_dir=request_dir, offline=offline,
                             log=lambda s: log(f"  {s}"), osrm=part_osrm, job_dir=root / f"컷{n}", extra=extra, geo=geo)
        warnings += [f"컷 {n}: {w}" for w in job["warnings"]]
        # 컷마다: 바탕 지도가 어디서 왔고(map.source) 도형이 몇 개인지, 어림으로 놓은 곳이 어디인지 — 영상 규격이 맞아도 바탕이 빈 컷은 따로 보인다
        made.append({"dir": job["dir"], "duration_seconds": cut["duration_seconds"], "from_m": round(s0), "to_m": round(s1), "distance_m": round(dist),
                     "from": show(cut["from"]), "to": show(cut["to"]), "map": job["map"],
                     "approx": [st["name"] for st in extra.get("stops", []) if st.get("approx")]})
        save("making")
    record = save("done")
    write_preview(root, made)
    return {"dir": root, **record, "cuts": made, "places": {"from": a, "to": b}}


def write_preview(root: Path, made: list[dict]) -> None:
    """묶음 폴더의 미리보기: 컷 화면을 차례로 돌려 보여 준다(영상으로 묶기 전에 브라우저로 볼 때)."""
    plan = json.dumps([[f"{m['dir'].name}/index.html", m["duration_seconds"]] for m in made], ensure_ascii=False)
    (root / "index.html").write_text(
        '<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>컷 나눔 미리보기</title>'
        '<style>html,body{margin:0;height:100%;background:#232830}iframe{border:0;width:100%;height:100%;display:block}</style></head>'
        f'<body><iframe id="f"></iframe><script>const C={plan};let i=0;const f=document.getElementById("f");'
        '(function next(){f.src=C[i][0]+location.search;setTimeout(next,C[i][1]*1000);i=(i+1)%C.length})();</script></body></html>',
        encoding="utf-8")


def load(root: Path) -> dict:
    """만들어 둔 컷 나눔 묶음 폴더를 다시 읽는다(메신저에서 나중에 영상을 내보낼 때)."""
    root = Path(root)
    record = json.loads((root / "job.json").read_text(encoding="utf-8"))
    return {**record, "dir": root, "cuts": [{**c, "dir": root / c["dir"]} for c in record["cuts"]]}


def export_all(job: dict, fps: int, size: tuple[int, int], *, alpha: bool = False, name: str = "지도", log=print) -> dict:
    """각 컷을 MP4로 바로 인코딩한 뒤 컷 순서대로 이어 붙인다. 요청한 알파 시퀀스만 PNG로 쓴다."""
    t0 = time.perf_counter()
    root = Path(job["dir"]).resolve()
    cut_dirs = [Path(c["dir"]).resolve() for c in job["cuts"]]
    counts = frame_plan([c["duration_seconds"] for c in job["cuts"]], fps)
    pool = export.workers(len(counts))
    output = root / f"{name}.mp4"
    log(f"지도 MP4 만들기: {len(counts)}컷 {sum(counts)}장 · 브라우저 {pool}개")
    with tempfile.TemporaryDirectory(dir=root, prefix=".cuts-") as tmp, \
            tempfile.TemporaryDirectory(prefix="mapcg-cuts-browser-", ignore_cleanup_errors=True) as browser_tmp:
        work = Path(tmp)
        profiles = [export.profile_for(Path(browser_tmp), i) for i in range(len(counts))]

        def encode_cut(i: int) -> int:
            return export.capture_mp4(cut_dirs[i], work / f"{i}.mp4", fps, counts[i], profiles[i], frames=counts[i])

        with ThreadPoolExecutor(pool) as ex:
            actual = list(ex.map(encode_cut, range(len(counts))))

        # MP4 컨테이너가 반올림한 길이 대신 실제 프레임 수를 써야 컷 경계가 분수 초인 경우에도 이어지는 시간이 맞는다.
        (work / "cuts.txt").write_text("".join(f"file '{i}.mp4'\nduration {n / fps:.12f}\n" for i, n in enumerate(actual)),
                                       encoding="ascii")
        joined = work / "joined.mp4"
        cmd = [export.find_ffmpeg(), "-nostdin", "-v", "error", "-f", "concat", "-i", "cuts.txt",
               "-map", "0:v:0", "-c:v", "copy", "-movflags", "+faststart", joined.name]
        run = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if run.returncode or not joined.is_file() or not joined.stat().st_size:
            raise export.ExportError(f"컷 영상을 잇지 못했습니다: {run.stderr.strip()[:400]}")

        result = {"mp4": output, "frames": sum(actual)}
        measured = export.probe(joined)
        if measured and (measured["frames"] != sum(actual) or measured["fps"] != fps):
            raise export.ExportError(f"이은 영상의 규격이 컷과 다릅니다({measured['frames']}장 · {measured['fps']}fps).")
        result["spec"] = export.check_spec({"width": size[0], "height": size[1], "fps": fps, "frames": sum(counts)}, measured)
        log(export.spec_line(result["spec"]))
        if alpha:
            dest = root / "알파_시퀀스"
            log(f"투명 PNG 시퀀스 만들기: {sum(actual)}장")

            def alpha_cut(i: int) -> int:
                return export.capture(cut_dirs[i], "stage", dest, actual[i], profiles[i], frames=counts[i], offset=sum(actual[:i]))

            with ThreadPoolExecutor(pool) as ex:
                alpha_counts = list(ex.map(alpha_cut, range(len(counts))))
            if alpha_counts != actual:
                raise export.ExportError(f"투명 시퀀스의 컷별 장수가 영상과 다릅니다({alpha_counts}/{actual}).")
            result["alpha_dir"] = dest
        joined.replace(output)
    result["seconds"] = round(time.perf_counter() - t0, 1)
    export._record(root, result)
    return result


def sheets(job: dict) -> list[Path]:
    """컷마다 검수판을 뽑아 묶음 폴더에 '검수판_컷N.png' 으로 모은다(컷마다 브라우저를 따로 띄워 동시에)."""
    cuts_ = job["cuts"]
    pool = export.workers(len(cuts_))

    def one(i: int) -> Path:
        cut = cuts_[i]
        src = export.sheet(Path(cut["dir"]), cut["duration_seconds"], export.profile_for(Path(job["dir"]).resolve().parent, i))     # 컷마다 다른 프로필(한 프로필을 두 브라우저가 같이 쓰면 둘째가 꺼진다)
        return Path(shutil.copyfile(src, Path(job["dir"]) / f"검수판_컷{i + 1}.png"))
    with ThreadPoolExecutor(pool) as ex:
        return list(ex.map(one, range(len(cuts_))))
