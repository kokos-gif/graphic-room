"""의뢰 봉투 → 작업 폴더(브라우저로 바로 열리는 지도 한 벌)."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import mapdata, request
from .geo import ATTRIBUTION, Geo, GeoError, GeoNotFound, meters

FAR_PAIR_M = 30_000     # 출발·도착의 첫 후보가 이보다 멀면, 같은 이름의 더 가까운 후보가 있는지 본다
TEMPLATES = Path(__file__).resolve().parent / "templates"
# 지도 화면과 지점 고르기 화면이 같이 쓰는 그리기 코드: 작가 도구 · 지도의 디자인 기준 묶음 · 바탕 지도 그리기
SHARED = ("brush.js", "mapskin.js", "basemap.js")
STAGE = "stage.js"      # 무대: 보기 방식·재생·내보내기 틀(지도 화면과 정보판 화면이 같이 쓴다)
DETAIL_LABELS = {"major": "큰길만", "minor": "골목길 포함", "foot": "산책로·광장까지"}
MAX_WORLD_PX = 4000     # 따라가는 카메라의 바탕 지도 한 변의 최대 픽셀 — 영상 뽑기(GPU 없는 브라우저)에서 바탕을 그리는 시간이 넓이에 비례한다(1920 화면의 약 2배 ≈ 45초)


def follow_camera(req: dict, osrm: dict, view_w: float, pw: int) -> dict | None:
    """따라가는 카메라(req['camera'] == 'follow')의 확대 배율. 화면 하나가 경로 길이의 약 40%(150m~2km)를 보이게 한다.

    바탕을 화면의 zoom 배로 크게 그리고, 화면은 그 위에서 이동 아이콘을 따라 움직인다(지도가 같이 흐른다).
    경로가 짧아 크게 볼 것이 없으면(1.3배 미만) 따라가지 않는다."""
    if req.get("camera") != "follow":
        return None
    coords = osrm["routes"][0]["geometry"]["coordinates"]
    length = sum(meters(coords[i - 1], coords[i]) for i in range(1, len(coords))) or 1
    window = max(150.0, min(2000.0, length * 0.4))
    zoom = max(1.3, min(view_w / window, 3.5, MAX_WORLD_PX / pw))
    return {"mode": "follow", "zoom": round(zoom, 3)} if view_w / window >= 1.3 else None
# 화풍: 화면 틀은 하나만 두고, 기본(claude) 말고는 화풍 파일 하나를 더 싣는다.
# 화풍 파일은 디자인 기준 묶음(색·재질)과 붓의 설정만 바꾼다 — 화면을 통째로 복사해 두면 한쪽만 고쳐져 어긋난다.
STYLE_FILES = {"codex": "codex_brush.js", "codex_v3": "style_v3.js", "v4": "style_v4.js", "v5": "style_v5.js"}
# 화풍 파일이 부르는 딸린 파일(글꼴 등). 작업 폴더에 같이 넣어 두어야 다른 컴퓨터에서도 같은 글자로 나온다
# 글꼴은 사용권 원문(OFL)과 함께 싣는다 — 글꼴 파일을 배포할 때 원문을 같이 두라는 것이 OFL 의 조건이다(templates/fonts/글꼴_사용권.md)
BROADCAST_ASSETS = ("fonts/Pretendard-Black.otf", "fonts/Pretendard-Bold.otf", "fonts/OFL_Pretendard.txt", "icons/icons_A_games.png", "icons/icons_B_score.png")
STYLE_ASSETS = {"v5": BROADCAST_ASSETS}
# 방송 기본 묶음(templates/broadcast.js — 안전 영역 · 같이 싣는 글꼴 · 소품 아이콘 · 가장 작은 글자). 이 묶음보다 먼저 있던 화풍은 옛 화면에서
# 예전과 똑같이 그리려고 넣지 않는다. 여기 없는 화풍(V6 …)은 어느 화면에서든 늘 넣는다 — 새 화풍 파일은 색 · 선 · 재질 · 바탕만 정하면 된다
BROADCAST_FILE = "broadcast.js"
LEGACY_STYLES = frozenset({"claude", "codex", "codex_v3", "v4", "v5"})


def _js(name: str, obj) -> str:
    return f"window.{name}=" + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";"


def write_art_page(html: str, dest: Path, art_style: str, after_script: str, *, broadcast: bool = False) -> None:
    """화면을 dest 에 쓴다. 기본이 아닌 화풍이면 화풍 파일을 옆에 넣고, 기준 묶음(after_script) 바로 뒤에서 부르게 한다.

    화풍 파일은 장면이 붓을 만들기 전에 실행되어야 하므로 디자인 기준 묶음 다음, 그리는 코드 앞에 둔다.
    broadcast = 방송 기본 묶음을 늘 넣는 화면(맞춤 그래픽 · 카드 퀴즈). 새 화풍(LEGACY_STYLES 밖)이면 어느 화면이든 넣는다.
    순서: 기준 묶음 → 방송 기본 묶음 → 화풍 파일(화풍이 기본 묶음의 값을 덮어쓸 수 있게).
    """
    name = STYLE_FILES.get(art_style)
    scripts, assets = [], []
    if broadcast or art_style not in LEGACY_STYLES:
        scripts.append(BROADCAST_FILE)
        assets += BROADCAST_ASSETS
    if name:
        scripts.append(name)
        assets += STYLE_ASSETS.get(art_style, ())
    if scripts:
        anchor = f'<script src="{after_script}"></script>'
        if html.count(anchor) != 1:
            raise ValueError(f"화풍을 적용할 기준 스크립트({after_script})를 한 곳에서 찾을 수 없습니다.")
        for file in scripts:
            shutil.copyfile(TEMPLATES / file, dest.parent / file)
        for asset in dict.fromkeys(assets):
            (dest.parent / asset).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATES / asset, dest.parent / asset)
        html = html.replace(anchor, anchor + "".join(f'\n<script src="{file}"></script>' for file in scripts))
    dest.write_text(html, encoding="utf-8")


def install_scene(job_dir: Path, art_style: str, page: str, files: tuple[str, ...], after_script: str, *, broadcast: bool = False) -> None:
    """작업 폴더에 화면(index.html)과 그 화면이 부르는 파일을 넣는다. 저장된 작업은 만든 때의 그리기 코드를 함께 갖는다."""
    html = (TEMPLATES / page).read_text(encoding="utf-8")
    write_art_page(html, job_dir / "index.html", art_style, after_script, broadcast=broadcast)
    for name in files:
        shutil.copyfile(TEMPLATES / name, job_dir / name)


AREA_KM = 5.0       # 의뢰에 '장소'가 적혀 있으면 지명을 먼저 그 둘레 이만큼 안에서 찾는다


def outside_note(p: dict) -> str:
    """서울 안에서 못 찾아 전국에서 잡은 지점의 알림(geo.geocode_loose 가 outside 표시를 남긴 것)."""
    return (f"'{p['name']}' 은(는) 서울 안에서 찾지 못해 전국에서 찾았습니다('{str(p.get('display', ''))[:40]}'). "
            "서울의 다른 곳이면 더 구체적인 이름을 적거나 '지점 지정'으로 찍어 주세요.")


def area_anchor(geo: Geo, req: dict) -> tuple[tuple[float, float] | None, str | None]:
    """의뢰의 '장소'(area.name — 확인 카드의 장소 칸)를 지도에서 찾아 (위도, 경도)를 돌려준다 → (자리 또는 None, 알림 또는 None).

    '시청' · '정문' · '주차장'처럼 어디에나 있는 이름은 장소 없이 찾으면 전국의 첫 후보(다른 도시)가 잡힌다.
    장소를 못 찾으면 예전처럼 넓게 찾고, 그 사실을 알린다."""
    name = str((req.get("area") or {}).get("name") or "").strip()
    if not name:
        return None, None
    for q in dict.fromkeys((name, name.replace(" ", ""), name.split()[0])):      # '서울시청 광장' → '서울시청광장' → '서울시청'
        try:
            hit = geo.geocode_loose(q)
            return (hit["lat"], hit["lon"]), None
        except GeoNotFound:
            continue
    return None, f"장소 '{name}' 을(를) 지도에서 찾지 못해 지명을 그 둘레로 좁히지 못했습니다. 지점이 다른 지역으로 잡혔으면 '지점 지정'으로 찍어 주세요."


def _resolve(geo: Geo, place: dict, near: tuple[float, float] | None = None) -> dict:
    """좌표가 있으면 그대로, 없으면 지명으로 찾는다. '홍대'처럼 넓은 이름은 첫 후보(그 일대의 대표 지점)를 쓴다.
    near = 의뢰의 장소 자리 — 먼저 그 둘레에서 찾고, 거기 없을 때만 넓게 찾는다."""
    if "lat" in place:
        return {**place, "display": "직접 지정한 좌표", "candidates": []}
    if not near:
        return geo.geocode_loose(place["name"])
    key = lambda s: re.sub(r"\s+", "", str(s))      # noqa: E731
    try:
        hit = geo.geocode_loose(place["name"], near=near, km=AREA_KM)
        cands = hit.get("candidates") or [hit]
        # 이름이 그대로 실린 후보를 먼저(주소에 그 낱말이 든 가게가 첫 후보로 올 때가 있다)
        exact = next((c for c in cands if key(c["display"].split(",")[0]) == key(hit.get("searched") or place["name"])), None)
        if exact:
            return {**hit, "lat": exact["lat"], "lon": exact["lon"], "display": exact["display"]}
        return {**hit, "near_note": f"'{place['name']}' 은(는) 장소 둘레에서 '{hit['display'][:40]}'(으)로 잡혔습니다. 지점이 맞는지 확인해 주세요."}
    except GeoNotFound:
        pass
    hit = geo.geocode_loose(place["name"])      # 그 둘레에 없다 — 넓게 찾되, 같은 이름이 여러 곳이면 장소에서 가장 가까운 후보를 쓴다
    best = min(hit.get("candidates") or [hit], key=lambda c: meters([c["lon"], c["lat"]], [near[1], near[0]]))
    far = meters([best["lon"], best["lat"]], [near[1], near[0]])
    note = f"'{place['name']}' 은(는) 장소 둘레에 없어 {far / 1000:.0f}km 떨어진 '{best['display'][:40]}'(으)로 잡혔습니다. 다른 곳이면 '지점 지정'으로 찍어 주세요." if far > AREA_KM * 1000 else None
    return {**hit, "lat": best["lat"], "lon": best["lon"], "display": best["display"], **({"near_note": note} if note else {})}


def resolve_or_search(geo: Geo, place: dict, find, searcher=None, others=(), notes: list[str] | None = None) -> dict:
    """find(geo, place) 로 찾고, 지도에 그 이름이 없으면 ① 띄어쓰기를 뺀 이름 ② Liner 웹 검색으로 확인한 정식 이름(searcher)으로 다시 찾는다.

    ① 지도에는 '안국역6번출구'(정류장)는 있고 '안국역 6번출구'는 없다(11-23 손그림 의뢰서 시험) — 돈이 들지 않으니 먼저 한다.
    ② searcher = intake.searched_name(컷 나눔 지도와 같은 것). others = 같은 의뢰의 다른 지명 · 장소(검색어를 그 지역으로 좁힌다).
    화면 이름표는 적힌 이름 그대로다(돌려주는 name). ①은 searched 로(부르는 쪽의 기존 알림), ②는 notes 에 근거 주소와 함께 남긴다.
    좌표를 직접 준 지점과 지도 서버가 안 되는 것(GeoError)은 그대로 올린다 — 이름을 바꿔 가며 다시 묻지 않는다."""
    try:
        return find(geo, place)
    except GeoNotFound as first:
        name = str(place.get("name") or "").strip()
        if "lat" in place or not name:
            raise
        tight = re.sub(r"\s+", "", name)
        if tight != name:
            try:
                hit = find(geo, {**place, "name": tight})
                return {**_written(hit, tight, name), "searched": hit.get("searched") or tight}
            except GeoNotFound:
                pass
        if searcher:
            near = " ".join(dict.fromkeys(o for o in others if o and o != name))
            try:
                got = searcher(name, near=near)
            except Exception:       # 검색이 안 된다(키 · 잔액 · 접속) — 처음의 '찾지 못함'을 그대로 올린다
                got = None
            official = str((got or {}).get("search_name") or "").strip()
            if official and re.sub(r"\s+", "", official) != tight:
                for q in dict.fromkeys((official, re.sub(r"\s+", "", official))):
                    try:
                        hit = find(geo, {**place, "name": q})
                    except GeoNotFound:
                        continue
                    if notes is not None:
                        src = f" 근거: {', '.join(got['sources'])}" if got.get("sources") else ""
                        notes.append(f"'{name}' 은(는) 지도에 그런 이름이 없어, Liner 검색으로 확인한 정식 이름 "
                                     f"'{hit.get('searched') or q}'(으)로 찾았습니다. 지점이 맞는지 확인해 주세요.{src}")
                    return {k: v for k, v in _written(hit, q, name).items() if k != "searched"}
            # ③ 정식 이름도 지도에 없다(작은 건물 · 우체국 …) — 검색 결과에 도로명주소가 있었으면 그 주소로 지점을 잡는다
            address = str((got or {}).get("address") or "").strip()
            if got and not address:
                # 이름은 확인했는데 주소가 없었다 — 주소만 한 번 더 찾는다(11-24 '상암디지털 우체국' · '상암동 쉼터정원'이 이 자리에서 실패했다).
                # 이름을 확인하지 못한 곳(got 이 없음)은 주소를 찾지 않는다 — 다른 곳의 주소를 잡을 수 있다
                try:
                    more = searcher(official or name, near=near, want="address")
                except Exception:       # 검색이 안 되거나, 주소 찾기를 모르는 검색 함수다
                    more = None
                if more and str(more.get("address") or "").strip():
                    address = str(more["address"]).strip()
                    got = {**got, "sources": more.get("sources") or got.get("sources") or []}
            if address:
                try:
                    hit = find(geo, {**place, "name": address})
                except GeoNotFound:
                    hit = None
                if hit:
                    if notes is not None:
                        src = f" 근거: {', '.join(got['sources'])}" if got.get("sources") else ""
                        notes.append(f"'{name}' 은(는) 지도에 그런 이름이 없어, Liner 검색으로 확인한 주소 '{address}'(으)로 지점을 잡았습니다. "
                                     f"지점이 맞는지 확인해 주세요.{src}")
                    out = {k: v for k, v in _written(hit, address, name).items() if k not in ("searched", "near_note")}
                    return {**out, "display": f"{address} (주소로 찾음)", "candidates": []}
        raise first


def _written(hit: dict, asked: str, name: str) -> dict:
    """다른 이름(asked)으로 찾은 지점을 적힌 이름(name)으로 돌려놓는다 — 장소 둘레 알림(near_note)도 적힌 이름으로 말하게."""
    note = hit.get("near_note")
    return {**hit, "name": name, **({"near_note": note.replace(f"'{asked}'", f"'{name}'", 1)} if note else {})}


STOP_NEAR_END_M = 60        # 출발 · 도착 · 앞 들르는 곳과 이만큼 가까운 들르는 곳은 경유지로 또 넣지 않는다(길이 0 인 구간이 생긴다)
STOP_OFF_ROUTE_M = 300      # 경유지로 넣지 않은 들르는 곳이 경로에서 이보다 멀면 알린다
# 들르는 곳 하나 때문에 돌아가는 거리가 이보다 크고 출발~도착 직선거리의 1.5배보다도 크면 다른 지역의 같은 이름으로 본다.
# 컷 나눔의 한도(cuts.DETOUR_M = 30km)를 그대로 쓰면 안 걸린다 — 들르는 곳은 find_stops 가 중심에서 8km 안팎으로만 찾아서 돌아가는 거리가
# 많아야 16 ~ 25km 다(11-24 정밀 리뷰: 출발~도착이 14km 를 넘어야 뺄 수 있었다)
STOP_DETOUR_M = 3_000


def find_stops(geo: Geo, req: dict, a: dict, b: dict, searcher=None) -> tuple[list[dict], list[str]]:
    """한 화면 지도의 들르는 곳(req['stops'])을 지도에서 찾는다 → ([{**들르는 곳, 'place'(못 찾으면 없음)}], 알림).

    출발 · 도착 둘레(둘 사이 거리의 0.75배 + 5km 안)에서만 찾는다 — '신용산'이 다른 도시의 같은 이름으로 잡히지 않게.
    지도에 없으면 한 화면 지도의 출발 · 도착과 같은 길(띄어쓰기 → Liner 검색)로 다시 찾고, 그래도 없으면 표시하지 않고 알린다."""
    items: list[dict] = []
    notes: list[str] = []
    span = meters([a["lon"], a["lat"]], [b["lon"], b["lat"]])
    center, reach = ((a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2), max(8.0, span / 1000 * 0.75 + 5)
    near_find = lambda g, p: g.geocode_loose(p["name"], near=center, km=reach)      # noqa: E731
    others = (req["from"].get("name", ""), req["to"].get("name", ""))
    for st in req.get("stops") or []:
        it = dict(st)
        if "lat" in st:     # 확인 카드에서 지도로 찍은 지점
            it["place"] = {"name": st["name"], "lat": st["lat"], "lon": st["lon"], "display": "직접 지정한 좌표"}
        else:
            lost = " — 그곳의 정차도 빠집니다" if st.get("hold") else ""
            try:
                it["place"] = resolve_or_search(geo, {"name": st["name"]}, near_find, searcher, others, notes)
                if it["place"].get("searched"):
                    notes.append(f"들르는 곳 '{st['name']}' 은(는) 지도에 그런 이름이 없어 '{it['place']['searched']}'(으)로 찾았습니다. 지점이 맞는지 확인해 주세요.")
            except GeoNotFound:
                notes.append(f"들르는 곳 '{st['name']}' 을(를) 이 지역의 지도에서 찾지 못해 표시하지 못했습니다{lost}. 확인 카드에서 📍로 지점을 찍어 주세요.")
            except GeoError as e:       # 지명 서버가 안 된다(오프라인이면 저장된 것이 없다) — 못 찾은 것과 다르다
                notes.append(f"들르는 곳 '{st['name']}' 을(를) 지금 찾아볼 수 없어 표시하지 못했습니다{lost}({str(e)[:70]}). 잠시 뒤 다시 만들어 주세요.")
        items.append(it)
    return items, notes


def stop_waypoints(items: list[dict], a: dict, b: dict, notes: list[str] | None = None) -> list[dict]:
    """찾은 들르는 곳을 차례대로 경유지로 — 경로가 그곳들을 지나게 한다. 출발 · 도착 · 바로 앞 경유지와 같은 자리는 또 넣지 않는다.
    각 항목에 'wp'(경유지 번호)를 남긴다(자리를 잡을 때 쓴다).

    출발 · 도착에서 동떨어진 곳(그곳을 들르면 STOP_DETOUR_M · 직선거리의 1.5배보다 더 돌아간다)은 다른 지역의 같은 이름으로
    보고 빼고 알린다(notes) — 그대로 두면 경로가 그곳을 갔다 돌아와 지도가 통째로 줄어든다. 지도에서 직접 찍은 지점은 그대로 둔다."""
    via: list[dict] = []
    ends = [a["lon"], a["lat"]], [b["lon"], b["lat"]]
    direct = meters(*ends)
    for it in items:
        p = it.get("place")
        if not p:
            continue
        pt = [p["lon"], p["lat"]]
        detour = meters(ends[0], pt) + meters(pt, ends[1]) - direct
        if "lat" not in it and detour > max(STOP_DETOUR_M, direct * 1.5):
            it.pop("place")
            if notes is not None:
                notes.append(f"들르는 곳 '{it['name']}' 이(가) 출발 · 도착에서 동떨어진 곳으로 잡혀(들르면 약 {detour / 1000:.0f}km 를 돌아갑니다) 표시하지 않았습니다"
                             + (" — 그곳의 정차도 빠집니다" if it.get("hold") else "") + ". 다른 지역의 같은 이름인 것 같습니다 — 확인 카드에서 📍로 지점을 찍어 주세요.")
            continue
        if min(meters(pt, ends[0]), meters(pt, ends[1])) < STOP_NEAR_END_M:
            continue
        if via and meters(pt, [via[-1]["lon"], via[-1]["lat"]]) < STOP_NEAR_END_M:
            it["wp"] = len(via) - 1
            continue
        via.append({"name": it["name"], "lat": p["lat"], "lon": p["lon"]})
        it["wp"] = len(via) - 1
    return via


def _route_point(coords: list, pos: float) -> list[float]:
    """경로([경도, 위도] 줄)의 처음부터 pos(m) 간 자리의 [경도, 위도]."""
    run = 0.0
    for p, q in zip(coords, coords[1:]):
        seg = meters(p, q)
        if seg and run + seg >= pos:
            f = max(0.0, min(1.0, (pos - run) / seg))
            return [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f]
        run += seg
    return list(coords[-1][:2])


def _drawn_pos(route: list, pt: tuple[float, float], floor: float = 0.0) -> float:
    """화면이 그리는 선(route — 미터 평면의 점들) 위에서 pt 에 가장 가까운 자리: 선의 처음부터 잰 m. floor(앞 들르는 곳의 자리)보다
    앞으로는 가지 않는다 — 같은 길을 갔다 되돌아오는 경로에서 차례가 뒤집히지 않게."""
    best, off, run = floor, float("inf"), 0.0
    for p, q in zip(route, route[1:]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        seg = (dx * dx + dy * dy) ** 0.5
        if seg and run + seg >= floor:
            f = max(0.0, min(1.0, ((pt[0] - p[0]) * dx + (pt[1] - p[1]) * dy) / (seg * seg)))
            f = max(f, min(1.0, (floor - run) / seg))
            d = ((p[0] + dx * f - pt[0]) ** 2 + (p[1] + dy * f - pt[1]) ** 2) ** 0.5
            if d < off:
                best, off = run + seg * f, d
        run += seg
    return best


def stop_marks(items: list[dict], osrm: dict, notes: list[str], plan: dict | None = None) -> list[dict]:
    """들르는 곳의 자리(경로 처음부터의 비율 at)와 정차(hold) → 화면에 넘길 J.stops. 경유지로 넣은 곳은 그 구간이 끝나는 자리,
    넣지 않은 곳(출발 · 도착 곁)은 경로 위의 가장 가까운 자리.

    plan = mapdata.plan_view 의 결과. 주면 비율을 화면이 실제로 그리는 선(plan['route']) 위에서 잰다 — 그 선은 출발 · 도착의 꼬인 고리와
    짧은 되돌이 가지를 잘라 낸 것이라 원본 경로보다 짧다. 원본 길이로 낸 비율을 그대로 쓰면 고리가 잘린 만큼 점과 정차가 뒤로 밀린다."""
    from .cuts import locate, route_length      # cuts 가 build 를 불러 쓰므로 여기서 불러 온다(서로 부르는 순환을 피한다)
    route = osrm["routes"][0]
    coords = route["geometry"]["coordinates"]
    total = route_length(coords) or 1.0
    legs = [float(leg.get("distance", 0)) for leg in route.get("legs", [])]
    k, at_via, run = total / (sum(legs) or 1), [], 0.0
    for d in legs[:-1]:
        run += d
        at_via.append(run * k)
    drawn = plan.get("route") if plan and len(plan.get("route") or []) > 1 and plan.get("total") else None
    out: list[dict] = []
    last, floor = 0.0, 0.0
    for it in items:
        p = it.get("place")
        if not p:
            continue
        if it.get("wp") is not None and it["wp"] < len(at_via):
            pos = at_via[it["wp"]]
        else:
            pos, off = locate(coords, [p["lon"], p["lat"]])
            if off > STOP_OFF_ROUTE_M:
                notes.append(f"들르는 곳 '{it['name']}' 은(는) 경로에서 약 {off:.0f}m 떨어져 있어 경로 위의 가장 가까운 자리에 표시했습니다.")
        in_order = pos >= last - 1
        if not in_order:
            notes.append(f"들르는 곳 '{it['name']}' 이(가) 경로의 차례와 맞지 않는 자리로 잡혔습니다 — 지점이 맞는지 확인해 주세요.")
        last = max(last, pos)
        if drawn:       # 원본 경로의 그 자리를 그려지는 선 위로 옮긴다. 차례가 어긋난 곳은 앞 곳에 묶지 않고 제자리에 둔다(알림은 위에서 냈다)
            here = _drawn_pos(drawn, plan["to_xy"](*_route_point(coords, pos)), floor if in_order else 0.0)
            if in_order:
                floor = here
            at = here / plan["total"]
        else:
            at = pos / total
        out.append({"name": it["name"], **({"label": it["label"]} if it.get("label") else {}), "caption": it.get("caption", ""), "pass": bool(it.get("pass")),
                    "at": round(max(0.0, min(1.0, at)), 4), **({"hold": it["hold"]} if it.get("hold") else {})})
    return out


PIER_TAIL = re.compile(r"\s*(선착장|나루터|부두|터미널|정류장)$")


def _resolve_pier(geo: Geo, place: dict) -> dict:
    """배가 서는 곳: 선착장을 먼저 찾는다 — '뚝섬'처럼 동네 이름만 적으면 대표 지점이 역·동사무소라 강에서 멀다.
    지도에 실린 표기가 제각각이라('여의도 선착장'은 없고 '여의도선착장' · '한강버스 여의도선착장'은 있다) 차례로 해 본다:
    '○○ 선착장' → '○○선착장' → '한강버스 ○○' → 적힌 이름 그대로. 좌표를 직접 준 지점은 그대로."""
    name = place.get("name", "").strip()
    if "lat" in place or not name:
        return _resolve(geo, place)
    base = PIER_TAIL.sub("", name).strip() or name
    for q in dict.fromkeys((f"{base} 선착장", f"{base}선착장".replace(" ", ""), f"한강버스 {base}")):
        try:
            hit = geo.geocode(q)
        except GeoError:
            continue
        return {**hit, "name": name} if q.replace(" ", "") == name.replace(" ", "") else {**hit, "name": name, "searched": q, "pier": True}
    return _resolve(geo, place)


def _faces(paths: list[str], bases: list[Path], job_dir: Path, warnings: list[str]) -> list[str | None]:
    """얼굴 그림을 브라우저가 읽는 PNG 로 바꿔 작업 폴더에 넣는다. 없는 파일은 임시 동그라미로 그린다."""
    out: list[str | None] = []
    if not paths:
        return out
    from PIL import Image   # TGA 를 읽으려고 쓴다
    (job_dir / "faces").mkdir(exist_ok=True)
    absent: list[int] = []
    for i, raw in enumerate(paths, 1):
        src = next((b / raw for b in bases if (b / raw).is_file()), Path(raw) if Path(raw).is_file() else None)
        if src is None:
            absent.append(i)
            out.append(None)
            continue
        if src.suffix.lower() == ".svg":        # SVG 는 브라우저가 바로 그린다 — 그대로 옮긴다
            shutil.copyfile(src, job_dir / "faces" / f"{i:02d}.svg")
            out.append(f"faces/{i:02d}.svg")
            continue
        try:
            im = Image.open(src).convert("RGBA")
            im.thumbnail((256, 256))
            im.save(job_dir / "faces" / f"{i:02d}.png")
            out.append(f"faces/{i:02d}.png")
        except OSError as e:
            warnings.append(f"얼굴 {i}번을 읽지 못했습니다({raw}: {e}) — 임시 동그라미로 그렸습니다.")
            out.append(None)
    if absent:      # 없는 파일은 한 줄로 모아 알린다
        folder = Path(paths[absent[0] - 1]).parent.as_posix()
        warnings.append(f"얼굴 {', '.join(map(str, absent))}번 파일이 없습니다({folder} 폴더) — 임시 동그라미로 그렸습니다.")
    return out


def _liner_picture(vehicle: str | None, offline: bool, warnings: list[str], log=print) -> str | None:
    """'Liner 그림 모델로 그린 그림'(vehicle_picture: liner) 아이콘의 그림 파일. 보관해 둔 그림이 있으면 그것(호출 없음),
    없으면 그린다(호출 1번 · 장당 약 0.015달러). 오프라인이거나 그리지 못하면 None — 코드로 그린 아이콘으로 그리고 알림을 남긴다."""
    subject = request.VEHICLE_SUBJECTS.get(vehicle or "")
    if not subject:
        warnings.append(f"{request.VEHICLES.get(vehicle, vehicle)} 아이콘은 그림으로 그리지 않습니다 — 코드로 그린 아이콘으로 그렸습니다.")
        return None
    from . import icons
    path = icons.path_for(subject)
    if path.is_file():
        return str(path)
    if offline:
        warnings.append("오프라인이라 Liner 그림 모델로 이동 아이콘을 그리지 못했습니다 — 코드로 그린 아이콘으로 그렸습니다.")
        return None
    try:
        log("이동 아이콘을 Liner 그림 모델로 그리는 중")
        return str(icons.make(subject)[0])
    except Exception as e:      # 그림 하나 때문에 제작을 멈추지 않는다
        from . import intake
        warnings.append(f"Liner 그림 모델이 이동 아이콘을 그리지 못했습니다({intake.reason(e)}) — 코드로 그린 아이콘으로 그렸습니다.")
        return None


def inside(raw: str, bases: list[Path]) -> Path | None:
    """raw 가 가리키는 파일이 bases 가운데 한 폴더의 안에 있으면 그 경로, 밖이면(다른 폴더 · 다른 드라이브 · 네트워크 공유) None.

    경로의 글자만으로 먼저 가린다 — 밖을 가리키는 경로는 열어 보지도 않는다(네트워크 공유는 있는지 보는 것만으로 바깥에 접속한다).
    서버가 화면에서 되돌아온 경로를 쓸 때 부른다. 명령줄에서 자기 컴퓨터의 그림을 직접 지정하는 길은 막지 않는다.
    """
    for base in bases:
        root = Path(os.path.abspath(base))
        cand = Path(os.path.abspath(root / str(raw)))
        if cand.is_relative_to(root) and cand.is_file() and cand.resolve().is_relative_to(root.resolve()):
            return cand
    return None


def _vehicle_image(raw: str | None, bases: list[Path], job_dir: Path, warnings: list[str]) -> str | None:
    """그림 이동 아이콘(투명 PNG)을 작업 폴더에 'vehicle.png' 로 넣는다. 둘레의 투명한 여백은 잘라 낸다(화면이 그림 크기로 자리를 잡는다).
    파일이 없거나 읽지 못하면 코드로 그린 아이콘으로 그리고 알림을 남긴다."""
    if not raw:
        return None
    src = next((b / raw for b in bases if (b / raw).is_file()), Path(raw) if Path(raw).is_file() else None)
    if src is None:
        warnings.append(f"이동 아이콘 그림({raw})이 없습니다 — 코드로 그린 아이콘으로 그렸습니다.")
        return None
    from PIL import Image
    try:
        im = Image.open(src).convert("RGBA")
        box = im.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
        if box:
            im = im.crop(box)
        im.thumbnail((640, 640))
        im.save(job_dir / "vehicle.png")
    except OSError as e:
        warnings.append(f"이동 아이콘 그림을 읽지 못했습니다({raw}: {e}) — 코드로 그린 아이콘으로 그렸습니다.")
        return None
    return "vehicle.png"


def near_pair(a: dict, b: dict) -> tuple[dict, dict, str | None]:
    """출발·도착의 첫 후보가 서로 다른 지역으로 잡혔을 때(전주의 '월드컵경기장' → 서울의 '노들로'), 서로 가까운 후보 쌍으로 바꾼다.

    첫 후보끼리 가까우면(30km 안쪽) 그대로 둔다 — '홍대'처럼 넓은 이름의 대표 지점을 바꾸지 않는다.
    바꾸는 것은 가까운 쌍이 훨씬 가까울 때(1/3 아래)뿐이고, 바꿨으면 그 사실을 알림으로 돌려준다. 좌표를 직접 준 지점은 건드리지 않는다.
    """
    first = meters([a["lon"], a["lat"]], [b["lon"], b["lat"]])
    if first <= FAR_PAIR_M or not (len(a.get("candidates") or []) > 1 or len(b.get("candidates") or []) > 1):
        return a, b, None
    ca, cb = min(((x, y) for x in (a.get("candidates") or [a]) for y in (b.get("candidates") or [b])),
                 key=lambda p: meters([p[0]["lon"], p[0]["lat"]], [p[1]["lon"], p[1]["lat"]]))
    best = meters([ca["lon"], ca["lat"]], [cb["lon"], cb["lat"]])
    if best * 3 > first:
        return a, b, None
    pick = lambda p, c: p if c is p else {**p, "lat": c["lat"], "lon": c["lon"], "display": c["display"]}       # noqa: E731
    a2, b2 = pick(a, ca), pick(b, cb)
    return a2, b2, (f"같은 이름이 여러 지역에 있어 서로 가까운 쪽으로 골랐습니다(첫 후보끼리는 {first / 1000:.0f}km, 고른 쪽은 {best / 1000:.1f}km). "
                    f"출발: {a2.get('display', '')[:40]} / 도착: {b2.get('display', '')[:40]} — 지점이 맞는지 확인해 주세요.")


def make_job(req: dict, *, project_dir: Path, out_root: Path, request_dir: Path | None = None,
             offline: bool = False, log=print, osrm: dict | None = None, job_dir: Path | None = None, extra: dict | None = None,
             geo: Geo | None = None, searcher=None) -> dict:
    """지도 데이터를 받아 작업 폴더를 만든다. 돌려주는 dict 의 'dir' 이 작업 폴더다.

    osrm    = 이미 받아 둔 경로(컷 나눔: 한 경로에서 잘라 낸 구간). 주면 지명 찾기·경로 받기를 건너뛰고 그 선을 그대로 그린다.
    job_dir = 작업 폴더를 정해 줄 때(컷 나눔의 컷1·컷2 …). extra = 화면에 그대로 넘길 값(다리 모양, 컷 사이의 움직임 등).
    geo     = 지도 받는 쪽을 넘겨줄 때(컷 나눔: 여러 컷이 한 의뢰의 몫 — 기다린 시간·받은 양 — 을 같이 쓴다).
    searcher = 지도에 없는 지명을 Liner 웹 검색으로 확인하는 함수(intake.searched_name). 없으면 띄어쓰기만 바꿔 다시 찾는다.
    """
    t0 = time.perf_counter()
    geo = geo or Geo(project_dir / "data" / "geo", offline=offline)
    out = req["output"]
    _, _, pw, ph = request.panel_rect(req["layout"], out["width"], out["height"])

    picked = None
    found_notes: list[str] = []     # Liner 검색으로 찾은 지명 · 들르는 곳의 알림
    marks: list[dict] = []          # 한 화면 지도의 들르는 곳(점 · 이름표 · 정차) → 화면의 J.stops
    stops: list[dict] = []          # 지도에서 찾은 들르는 곳(자리는 그려지는 선이 정해진 뒤에 잡는다)
    if osrm is None:
        log(f"지명 찾기: {req['from']['name']} → {req['to']['name']}")
        walk, boat = req["vehicle"] == "walk", req["vehicle"] == "boat"
        near, area_note = area_anchor(geo, req)         # 의뢰에 적힌 장소 — 지명을 그 둘레에서 먼저 찾는다
        find = _resolve_pier if boat else (lambda g, p: _resolve(g, p, near))      # 배는 선착장을 먼저 찾는다
        area = str((req.get("area") or {}).get("name") or "")
        a = resolve_or_search(geo, req["from"], find, searcher, (req["to"].get("name", ""), area), found_notes)
        b = resolve_or_search(geo, req["to"], find, searcher, (req["from"].get("name", ""), area), found_notes)
        if near and not boat:       # 장소 둘레에서 잡은 지점은 '서로 가까운 후보 쌍'으로 다시 고르지 않는다(둘 다 다른 지역으로 끌려간다)
            picked = " ".join(x for x in (area_note, a.get("near_note"), b.get("near_note")) if x) or None
        else:
            a, b, picked = near_pair(a, b)
            picked = " ".join(x for x in (area_note, picked) if x) or None
        stops, stop_notes = find_stops(geo, req, a, b, searcher) if req.get("stops") else ([], [])
        found_notes += stop_notes
        via = stop_waypoints(stops, a, b, found_notes)       # 들르는 곳을 차례로 지나는 경로(동떨어진 곳은 빼고 알린다)
        log("경로 받기(" + ("강 위 뱃길" if boat else "걸어서 가는 길" if walk else "실제 도로") + (f", 들르는 곳 {len(via)}곳" if via else "") + ")")
        if via:
            osrm = geo.water_route_via([a, *via, b]) if boat else geo.route_via([a, *via, b], foot=walk)
        else:
            osrm = geo.water_route(a, b) if boat else geo.route(a, b, foot=walk)
    else:
        a, b = ({**req[k], "display": "경로에서 잘라 낸 구간", "candidates": []} for k in ("from", "to"))
    # 지명으로 찾은 지점은 꼬인 고리를 정리하고, 좌표로 직접 지정한 지점은 그대로 둔다(뱃길은 도로의 꼬인 고리가 없다 — 정리하지 않는다)
    road = req["vehicle"] != "boat"
    plan = mapdata.plan_view(osrm, aspect=pw / ph, tidy_start=road and "lat" not in req["from"], tidy_end=road and "lat" not in req["to"])
    marks = stop_marks(stops, osrm, found_notes, plan) if stops else []      # 자리는 꼬인 고리를 잘라 낸 뒤의 선(plan) 위에서 잰다
    if marks:
        extra = {**(extra or {}), "stops": marks}
    view_w = plan["view"][2] - plan["view"][0]
    camera = follow_camera(req, osrm, view_w, pw)       # 따라가는 카메라: 바탕을 화면보다 zoom 배 크게 그리고 화면이 이동 아이콘을 따라간다
    ppm = pw / view_w * (camera["zoom"] if camera else 1)       # 실제로 그리는 크기(1m 가 몇 픽셀인지) — 도형의 자세함을 이것으로 정한다
    detail = ("foot" if req["vehicle"] == "walk" and ppm >= 0.6 else "minor") if ppm >= 0.25 else "major"
    log(f"지도 도형 받기(가로 {view_w / 1000:.1f}km, {DETAIL_LABELS[detail]})")
    osm = geo.osm(mapdata.view_bbox(plan), detail)
    data = mapdata.build(osm, plan, req["from"]["name"], req["to"]["name"], ppm)

    name = re.sub(r"[^0-9A-Za-z가-힣]+", "", f"{req['from']['name']}_{req['to']['name']}") or "job"
    art_style = req.get("art_style", request.DEFAULT_ART_STYLE)
    suffix = "" if art_style == request.DEFAULT_ART_STYLE else f"_{art_style}"
    job_dir = job_dir or out_root / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}_{req['layout']}{suffix}"
    job_dir.mkdir(parents=True, exist_ok=False)
    warnings: list[str] = [osm["_note"]] if osm.get("_note") else []
    if picked:
        warnings.append(picked)
    if req.get("camera") == "follow" and not camera:
        warnings.append("경로가 짧아 화면 하나에 다 들어오므로 카메라가 따라가지 않고 지도를 고정해 그렸습니다.")
    warnings += [(f"배가 서는 곳이라 '{p['name']}' 대신 '{p['searched']}' 을(를) 찾았습니다. 지점이 맞는지 확인해 주세요." if p.get("pier") else
                  f"'{p['name']}' 은(는) 지도에 그런 이름이 없어 '{p['searched']}'(으)로 찾았습니다. 지점이 맞는지 확인해 주세요.")
                 for p in (a, b) if p.get("searched")]
    warnings += found_notes
    warnings += [outside_note(p) for p in (a, b) if p.get("outside")]
    for label, m in zip(("출발", "도착"), plan["moved"]):
        if m:
            warnings.append(f"{label} 지점을 큰길 쪽으로 약 {m}m 옮겼습니다(길이 꼬여 도는 구간 정리). 정확한 지점이 필요하면 지점을 직접 지정해 주세요(메신저의 '지도에서 정확히 지정', 의뢰 봉투에서는 lat·lon).")
    faces =_faces(req["faces"], [d for d in (request_dir, project_dir) if d], job_dir, warnings)
    pic = req.get("vehicle_image") or (_liner_picture(req.get("vehicle"), offline, warnings, log) if req.get("vehicle_picture") == "liner" else None)
    vimg = _vehicle_image(pic, [d for d in (request_dir, project_dir) if d], job_dir, warnings)

    job = {
        "program": req["program"], "from": req["from"], "to": req["to"], "vehicle": req["vehicle"],
        "map_style": req["map_style"], "face_mode": req["face_mode"], "art_style": art_style,
        "duration_seconds": req["duration_seconds"], "layout": req["layout"],
        "layout_rect": list(request.LAYOUTS[req["layout"]]), "output": out, "faces": faces,
        # 뱃길은 경로 서버(OSRM)가 아니라 지도 데이터의 강 중심선으로 그린다 — 출처에 경로 서버를 적지 않는다
        "attribution": ATTRIBUTION.split(" · ")[0] if req["vehicle"] == "boat" else ATTRIBUTION,
        **{k: req[k] for k in ("vehicle_color", "vehicle_scale", "who", "face_scale") if k in req}, **(extra or {}),
        **({"vehicle_image": vimg} if vimg else {}),
        **({"camera": camera} if camera else {}),
    }
    route = osrm["routes"][0]
    # 화면의 '남은 거리'가 세는 거리 = 확인 카드에 나오는 도로 거리(실제 주행거리). 그린 선의 길이(map-data 의 total)와는 따로 둔다
    job["distance_m"] = round(route.get("distance", 0))
    (job_dir / "job.js").write_text(_js("JOB", job), encoding="utf-8")
    (job_dir / "map-data.js").write_text(_js("MAPDATA", data), encoding="utf-8")
    install_scene(job_dir, art_style, "route_move.html", (STAGE, *SHARED), "mapskin.js")

    record = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": request.private(req),      # 전화번호는 가린다(작업 폴더는 넘겨진다)
        "places": {"from": a, "to": b},
        "route": {"distance_m": round(route.get("distance", 0)), "drive_seconds": round(route.get("duration", 0)),
                  "drawn_m": data["total"]},
        # source = 바탕 지도가 어디서 왔는지: server 방금 받음 · cache 받아 둔 것 · pack 앱의 지도 묶음 ·
        #          cover(_major)·pack_major 대신 쓴 것(…_major 는 큰길만) · blank 바탕 없음
        "map": {"detail": detail, "source": osm.get("_source", ""), "pixels_per_meter": round(ppm, 4),
                "counts": {k: len(data[k]) for k in ("roads", "bridges", "water", "green", "rail", "labels")}},
        "warnings": warnings,
        "attribution": ATTRIBUTION,
        "timing": {"build_seconds": round(time.perf_counter() - t0, 1)},      # 지명 찾기부터 지도 한 벌이 나올 때까지
    }
    (job_dir / "job.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dir": job_dir, **record}
