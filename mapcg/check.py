"""Liner 연결 점검 — 키를 넣은 뒤 한 번 돌려, 이 앱이 쓰는 호출이 실제로 되는지 단계별로 확인한다.

  python -m mapcg check                      글·정해진 틀·그림·감사 모델을 차례로 불러 본다(아주 짧은 호출 몇 번)
  python -m mapcg check --image 의뢰지.jpg   그 의뢰지를 앱과 같은 길(지도 읽기 → 아니면 순위판 읽기)로 실제로 읽혀 본다

키는 화면에 찍지 않는다. 단계마다 됨/안 됨과 걸린 시간, 안 되면 까닭을 한 줄로 적는다.
"""
from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path

from . import audit, board, intake

TEXT = "가상 예능 〈오늘도 출발〉 김가상 조연출입니다. 홍대에서 여의도로 버스 이동, 좌하단, 8초, 출연진 4명."
TABLE = "점수 순위판 부탁드립니다. 제목: 가상 게임 점수\n가람 120점\n나래 95점"


def data_url(path: Path, side: int = 2000) -> str:
    """그림 파일을 앱의 화면이 보내는 것과 같은 모양(긴 변 2000 이하의 JPEG data URL)으로 바꾼다."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")       # 전화기 사진의 돌림 정보를 반영한다
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def sample_image() -> str:
    """글자가 적힌 작은 시험 그림(같이 실은 글꼴로 그린다) — 모델이 그림 속 한글을 읽는지 볼 때 쓴다."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(str(Path(__file__).resolve().parent / "templates" / "fonts" / "Pretendard-Bold.otf"), 44)
    im = Image.new("RGB", (900, 360), "#ffffff")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(("지도 의뢰서", "출발: 노들섬   도착: 여의도", "버스 이동 · 전체 화면 · 10초", "의뢰자: 이가상 PD")):
        d.text((40, 30 + i * 78), line, fill="#111111", font=font)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _step(name: str, fn, log) -> bool:
    t0 = time.perf_counter()
    try:
        detail = fn()
    except Exception as e:      # 단계 하나가 안 돼도 다음 단계를 본다
        log(f"  [안 됨] {name} — {intake.explain(e)} ({time.perf_counter() - t0:.1f}초)")
        return False
    log(f"  [됨]   {name} — {detail} ({time.perf_counter() - t0:.1f}초)")
    return True


def run(image: Path | None = None, log=print) -> int:
    """점검을 돌린다. 돌려주는 값: 0 다 됨 · 1 안 되는 단계가 있음 · 2 키가 없음."""
    liner = intake._liner()
    env = Path(liner.__file__).resolve().parent / ".env"
    if not intake.has_key():
        log("Liner 키가 없습니다.")
        log(f"  1) {env} 파일을 메모장으로 엽니다(없으면 같은 폴더의 .env.example 을 .env 로 복사합니다).")
        log("  2) LINER_API_KEY= 뒤에 키를 붙여 넣고 저장합니다. 키 발급: https://platform.liner.com/keys")
        log("  3) 이 명령을 다시 실행합니다:  python -m mapcg check")
        return 2
    model = os.environ.get("LINER_MODEL") or liner.DEFAULT_MODEL
    log(f"Liner 연결 점검 — 읽기 모델 {model}")
    seen: dict = {}

    def plain():
        r = liner.chat([{"role": "user", "content": "한 단어로만 답하세요: 연결"}], max_completion_tokens=200, retries=1, timeout=60)
        seen["model"] = r.get("model")
        return f"응답한 모델 {r.get('model') or '(응답에 모델 이름 없음)'} · 답 {str(r['choices'][0]['message'].get('content') or '').strip()[:20]!r}"

    def schema():
        # 앱과 같은 길(intake.analyze)로 읽히고, 읽은 값을 시험 글에 적힌 값(홍대 → 여의도 · 8초)과 견준다 — 틀만 맞고 값이 빈 답은 통과가 아니다
        calls = []
        r = intake.analyze(TEXT, [], order=["liner"], chat=lambda m, **o: calls.append(o["response_format"]["type"]) or liner.chat(m, **o))
        if r["source"] != "liner":
            raise ValueError(" / ".join(r["notes"]) or "Liner 가 읽지 못했습니다")
        d = r["draft"]
        if "홍대" not in d["from"]["name"] or "여의도" not in d["to"]["name"] or d["duration_seconds"] != 8:
            raise ValueError(f"읽은 값이 시험 글과 다릅니다(출발 {d['from']['name']!r}, 도착 {d['to']['name']!r}, 길이 {d['duration_seconds']!r} — 적힌 것은 홍대 → 여의도 · 8초)")
        seen["reading"] = r
        how = "정해진 틀(json_schema)" if calls[-1] == "json_schema" else "틀을 받지 않아 JSON 으로만 답하게 함(json_object)"
        return f"{how} · 출발 {d['from']['name']!r} 도착 {d['to']['name']!r} 길이 {d['duration_seconds']!r} — 시험 글과 같음"

    def table():
        d = board.via_liner(TABLE, [])
        digits = lambda v: "".join(ch for ch in str(v or "") if ch.isdigit())       # noqa: E731 — '120점'·'120' 어느 쪽으로 읽어 와도 숫자만 견준다
        got = {str(r.get("name") or "").strip(): digits(r.get("value_text")) for r in d.get("rows") or [] if isinstance(r, dict)}
        if got.get("가람") != "120" or got.get("나래") != "95":
            raise ValueError(f"읽은 표가 시험 글과 다릅니다(읽은 것 {got!r} — 적힌 것은 가람 120 · 나래 95)")
        return f"순위판 틀 · 줄 {len(got)}개 · 제목 {d.get('title')!r} · 가람 120 · 나래 95 — 시험 글과 같음"

    def picture():
        d = intake.via_liner("", [sample_image()])
        ok = "노들섬" in str(d.get("from_name")) and "여의도" in str(d.get("to_name"))
        if not ok:
            raise ValueError(f"그림 속 글자를 다르게 읽었습니다(출발 {d.get('from_name')!r}, 도착 {d.get('to_name')!r})")
        return f"그림 속 한글을 읽음 · 출발 {d.get('from_name')!r} 도착 {d.get('to_name')!r} 길이 {d.get('duration_seconds')!r}"

    def auditor():
        reading = seen.get("reading")       # 위에서 Liner 가 실제로 읽은 그 초안을 감사한다
        if not reading:
            raise ValueError("앞 단계(지도 의뢰 읽기)가 안 돼 감사할 판독이 없습니다")
        a = audit.check(TEXT, [], reading, available={"liner": True, "claude": False})
        if a.get("state") != "done":
            raise ValueError(f"감사 {a.get('state')}: {a.get('reason')}")
        return f"감사 모델 {a.get('model')} · 항목 {len(a.get('checks') or [])}개 · 확인할 곳 {a.get('flags')} · 가리지 못한 곳 {a.get('unverified')}"

    steps = [("글 한 줄 주고받기", plain), ("지도 의뢰를 정해진 틀로 읽기", schema), ("순위판 표를 정해진 틀로 읽기", table),
             ("그림(의뢰서) 읽기", picture), ("판독 감사(다른 모델)", auditor)]
    results = []
    for name, fn in steps:
        results.append(_step(name, fn, log))
        if not results[0]:      # 첫 호출이 안 되면(키·잔액·연결) 나머지도 같은 까닭으로 안 된다
            log("  첫 호출이 안 돼 나머지는 건너뜁니다.")
            return 1
    if image:
        log(f"의뢰지 읽기 — {image.name}")
        url = data_url(image)
        log(f"  보내는 그림 크기 {len(url) * 3 // 4 // 1024}KB")

        def sheet():
            b = board.read("", [url], available={"liner": True, "claude": False})
            if b and b.get("kind") == "plan":      # 틀이 없는 정보판 — Liner 가 낸 설계도
                d = b["draft"]
                for n in b["notes"]:
                    log(f"    알림: {n}")
                return f"정보판 설계도 '{d['title']}' — {d['shape']} · {len(d['rows'])}줄"
            if b:
                d = b["draft"]
                rows = " · ".join(f"{r['name']} {r['value_text']}" for r in d["rows"])
                nxt = f" / 다음 순위({d['next']['title']}): " + " · ".join(f"{r['name']} {r['value_text']}" for r in d["next"]["rows"]) if d.get("next") else ""
                for n in b["notes"]:
                    log(f"    알림: {n}")
                return f"순위판 '{d['title']}' — {rows}{nxt}"
            r = intake.analyze("", [url], order=["liner"])
            if r["source"] != "liner":
                raise ValueError(" / ".join(r["notes"]) or "Liner 가 읽지 못했습니다")
            d = r["draft"]
            for n in r["notes"]:
                log(f"    알림: {n}")
            cuts = f" · 컷 {audit.cuts_line(d['cuts'])}" if d.get("cuts") else ""
            return (f"지도 — {d['from']['name']} → {d['to']['name']} · {d['vehicle']}{'(' + d['vehicle_color'] + ')' if d.get('vehicle_color') else ''}"
                    f" · {d['duration_seconds']}초{cuts} · 못 찾은 항목 {r['missing']}")
        results.append(_step("의뢰지를 앱과 같은 길로 읽기", sheet, log))
    bad = results.count(False)
    log("모두 됩니다 — 메신저(python -m mapcg serve)에서 의뢰서를 올려 보세요." if not bad else f"안 되는 단계가 {bad}개 있습니다. 위의 까닭을 확인해 주세요.")
    return 1 if bad else 0
