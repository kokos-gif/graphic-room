"""완성 화면 검수 — 다 만든 화면을 Liner 가 읽고, 의뢰에 적힌 글자 · 숫자와 맞는지 코드가 대조한다.

의뢰서를 읽을 때의 감사(audit.py)는 '읽은 값'을 본다. 여기서는 그 값이 '화면에 그대로 나왔는지'를 본다 — 납품 직전의 마지막 확인이다.

나누어 맡는 일
- Liner(그림을 읽는 모델, 호출 1번): 화면에 보이는 글자를 그대로 옮겨 적는다. 판단하지 않는다.
- 코드: 옮겨 적은 글자를 작업 기록(job.json)의 값과 대조한다. 같고 다름은 여기서 정한다.
  (모델에게 '다른 곳을 찾아라'고 시키면 단위 표기 같은 것을 문제로 적는다 — 2026-11-20 실측. 그래서 대조는 코드가 한다.)
- 잘리거나 겹쳐 읽기 어려운 글자는 모델의 의견으로 따로 싣는다(opinions). 틀림으로 세지 않는다.

보는 화면
- 순위판: 마지막 화면. 순위 변동판이면 바뀌기 전 화면(영상의 50% 지점)과 마지막 화면 두 장.
- 지도: 마지막 화면(이름표 · 거리 숫자가 다 나온 때).
- 컷 나눔: 컷마다 끝 화면(4장까지).

대조하는 것
- 순위판: 제목, 줄마다 순위 · 이름 · 값(화면에 올린 글자 그대로).
- 지도: 출발지 · 도착지 이름표가 화면에 있는지, 화면의 거리 숫자(km)가 작업 기록과 0.1km 안에서 맞는지.
아직 안 보는 것('건너뜀'): 정보판(VS · 설명판 · 건물 안 이동) · 카드 퀴즈 · 네비 화면, 경유지 이름표, 출연자 얼굴 · 차량 그림.

check() 는 예외를 내지 않는다. 오프라인이거나 Liner 가 안 되면 '건너뜀' · '검수 못 함'으로 돌아간다 — 검수가 안 돼도 납품물은 그대로다.
메신저: 영상 내보내기가 끝나면 서버(server.Desk._proof)가 같은 의뢰 번호로 부르고, 결과 카드에 싣는다(2026-11-21).

혼자 돌려 보기:  python -m mapcg.proof <작업 폴더>      (완성 MP4 또는 이전 작업의 frames_panel 에서 읽는다)
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

from . import intake, proof_frames

MODEL = "liner-gpt-router"      # MAPCG_PROOF_MODEL 로 바꾼다. 그림이 든 요청은 라우터가 그림을 읽는 모델로 보낸다
MAX_SHOTS = 4                   # 한 번에 보내는 화면 수의 한도
KM_SLACK = 0.1                  # 화면의 거리 숫자와 작업 기록의 차이를 이만큼(km)까지 같다고 본다
SAVE_NAME = "화면검수.json"
WHY = ["잘림", "겹침", "너무 작음", "흐림"]

SYSTEM = """방송 그래픽의 완성 화면을 보고, 화면에 보이는 글자를 그대로 옮겨 적는 일이다. 판단하거나 고치지 않는다.
그림마다 다음을 적는다.
- title: 화면의 제목판에 적힌 글자. 없으면 빈 문자열.
- rows: 순위판일 때만. 위에서부터 줄마다 rank(순위 숫자) · name(이름) · value(값 글자 그대로, 단위 포함). 순위판이 아니면 빈 배열.
- texts: 화면에 보이는 그 밖의 모든 글자를 덩어리별로(이름표 · 거리 · 안내판 · 지도 위 지명 · 출처 표기). title 과 rows 에 적은 것은 다시 적지 않는다.
- hard_to_read: 화면 밖으로 잘렸거나, 다른 글자 · 그림과 겹쳤거나, 너무 작거나 흐려서 읽기 어려운 글자. 없으면 빈 배열.
보이는 그대로 적는다. 오타처럼 보여도 고치지 않는다. 안 보이는 글자를 짐작해 채우지 않는다. 띄어쓰기 · 쉼표 · 단위도 화면 그대로."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["shots"],
    "properties": {"shots": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["index", "title", "rows", "texts", "hard_to_read"],
        "properties": {
            "index": {"type": "integer"},
            "title": {"type": "string"},
            "rows": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["rank", "name", "value"],
                                                "properties": {"rank": {"type": "string"}, "name": {"type": "string"}, "value": {"type": "string"}}}},
            "texts": {"type": "array", "items": {"type": "string"}},
            "hard_to_read": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text", "why"],
                                                        "properties": {"text": {"type": "string"}, "why": {"type": "string", "enum": WHY}}}},
        }}}},
}


# ---------------------------------------------------------------- 글자 맞대기

def _norm(s) -> str:
    """대조용: 전각 · 반각을 맞추고 빈칸을 뺀다."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s if s is not None else "")))


def same_value(want, seen) -> bool:
    """값 글자가 같은가. 쉼표 · 빈칸은 무시한다. 숫자가 같고 한쪽에만 단위가 붙은 것은 같다고 본다(모델이 단위를 빼고 옮겨 적는 일이 있다)."""
    a, b = _norm(want).replace(",", ""), _norm(seen).replace(",", "")
    if a == b:
        return True
    da, db = re.sub(r"[^\d.\-]", "", a), re.sub(r"[^\d.\-]", "", b)
    if not da or da != db:
        return False
    ra, rb = re.sub(r"[\d.\-]", "", a), re.sub(r"[\d.\-]", "", b)
    return not ra or not rb


def _rank(s) -> int | None:
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else None


def compare_board(want: dict, seen: dict) -> list[dict]:
    """순위판 한 장: 제목과 줄을 맞대어 다른 곳을 돌려준다. [{kind, where, want, seen}]"""
    out = []
    if want.get("title") and _norm(want["title"]) != _norm(seen.get("title")):
        out.append({"kind": "제목이 다름", "where": "제목", "want": want["title"], "seen": seen.get("title") or ""})
    rows = [r for r in seen.get("rows") or [] if isinstance(r, dict)]
    by_rank: dict[int, dict] = {}
    for n, r in enumerate(rows, 1):
        k = _rank(r.get("rank"))
        by_rank.setdefault(k if k is not None else n, r)
    spare = dict(by_rank)
    tied = len({w.get("rank") for w in want["rows"]}) != len(want["rows"])       # 공동 순위가 있으면 순위로 못 맞춘다 — 놓인 차례로 맞춘다
    for n, w in enumerate(want["rows"], 1):
        key = n if tied else w.get("rank", n)
        got = (rows[n - 1] if n <= len(rows) else None) if tied else spare.pop(key, None)
        where = f"{w.get('rank', n)}위"
        if got is None:
            out.append({"kind": "빠진 줄", "where": where, "want": f"{w['name']} {w['value']}".strip(), "seen": ""})
            continue
        if _norm(w["name"]) != _norm(got.get("name")):
            out.append({"kind": "이름이 다름", "where": where + " 이름", "want": w["name"], "seen": got.get("name") or ""})
        if w.get("value") and not same_value(w["value"], got.get("value")):
            out.append({"kind": "값이 다름", "where": where + " 값", "want": w["value"], "seen": got.get("value") or ""})
    extra = rows[len(want["rows"]):] if tied else list(spare.values())
    for r in extra:
        out.append({"kind": "남는 줄", "where": f"{r.get('rank') or '?'}위", "want": "", "seen": f"{r.get('name', '')} {r.get('value', '')}".strip()})
    return out


def compare_map(want: dict, seen: dict) -> list[dict]:
    """지도 한 장: 꼭 있어야 하는 이름표가 화면에 있는지, 화면의 거리 숫자가 맞는지."""
    out = []
    raw = [str(t) for t in [seen.get("title") or "", *(seen.get("texts") or [])] if str(t).strip()]
    bits = [_norm(t) for t in raw]
    heap = "\n".join(bits)
    # 이름표는 글자 덩어리 하나와 통째로 같아야 한다. 제목판의 '출발 → 도착'은 화살표에서 나눠 본다.
    # (일부만 겹치는 것을 같다고 보면 '여의나루'를 지도 위의 '여의나루역'으로 통과시킨다 — 2026-11-20 실측)
    whole = {p for b in bits for p in [b, *re.split(r"→|->|➜|➔|▶|⇒|~", b)] if p}
    for it in want.get("texts") or []:
        name = _norm(it["text"])
        if name and name not in whole:
            near = [t for t, b in zip(raw, bits) if name in b or (len(b) >= 2 and b in name)]
            out.append({"kind": "이름표가 안 보임", "where": it["what"], "want": it["text"], "seen": " · ".join(near[:3])})
    km = want.get("km")
    if km is not None:
        found = [float(m) for m in re.findall(r"(\d+(?:\.\d+)?)km", heap, flags=re.I)]
        if found and not any(abs(f - km) <= KM_SLACK + 1e-9 for f in found):
            out.append({"kind": "거리 숫자가 다름", "where": "거리", "want": f"{km:.1f}km", "seen": " · ".join(f"{f:g}km" for f in found)})
    return out


# ---------------------------------------------------------------- 무엇을 볼지

def _load(path: Path) -> dict:
    got = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(got, dict):
        raise ValueError("작업 기록의 모양이 다릅니다")
    return got


def _board_want(title, rows) -> dict:
    return {"title": str(title or ""), "rows": [{"rank": r.get("rank"), "name": str(r.get("name") or ""),
                                                 "value": str(r.get("show") or r.get("value_text") or "")} for r in rows or []]}


def _map_want(job: dict) -> dict:
    req, route = job.get("request") or {}, job.get("route") or {}
    texts = [{"text": str((req.get(k) or {}).get("name") or ""), "what": what} for k, what in (("from", "출발지 이름표"), ("to", "도착지 이름표"))]
    # 화면의 거리 숫자는 실제 주행거리(distance_m)다 — 그린 선의 길이(drawn_m)가 아니다(route_move.html · style_v5.js 와 같게)
    m = route.get("distance_m") or route.get("drawn_m")
    return {"texts": [t for t in texts if t["text"]], "km": round(float(m) / 1000, 1) if isinstance(m, (int, float)) and m > 0 else None}


class Skip(ValueError):
    """이 종류는 화면 검수를 하지 않는다 — 고장이 아니라 '건너뜀'이다."""


# 아직 보지 않는 종류. 이름은 각 모듈의 값과 같다(plan.KIND · quiz.KIND · navi.STYLE — 무거운 모듈을 불러오지 않으려고 글자로 둔다. 시험이 맞는지 본다)
SKIP_KINDS = {"plan": "정보판", "card_quiz": "카드 퀴즈", "scene": "맞춤 그래픽"}
NAVI_STYLE = "navi_screen"


def shots(job_dir: Path) -> tuple[list[dict], list[str]]:
    """그 작업에서 볼 화면들. ([{label, frame, kind 'board'|'map', want}], notes).
    볼 것이 없으면 ValueError(사유). 아직 보지 않는 종류(정보판 · 카드 퀴즈 · 네비 화면)면 Skip(사유)."""
    job_dir = Path(job_dir)
    job = _load(job_dir / "job.json")
    req, notes = job.get("request") or {}, []
    kind = job.get("kind") or req.get("kind")
    if kind == "scene":     # 시안 단계에서 담당이 확인할 점마다 Liner 그림 읽기 검수를 받았다(같은 장면 · 같은 그림 코드라 영상 화면도 같다)
        raise Skip("맞춤 그래픽은 시안 단계에서 담당이 확인할 점마다 Liner 검수를 받았습니다 — 카드의 검수 결과를 봐 주세요.")
    if kind in SKIP_KINDS or req.get("map_style") == NAVI_STYLE:
        raise Skip(f"이 종류({SKIP_KINDS.get(kind) or '네비 화면'})는 아직 화면 검수를 하지 않습니다.")
    frames = proof_frames.for_job(job_dir, job)
    if not frames:
        raise ValueError("화면이 없습니다 — 영상을 먼저 만들어 주세요.")
    n = len(frames)
    if kind == "rank_board":
        board, nxt = job.get("board") or {}, req.get("next") or {}
        if board.get("next_rows"):
            return [{"label": "바뀌기 전", "frame": frames[min(n - 1, round(n * .50))], "kind": "board", "want": _board_want(req.get("title"), board.get("rows"))},
                    {"label": "바뀐 뒤", "frame": frames[-1], "kind": "board", "want": _board_want(nxt.get("title") or req.get("title"), board["next_rows"])}], notes
        return [{"label": "순위판", "frame": frames[-1], "kind": "board", "want": _board_want(req.get("title"), board.get("rows") or req.get("rows"))}], notes
    if kind == "cuts":
        cuts = [c for c in job.get("cuts") or [] if isinstance(c, dict)]
        spans = [float(c.get("duration_seconds") or 0) for c in req.get("cuts") or []]
        if not cuts or len(spans) != len(cuts) or sum(spans) <= 0:
            raise ValueError("컷 나눔 작업 기록에서 컷 길이를 찾지 못했습니다.")
        out, run, start = [], 0.0, 0
        for k, (c, d) in enumerate(zip(cuts, spans), 1):
            run += d
            end = min(n, int(n * run / sum(spans) + .5))       # 반은 올린다(내보내기의 프레임 수 세기와 같게)
            sub = _load(job_dir / str(c.get("dir") or f"컷{k}") / "job.json")
            out.append({"label": f"컷 {k}", "frame": frames[max(start, end - 2)], "kind": "map", "want": _map_want(sub)})      # 끝에서 한 장 앞: 경계 반올림에 걸리지 않게
            start = end
        if len(out) > MAX_SHOTS:
            keep = sorted({round(i * (len(out) - 1) / (MAX_SHOTS - 1)) for i in range(MAX_SHOTS)})
            notes.append(f"컷이 {len(out)}개라 {MAX_SHOTS}개만 봤습니다: " + " · ".join(out[i]["label"] for i in keep))
            out = [out[i] for i in keep]
        return out, notes
    if kind in (None, "map") or req.get("map_style"):
        return [{"label": "지도", "frame": frames[-1], "kind": "map", "want": _map_want(job)}], notes
    raise Skip(f"이 종류({kind})는 아직 화면 검수를 하지 않습니다.")


# ---------------------------------------------------------------- Liner 에 읽히기

def picture(path: Path | proof_frames.VideoFrame) -> str:
    """보낼 그림(data URL). 큰 화면은 가로 1280 으로 줄이고, 작은 시험 화면(세로 540 미만)은 글자가 읽히게 키운다."""
    raw = path.read_bytes() if isinstance(path, proof_frames.VideoFrame) else Path(path).read_bytes()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        k = 1280 / im.width if im.width > 1280 else 540 / im.height if im.height < 540 else 1
        if k != 1:
            im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except ImportError:
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def read(items: list[dict], chat=None) -> tuple[dict[int, dict], str]:
    """화면들을 Liner 에 읽힌다(호출 1번). ({그림 번호(1부터): 옮겨 적은 것}, 답한 모델)."""
    liner = intake._liner()
    chat = chat or liner.chat
    content: list[dict] = [{"type": "text", "text": f"그림 {len(items)}장이다. 차례대로 index 1부터 적는다. " +
                            " / ".join(f"그림 {i}: {'순위판' if s['kind'] == 'board' else '지도'}" for i, s in enumerate(items, 1))}]
    content += [{"type": "image_url", "image_url": {"url": picture(s["frame"])}} for s in items]
    resp = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
                model=os.environ.get("MAPCG_PROOF_MODEL", "").strip() or MODEL, routing_strategy="quality",
                max_completion_tokens=1500 + 1500 * len(items),
                response_format={"type": "json_schema", "json_schema": {"name": "screen_proof", "strict": True, "schema": SCHEMA}})
    choice = resp["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("모델의 답이 길이 한도에서 잘렸습니다")
    got = intake._parse_json(choice["message"]["content"])
    seen: dict[int, dict] = {}
    for n, s in enumerate((got or {}).get("shots") or [], 1):
        if isinstance(s, dict):
            i = s.get("index") if isinstance(s.get("index"), int) and 1 <= s["index"] <= len(items) and s["index"] not in seen else n
            seen.setdefault(i, s)
    return seen, str(resp.get("model") or "")


def check(job_dir, *, offline: bool = False, chat=None, save: bool = True) -> dict:
    """작업 폴더의 완성 화면을 검수한다. 예외를 내지 않는다.

    돌려주는 것: {'state': '같음' | '다른 곳 있음' | '검수 못 함' | '건너뜀', 'note': 사유, 'problems': [{shot, kind, where, want, seen}],
                 'opinions': [{shot, text, why}] 읽기 어려운 글자(모델의 의견), 'shots': [{label, frame}], 'model', 'seconds', 'notes'}
    '다른 곳 있음'은 화면을 고치라는 뜻이 아니다 — 모델이 잘못 읽었을 수도 있으니 사람이 그 자리를 한 번 보라는 뜻이다.
    """
    job_dir, t0 = Path(job_dir), time.perf_counter()
    out = {"state": "건너뜀", "note": "", "problems": [], "opinions": [], "shots": [], "model": "", "seconds": 0.0, "notes": [],
           "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def done(state, note=""):
        out.update(state=state, note=note, seconds=round(time.perf_counter() - t0, 1))
        if save and state != "건너뜀":
            try:
                (job_dir / SAVE_NAME).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
        return out

    if offline:
        return done("건너뜀", "오프라인이라 화면 검수를 하지 않았습니다.")
    try:
        items, out["notes"] = shots(job_dir)
    except Skip as e:
        return done("건너뜀", str(e)[:200])
    except (OSError, ValueError) as e:
        return done("검수 못 함", str(e)[:200])
    out["shots"] = [{"label": s["label"], "frame": s["frame"].name} for s in items]
    try:
        seen, out["model"] = read(items, chat)
    except Exception as e:      # 키 · 잔액 · 접속 · 잘린 답 — 무엇이든 납품은 막지 않는다
        return done("검수 못 함", f"Liner 가 화면을 읽지 못했습니다({intake.explain(e)[:160]}).")      # 영어 오류문 · 응답 본문을 그대로 싣지 않는다
    for i, s in enumerate(items, 1):
        got = seen.get(i)
        if not got:
            out["problems"].append({"shot": s["label"], "kind": "읽지 못함", "where": "화면 전체", "want": "", "seen": ""})
            continue
        for p in (compare_board if s["kind"] == "board" else compare_map)(s["want"], got):
            out["problems"].append({"shot": s["label"], **p})
        for h in got.get("hard_to_read") or []:
            if isinstance(h, dict) and str(h.get("text") or "").strip():
                out["opinions"].append({"shot": s["label"], "text": str(h["text"])[:60], "why": h.get("why") if h.get("why") in WHY else "흐림"})
    return done("다른 곳 있음" if out["problems"] else "같음")


def lines(result: dict) -> list[str]:
    """사람이 읽는 줄로."""
    out = [f"화면 검수: {result['state']}" + (f" — {result['note']}" if result.get("note") else "") +
           (f" ({' · '.join(s['label'] for s in result['shots'])} · {result['seconds']}초 · {result['model']})" if result.get("shots") and result.get("model") else "")]
    for p in result.get("problems") or []:
        out.append(f"  [{p['shot']}] {p['kind']} · {p['where']}: 의뢰 '{p['want']}' ↔ 화면 '{p['seen']}'")
    for o in result.get("opinions") or []:
        out.append(f"  [{o['shot']}] 읽기 어려움({o['why']}): {o['text']}  ← Liner 의 의견")
    out += [f"  - {n}" for n in result.get("notes") or []]
    return out


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [a for a in args if not a.startswith("--")]
    if len(paths) != 1:
        print("쓰는 법: python -m mapcg.proof <작업 폴더> [--offline]")
        return 2
    result = check(paths[0], offline="--offline" in args)
    print("\n".join(lines(result)))
    return 0 if result["state"] in ("같음", "건너뜀") else 1


if __name__ == "__main__":
    raise SystemExit(main())
