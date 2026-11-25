"""실무 확인 목록(노하우 장부) — 방송 CG 감독이 의뢰 한 건마다 돌리던 확인 순서를 번호(K01 …)로 모은 것.

원본은 같은 폴더의 knowhow.json 하나다. CG Director(director.py)의 지시문 · 접수 판단 카드의 '적용한 실무 확인' ·
저장소 맨 위 노하우_실무확인목록.md(tools/knowhow_md.py) 가 모두 여기서 나온다. 규칙마다 그것을 실제로 지키는 코드 자리(code)가 있다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent / "knowhow.json"
ROOT = PATH.parent.parent
STAGES = ["접수", "확인", "화면", "납품"]


@lru_cache(maxsize=1)
def rules() -> tuple[dict, ...]:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    return tuple(r for r in data["rules"] if isinstance(r, dict) and r.get("id"))


def by_id() -> dict[str, dict]:
    return {r["id"]: r for r in rules()}


def for_stages(*stages: str) -> list[dict]:
    return [r for r in rules() if r.get("stage") in stages]


def prompt_lines(*stages: str) -> list[str]:
    """지시문에 넣을 줄: '- K02 '아님'은 빼는 조건: 규칙'."""
    return [f"- {r['id']} {r['name']}: {r['rule']}" for r in for_stages(*stages)]


def missing_code() -> list[str]:
    """규칙이 가리키는 코드 자리(파일과 그 안의 글자)가 없는 것 — 시험이 빈 목록인지 본다('말만 있는 노하우'를 막는다)."""
    out = []
    for r in rules():
        for c in r.get("code") or []:
            path = ROOT / c["file"]
            if not path.is_file() or c["anchor"] not in path.read_text(encoding="utf-8"):
                out.append(f"{r['id']}: {c['file']} 에 '{c['anchor']}' 가 없다")
    return out


def markdown() -> str:
    """심사자 · 제작진이 읽는 목록(노하우_실무확인목록.md)."""
    lines = ["# 실무 확인 목록 — 방송 CG 감독의 접수 노하우",
             "",
             "의뢰 한 건마다 머릿속에서 돌리던 확인 순서를 번호로 모았다. 앱의 **CG Director** 가 의뢰를 받으면 이 목록의 '접수 · 확인' 항목을 적용하고,",
             "접수 판단 카드에 적용한 번호와 의뢰 원문의 근거 구절을 보여 준다. '화면 · 납품' 항목은 그리기 코드와 검수가 지킨다.",
             "",
             "원본은 `mapcg/knowhow.json` 이고 이 파일은 `python tools/knowhow_md.py` 가 만든다(손으로 고치지 않는다).",
             "**지키는 곳**의 코드 자리가 실제로 있는지는 시험(`tests/test_director.py`)이 확인한다.",
             ""]
    for stage in STAGES:
        rows = for_stages(stage)
        if not rows:
            continue
        lines += [f"## {stage}", "", "| 번호 | 이름 | 규칙 | 지키는 곳 | 코드 |", "|---|---|---|---|---|"]
        for r in rows:
            code = " · ".join(f"`{c['file']}`" for c in r.get("code") or [])
            lines.append(f"| {r['id']} | {r['name']} | {r['rule']} | {r.get('keeper', '')} | {code} |")
            if r.get("story"):
                lines.append(f"| | ↳ 겪은 일 | {r['story']} | | |")
        lines.append("")
    return "\n".join(lines)
