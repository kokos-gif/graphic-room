"""의뢰 봉투 — 의뢰서 분석이 채우고 지도 제작이 읽는 자료 구조 하나.

의뢰서(JPG·PDF)를 읽는 단계는 이 모양의 dict 를 만들어 넘기면 된다. 지도 제작은 이것만 본다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 지도가 화면에서 차지하는 자리: (x, y, w, h) — 전체 화면에 대한 비율
LAYOUTS = {
    "full": (0.0, 0.0, 1.0, 1.0),
    "bottom_half": (0.0, 0.5, 1.0, 0.5),
    "bottom_left_quarter": (0.0, 0.5, 0.5, 0.5),
    "bottom_right_quarter": (0.5, 0.5, 0.5, 0.5),
}
LAYOUT_LABELS = {
    "full": "전체 화면",
    "bottom_half": "하단 1/2",
    "bottom_left_quarter": "좌하단 1/4",
    "bottom_right_quarter": "우하단 1/4",
}
# 코드로 그리는 지도 형식. 레퍼런스 분석이 끝나면 여기에 늘린다.
# nav = 같은 그림에 남은 거리 표시가 붙는다 · navi_screen = 휴대폰 길안내 화면 모양(초록 안내판 · 차량 화살표 · 목적지 카드, navi.py)
STYLES = {"route_move": "경로 이동 지도", "nav": "네비 지도", "navi_screen": "네비 화면"}
DEFAULT_ART_STYLE = "claude"       # 화풍을 적지 않은 봉투의 뜻(옛 봉투 · 예시) — 저장된 의뢰가 같게 다시 그려지도록 바꾸지 않는다
# 방송 화풍: 새 의뢰의 확인 카드가 고른 채로 시작하는 화풍 — 지도 · 컷 · 순위판 · 정보판 · 카드 퀴즈 · 맞춤 그래픽이 모두 따른다.
# 화풍을 통째로 바꿀 때(V6 …)는 화풍 파일을 더한 뒤 이 한 줄만 고친다(README '새 화풍 더하기')
HOUSE_STYLE = "v5"
# 화풍. 키는 저장된 의뢰와 맞추려고 그대로 두고(claude·codex·codex_v3), 화면에는 판 번호와 특징으로 보여 준다
ART_STYLES = {
    "claude": "V1 · 물감과 붓펜",
    "codex": "V2 · 거친 마른붓",
    "codex_v3": "V3 · 붓터치",
    "v4": "V4 · 예능 그래픽",
    "v5": "V5 · 방송 화면(새 설계)",
}
VEHICLES = {"bus": "버스", "car": "승용차", "suv": "SUV", "dot": "점", "walk": "사람(걸어서)", "boat": "배(강 위 뱃길)"}     # boat = 한강버스·유람선 — 도로가 아니라 강 중심선을 따라간다
# 이동 아이콘을 무엇으로 그릴지: Liner 그림 모델이 그린 그림(만들 때 그린다 · 한 번 그린 것은 보관해 다시 쓴다) · 앱이 코드로 그린 아이콘
PICTURES = {"liner": "Liner 그림 모델로 그린 그림", "code": "코드로 그린 아이콘"}
# 그림으로 그릴 때 그림 모델에게 주는 물건 이름(고정 — 같은 아이콘은 보관한 그림을 다시 쓴다). 걷기 · 점은 그림으로 그리지 않는다
VEHICLE_SUBJECTS = {"boat": "a white river water bus ferry boat, side view facing right", "bus": "a city bus, side view facing right",
                    "car": "a small passenger car, side view facing right", "suv": "an SUV car, side view facing right"}
# 카메라: fixed 경로 전체를 한 화면에 고정 · follow 이동 아이콘을 가까이서 따라간다(지도가 같이 움직인다)
CAMERAS = {"fixed": "지도 고정", "follow": "따라가기"}
# 얼굴을 어디에 그릴지. ride 는 이동 아이콘이 버스·배일 때만 통하고(창문에 태운다), 아니면 above 로 그린다.
FACE_MODES = {"above": "아이콘 위에 나란히", "ride": "버스·배에 태우기"}

MAX_FACES = 8
# 이동 아이콘의 색을 말로 적었을 때('빨간 버스'). 그 밖에는 #rrggbb 로 적는다
COLOR_NAMES = {"red": "#e8443a", "빨강": "#e8443a", "빨간색": "#e8443a", "black": "#2b2b33", "검정": "#2b2b33", "검정색": "#2b2b33",
               "blue": "#3d7be0", "파랑": "#3d7be0", "파란색": "#3d7be0", "yellow": "#ffd23f", "노랑": "#ffd23f", "노란색": "#ffd23f",
               "green": "#3fae6a", "초록": "#3fae6a", "초록색": "#3fae6a", "white": "#f6f3ea", "흰색": "#f6f3ea", "하양": "#f6f3ea"}


class RequestError(ValueError):
    """의뢰 봉투에 빠졌거나 틀린 항목. problems 에 전부 모아 한 번에 알려 준다."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__(" / ".join(self.problems))


def _place(raw: dict, key: str, problems: list[str]) -> dict:
    label = "출발지" if key == "from" else "도착지"
    v = raw.get(key)
    if isinstance(v, str):
        v = {"name": v}
    if not isinstance(v, dict) or not str(v.get("name", "")).strip():
        problems.append(f"{label}({key}.name)가 없습니다.")
        return {"name": ""}
    out = {"name": str(v["name"]).strip()}
    # 좌표를 직접 주면 지명 찾기를 건너뛰고 그 지점에서 출발·도착한다(정확한 지점이 필요할 때).
    if "lat" in v or "lon" in v:
        try:
            lat, lon = float(v["lat"]), float(v["lon"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{label} 좌표는 lat·lon 숫자 두 개가 다 있어야 합니다.")
        else:
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                out.update(lat=lat, lon=lon)
                if isinstance(v.get("display"), str) and v["display"].strip():      # 앞서 찾은 곳의 설명(다시 만들기에서 그대로 보인다)
                    out["display"] = v["display"].strip()[:160]
            else:
                problems.append(f"{label} 좌표가 범위를 벗어났습니다.")
    return out


def art_style(raw: dict, problems: list[str]) -> str:
    """화풍을 지정하지 않은 기존 의뢰는 Claude 화풍으로 그린다."""
    value = raw.get("art_style", DEFAULT_ART_STYLE)
    if not isinstance(value, str) or value not in ART_STYLES:
        problems.append(f"화풍(art_style)은 {', '.join(ART_STYLES)} 중에서 골라 주세요.")
        return DEFAULT_ART_STYLE
    return value


def duration(raw: dict, problems: list[str]) -> float:
    """영상 길이(초). 지도와 정보판이 같은 규칙을 쓴다."""
    try:
        dur = float(raw.get("duration_seconds"))
    except (TypeError, ValueError):
        problems.append("영상 길이(duration_seconds)가 없습니다.")
        return 0.0
    if not 1 <= dur <= 60:
        problems.append("영상 길이(duration_seconds)는 1~60초여야 합니다.")
    return dur


def output_spec(raw: dict, problems: list[str]) -> dict:
    """출력 규격(전체 프레임의 가로·세로·fps). 지도와 정보판이 같은 규칙을 쓴다."""
    o = raw.get("output") or {}
    try:
        out = {"width": int(o.get("width", 1920)), "height": int(o.get("height", 1080)), "fps": int(o.get("fps", 30))}
    except (TypeError, ValueError):
        problems.append("출력 규격(output)은 width·height·fps 정수여야 합니다.")
        out = {"width": 1920, "height": 1080, "fps": 30}
    if not (320 <= out["width"] <= 3840 and 180 <= out["height"] <= 2160 and 1 <= out["fps"] <= 60):
        problems.append("출력 규격(output)이 범위를 벗어났습니다(가로 320~3840, 세로 180~2160, fps 1~60).")
    return out


def normalize(raw: dict) -> dict:
    """빠진 값에 기본값을 채우고 틀린 값을 모아 알린다. 원본은 고치지 않는다."""
    if not isinstance(raw, dict):
        raise RequestError(["의뢰 봉투는 JSON 객체여야 합니다."])
    problems: list[str] = []
    req = {
        "schema_version": 1,
        "art_style": art_style(raw, problems),
        "program": str(raw.get("program", "")).strip(),
        "text": str(raw.get("text", "")).strip(),
        "requester": {k: str((raw.get("requester") or {}).get(k, "")).strip() for k in ("name", "role", "phone")},
        "from": _place(raw, "from", problems),
        "to": _place(raw, "to", problems),
    }

    style = raw.get("map_style", "route_move")
    if style not in STYLES:
        problems.append(f"지도 형식(map_style) '{style}' 은 아직 없습니다. 가능: {', '.join(STYLES)}")
    req["map_style"] = style

    layout = raw.get("layout", "full")
    if layout not in LAYOUTS:
        problems.append(f"배치(layout) '{layout}' 은 없습니다. 가능: {', '.join(LAYOUTS)}")
    req["layout"] = layout

    vehicle = raw.get("vehicle", "bus")
    if vehicle not in VEHICLES:
        problems.append(f"이동 아이콘(vehicle) '{vehicle}' 은 없습니다. 가능: {', '.join(VEHICLES)}")
    req["vehicle"] = vehicle

    req["duration_seconds"] = duration(raw, problems)
    req["output"] = output_spec(raw, problems)

    faces = raw.get("faces") or []
    if not isinstance(faces, list) or not all(isinstance(f, str) for f in faces):
        problems.append("얼굴(faces)은 파일 경로 목록이어야 합니다.")
        faces = []
    if len(faces) > MAX_FACES:
        problems.append(f"얼굴(faces)은 {MAX_FACES}장까지입니다.")
    req["faces"] = faces

    mode = raw.get("face_mode", "above")
    if mode not in FACE_MODES:
        problems.append(f"얼굴 위치(face_mode) '{mode}' 은 없습니다. 가능: {', '.join(FACE_MODES)}")
    req["face_mode"] = mode

    # 이동 아이콘의 색과 크기(안 주면 화풍의 색, 기본 크기)
    color = raw.get("vehicle_color")
    if color not in (None, ""):
        color = COLOR_NAMES.get(str(color).strip().lower(), str(color).strip())
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            req["vehicle_color"] = color.lower()
        else:
            problems.append(f"이동 아이콘의 색(vehicle_color)은 #rrggbb 나 색 이름({', '.join(sorted(set(COLOR_NAMES)))})으로 적어 주세요.")
    if raw.get("vehicle_scale") is not None:
        try:
            scale = float(raw["vehicle_scale"])
            if not 0.5 <= scale <= 3:
                raise ValueError
            req["vehicle_scale"] = scale
        except (TypeError, ValueError):
            problems.append("이동 아이콘의 크기(vehicle_scale)는 0.5~3 의 숫자여야 합니다.")
    # 그림 이동 아이콘: 코드로 그린 버스·배 대신 쓸 투명 PNG(예: Liner 그림 모델로 그린 배). 그림은 오른쪽을 보고 있어야 한다
    if raw.get("vehicle_image") not in (None, ""):
        if isinstance(raw["vehicle_image"], str) and raw["vehicle_image"].lower().endswith(".png"):
            req["vehicle_image"] = raw["vehicle_image"]
        else:
            problems.append("이동 아이콘 그림(vehicle_image)은 PNG 파일 경로여야 합니다.")
    # 그림 파일 대신 '만들 때 Liner 그림 모델로 그려라'(수정 지시 · 확인 카드). 그림 파일을 준 의뢰는 그 파일을 쓴다
    if raw.get("vehicle_picture") not in (None, "", "code"):
        if raw["vehicle_picture"] != "liner":
            problems.append("이동 아이콘 그림 방식(vehicle_picture)은 liner 또는 code 여야 합니다.")
        elif not req.get("vehicle_image"):
            req["vehicle_picture"] = "liner"
    # 얼굴 크기 배율(태우기·위에 나란히 모두)
    if raw.get("face_scale") is not None:
        try:
            fs = float(raw["face_scale"])
            if not 0.5 <= fs <= 2:
                raise ValueError
            if fs != 1:
                req["face_scale"] = fs
        except (TypeError, ValueError):
            problems.append("얼굴 크기(face_scale)는 0.5~2 의 숫자여야 합니다.")

    camera = raw.get("camera") or "fixed"
    if camera not in CAMERAS:
        problems.append(f"카메라(camera) '{camera}' 은 없습니다. 가능: {', '.join(CAMERAS)}")
    elif camera != "fixed":         # 고정이 기본이다 — 기본값은 봉투에 적지 않는다(예전 의뢰와 같은 모양)
        req["camera"] = camera
    area = raw.get("area")
    if isinstance(area, str):
        area = {"name": area}
    if isinstance(area, dict) and str(area.get("name") or "").strip():      # 지점들이 다 그 안에 있는 장소(공원 등)
        req["area"] = {"name": str(area["name"]).strip()[:40]}
    if str(raw.get("who") or "").strip():       # 이동 아이콘에 적을 사람·팀(걸어서 가는 사람 표시)
        req["who"] = str(raw["who"]).strip()[:12]
    stops = route_stops(raw.get("stops"), req["duration_seconds"], problems)
    if stops:
        req["stops"] = stops

    if problems:
        raise RequestError(problems)
    return req


MAX_STOPS = 6       # 한 화면 지도의 들르는 곳
MOVE_MIN = 2.0      # 정차를 다 빼고도 이만큼(초)은 움직여야 한다


def route_stops(raw, duration, problems: list[str]) -> list[dict]:
    """한 화면 지도에서 출발지와 도착지 사이에 지나거나 들르는 곳(차례대로). 지도에 점과 이름표를 놓고 경로가 차례로 지난다.
    [{'name', 'label'(화면 이름표 — 상호를 가릴 때 등, 없으면 name), 'caption'(이름표 아래 설명), 'pass'(지나가기만), 'hold'(그곳에서 멈추는 초 — 정차),
      'lat'·'lon'(지도에서 찍은 지점)}]"""
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        problems.append("들르는 곳(stops)은 목록이어야 합니다.")
        return []
    if len(raw) > MAX_STOPS:
        problems.append(f"들르는 곳(stops)은 {MAX_STOPS}곳까지입니다.")
    out: list[dict] = []
    for n, s in enumerate(raw[:MAX_STOPS], 1):
        if not isinstance(s, dict) or not str(s.get("name") or "").strip():
            problems.append(f"들르는 곳 {n}번의 이름을 적어 주세요.")
            continue
        st = {"name": str(s["name"]).strip()[:30], "caption": str(s.get("caption") or "").strip()[:30], "pass": s.get("pass") is True}
        if str(s.get("label") or "").strip():      # 화면 이름표(지도에서는 name 으로 찾는다)
            st["label"] = str(s["label"]).strip()[:30]
        if s.get("hold") not in (None, "", 0):
            try:
                hold = float(s["hold"])
                if not 0 <= hold <= 10:
                    raise ValueError
                if hold and not st["pass"]:     # 지나가기만 하는 곳에서는 멈추지 않는다
                    st["hold"] = round(hold, 2)
            except (TypeError, ValueError):
                problems.append(f"들르는 곳 {n}번('{st['name']}')의 정차 시간(hold)은 0~10 초여야 합니다.")
        if s.get("lat") is not None or s.get("lon") is not None:        # 지도에서 찍은 지점
            try:
                lat, lon = float(s["lat"]), float(s["lon"])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError
                st.update(lat=lat, lon=lon)
            except (TypeError, ValueError, KeyError):
                problems.append(f"들르는 곳 {n}번('{st['name']}')의 지점(lat·lon)이 숫자가 아닙니다.")
        out.append(st)
    held = sum(st.get("hold", 0) for st in out)
    if isinstance(duration, (int, float)) and held and duration - held < MOVE_MIN:
        problems.append(f"정차 시간을 다 더하면 {held:g}초라 영상 {duration:g}초 안에 움직일 시간이 {MOVE_MIN:g}초도 남지 않습니다 — 정차를 줄이거나 길이를 늘려 주세요.")
    return out


# 전화번호(휴대전화 · 지역번호): 010-1234-5678 · 02 123 4567 · 01012345678
_PHONE = re.compile(r"(?<!\d)(0\d{1,2})[-\s.]?(\d{3,4})[-\s.]?(\d{4})(?!\d)")


def private(value):
    """작업 기록(job.json)에 남길 의뢰 — 전화번호는 가운데를 가린다(010-****-5678). 의뢰 글(text) 속 번호도.

    작업 폴더는 납품하며 다른 사람에게 넘어간다. 이름 · 역할은 남긴다(누가 의뢰했는지는 납품 기록에 필요하다).
    메신저 대화방 · 확인 카드의 의뢰는 그대로다(제작진 자기 기록). 받은 값은 바꾸지 않고 새 값을 돌려준다.
    """
    if isinstance(value, str):
        return _PHONE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", value)
    if isinstance(value, dict):
        return {k: private(v) for k, v in value.items()}
    if isinstance(value, list):
        return [private(v) for v in value]
    return value


def load(path: str | Path) -> dict:
    return normalize(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def panel_rect(layout: str, width: int, height: int) -> tuple[int, int, int, int]:
    """지도 자리의 픽셀 사각형 (x, y, w, h)."""
    x, y, w, h = LAYOUTS[layout]
    return round(x * width), round(y * height), round(w * width), round(h * height)
