"""연구실 — 앱에 없는 종류의 그래픽 의뢰가 왔을 때, Liner 에게 새 화면 틀의 초안을 짜게 하고 격리해 그려 본다.

제작 현장(메신저 · 명령줄)은 모델이 만든 코드를 실행하지 않는다. 그 원칙은 그대로다. 이 도구는 **개발 단계에서만** 쓰는 연구실이고,
여기서 나온 화면 틀은 사람이 화면과 코드를 보고 시험과 함께 저장소에 넣기 전에는 어떤 의뢰에도 쓰이지 않는다.

  python tools/lab.py new 과제.json           1회차: Liner 가 초안을 짠다(호출 1번) → 글자 검사 → 격리해 찍기
  python tools/lab.py fix 과제이름 "고칠 점"    다음 회차: 앞 회차의 화면을 같이 보여 주고 고치게 한다(호출 1번)
  python tools/lab.py shoot 과제이름 [회차]     다시 찍기만(Liner 를 부르지 않는다)
  python tools/lab.py check 파일.html          글자 검사만
  python tools/lab.py selftest                격리가 실제로 바깥 접속을 막는지 확인(과제를 돌리기 전에 한 번)
  --proof 를 붙이면 Liner 가 끝 화면의 글자를 읽어 자료의 글자가 다 나왔는지 견준다(호출 +1)

과제 파일(JSON): {"name": "대진표", "brief": "무엇을 어떻게 보여 줄지(요구)", "job": {"title": …, 그 종류의 자료 …},
                 "variants": [{"label": "긴 이름", "job": {…}}], "duration_seconds": 10}
결과는 output/연구실/<과제이름>/회차N/ 에 쌓인다(깃 · 제출 묶음에 들어가지 않는다): 받은코드.html · 기록.json · 화면_*.png, 그리고 과제 폴더의 보기.html.

격리는 세 겹이다 — ① 받은 코드의 글자 검사(네트워크 · 시계 · 난수 · 우회 수법) ② 실행하는 사본에 내용 보안 정책(CSP)을 넣어 같은 출처 밖으로는 아무것도 못 부르게
③ 브라우저를 '죽은 프록시'로 띄워 이 컴퓨터 밖으로 나가는 접속을 막는다. selftest 가 ②와 ③을 따로 확인한다.

사람이 꼭 할 일(도구가 못 한다): 화면을 눈으로 본다(자동 검사는 간격 · 구도 · 어색함을 모른다) · 받은 코드를 읽는다 · 다른 자료(variants)로도 본다 ·
채택하려면 mapcg/templates 로 옮기고, 읽기(설계도 틀) · 검사(normalize) · 시험을 손으로 붙인다.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LAB = ROOT / "output" / "연구실"
MODEL = "liner-claude-router"       # MAPCG_LAB_MODEL 로 바꾼다. quality 로 부른다(2026-11-21 실험: claude-fable-5.2 가 답했고 초안 100초 · 고치기 117초)
MAX_TOKENS = 32000                  # 답의 길이 한도. 생각하는 데 쓴 토큰도 여기에 든다 — 16000 으로는 그래프 화면의 답이 잘렸다(2026-11-21)
EXAMPLE = "plan_board.html"         # 본보기로 보여 주는 기존 화면
ALLOWED_SRC = ("stage.js", "job.js", "brush.js", "boardskin.js", "style_v5.js")
# 받은 코드에 있으면 실행하지 않는 글자. 바깥 접속 · 시계 · 난수 · 저장소 · 다른 창 · 코드 만들어 돌리기 · 글자를 이어 붙여 이름을 숨기는 수법
BANNED = ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import(", "importScripts", "eval(", "new Function", "Function(",
          "http://", "https://", 'src="//', "src='//", "Date.now", "new Date", "performance.now", "Math.random", "requestAnimationFrame",
          "setTimeout", "setInterval", "localStorage", "sessionStorage", "indexedDB", "document.cookie", "window.open", "Worker(", "postMessage",
          "<iframe", "<object", "<embed", "<link", "<form", "<img", "<base", "navigator.", "location.", "atob(", "fromCharCode", "constructor",
          "window[", "globalThis", "self[", "top[", "parent.", "document.write", "innerHTML", "\\x", "\\u00",
          "defaultView", "ownerDocument", "new Image")      # 창을 다른 이름으로 집어 금지 글자를 돌아가는 길 · 태그 없이 바깥 그림을 부르는 길
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self' data: blob:; base-uri 'none'; form-action 'none'")
# connect-src 의 data: · blob: 는 바깥 접속이 아니다 — 무대(stage.js)가 캔버스 그림을 data 주소로 바꿔 fetch 로 읽는다(메모리 안의 일). 이것이 없으면 프레임을 못 보낸다
# 브라우저를 이 컴퓨터 밖으로 못 나가게 띄우는 옵션: 모든 접속을 받는 곳이 없는 프록시로 보낸다(이 컴퓨터 안의 주소만 프록시를 거치지 않는다)
NET_OFF = ("--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>;127.0.0.1;localhost",
           "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost", "--disable-background-networking", "--no-pings")

RULES = """
[무대 약속 — 반드시 지킨다]
1. 답은 index.html 한 파일이다. 본보기와 같은 <style> 과 <canvas id="c"> 를 두고, 스크립트는 이 순서로만 부른다:
   stage.js → job.js → brush.js → boardskin.js → style_v5.js → (네가 쓰는 인라인 <script> 하나). 다른 파일·주소는 부르지 않는다.
2. 시작은 본보기 그대로:  const J = window.JOB, SK = window.BOARDSKIN;  const ST = Stage(J, SK), { X, PW, PH, U, SAFE } = ST;  const B = Brush(X, SK);
   - X = 2D 캔버스, PW×PH = 그래픽 자리(전체 화면 1920×1080), U = PH/540 (모든 길이·글자 크기는 U 를 곱해 쓴다).
   - SAFE = { l, t, r, b } 글자가 넘지 말아야 할 가장자리 여백(픽셀). 글자와 칸은 SAFE.l ~ PW-SAFE.r, SAFE.t ~ PH-SAFE.b 안에 둔다.
   - ST.LAST = 마지막 프레임의 시각. 모든 등장은 이 시각 전에 끝나서 마지막 프레임에는 전부 나와 있어야 한다. ST.DUR = 길이(초).
3. 글자 너비를 재서 자리를 잡는 일은 전부  ST.ready.then(() => { … })  안에서 한다(글꼴이 불러와진 뒤). 그 끝에서
   ST.run(content, null, () => [...new Set(ISSUES)]);  를 부른다.
4. content(t) 는 "t 초의 화면 전체"를 매번 처음부터 그리는 함수다. 프레임 사이에 상태를 쌓지 않는다. 같은 t 면 언제 그려도 같은 그림이어야 한다.
   주소 옵션(?t= · ?export=1 · ?times=)은 stage.js 가 처리하니 건드리지 않는다.
5. 금지: fetch · XMLHttpRequest · WebSocket · import( · eval( · new Function · http:// · https:// · Date.now · new Date · performance.now · Math.random ·
   requestAnimationFrame · setTimeout · setInterval · localStorage · innerHTML · location · navigator · 외부 그림/글꼴 · <img> · <link> ·
   new Image · defaultView · ownerDocument. 스크립트는 <head> 뒤에만 둔다.
   난수가 필요하면 시드 고정인 B.rng(seed)() 나 B.hash(n) 만 쓴다. 글자를 이어 붙여 이름을 만드는 코드(window['a'+'b'] 따위)는 쓰지 않는다.
6. 색은 화풍 값만 쓴다: SK.ink(먹·선·글자) · SK.paper(종이 칸) · SK.yel(강조 노랑) · SK.bg · SK.bar[](줄마다 다른 물감색 배열) ·
   SK.team{blue,red,yellow,green,purple,pink,orange,aqua} · SK.title.plate{fill,color,line}(제목판) · SK.finish. 직접 적는 색은 본보기처럼 흰색 '#ffffff' 하나만 허용한다.
   글꼴은 SK.font(B.text 가 알아서 쓴다. 너비를 잴 때는 X.font = `900 ${px}px ${SK.font}`).
7. 그리기는 붓 도구(Brush)만 쓴다. 맨 캔버스 호출(X.fillRect·X.stroke 등)로 칸·선·글자를 그리지 않는다(변환·투명도·배경판 붙이기·마감 층은 본보기처럼 X 를 써도 된다).
   - B.rrd(x,y,w,h,r) / B.ell(cx,cy,rx,ry) / B.star(x,y,r,n,inner) → path 글(문자열)을 돌려준다
   - B.art(d, { fill, tex:'paper'|'paint', scale:U, lw, seed, drop, shade, pool })  면 칠하기 + 붓펜 외곽선(선 색은 SK.ink)
   - B.paint(d, { fill, tex, scale, drop, pool })  면만 칠하기,  B.outline(d, lw, color, seed)  외곽선만
   - B.seg(x0,y0,x1,y1,step) → 점 배열,  B.ink(pts, 굵기, 색, seed, { to: 0~1, taper })  붓펜 선. to 를 0→1 로 키우면 선이 자라난다. pts 는 [[x,y],…] 꺾은선이어도 된다
   - B.text(str, x, baselineY, px, { align:'left'|'center'|'right', color, maxW })  글자(기본 색 SK.ink). 세로 가운데에 놓으려면 baselineY = cy + px*0.36. 돌려주는 값 { px, x0, w }
     제목급 글자에만 { outline, tex, block, gloss } 를 쓸 수 있다. 작은 글자에는 겹을 두르지 않는다.
   - B.mk(w,h) 보조 캔버스, B.wash / B.grain / B.mottle / B.touch / B.tex 는 본보기의 배경판·마감 층 코드에서 쓰는 그대로만 쓴다.
   - seed 는 도형마다 다른 정수로 고정한다. B.boil = 0 을 content 첫 줄에 둔다.
8. 배경판(PLATE)·마감 층(WASH·GRAIN·가장자리 어둡게)·제목판(TPLATE 방식) 코드는 본보기에서 그대로 가져와 쓴다. SK.overlay 호출도 본보기처럼 둔다.
9. 자동 검사: ISSUES 배열에 사람이 읽을 한국어 문장을 넣는다. 적어도 ① 글자를 25% 넘게 줄여야 들어가는 글자 ② 가장 작은 글자가 SK.minText*U(=PH/30) 보다 작음
   ③ 칸·제목이 안전 영역 밖으로 나감 ④ SK.fontCheck 실패 ⑤ 영상이 짧아 마지막 화면에 다 나오지 않음 을 검사한다.
10. 화면의 숫자와 글자는 자료(JOB)에 있는 것만 쓴다. 지어낸 제목·수치·장식 글자를 넣지 않는다. 글자는 크고 굵게, 겹을 쌓지 않는다.
    한 점에서 사방으로 뻗는 줄무늬(햇살·집중선), 망점, 찢은 종이·테이프, 기울인 자막은 쓰지 않는다.
"""
TAIL = "답은 설명·머리말·코드 울타리(```) 없이 index.html 전문 하나만. <!doctype html> 로 시작해 </html> 로 끝낸다."
KEEP = "그대로 둘 것: 무대 약속 전부(금지 사항 · 화풍 색만 · 붓 도구만 · 시드 고정 · 자동 검사 ISSUES), 제목판 · 배경판 · 마감 층, 자료(JOB) 모양."


class LabError(RuntimeError):
    pass


# ---------------------------------------------------------------- 받은 코드 다루기

def strip_fence(text: str) -> str:
    """모델의 답에서 index.html 전문만 남긴다(코드 울타리 · 앞뒤 말 떼기)."""
    s = (text or "").strip()
    m = re.search(r"```(?:html)?\s*\n(.*?)```", s, re.S)
    if m and "<!doctype" not in s[:20].lower():
        s = m.group(1).strip()
    i, j = s.lower().find("<!doctype"), s.lower().rfind("</html>")
    return s[max(i, 0):j + 7] if j >= 0 else s[max(i, 0):]


def scan(code: str) -> list[str]:
    """받은 코드를 실행해도 되는지 글자로 본다. 걸린 것의 목록(비어 있으면 통과). 이 검사를 통과해도 격리(CSP · 죽은 프록시) 안에서만 실행한다."""
    bad = [f"금지된 글자 {w!r} × {code.count(w)}" for w in BANNED if w in code]
    low = code.lower()
    if not low.lstrip().startswith("<!doctype html") or not low.rstrip().endswith("</html>"):
        bad.append("index.html 전문이 아니다(<!doctype html> 로 시작해 </html> 로 끝나야 한다)")
    srcs = re.findall(r"<script[^>]*\bsrc\s*=\s*[\"']?([^\"'\s>]+)", code, re.I)
    bad += [f"허용하지 않은 스크립트 {s!r}" for s in srcs if s not in ALLOWED_SRC]
    bad += [f"빠진 스크립트 {s!r}" for s in ALLOWED_SRC if s not in srcs]
    head, first = re.search(r"<head[\s>]", code, re.I), re.search(r"<script", code, re.I)
    if first and (not head or first.start() < head.start()):
        bad.append("<head> 보다 앞에 있는 스크립트")
    if re.search(r"\bon[a-z]+\s*=\s*[\"']", code, re.I):
        bad.append("태그에 붙인 이벤트 코드(on…=)")
    if len(code.encode("utf-8")) > 300_000:
        bad.append("코드가 너무 크다(300KB 초과)")
    return bad


def seal(code: str) -> str:
    """실행하는 사본에 내용 보안 정책을 넣는다 — 같은 출처(프레임을 받는 이 컴퓨터의 임시 서버) 밖으로는 아무것도 부르지 못한다.

    정책은 문서의 맨 앞(<!doctype> 바로 뒤)에 넣는다. 정책 태그는 그 뒤에 오는 것에만 먹는다 — <head> 뒤에 넣으면
    <head> 보다 앞에 둔 스크립트가 정책 없이 먼저 돈다(2026-11-24 리뷰에서 실제로 바깥 접속이 나가는 것을 확인했다).
    """
    meta = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    # 앞머리의 BOM · 빈칸 · 주석은 건너뛴다 — 정책 태그가 <!doctype> 보다 앞에 가면 doctype 이 무시돼 화면이 호환 모드로 그려진다
    m = re.match(r"(?:﻿|\s|<!--.*?-->)*<!doctype[^>]*>", code, re.I | re.S)
    return code[:m.end()] + "\n" + meta + code[m.end():] if m else meta + "\n" + code


@contextlib.contextmanager
def sealed_browser(on: bool = True):
    """이 안에서 띄우는 프레임 받기용 브라우저는 이 컴퓨터 밖으로 나가지 못한다(앱의 내보내기 옵션에 잠깐 덧붙였다가 되돌린다)."""
    from mapcg import export
    saved = (list(export.CPU_FLAGS), list(export.GPU_FLAGS))
    if on:
        export.CPU_FLAGS[:] = [*saved[0], *NET_OFF]
        export.GPU_FLAGS[:] = [*saved[1], *NET_OFF]
    try:
        yield
    finally:
        export.CPU_FLAGS[:], export.GPU_FLAGS[:] = saved


# ---------------------------------------------------------------- 과제와 물음

def load_task(path) -> dict:
    task = json.loads(Path(path).read_text(encoding="utf-8"))
    name = re.sub(r"[^0-9A-Za-z가-힣_-]+", "", str(task.get("name") or ""))[:30]
    if not name or not str(task.get("brief") or "").strip() or not isinstance(task.get("job"), dict) or not str(task["job"].get("title") or "").strip():
        raise LabError("과제 파일에는 name · brief · job(title 포함)이 있어야 합니다.")
    return {**task, "name": name, "duration_seconds": float(task.get("duration_seconds") or 10),
            "variants": [v for v in task.get("variants") or [] if isinstance(v, dict) and isinstance(v.get("job"), dict)]}


def base_folder(task_dir: Path) -> tuple[Path, dict]:
    """공용 스크립트 · 화풍 · 글꼴이 들어 있는 기준 작업 폴더(기존 VS 대결판 한 벌)를 만든다. Liner 를 부르지 않는다. (폴더, 그 job)"""
    from mapcg import plan
    home = task_dir / "기준"
    made = sorted(p for p in home.iterdir() if p.is_dir()) if home.is_dir() else []
    if not made:
        req = plan.normalize({"kind": plan.KIND, "shape": "versus", "title": "기준 화면", "left_color": "blue", "right_color": "red", "art_style": "v5",
                              "duration_seconds": 8, "layout": "full", "output": {"width": 1920, "height": 1080, "fps": 30},
                              "rows": [{"left": {"name": "가람", "value_text": "42"}, "right": {"name": "나래", "value_text": "58"}}]})
        made = [plan.make_job(req, project_dir=ROOT, out_root=home, log=lambda *a: None)["dir"]]
    job = json.loads((made[-1] / "job.js").read_text(encoding="utf-8").strip()[len("window.JOB="):].rstrip(";"))
    return made[-1], job


def full_job(data: dict, base: dict, seconds: float) -> dict:
    """과제의 자료에 무대가 쓰는 공통 값(크기 · 자리 · 길이)을 붙인다."""
    return {**data, "art_style": "v5", "layout": "full", "output": base["output"], "program": "", "layout_rect": base["layout_rect"], "duration_seconds": seconds}


def first_prompt(task: dict, job: dict) -> str:
    example = (ROOT / "mapcg" / "templates" / EXAMPLE).read_text(encoding="utf-8")
    return ("너는 방송 그래픽을 캔버스 코드로 짜는 개발자다. 아래 '무대 약속'과 본보기(" + EXAMPLE + " 전문)를 읽고, 같은 화풍·같은 약속으로 새 종류의 화면을 짠다.\n" + RULES +
            f"\n[본보기: {EXAMPLE} 전문 — 실제 작업 폴더에서는 boardskin.js 다음에 style_v5.js 를 하나 더 부른다]\n" + example +
            f"\n\n[새로 짤 화면: {task['name']}]\n자료는 job.js 가 아래 모양으로 준다(값은 예시다 — 이름·수치·줄 수는 바뀔 수 있으니 자료에서 읽어 자리를 계산한다).\n"
            + json.dumps(job, ensure_ascii=False, indent=1) + "\n\n요구\n" + task["brief"].strip() +
            f"\n- 길이는 J.duration_seconds({job['duration_seconds']:g}초). 마지막 약 1/3 은 다 나온 화면으로 머문다."
            "\n- 글자는 전부 안전 영역 안, 가장 작은 글자도 화면 높이의 1/30(= 18*U) 이상. 글자끼리·글자와 선이 겹치지 않게 자리를 계산한다.\n- " + TAIL)


def messages_for(task_dir: Path, task: dict, job: dict, upto: int) -> list[dict]:
    """upto 회차를 묻는 대화: 첫 물음 → (앞 회차의 코드 → 고칠 점[+ 그 화면]) …"""
    msgs: list[dict] = [{"role": "user", "content": first_prompt(task, job)}]
    for k in range(1, upto):
        d = task_dir / f"회차{k}"
        msgs.append({"role": "assistant", "content": (d / "받은코드.html").read_text(encoding="utf-8")})
        fix = (task_dir / f"회차{k + 1}" / "고칠점.txt").read_text(encoding="utf-8")
        text = ("네가 준 index.html 을 격리해 실행했다. 사람 눈으로 본 아래 문제만 고쳐서 index.html 전문을 다시 달라"
                + ("(붙인 그림이 지금 코드의 끝 화면이다).\n\n" if (d / "화면_끝.png").is_file() else ".\n\n") + fix.strip() + "\n\n" + KEEP + "\n" + TAIL)
        shot = d / "화면_끝.png"
        msgs.append({"role": "user", "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": _data_url(shot)}}]
                     if shot.is_file() and k == upto - 1 else text})
    return msgs


def _data_url(png: Path) -> str:
    from PIL import Image
    im = Image.open(png).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def stream_chat(messages: list[dict], *, model: str, timeout: float = 300, retries: int = 0, **options) -> dict:
    """긴 답을 조각으로 이어 받는다. 돌려주는 모양은 liner.chat 과 같다.

    화면 틀 하나는 답이 2분쯤 걸린다. 한 번에 받으면 Liner 앞의 중간 서버가 100초쯤에서 끊는다(오류 524 — 2026-11-21 에 겪음).
    조각으로 받으면 그 사이에도 글자가 오가서 끊기지 않는다. 다시 보내지는 않는다(끊겨도 그 요청은 과금됐을 수 있다).
    """
    import liner
    body = {"model": model, "messages": messages, "stream": True, **options}
    if options.pop("_usage", True):     # 쓴 토큰도 마지막 조각에 실어 달라고 한다. 이 옵션을 안 받는 모델이면(400) 빼고 한 번 더 묻는다
        try:
            return stream_chat(messages, model=model, timeout=timeout, _usage=False, stream_options={"include_usage": True}, **options)
        except liner.LinerError as e:
            if e.status != 400:
                raise
            return stream_chat(messages, model=model, timeout=timeout, _usage=False, **options)
    body.pop("_usage", None)
    tag = {"model": model, "messages": messages, "response_format": {"type": "lab_template"}}      # 호출 기록용 이름표(보내는 본문이 아니다)
    req = urllib.request.Request(liner.BASE_URL + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {liner.api_key()}", "Content-Type": "application/json", "Accept": "text/event-stream"})
    t0, parts, seen = time.perf_counter(), [], {"model": None, "usage": None, "finish": None}
    try:
        with liner._open(req, timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                try:
                    ev = json.loads(line[5:])
                except ValueError:
                    continue
                if ev.get("error"):
                    raise liner.LinerError(0, json.dumps(ev["error"], ensure_ascii=False))
                seen["model"], seen["usage"] = ev.get("model") or seen["model"], ev.get("usage") or seen["usage"]
                for ch in ev.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content")
                    if isinstance(piece, str):
                        parts.append(piece)
                    seen["finish"] = ch.get("finish_reason") or seen["finish"]
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        e.close()
        liner._log("/chat/completions", tag, e.code, 0, time.perf_counter() - t0, None)
        raise liner.LinerError(e.code, text) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        liner._log("/chat/completions", tag, 0, 0, time.perf_counter() - t0, None, note="조각으로 받다가 끊김")
        raise liner.LinerError(0, f"Liner 의 답을 받다가 끊겼습니다({type(e).__name__}) — 다시 보내지 않았습니다.") from None
    out = {"model": seen["model"], "usage": seen["usage"], "choices": [{"message": {"content": "".join(parts)}, "finish_reason": seen["finish"]}]}
    liner._log("/chat/completions", tag, 200, 0, time.perf_counter() - t0, out)
    return out


def ask(task_dir: Path, task: dict, job: dict, n: int, chat=None) -> dict:
    """n 회차의 코드를 Liner 에게 받는다(호출 1번). 받은 것을 회차 폴더에 적고 기록을 돌려준다."""
    import liner
    d = task_dir / f"회차{n}"
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with liner.request(f"연구-{task['name']}-{time.strftime('%m%d')}"):
        out = (chat or stream_chat)(messages_for(task_dir, task, job, n), model=os.environ.get("MAPCG_LAB_MODEL", "").strip() or MODEL,
                                    routing_strategy="quality", max_completion_tokens=MAX_TOKENS, timeout=300, retries=0)
    choice = out["choices"][0]
    raw = choice["message"].get("content") or ""
    code = strip_fence(raw)
    (d / "받은코드.html").write_text(code, encoding="utf-8")
    info = {"round": n, "asked": time.strftime("%Y-%m-%d %H:%M:%S"), "model": out.get("model"), "seconds": round(time.perf_counter() - t0, 1),
            "usage": out.get("usage"), "finish_reason": choice.get("finish_reason"), "lines": code.count("\n") + 1, "scan": scan(code)}
    if choice.get("finish_reason") == "length":
        info["scan"].append("답이 길이 한도에서 잘렸다")
    return info


# ---------------------------------------------------------------- 격리해 그리기

def stage(round_dir: Path, base: Path, job: dict, label: str = "실행") -> Path:
    """기준 폴더의 사본에 받은 코드(CSP 를 넣은 것)와 자료를 넣어 실행 폴더를 만든다."""
    run = round_dir / label
    if not run.is_dir():
        shutil.copytree(base, run)
    (run / "index.html").write_text(seal((round_dir / "받은코드.html").read_text(encoding="utf-8")), encoding="utf-8")
    (run / "job.js").write_text("window.JOB=" + json.dumps(job, ensure_ascii=False) + ";", encoding="utf-8")
    return run


def snap(run: Path, t: float, out_png: Path, work: Path) -> str:
    """실행 폴더의 t 초 화면을 격리된 브라우저로 찍어 960×540 으로 줄여 둔다. 돌려주는 것은 원본의 해시(같은 프레임 견주기용)."""
    from PIL import Image
    from mapcg import export
    tag = f"{out_png.stem}_{secrets_hex()}"
    frames = work / "frames" / tag
    with sealed_browser():
        export.capture(run, f"preview&times={t:g}", frames, 1, work / "profiles" / tag, timeout=120)
    raw = (frames / "00000.png").read_bytes()
    im = Image.open(io.BytesIO(raw))
    w, h = im.size
    fh = round(w * 9 / 16)
    im.crop((0, 0, w, fh)).resize((960, 540), Image.LANCZOS).save(out_png)
    if h > fh:
        im.crop((0, fh, w, h)).save(out_png.with_name(out_png.stem + "_검사줄.png"))      # 화면 틀의 자동 검사(ISSUES)가 적힌 줄
    return hashlib.sha256(raw).hexdigest()


def secrets_hex() -> str:
    return hashlib.sha1(str(time.perf_counter_ns()).encode()).hexdigest()[:6]


def shoot(task_dir: Path, task: dict, base: Path, base_job: dict, n: int, log=print) -> dict:
    """n 회차의 코드를 격리해 그려 본다: 처음 · 가운데 · 끝, 같은 프레임 두 번, 다른 자료(variants)의 끝 화면."""
    d, secs = task_dir / f"회차{n}", task["duration_seconds"]
    job = full_job(task["job"], base_job, secs)
    run, work, out = stage(d, base, job), d / "작업", {"shots": [], "errors": []}
    times = {"처음": round(secs * .18, 2), "가운데": round(secs * .5, 2), "끝": round(secs - .1, 2)}
    hashes = {}
    for name, t in times.items():
        try:
            hashes[name] = snap(run, t, d / f"화면_{name}.png", work)
            out["shots"].append(f"화면_{name}.png")
            log(f"  {name}({t:g}초) 찍음")
        except Exception as e:      # 페이지 오류는 내보내기가 ExportError 로 올린다
            out["errors"].append(f"{name}({t:g}초): {type(e).__name__}: {str(e)[:200]}")
            log(f"  {name}({t:g}초) 실패: {str(e)[:160]}")
    if "가운데" in hashes:
        try:
            out["same_frame"] = snap(run, times["가운데"], d / "작업" / "다시.png", work) == hashes["가운데"]
        except Exception as e:
            out["errors"].append(f"다시 찍기: {str(e)[:160]}")
    for v in task["variants"]:
        label = re.sub(r"[^0-9A-Za-z가-힣_-]+", "", str(v.get("label") or "다른자료"))[:20] or "다른자료"
        try:
            vrun = stage(d, base, full_job(v["job"], base_job, secs), f"실행_{label}")
            snap(vrun, times["끝"], d / f"화면_끝_{label}.png", work)
            out["shots"].append(f"화면_끝_{label}.png")
            log(f"  다른 자료({label}) 끝 화면 찍음")
        except Exception as e:
            out["errors"].append(f"다른 자료({label}): {type(e).__name__}: {str(e)[:200]}")
    return out


def words(data) -> list[str]:
    """자료에 든, 화면에 나와야 할 글자들(이름 · 제목 · 값). 코드용 짧은 영문 값은 뺀다."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.strip() and not re.fullmatch(r"[a-z_]{1,12}", v.strip()) and v.strip() not in out:
            out.append(v.strip())
    walk(data)
    return out


def proof(task_dir: Path, task: dict, n: int, chat=None) -> dict:
    """Liner 가 끝 화면의 글자를 읽고(호출 1번), 자료의 글자가 다 나왔는지 코드가 견준다."""
    from mapcg import proof as screen
    shot = task_dir / f"회차{n}" / "화면_끝.png"
    seen, model = screen.read([{"kind": "map", "frame": shot}], chat)
    got = seen.get(1) or {}
    heap = [screen._norm(t) for t in [got.get("title") or "", *(got.get("texts") or []), *(f"{r.get('rank', '')}{r.get('name', '')}{r.get('value', '')}" for r in got.get("rows") or [])]]
    missing = [w for w in words(task["job"]) if not any(screen._norm(w) in h for h in heap)]
    return {"model": model, "missing": missing, "hard_to_read": [h.get("text") for h in got.get("hard_to_read") or [] if isinstance(h, dict)]}


# ---------------------------------------------------------------- 묶어 돌리기

def view_page(task_dir: Path, task: dict) -> Path:
    """회차별 화면을 한 쪽에 모아 보는 보기.html(스크립트 없음)."""
    rows = []
    for d in (task_dir / f"회차{k}" for k in rounds(task_dir)):
        try:
            info = json.loads((d / "기록.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        pics = "".join(f'<figure><img src="{d.name}/{p}"><figcaption>{p[3:-4]}</figcaption></figure>' for p in info.get("shots") or [])
        notes = "".join(f"<li>{_esc(x)}</li>" for x in [*(info.get("scan") or []), *(info.get("errors") or []),
                                                        *([f"화면에서 못 찾은 글자: {' · '.join(info['proof']['missing'])}"] if (info.get("proof") or {}).get("missing") else [])])
        fix = d / "고칠점.txt"
        rows.append(f"<section><h2>{d.name} — {_esc(str(info.get('model') or ''))} · {info.get('seconds', '?')}초 · {info.get('lines', '?')}줄"
                    f" · 같은 프레임 {'같음' if info.get('same_frame') else '확인 안 됨' if info.get('same_frame') is None else '다름'}</h2>"
                    + (f"<p class=fix>고칠 점: {_esc(fix.read_text(encoding='utf-8'))}</p>" if fix.is_file() else "")
                    + f"<div class=row>{pics}</div>" + (f"<ul>{notes}</ul>" if notes else "") + "</section>")
    page = ("<!doctype html><meta charset=utf-8><title>연구실 · " + _esc(task["name"]) + "</title><style>body{font:16px 'Pretendard','Malgun Gothic',sans-serif;margin:24px;background:#fff5e1;color:#2b2756}"
            "h1{font-size:28px}h2{font-size:18px;margin:28px 0 8px}.row{display:flex;gap:12px;flex-wrap:wrap}figure{margin:0}img{width:460px;border:3px solid #2b2756;border-radius:10px;display:block}"
            "figcaption{font-weight:700;text-align:center;margin-top:4px}.fix{white-space:pre-wrap;background:#fffaf2;border:2px solid #2b2756;border-radius:8px;padding:8px 12px}li{color:#b3261e;font-weight:700}</style>"
            f"<h1>연구실 · {_esc(task['name'])}</h1><p>{_esc(task['brief'])}</p>" + "".join(rows))
    out = task_dir / "보기.html"
    out.write_text(page, encoding="utf-8")
    return out


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run_round(task: dict, n: int, *, fresh: bool, want_proof: bool = False, chat=None, log=print) -> dict:
    task_dir = LAB / task["name"]
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "과제.json").write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
    base, base_job = base_folder(task_dir)
    d = task_dir / f"회차{n}"
    if fresh:
        log(f"[{task['name']} {n}회차] Liner 에게 묻는 중 …")
        info = ask(task_dir, task, full_job(task["job"], base_job, task["duration_seconds"]), n, chat)
        log(f"  답한 모델 {info['model']} · {info['seconds']}초 · {info['lines']}줄 · 토큰 {(info.get('usage') or {}).get('total_tokens')}")
    else:
        info = json.loads((d / "기록.json").read_text(encoding="utf-8"))
        info["scan"] = scan((d / "받은코드.html").read_text(encoding="utf-8"))
    if info["scan"]:
        log("  글자 검사 불합격 — 실행하지 않는다:")
        for x in info["scan"]:
            log("   -", x)
        info.update(shots=[], errors=["글자 검사 불합격으로 실행하지 않음"])
    else:
        log("  글자 검사 통과 — 격리해 그려 본다")
        ensure_sealed(log)
        info.update(shoot(task_dir, task, base, base_job, n, log))
        if want_proof and (d / "화면_끝.png").is_file():
            try:
                info["proof"] = proof(task_dir, task, n)
                log("  화면 글자 대조:", "자료의 글자가 다 보임" if not info["proof"]["missing"] else "못 찾은 글자 " + " · ".join(info["proof"]["missing"]))
            except Exception as e:
                info["proof"] = {"error": str(e)[:160]}
    (d / "기록.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    page = view_page(task_dir, task)
    log(f"  같은 프레임 두 번: {'같다' if info.get('same_frame') else '확인 안 됨' if info.get('same_frame') is None else '다르다'} · 오류 {len(info.get('errors') or [])}건")
    log(f"  화면 모아 보기: {page}")
    return info


TRIPWIRE = """<!doctype html><html><head><meta charset="utf-8">@CSP@</head><body><script>
(async () => {
  const out = {};
  const get = async (name, url) => { try { await fetch(url, { mode: 'no-cors', cache: 'no-store' }); out[name] = '나감'; } catch (e) { out[name] = '막힘'; } };
  const pic = (name, url) => new Promise(done => { const im = new Image(); im.onload = () => { out[name] = '나감'; done(); }; im.onerror = () => { out[name] = '막힘'; done(); }; im.src = url; });
  await Promise.all([get('이름으로 접속', 'https://example.com/'), get('숫자 주소로 접속', 'http://1.1.1.1/'), pic('바깥 그림', 'https://www.google.com/favicon.ico')]);
  const c = document.createElement('canvas'); c.width = c.height = 2;
  const blob = await new Promise(r => c.toBlob(r, 'image/png'));
  await fetch('/plan', { method: 'POST', body: JSON.stringify({ frames: 1, fps: 1, width: 2, height: 2, net: out }) });
  await fetch('/frame/0', { method: 'POST', body: blob });
  await fetch('/done', { method: 'POST' });
})();
</script></body></html>"""


def rounds(task_dir: Path) -> list[int]:
    """코드를 받은 회차 번호들(작은 것부터). 호출이 실패해 고칠 점만 남은 폴더는 회차로 치지 않는다 — 다음 fix 가 그 번호를 다시 쓴다."""
    return sorted(int(p.name[2:]) for p in task_dir.glob("회차*") if p.name[2:].isdigit() and (p / "받은코드.html").is_file())


def _browser_mark() -> dict:
    from mapcg import export
    p = Path(export.find_browser())
    st = p.stat()
    return {"browser": str(p), "size": st.st_size, "mtime": int(st.st_mtime)}


def ensure_sealed(log=print) -> None:
    """받은 코드를 실행하기 전에: 이 컴퓨터의 이 브라우저에서 격리가 먹는다고 확인한 기록이 있는가. 없으면 지금 점검하고, 안 막히면 실행하지 않는다.

    격리 옵션은 브라우저가 알아들어야 먹는다. 브라우저 종류나 판이 바뀌면(MAPCG_BROWSER 로 다른 것을 지정한 컴퓨터 포함) 다시 확인한다.
    """
    mark, record = _browser_mark(), LAB / "_격리점검" / "통과.json"
    try:
        if json.loads(record.read_text(encoding="utf-8")).get("mark") == mark:
            return
    except (OSError, ValueError):
        pass
    log("  이 브라우저에서 격리를 확인한 기록이 없어 먼저 점검한다")
    ok, meaningful = selftest(log)
    if not ok:
        raise LabError("격리가 바깥 접속을 막지 못했습니다 — 받은 코드를 실행하지 않습니다.")
    if meaningful:
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"mark": mark, "checked": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=1), encoding="utf-8")
    else:       # 인터넷이 안 되면 격리 없이도 다 '막힘'이라 점검이 아무것도 말해 주지 않는다. 지금은 나갈 길이 없으니 실행하되 통과로 적지 않는다
        log("  (인터넷이 안 되는 상태라 점검이 뜻이 없다 — 이번만 실행하고 통과 기록은 남기지 않는다)")


def selftest(log=print) -> tuple[bool, bool]:
    """격리가 실제로 바깥 접속을 막는지 본다. 일부러 바깥으로 나가려는 쪽을 세 가지 조건에서 돌린다.
    돌려주는 것: (격리한 두 조건이 다 막혔나, 격리 없는 기준에서는 나갔나 — 나가야 이 점검이 뜻이 있다)."""
    from mapcg import export
    home = LAB / "_격리점검"
    ok, meaningful = True, False
    for label, csp, net_off in (("격리 없음(견줄 기준 — 인터넷이 되면 '나감'이어야 점검이 뜻이 있다)", False, False),
                                 ("내용 보안 정책만", True, False), ("죽은 프록시만", False, True)):
        d = home / secrets_hex()
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(TRIPWIRE.replace("@CSP@", f'<meta http-equiv="Content-Security-Policy" content="{CSP}">' if csp else ""), encoding="utf-8")
        try:
            with sealed_browser(net_off):
                plan = export.capture_planned(d, "preview", d / "frames", d / "profile", timeout=60)
            net = plan.get("net") or {}
        except Exception as e:
            net = {"점검 실패": f"{type(e).__name__}: {str(e)[:120]}"}
        log(f"  {label}: " + " · ".join(f"{k} {v}" for k, v in net.items()))
        if (csp or net_off) and (not net or any(v != "막힘" for v in net.values())):
            ok = False
        if not (csp or net_off):
            meaningful = any(v == "나감" for v in net.values())
    log("  → " + ("격리한 두 조건에서 바깥 접속이 모두 막혔다" if ok else "막히지 않은 접속이 있다 — 받은 코드를 실행하지 마라"))
    return ok, meaningful


def main(argv=None) -> int:
    # 출력을 파일 · 다른 프로그램으로 넘기면 한국어 윈도우는 cp949 로 찍는다 — 못 찍는 글자('—' 등)가 있어도 돌다가 죽지 않게(tools/pack.py 와 같다)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    want_proof = "--proof" in args
    args = [a for a in args if not a.startswith("--")]
    try:
        if len(args) == 2 and args[0] == "new":
            task = load_task(args[1])
            done = LAB / task["name"] / "회차1" / "기록.json"
            if done.is_file() and json.loads(done.read_text(encoding="utf-8")).get("shots"):     # 찍힌 화면이 없는 1회차(잘린 답 · 검사 불합격)는 다시 묻는다
                raise LabError(f"'{task['name']}' 과제는 이미 있습니다 — 고치려면 fix, 새로 하려면 과제 이름을 바꾸세요.")
            run_round(task, 1, fresh=True, want_proof=want_proof)
        elif len(args) == 3 and args[0] == "fix":
            task_dir = LAB / args[1]
            task = load_task(task_dir / "과제.json")
            had = rounds(task_dir)
            if not had or not args[2].strip():
                raise LabError("고칠 앞 회차가 없거나 고칠 점이 비었습니다.")
            n = had[-1] + 1     # 앞선 fix 가 Liner 호출에서 실패해 고칠 점만 남은 폴더가 있으면 그 번호를 다시 쓴다
            (task_dir / f"회차{n}").mkdir(exist_ok=True)
            (task_dir / f"회차{n}" / "고칠점.txt").write_text(args[2].strip(), encoding="utf-8")
            run_round(task, n, fresh=True, want_proof=want_proof)
        elif len(args) in (2, 3) and args[0] == "shoot":
            task_dir = LAB / args[1]
            task = load_task(task_dir / "과제.json")
            had = rounds(task_dir)
            n = int(args[2]) if len(args) == 3 else (had[-1] if had else 0)
            if n not in had:
                raise LabError(f"코드를 받은 회차가 아닙니다({n}회차). 있는 회차: {had or '없음'}")
            run_round(task, n, fresh=False, want_proof=want_proof)
        elif len(args) == 2 and args[0] == "check":
            bad = scan(Path(args[1]).read_text(encoding="utf-8"))
            print("글자 검사 통과" if not bad else "글자 검사 불합격:\n - " + "\n - ".join(bad))
            return 1 if bad else 0
        elif args == ["selftest"]:
            return 0 if selftest()[0] else 1
        else:
            print(__doc__)
            return 2
    except (LabError, OSError, ValueError) as e:
        print("연구실:", e)
        return 1
    except RuntimeError as e:       # Liner 가 안 됨(키 · 잔액 · 끊김) — 까닭만 알린다
        print(f"연구실: Liner 호출이 안 됐습니다 — {str(e)[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
