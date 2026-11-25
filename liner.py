"""Liner Platform 최소 클라이언트 — 표준 라이브러리만 쓴다(설치할 것 없음).

키는 환경 변수 LINER_API_KEY 에서만 읽는다. 코드에 적지 않는다.
심사 때 운영팀이 자기 키로 바꿔 실행하므로, 이 파일 밖에서 키를 다루지 않는다.

문서(2026-11-18 확인)
  모델  POST /chat/completions      Authorization: Bearer <키>
  검색  POST /tools/search/web      x-api-key: <키>
        POST /tools/search/scholar  x-api-key: <키>
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://platform.liner.com/api/v1"
DEFAULT_MODEL = "liner-gpt-router"  # LINER_MODEL 환경 변수로 바꿀 수 있다

# 다시 시도해도 되는 상태 코드. 402(잔액 부족)는 다시 해도 소용없으니 넣지 않는다.
_RETRYABLE = {429, 500, 502, 504}


class LinerError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Liner API {status}: {body[:300]}")


def load_env(path: str | os.PathLike | None = None) -> None:
    """.env 를 읽어 환경 변수에 넣는다. 이미 설정된 값은 덮어쓰지 않는다."""
    env_path = Path(path) if path else Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(name.strip(), value)


def api_key() -> str:
    load_env()
    key = os.environ.get("LINER_API_KEY", "").strip()
    if not key or key == "your-api-key":
        raise LinerError(0, "LINER_API_KEY 가 없습니다. .env.example 을 .env 로 복사하고 키를 넣으세요.")
    return key


LOG_PATH = Path(__file__).resolve().parent / "data" / "liner_calls.jsonl"
RETRY_WINDOW = 150      # 처음 보낸 때부터 이만큼(초)이 지나면 다시 보내지 않는다(429 로 오래 기다리며 작업을 붙잡지 않게)
_ctx = threading.local()            # 지금 이 스레드가 처리하는 의뢰의 번호
_blocked: dict[str, tuple[int, float]] = {}     # 의뢰 번호 → (그 의뢰에서 받은 거절 401 키 · 402 잔액, 거절된 시각)
BLOCK_SECONDS = 1800    # 거절된 의뢰는 이만큼(초) 동안 다시 보내지 않는다 — 읽기(카드)와 만들기는 같은 번호로 따로 열리므로 블록이 끝나도 지우지 않는다


def _refused(rid: str | None) -> int | None:
    """그 의뢰가 아직 거절 상태면 거절 코드, 아니면 None. 오래된 기록은 여기서 치운다."""
    now = time.monotonic()
    for k in [k for k, (_, at) in _blocked.items() if now - at > BLOCK_SECONDS]:
        _blocked.pop(k, None)
    hit = _blocked.get(rid) if rid else None
    return hit[0] if hit else None


@contextlib.contextmanager
def request(request_id: str):
    """이 안에서 하는 호출을 한 의뢰로 묶는다.

    호출 기록에 의뢰 번호가 남아 의뢰 하나의 읽기·감사·검색을 따로 셀 수 있다. 또 키·잔액 문제로 한 번 거절되면
    같은 의뢰의 다음 호출은 보내지 않는다(같은 그림을 다른 단계가 다시 보내 봐야 똑같이 거절된다).
    거절은 블록이 끝나도 남는다 — 확인 카드를 거쳐 같은 번호로 여는 제작 단계(지명 묻기·검색)도 보내지 않는다.
    """
    prev = getattr(_ctx, "request", None)
    _ctx.request = request_id
    try:
        yield request_id
    finally:
        _ctx.request = prev


def _open(req, timeout: float):
    """서버에 접속한다(시험에서 가짜 응답으로 바꿔 끼우는 자리)."""
    return urllib.request.urlopen(req, timeout=timeout)


def _log(path: str, body: dict, status: int, retried: int, seconds: float, out: dict | None, *,
         call: str = "", final: bool = True, note: str = "", task: str = "") -> None:
    """보낸 시도 하나를 한 줄로 남긴다 — 언제, 어느 의뢰의, 어느 모델에, 무엇을 물었는지(틀 이름), 그림 몇 장, 걸린 시간, 쓴 토큰.

    call 은 한 번의 물음(다시 보낸 시도들이 같은 값을 갖는다), attempt 는 그 가운데 몇 번째 시도인지, final 은 그 물음의 마지막 시도인지.
    키와 주고받은 내용(글·그림)은 적지 않는다. LINER_LOG=off 로 끈다. 기록을 못 써도 호출은 그대로 간다.
    """
    if os.environ.get("LINER_LOG", "").strip().lower() in ("off", "0", "false", "no"):
        return
    try:
        fmt = body.get("response_format") or {}
        images = sum(1 for m in body.get("messages") or [] if isinstance(m.get("content"), list)
                     for part in m["content"] if isinstance(part, dict) and part.get("type") == "image_url")
        task = (fmt.get("json_schema") or {}).get("name") or task or fmt.get("type") or (
            "web_search" if path.endswith("/web") else "scholar_search" if path.endswith("/scholar")
            else "research_" + path.rsplit("/", 1)[-1].replace("-", "_") if path.startswith("/agents/") else "")
        line = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "request": getattr(_ctx, "request", None) or "", "call": call, "attempt": retried, "final": final,
                "path": path, "asked_model": body.get("model"), "answered_model": (out or {}).get("model"), "task": task,
                "images": images, "status": status, "retried": retried, "seconds": round(seconds, 2), "usage": (out or {}).get("usage"),
                **({"note": note} if note else {})}
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + chr(10))
    except Exception:
        pass


def tally(request_id: str, path: Path | None = None) -> dict:
    """의뢰 하나가 쓴 호출을 일(틀 이름)마다 센다: {일: {'calls' 물음 수, 'attempts' 보낸 시도 수, 'failed' 실패로 끝난 물음 수, 'tokens' 토큰 합}}."""
    out: dict = {}
    try:
        lines = (path or LOG_PATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        try:
            it = json.loads(raw)
        except ValueError:
            continue
        if it.get("request") != request_id:
            continue
        t = out.setdefault(it.get("task") or "글", {"calls": 0, "attempts": 0, "failed": 0, "tokens": 0})
        t["attempts"] += 0 if it.get("note", "").startswith("보내지 않음") else 1
        if it.get("final", True):
            t["calls"] += 1
            t["failed"] += it.get("status") != 200
        t["tokens"] += ((it.get("usage") or {}).get("total_tokens") or 0)
    return out


# ---- 의뢰 하나가 쓴 Liner — 호출 수 · 걸린 시간 · 어림 비용 · 일마다 답한 모델(앱 카드의 한 줄) ----
# 단가: 문서 Pricing(11-22) 의 100만 토큰당 달러(입력, 출력). 표에 없는 새 판(gpt-6.2-sol · claude-fable-5.2 등)은 같은 이름 계열의 값으로 어림한다.
# 캐시 할인 · 긴 글 값은 따지지 않는다 — 어림이다.
PRICES = {"flash-lite": (0.30, 2.50), "luna": (0.10, 0.50), "terra": (1.00, 5.00), "sol": (2.00, 10.00), "astra": (10.00, 50.00),
          "haiku": (0.10, 0.50), "sonnet": (2.00, 10.00), "opus": (4.00, 20.00), "fable": (10.00, 50.00), "flash": (0.75, 3.75), "argon": (2.00, 10.00)}
PER_CALL = {"/tools/search/web": 0.002, "/tools/search/scholar": 0.0003, "/agents/quick-answer": 0.003, "/agents/search": 0.02,
            "/agents/deep-research": 0.2}       # 요청 한 번 값(웹 검색은 프리미엄 데이터 포함 기본값)


def _price(model: str | None) -> tuple[float, float] | None:
    m = (model or "").lower()
    return next((PRICES[k] for k in PRICES if k in m), None)


def summary(request_id: str, path: Path | None = None) -> dict:
    """의뢰 하나의 Liner 사용: {'calls' 물음 수, 'failed', 'seconds' 기다린 시간 합, 'usd' 어림 비용, 'guess' 단가를 모르는 호출이 섞였나,
    'models' [(일, 답한 모델)] — 나온 차례대로, 같은 짝은 한 번}."""
    out = {"calls": 0, "failed": 0, "seconds": 0.0, "usd": 0.0, "guess": False, "models": []}
    try:
        lines = (path or LOG_PATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        try:
            it = json.loads(raw)
        except ValueError:
            continue
        if it.get("request") != request_id or str(it.get("note", "")).startswith("보내지 않음"):
            continue
        out["seconds"] += float(it.get("seconds") or 0)
        if not it.get("final", True):
            continue
        out["calls"] += 1
        if it.get("status") != 200:
            out["failed"] += 1
            continue
        usage, p = it.get("usage") or {}, it.get("path") or ""
        model = it.get("answered_model") or it.get("asked_model") or p.rsplit("/", 1)[-1]
        if isinstance(usage.get("cost"), (int, float)):
            out["usd"] += usage["cost"]
        elif p in PER_CALL:
            out["usd"] += PER_CALL[p]
        elif _price(model):
            pin, pout = _price(model)
            out["usd"] += ((usage.get("prompt_tokens") or 0) * pin + (usage.get("completion_tokens") or 0) * pout) / 1e6
        else:
            out["guess"] = True
        pair = (it.get("task") or "", str(model))
        if pair not in out["models"]:
            out["models"].append(pair)
    out["seconds"] = round(out["seconds"], 1)
    out["usd"] = round(out["usd"], 4)
    return out


def _post(path: str, body: dict, *, bearer: bool, timeout: float = 120, retries: int = 3, task: str = "", note: str = "") -> dict:
    key = api_key()
    call = secrets.token_hex(3)
    rid = getattr(_ctx, "request", None)
    refused = _refused(rid)
    if refused:     # 같은 의뢰에서 이미 키·잔액 문제로 거절됐다 — 보내지 않는다
        _log(path, body, refused, 0, 0.0, None, call=call, task=task, note="보내지 않음: 같은 의뢰의 앞선 호출이 거절됨")
        raise LinerError(refused, "같은 의뢰의 앞선 호출이 거절되어(키·잔액) 다시 보내지 않았습니다. 키·잔액을 고친 뒤에는 의뢰를 새로 보내 주세요.")
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers["x-api-key"] = key
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    t0 = time.perf_counter()
    for attempt in range(retries + 1):
        req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method="POST")
        t1 = time.perf_counter()
        try:
            with _open(req, timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
                _log(path, body, 200, attempt, time.perf_counter() - t1, out, call=call, task=task, note=note)
                return out
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            wait = e.headers.get("Retry-After") if e.headers else None
            pause = float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2 ** attempt
            again = e.code in _RETRYABLE and attempt < retries and (time.perf_counter() - t0) + pause < RETRY_WINDOW
            _log(path, body, e.code, attempt, time.perf_counter() - t1, None, call=call, task=task, final=not again)
            if again:
                time.sleep(pause)
                continue
            # 키(401) · 잔액(402)은 같은 의뢰의 다음 호출도 똑같이 거절된다 — 의뢰째 막는다.
            # 403 은 그 기능만 권한이 없거나 그 내용만 거절된 것일 수 있어(검색 · 그림 · 받아쓰기) 이 호출만 실패로 돌린다
            if e.code in (401, 402) and rid:
                _blocked[rid] = (e.code, time.monotonic())
            raise LinerError(e.code, text) from None
        except urllib.error.URLError as e:      # 보내지 못했다(접속·이름 풀이 실패) — 접수된 것이 없으니 다시 보내도 된다
            again = attempt < retries and (time.perf_counter() - t0) + 2 ** attempt < RETRY_WINDOW
            _log(path, body, 0, attempt, time.perf_counter() - t1, None, call=call, task=task, final=not again, note="보내지 못함")
            if again:
                time.sleep(2 ** attempt)
                continue
            err = LinerError(0, f"연결 실패: {e.reason}")
            err.unsent = True       # 보내지 못했다 — 접수된 것이 없어 다른 라우터로 보내도 두 번 처리되지 않는다
            raise err from None
        except (TimeoutError, OSError):         # 보낸 뒤 답을 기다리다 끊겼다 — 접수됐는지 알 수 없다. 다시 보내면 두 번 처리될 수 있어 다시 보내지 않는다
            _log(path, body, 0, attempt, time.perf_counter() - t1, None, call=call, task=task, note="접수 여부 불명: 답을 기다리다 끊김")
            raise LinerError(0, "Liner 의 답을 기다리다 끊겼습니다 — 요청이 접수됐는지 알 수 없어 다시 보내지 않았습니다.") from None
        except http.client.HTTPException:       # 답이 오다가 중간에 끊겼다(IncompleteRead 등) — 접수는 됐을 수 있다. 기록을 남기고 다시 보내지 않는다
            _log(path, body, 0, attempt, time.perf_counter() - t1, None, call=call, task=task, note="접수 여부 불명: 답이 중간에 끊김")
            raise LinerError(0, "Liner 의 답이 중간에 끊겼습니다 — 요청이 접수됐는지 알 수 없어 다시 보내지 않았습니다.") from None
        except ValueError:                      # 답은 왔는데 JSON 이 아니다 — 처리는 됐을 수 있다. 다시 보내지 않는다
            _log(path, body, 0, attempt, time.perf_counter() - t1, None, call=call, task=task, note="답을 알아볼 수 없음(응답 200 · JSON 아님)")
            raise LinerError(0, "Liner 의 답을 알아볼 수 없습니다(JSON 이 아님) — 같은 요청을 다시 보내지 않았습니다.") from None
    raise LinerError(0, "재시도 횟수를 넘었습니다.")


# ---- 일마다 라우터 · 전략 — 앱의 모델 호출은 이 표를 따른다(README 'Liner API 를 사용한 기능'과 같다) ----
# 라우터는 그 회사 모델 가운데 어려운 요청만 비싼 모델로 보낸다. 전략은 우선순위만 준다: cost(싼 모델 먼저) · balanced(기본) · quality(좋은 모델 먼저).
# 그림이 든 요청은 전략과 상관없이 그림 읽는 모델로 고정된다(gpt → gpt-6.1-sol 계열, claude → claude-sonnet-5.5 — 문서).
# 원칙: 만든 쪽과 검사하는 쪽은 다른 회사 모델(읽기 gpt ↔ 감사 claude, 담당 claude ↔ 검수 gpt).
# 11-22 실측: 지명 정식 이름을 cost 로 물으면 gpt-6.1-terra 가 4.4초(balanced 는 gpt-6-sol 2.5초)에 답하고 경기장 이름이 지도 이름에서 멀어져 balanced 로 둔다.
ROUTES = {
    # 의뢰서 그림 받아쓰기 — 다른 읽기보다 먼저, 틀 없이 글씨만(11-23 실측: 같은 손그림을 gpt-6.2-sol 이 claude-sonnet-5.5 보다 맞게 읽었다)
    "sheet_transcribe": ("liner-gpt-router", "quality"),
    # 의뢰 읽기(글 · 의뢰서 그림) — 그림이 있으면 어차피 고정 모델
    "map_request":("liner-gpt-router", "balanced"), "rank_board_request": ("liner-gpt-router", "balanced"),
    "info_board_plan": ("liner-gpt-router", "balanced"), "card_quiz_request": ("liner-gpt-router", "balanced"),
    # 지명 · 그림 주제 같은 짧은 판단
    "place_names": ("liner-gpt-router", "balanced"), "place_search": ("liner-gpt-router", "balanced"),
    "place_pick": ("liner-gpt-router", "balanced"), "icon_subjects": ("liner-gpt-router", "balanced"),
    # 수정 지시 — 바뀔 항목만 뽑는다
    "map_revision": ("liner-gpt-router", "balanced"), "board_revision": ("liner-gpt-router", "balanced"),
    # 검사 — 판독 감사는 읽은 쪽과 다른 회사(모델은 audit 가 고른다), 화면 · 시안 검수는 그림을 읽는다
    "reading_audit": ("liner-claude-router", "balanced"),
    "screen_proof": ("liner-gpt-router", "quality"), "scene_judge": ("liner-gpt-router", "quality"),
    # 맞춤 그래픽 메인 · 담당 — 도구 호출로 계획하고 일한다
    "scene_agent": ("liner-claude-router", "quality"),
    # CG Director — 의뢰를 맨 먼저 받아 실무 확인을 적용하고 일을 나눈다(도구 호출). 모든 의뢰가 거치므로 기다림을 줄이려 balanced
    "director": ("liner-claude-router", "balanced"),
}


def _is_router(model: str) -> bool:
    return model.startswith("liner-") and "-router" in model


# 라우터가 실패하면(접속 실패로 보내지 못함 · 5xx) 다른 회사 라우터로 한 번 더 — 한 회사 쪽이 막혀도 의뢰가 끝까지 간다.
# 답을 기다리다 끊긴 것은 접수됐을 수 있어 바꿔 보내지 않는다(같은 라우터로도 다시 보내지 않는 원래 규칙 그대로).
# 키 · 잔액 · 권한(401 · 402 · 403)과 요청 오류(400)는 라우터를 바꿔도 같아서 하지 않는다.
# 만든 쪽과 검사하는 쪽을 다른 회사로 두는 일(판독 감사 · 시안 검수)은 부르는 쪽이 세 번째 회사(fallback=…)를 준다.
FALLBACK = {"liner-gpt-router": "liner-claude-router", "liner-claude-router": "liner-gpt-router", "liner-gemini-router": "liner-gpt-router"}


# 모델 직접 지정(11-24 실측) — 라우터는 그림이 든 물음을 전략과 상관없이 claude-sonnet-5.5 로 보낸다(호출 기록: 그림 있음 147/147,
# 글만 + quality 인 scene_agent 는 claude-opus-5.5 60/60). 의뢰서 그림을 직접 보는 일(메인 · 담당 · CG Director)이 늘 Sonnet 으로 가던 까닭이다.
# model 에 '회사/모델' 을 주면 그림이 있어도 그 모델이 답한다. 이 표의 일은 부른 쪽이 ROUTES 의 라우터를 그대로 줘도 이 모델로 보낸다.
# 직접 지정이 거절되면(그런 모델 없음 · 권한 없음) 그 뒤로는 원래 라우터로 — 심사자의 키로 안 되더라도 의뢰는 끝까지 간다.
# 기본은 꺼 둔다 — .env 의 LINER_DIRECT=on 으로 켠다. 실측(11-24, 손그림 '아이템 구매리스트' 의뢰서 한 장을 접수 → 지시서 → 제작 → 검수까지):
#   라우터 11번 · 88초 · 약 $0.35 / 직접 지정 14번 · 197초 · 약 $1.04 — 둘 다 같은 검수 항목(화살표가 하나씩 나오는가)에서 '미해결 제출'.
#   막힌 것은 모델이 아니라 검수판이 네 순간만 찍어 차례 등장을 못 보는 것이었다. 시안은 직접 지정 쪽이 조금 낫다(화살표 색 · 물건 아이콘).
DIRECT = {"scene_agent": "anthropic/claude-opus-5.5", "director": "anthropic/claude-opus-5.5"}
_direct_refused: set[str] = set()       # 이 실행에서 거절된 직접 지정 모델 — 다시 보내 보지 않는다


def direct_model(task: str | None, asked: str) -> str | None:
    """그 일을 직접 맡길 모델(없으면 None). 부른 쪽이 그 일의 라우터가 아닌 다른 모델을 골랐으면 그 선택을 따른다."""
    if os.environ.get("LINER_DIRECT", "").strip().lower() not in ("on", "1", "true", "yes"):
        return None
    route, direct = ROUTES.get(task or ""), DIRECT.get(task or "")
    return direct if direct and route and asked == route[0] and direct not in _direct_refused else None


def _model_refused(e: LinerError) -> bool:
    """모델 이름 자체가 거절됐나(400 · 403 · 404 이고 답이 모델을 가리킨다) — 요청 내용이 틀린 400 과 가른다."""
    return e.status in (400, 403, 404) and "model" in str(getattr(e, "body", "") or "").lower()


def chat(messages: list[dict], *, model: str | None = None, timeout: float = 120, retries: int = 3, task: str | None = None,
         fallback: str | bool = True, **options) -> dict:
    """모델 호출. OpenAI chat.completion 형식의 응답 전체를 돌려준다.

    timeout(초)·retries 는 기다리는 한도다. 요청 본문에는 들어가지 않는다.
    task = 일 이름(ROUTES). 안 주면 json_schema 이름을 쓴다. 그 일의 라우터(model 을 안 줬고 LINER_MODEL 도 없을 때)와
    전략(routing_strategy 를 안 줬을 때)을 표에서 채운다.
    fallback = 라우터가 실패했을 때 한 번 더 보낼 라우터(True 면 FALLBACK 표, False 면 하지 않음, 글자면 그 라우터).

    options 예: max_completion_tokens=800, reasoning_effort="low",
                response_format={"type": "json_object"}, tools=[...],
                routing_strategy="cost" | "balanced" | "quality"
    temperature·top_p·seed 는 받기만 하고 반영되지 않는다(문서 기준).
    """
    task = task or ((options.get("response_format") or {}).get("json_schema") or {}).get("name")
    route = ROUTES.get(task or "")
    body = {"model": model or os.environ.get("LINER_MODEL") or (route[0] if route else DEFAULT_MODEL), "messages": messages}
    body.update(options)
    direct = direct_model(task, body["model"])
    if direct:
        try:
            return _post("/chat/completions", {**body, "model": direct}, bearer=True, timeout=timeout, retries=retries, task=task or "")
        except LinerError as e:
            # 모델 이름이 거절됐으면(바로 돌아온다 — 과금 없음) 원래 라우터로 이어 가고, 이 실행에서는 다시 직접 보내지 않는다.
            # 5xx · 보내지 못함은 fallback 을 켠 호출만 라우터로 넘긴다. 답을 기다리다 끊긴 것은 접수됐을 수 있어 그대로 올린다
            refused = _model_refused(e)
            if refused:
                _direct_refused.add(direct)
            elif not (fallback and (e.status >= 500 or getattr(e, "unsent", False))):
                raise
            body.setdefault("routing_strategy", route[1])
            return _post("/chat/completions", body, bearer=True, timeout=timeout, retries=retries, task=task or "",
                         note=f"직접 지정 {direct} {'거절' if refused else '실패'}({e.status or '끊김'}) — 라우터로 다시")
    if route and _is_router(body["model"]):
        body.setdefault("routing_strategy", route[1])
    try:
        return _post("/chat/completions", body, bearer=True, timeout=timeout, retries=retries, task=task or "")
    except LinerError as e:
        other = (fallback if isinstance(fallback, str) else FALLBACK.get(body["model"])) if fallback and _is_router(body["model"]) else None
        # 답을 기다리다 끊김 · 중간에 끊김 · 알아볼 수 없는 답은 접수됐을 수 있어 다시 보내지 않는다(두 번 처리 · 두 번 과금) — 보내지 못한 것과 5xx 만
        if not other or other == body["model"] or not (e.status >= 500 or getattr(e, "unsent", False)):
            raise
        return _post("/chat/completions", {**body, "model": other}, bearer=True, timeout=timeout, retries=retries, task=task or "",
                     note=f"다른 회사 라우터로 다시 — {body['model']} 실패({e.status or '끊김'})")


def chat_text(prompt: str, *, system: str | None = None, **options) -> str:
    """질문 하나 넣고 답 글자만 받는 지름길."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    return chat(messages, **options)["choices"][0]["message"]["content"] or ""


def search_web(query: str, *, max_results: int = 10, include_knowledge: bool = True, timeout: float = 120, retries: int = 3,
               **options) -> dict:
    """웹 검색. results[] 마다 title·url·hostname·description·date 가 온다.

    include_knowledge=True 면 통계형 질의에 knowledge[](표 데이터)가 붙는다.
    요금: True 1,000건당 $2, False 1,000건당 $1.
    timeout(초)·retries 는 기다리는 한도다. 요청 본문에는 들어가지 않는다.
    options 예: date_range="past_week", country_code="KR", lang="ko"
    """
    body = {"query": query, "max_results": max_results, "include_knowledge": include_knowledge}
    body.update(options)
    return _post("/tools/search/web", body, bearer=False, timeout=timeout, retries=retries)


def search_scholar(query: str, *, max_results: int = 10, **options) -> dict:
    """학술 검색. results[] 에 citationCount·authors·journal 이 더 붙는다."""
    body = {"query": query, "max_results": max_results}
    body.update(options)
    return _post("/tools/search/scholar", body, bearer=False)


def research(question: str, *, agent: str = "quick-answer", timeout: float = 90, retries: int = 1) -> dict:
    """출처가 붙은 답(Liner 에이전트). quick-answer = 빠르고 짧은 답 · 출처 3개 안팎(0.003달러), search = 표 · 칸마다 인용(0.02달러, 30초 안팎).
    돌려주는 것: {'answer', 'references': [{title · url · hostname · description · date}]} (stream: false — 한 번에 받는다)."""
    body = {"messages": [{"role": "user", "content": question}], "stream": False}
    return _post(f"/agents/{agent}", body, bearer=False, timeout=timeout, retries=retries, task="research_" + agent.replace("-", "_"))


def as_sources(results: list[dict]) -> str:
    """검색 결과를 모델에 넘길 번호 붙은 출처 목록으로 만든다. 답에 [1] 식으로 인용시키는 용도."""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('title', '')} ({r.get('date') or '날짜 없음'})\n    {r.get('url', '')}\n    {r.get('description', '')}")
    return "\n".join(lines)
