"""의뢰서 그림 받아쓰기 — 다른 읽기보다 먼저, 그림의 글씨를 틀 없이 그대로 받아 적는다.

11-23 사용자 시험: 손그림 의뢰서를 앱은 'PMC역' · '5, 2, 8초' 로 읽고 끊어진 세 구간을 하나의 경로로 묶었다.
같은 그림을 칸 채우기 틀(json_schema) 없이 '받아 적기 · 요약'만 시키니 gpt-6.2-sol 은 'DMC역 → 수색역 / 수색역 → 서부운전면허 /
가양대교 북단 → 일산으로 빠지는 길 · 각각 5~8초 · 3종 순서대로'를 맞게 읽었다(claude-sonnet-5.5 는 더 틀렸다 — 라우터가 그림이 든
요청을 고정 모델로 보내므로 클로드 쪽은 고를 수 없다).
그래서 그림을 먼저 GPT 로 받아 적고, 받아 적은 글을 메인 에이전트 · 의뢰서 읽기에 그림과 함께 넘긴다.
판독 감사에는 넘기지 않는다 — 감사는 받아 적은 글에 기대지 않고 그림을 따로 읽어야 틀린 곳을 잡는다.
받아쓰기가 실패해도 의뢰는 예전처럼 그림만으로 읽는다.
"""
from __future__ import annotations

import time

TASK = "sheet_transcribe"
MAX_IMAGES = 4
MAX_CHARS = 2500        # 받아 적은 글의 길이 한도(자) — 의뢰 글과 합쳐 읽기에 넘긴다
HEAD = "[의뢰서 그림 받아쓰기 — 앱이 먼저 그림의 글씨를 받아 적은 것이다. 의뢰자가 쓴 글이 아니며, 그림과 다르면 그림이 맞다]"

PROMPT = """방송 그래픽(CG) 의뢰서 사진이다. 사진 속 글씨를 해석하기 전에 먼저 그대로 받아 적는다.

받아쓰기
- 위에서 아래로, 빠짐없이, 줄 단위로 적는다. 번호(①②③ · 1. 2.)와 화살표(→ ↓)로 이어진 관계를 살린다.
- 화면 스케치(네모 · 휴대폰 모양) 안에 적힌 글자는 [스케치 안] 이라고 표시하고, 스케치가 무엇을 그린 것인지 한 줄로 적는다.
- 흐리거나 엇갈리는 글자는 가장 그럴듯한 읽기를 적고 괄호에 다른 후보를 적는다. 지명은 실제 있는 이름으로 읽힐 수 있으면 그 이름을 먼저 적는다(예: 'PMC역'처럼 보이면 'DMC역 (PMC역처럼도 보임)').
- 줄을 긋거나 덧칠해 지운 글자는 [지움] 으로 적고, 보이면 무엇을 지웠는지 괄호에.
- 숫자 · 범위 · 단위는 보이는 그대로(예: '각각 5~8초').
- 그림이 여러 장이면 '그림 1', '그림 2' 로 나눠 적는다.

요약
- 마지막에 '요약' 아래에 이 의뢰가 원하는 것을 3~5줄로 적는다: 무엇을 몇 개 · 어떤 순서로 · 각각 얼마 동안 · 구간마다 시작과 끝.
- 구간들이 서로 이어지는지(앞 구간의 끝이 다음 구간의 시작인지), 따로 떨어진 구간인지도 적는다.
- 확실하지 않은 것은 '확실하지 않음'이라고 적는다. 지어내지 않는다.

답은 받아쓰기와 요약만, 꾸밈 없이 평범한 글로."""


def run(images, *, chat=None, timeout: float = 90, retries: int = 1) -> dict:
    """그림(data URL 목록)을 받아 적는다. 돌려주는 것: {'ok', 'text', 'model', 'seconds', 'why'}.

    ok 가 거짓이면 text 는 비어 있고 why 에 까닭이 있다 — 부르는 쪽은 그림만으로 예전처럼 읽는다.
    """
    pics = [u for u in images or [] if isinstance(u, str) and u.startswith("data:image/")][:MAX_IMAGES]
    if not pics:
        return {"ok": False, "text": "", "model": "", "seconds": 0.0, "why": "받아 적을 그림이 없습니다"}
    if chat is None:
        from mapcg import intake
        chat = intake._liner().chat
    content = [{"type": "text", "text": PROMPT}, *[{"type": "image_url", "image_url": {"url": u}} for u in pics]]
    t = time.monotonic()
    try:
        resp = chat([{"role": "user", "content": content}], task=TASK, max_completion_tokens=6000, timeout=timeout, retries=retries)
        choice = resp["choices"][0]
        text = str(choice["message"]["content"] or "").strip()
    except Exception as e:      # noqa: BLE001 — 연결 · 한도 · 알아볼 수 없는 답: 받아쓰기 없이 예전처럼 읽는다
        return {"ok": False, "text": "", "model": "", "seconds": round(time.monotonic() - t, 1), "why": str(e)[:160]}
    seconds = round(time.monotonic() - t, 1)
    if not text:
        why = "답이 길이 한도에서 잘렸습니다" if choice.get("finish_reason") == "length" else "빈 답이 왔습니다"
        return {"ok": False, "text": "", "model": str(resp.get("model") or ""), "seconds": seconds, "why": why}
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " …(받아쓰기가 길어 여기까지만 넘깁니다)"
    return {"ok": True, "text": text, "model": str(resp.get("model") or ""), "seconds": seconds, "why": ""}


def attach(text: str, sheet: str) -> str:
    """의뢰 글 뒤에 받아 적은 글을 붙인다(읽는 쪽에 넘길 글). 이미 붙어 있으면 다시 붙이지 않는다."""
    if not sheet or HEAD in (text or ""):
        return text
    return f"{text}\n\n{HEAD}\n{sheet}".strip()
