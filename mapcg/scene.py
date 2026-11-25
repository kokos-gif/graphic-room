"""맞춤 그래픽(scene) — 앱에 틀이 없는 그래픽 의뢰를 메인 에이전트가 읽고, 의뢰마다 임시 담당(서브 에이전트)을 스스로 꾸려 시안(정지 · 움직임)을 만든다.

메신저(server.Desk)에서는 두 단계로 쓴다: 읽을 때 plan_request(메인이 지시서를 쓰고 담당을 정의 → 지시서 카드),
사람이 지시서 카드에서 '만들기'를 누르면 make_request(담당들이 일하고 메인이 다시 시키거나 납품 → 시안 카드).
명령줄 실험(tools/agent_lab.py)은 두 단계를 한 번에 돈다. 실험 결과는 output/에이전트실험/ 아래에만 쌓인다(깃 · 제출 묶음 밖).

    python tools/agent_lab.py run 시험의뢰/에이전트_개발/04_진술대조.txt [--tries 4]
    python tools/agent_lab.py batch 시험의뢰/에이전트_개발 [--tries 4]       폴더의 .txt 를 차례로
    python tools/agent_lab.py samples                                       배치 9종 보기 그림(Liner 를 부르지 않는다)
    python tools/agent_lab.py shoot <장면.json>                              장면 설계 하나를 다시 그리기만(Liner 를 부르지 않는다)
    python tools/agent_lab.py export <시안 폴더> [--alpha]                  납품: MP4(30fps) + 투명 PNG 시퀀스 — 무거운 일이라 공용 대기열로 돌린다

메인(Liner 도구 호출): 의뢰 → write_brief(제작 지시서) + spawn_worker(임시 담당) 또는 report_unable(못 하는 까닭).
  미리 정해 둔 담당 목록은 주지 않는다 — 우리 그림 코드가 그릴 수 있는 배치(재료) 목록만 준다.
  담당이 끝나면 spawn_worker 의 도구 결과로 결과와 마지막 시안이 메인에게 돌아가고, 메인이 redo_worker(다시 시킴) 나 deliver(납품)를 고른다.
담당(서브 에이전트, 자기 대화 + 도구): draw_draft(시안 그리기 · 코드 검사 · 자동 검사, 과금 없음) · request_review(Liner 그림 읽기로 합격 기준 판정)
  · submit · give_up 을 언제 몇 번 부를지 스스로 정한다. 코드는 그리기 · 검수 한도만 지킨다.
모델은 그림 코드를 짜지 않는다 — 장면 설계(JSON)만 쓰고 그리는 것은 templates/scene.html(우리 화풍 코드)이다.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

from . import board, build, export, icons, intake, plan, proof, request

KIND, KIND_LABEL = "scene", "맞춤 그래픽"
PICTURED = "의뢰서 그림"   # 사실의 source — 의뢰서 그림에서 읽은 것(글에는 없다). 코드가 대조하지 못하므로 지시서 카드에서 사람이 본다
ROOT = Path(__file__).resolve().parent.parent
PAGE = build.TEMPLATES / "scene.html"
OUT = ROOT / "output" / "에이전트실험"
MAIN_MODEL = "liner-claude-router"
WORKER_MODEL = "liner-claude-router"
JUDGE_MODEL = "liner-gpt-router"        # 그림이 든 물음 — 화면 검수(proof.py)와 같은 라우터
MAX_TRIES = 4
COLORS = ["blue", "red", "yellow", "green", "purple", "pink", "orange", "aqua"]
MOTION_IN = ["none", "fade", "pop", "slide_up", "slide_left"]
MOTION_OUT = ["none", "fade", "slide_down", "slide_left"]
MOTION_ORDER = ["together", "one_by_one", "swap"]      # swap: 큰 글자 판에서만 — 항목이 같은 자리에서 차례로 바뀐다(카운트다운)
FPS = 30

# 우리 그림 코드(templates/scene.html)가 그릴 수 있는 배치. 메인과 담당에게 주는 '재료 목록'이다
LAYOUTS = {
    "rows": "줄 목록 — 항목마다 가로 띠 한 줄(group 이름표 · label · 짧은 text · 오른쪽 value_text). 목록 · 일정 · 소개 · 순서에. 8줄까지",
    "receipt": "영수증 — 흰 종이에 품목과 값 줄. emphasis 줄은 굵은 두 줄 아래에 크게(합계 · 남은 것). 12줄까지",
    "grid": "칸 격자 — 전체 칸(grid_total) 가운데 항목마다 value 개만큼 그 색으로 칠한 동그라미 칸 + 오른쪽 범례(label, 없으면 group 이름 + value_text). 분포 · 비율을 칸으로 셀 때. 400칸까지",
    "bars": "막대 — 항목마다 왼쪽 이름(label, 없으면 group 이름) + value 에 비례한 가로 막대 + 끝에 value_text. 견주기 · 결과. 8개까지",
    "lanes": "시간표 — groups 가 가로줄(사람 · 장소), 항목의 time 이 세로 칸(시각). 같은 시각 칸을 위아래로 견줄 수 있다. 시각 5개 · 줄 5개까지",
    "columns": "나란히 — groups 가 세로 칸(4개까지), 칸마다 항목 카드를 쌓는다. empty 는 점선 빈 칸. 두 묶음 견주기 · 보관함 · 진술 카드. links 로 카드 사이 화살표",
    "ring": "둘러앉기 — 항목 카드(label · 한 줄 text, group 은 카드 색으로만)를 타원 둘레에 놓고 links 로 카드 사이에 이름표 붙은 화살표(방향 · 점선 · 양쪽). 관계도. 8장까지",
    "chat": "대화 — 항목마다 보낸 사람 이름표(group 이름, group 이 없으면 label) + 말풍선(text) + 오른쪽 보낸 시각(time). emphasis 말풍선은 노랗게. 7개까지",
    "banner": "하단 띠 — 방송 화면 아래에 깔리는 띠 하나, 글 1~2줄(항목마다 group 이름표 · label 이름표 · text). title 은 띠 위 작은 머리표. 상황 설명 자막",
    "headline": "큰 글자 판 — 화면 가운데 큰 글자 하나(value_text, 없으면 text). label 은 그 위 머리표, 둘째 항목은 아래 줄. 아이캐치 · 점수 · 오프닝 제목. 2항목까지. "
                "motion.order 가 swap 이면 항목(2~6개)이 같은 자리에서 하나씩 차례로 바뀐다 — 카운트다운 5 · 4 · 3 · 2 · 1 처럼(길이를 항목 수로 똑같이 나눈다: 5개 · 3초면 하나에 0.6초). "
                "항목마다 글자 색이 다르고, 찍힐 때마다 등장 효과(퍼지는 원 고리 · 튀는 색 조각 · 가로지르는 평행 띠)가 잠깐 붙었다 사라진다 — 색과 효과는 코드가 자동으로 붙인다. "
                "over_video 면 바탕판은 없고 색 글자와 이 등장 효과만 그려진다(바탕은 투명). "
                "등장 효과는 장식이라 확인할 점에 넣지 않는다 — '글자만 보이는가' · '효과가 보이는가' 같은 기준은 쓰지 않는다(검수 그림은 효과가 걷히고 글자가 자리 잡은 순간을 항목마다 찍는다)",
    "wipe": "덮는 전환 — 비스듬한 평행 띠(groups 색, 없으면 기본 색)가 화면을 덮었다가 빠진다. 덮인 동안 첫 항목의 text 가 가운데 잠깐. 바탕 투명 · 길이 1~2초",
}
LAYOUT_LABELS = {k: v.split(" — ")[0] for k, v in LAYOUTS.items()}     # 앱 카드에 보이는 짧은 이름(줄 목록 · 영수증 …)
COMMON = ("모든 배치에 붙일 수 있는 것: 짙은 제목판(title) · 제목 아래 한 줄(subtitle) · 아래 한 줄 주석(note) · 붉은 배지 표시(marks) · "
          "노란 강조(emphasis) · 점선 빈 칸(empty) · 사람/편 색(색 이름만) · 소품 아이콘(아래 목록의 코드만) · "
          "움직임(motion: 들어오기 · 나가기 · 순서만 고르고 곡선과 때는 코드가 정함) · 길이(duration_seconds) · 방송 화면 위에 얹기(over_video: 바탕 없이 투명)")
CANNOT = ("할 수 없는 것: 사진 · 영상 · 첨부 그림을 화면에 넣거나 그 위에 위치를 표시하기, 자유 도형 · 기계 구조 그림, 지도 위에 그리기, "
          "항목이 아닌 자리(제목 옆 · 바탕)에 두는 장식 그림 — 항목 이름에 맞는 작은 그림은 코드가 항목마다 붙인다, 촬영본(영상) 위 합성, 실제 두 장면을 섞는 전환(앞뒤 원본이 필요)")
EXISTING = {
    "지도": "장소 사이 이동 경로를 지도 위에 그리는 영상(출발 · 도착 · 경유지 · 탈것)",
    "네비": "휴대폰 길안내 화면 모양의 이동 영상",
    "순위판": "순위 · 점수 표를 줄 띠로 보여 주는 판(순위 변동 포함)",
    "정보판": "VS 대결판(두 편의 값 견주기) 또는 번호 붙은 설명판(규칙)",
    "카드 퀴즈": "그림 카드로 내는 퀴즈 영상",
}
ROUTES = ["기존 담당", "자료 필요", "사람 지정 필요", "새 도구 필요"]
ICON_LINE = "아이콘 코드: " + " · ".join(f"{k} {v}" for k, v in plan.ICONS.items())


def _fn(name: str, desc: str, props: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(props), "additionalProperties": False}}}


S, B_, I_ = {"type": "string"}, {"type": "boolean"}, {"type": "integer"}
SL = {"type": "array", "items": S}
MAIN_TOOLS = [
    _fn("research", "의뢰를 그래픽으로 만들려면 의뢰 글에 없는 공개 사실(기록 · 순위 · 일정 · 수치)이 있어야 할 때, Liner 검색 에이전트로 찾는다. "
        "출처가 붙은 짧은 답과 출처 목록(번호 · 제목 · 사이트 · 날짜 · 원문)이 돌아온다. write_brief 앞에서, 필요할 때만(의뢰마다 2번까지).", {
            "question": {**S, "description": "찾을 것 한 문장 — 무엇을 어떤 기준으로(예: 'FIFA 월드컵 역대 우승 횟수 상위 5개국과 횟수')"},
            "why": {**S, "description": "의뢰의 어느 요청 때문에 필요한지"},
        }),
    _fn("write_brief", "이 의뢰의 제작 지시서를 한 장으로 쓴다. 자료를 찾았으면 그 뒤에, 아니면 맨 먼저 한 번 부른다.", {
        "viewer_point": {**S, "description": "시청자가 이 한 장에서 알아야 할 것, 한 문장"},
        "facts": {"type": "array", "description": "화면에 들어갈 사실. 의뢰 글이나 research 로 받은 출처에 있는 것만", "items": {
            "type": "object", "required": ["id", "fact", "quote", "source"], "additionalProperties": False, "properties": {
                "id": {**S, "description": "f1, f2 …"}, "fact": {**S, "description": "사실 하나"},
                "quote": {**S, "description": "그 사실의 근거 원문 일부(그대로 옮김) — 의뢰 글, 또는 research 출처의 원문 · 답"},
                "source": {**S, "description": "근거가 온 곳: '의뢰' 또는 research 출처 번호(예: r1-2)"}}}},
        "must_not_assert": {**SL, "description": "화면이 단정하면 안 되는 것"},
        "open_questions": {**SL, "description": "의뢰만으로 정할 수 없어 사람에게 물을 것"},
        "checks": {**SL, "description": "시안을 볼 때 확인할 점 — 검수자가 그림과 이 지시서의 사실(원문)만 보고 예/아니오로 답할 수 있게"},
    }),
    _fn("spawn_worker", "이 의뢰(또는 그 일부)를 맡을 임시 담당(서브 에이전트)을 만든다. 담당은 자기 도구로 시안을 그리고 검수받아 스스로 고친 뒤 결과를 돌려준다. "
        "독립된 그래픽이 둘 이상이면 담당마다 한 번씩 부른다.", {
            "role": {**S, "description": "담당 이름 — 이 의뢰에 맞게 지어라"},
            "goal": {**S, "description": "연출 목표 한두 문장"},
            "fact_ids": {**SL, "description": "맡을 사실 id(write_brief 의 facts)"},
            "layouts": {"type": "array", "description": "쓰게 할 배치(1~3개)", "items": {"type": "string", "enum": list(LAYOUTS)}},
            "checks": {**SL, "description": "이 담당의 합격 기준(그림과 원문만 보고 예/아니오로 판정할 수 있게)"},
            "max_tries": {**I_, "description": "검수받을 수 있는 횟수 한도(1~5)"},
        }),
    _fn("report_unable", "지금 재료로 이 의뢰(또는 그 일부)를 만들 수 없을 때 까닭을 알린다.", {
        "route": {"type": "string", "enum": ROUTES},
        "existing": {"type": "string", "enum": [*EXISTING, "없음"], "description": "route 가 '기존 담당'일 때 맡을 담당, 아니면 '없음'"},
        "reason": {**S, "description": "만들 수 없는 까닭"},
        "needed": {**SL, "description": "무엇이 있으면 만들 수 있는지"},
    }),
    _fn("redo_worker", "담당이 돌려준 결과와 시안을 보고, 고칠 점을 주어 그 담당을 다시 일하게 한다(담당마다 한 번).", {
        "worker": {**S, "description": "담당 번호(w1, w2 …)"},
        "instruction": {**S, "description": "고칠 점 — 담당이 바로 따를 수 있게 구체적으로"},
    }),
    _fn("deliver", "담당들의 결과를 보고 이 의뢰를 마친다. 맨 마지막에 한 번 부른다.", {
        "accept": {**SL, "description": "납품할 담당 번호(w1 …). 없으면 []"},
        "message": {**S, "description": "제작진에게 보낼 두세 문장 — 무엇을 만들었고, 무엇이 남았고, 무엇을 확인해 달라는지"},
    }),
]
FIRST_TOOLS, LAST_TOOLS = {"research", "write_brief", "spawn_worker", "report_unable"}, {"redo_worker", "deliver", "report_unable"}
MAX_RESEARCH = 2            # 의뢰 하나에서 자료 찾기(Liner 에이전트) 한도

MAIN_SYSTEM = "\n".join([
    "너는 방송 그래픽 제작실(Graphic Room)의 메인 에이전트다. 제작진이 평소 말투로 보낸 의뢰를 읽고 제작 지시서를 쓴 뒤, "
    "이 의뢰를 맡을 임시 담당을 스스로 정의해 띄우고, 담당들이 돌려준 결과를 보고 납품을 정한다. "
    "미리 정해 둔 담당은 없다 — 의뢰마다 역할 · 목표 · 맡을 사실 · 쓸 배치 · 확인할 점 · 시도 한도를 네가 정한다.",
    "",
    "[우리 그림 코드가 그릴 수 있는 배치] 담당은 이 가운데서만 고른다. 좌표 · 크기는 코드가 정한다.",
    *[f"- {k}: {v}" for k, v in LAYOUTS.items()],
    COMMON, ICON_LINE, CANNOT,
    "",
    "[이미 있는 담당]",
    *[f"- {k}: {v}" for k, v in EXISTING.items()],
    "",
    "순서:",
    "0. 의뢰 글에 없는 공개 사실(기록 · 순위 · 일정 · 수치)이 있어야 만들 수 있으면, 먼저 research 로 찾는다(의뢰마다 2번까지 — 그 답에는 research 만 부른다). "
    "출처에 없는 수치는 쓰지 않는다. 찾아도 없으면 report_unable(route='자료 필요'). 의뢰 글만으로 되면 research 를 부르지 않는다.",
    "1. write_brief 를 한 번 부른다. facts 는 의뢰 글이나 research 로 받은 출처에 있는 것만, quote 는 그 원문을 그대로 옮기고 "
    "source 에 '의뢰' 또는 출처 번호(r1-2)를 적는다. 이름 · 숫자 · 시각을 지어내지 않는다. "
    "research 출처의 사실을 맡은 담당의 확인할 점에는 '화면에 자료 출처(사이트 이름)가 보이는가'를 넣는다. "
    "확인할 점은 눈으로 볼 것 위주로 쓴다 — 길이 · 장수 · 바탕 투명 같은 기술 조건은 코드가 재서 검수자에게 실측으로 준다(화면에 '3초' 같은 글을 적어 증명하지 않는다).",
    f"의뢰서 그림이 같이 왔으면 그림도 의뢰 원문이다. 그림에만 있는 사실은 source 를 '{PICTURED}'으로 적고, quote 에는 그림에서 읽은 글자나 본 모양을 짧게 적는다"
    "(예: '① 10000 에서 ② 기차로 가는 화살표'). 화살표가 그려져 있으면 화살표마다 시작과 끝을 사실 하나씩으로 적고, 그 담당의 goal 에 "
    "'links 로 이 화살표들을 그린다'를, 확인할 점에 '그 화살표가 그 둘을 잇는가'를 넣는다. 화살표에 순서 번호가 있거나 '순서대로'라고 적혀 있으면 "
    "goal 에 '화살표가 차례로 나오게 motion.order 를 one_by_one 으로'를 적는다. 그림에서 읽을 수 있는 것은 묻지 않는다 — 흐려서 확실하지 않은 것만 open_questions 에 적는다.",
    "2. 그다음, 같은 답 안에서 아래를 부른다.",
    "  - 위 배치로 만들 수 있으면 spawn_worker. 독립된 그래픽이 둘 이상이면 여러 번. 담당 이름과 목표는 이 의뢰에 맞게 짓는다.",
    "    담당 한 명은 시안 한 장만 낸다. 항목 수가 배치의 한도(예: bars 8개 · rows 8줄)를 넘으면 지금 묶음마다 담당을 나눠 띄운다(네 팀 × 막대 4개 = 16개면 팀마다 한 명씩 네 명). "
    "담당들이 일을 마친 뒤에는 새 담당을 띄울 수 없다.",
    "  - 이미 있는 담당이 맡을 의뢰면 report_unable(route='기존 담당').",
    "  - 사진 속 위치처럼 사람이 찍어 줘야 하면 '사람 지정 필요', 구조 · 수치 같은 자료가 모자라면 '자료 필요', "
    "위 재료 조합으로 안 되는 표현이면 '새 도구 필요'. 무엇이 있으면 되는지 needed 에 적는다.",
    "  - 일부만 만들 수 있으면 만들 수 있는 부분은 spawn_worker, 나머지는 report_unable 을 함께 부른다.",
    "3. 담당들이 일을 마치면 spawn_worker 의 결과(담당 번호 · 결과 · 일한 순서 · 미해결 점)와 마지막 시안 그림이 돌아온다. "
    "시안을 직접 보고 지시서의 확인할 점과 견준다.",
    "  - 고칠 점이 있으면 redo_worker 로 그 담당을 다시 시킨다(담당마다 한 번).",
    "  - 마치면 deliver 를 부른다: 납품할 담당과 제작진에게 보낼 말. 미해결이 남았으면 숨기지 말고 message 에 적는다.",
    "도구만 부르고 다른 말은 하지 않는다.",
])

# 칸 이름에 "thinking" 을 쓰지 않는다 — Claude 라우터가 strict 틀의 그 이름을 내용 거름(content_filter)으로 막아 빈 답이 온다(2026-11-21 실측)
SCENE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["memo", "layout", "title", "subtitle", "note", "groups", "items", "links", "marks", "grid_total", "grid_cols", "motion", "duration_seconds", "over_video"],
    "properties": {
        "memo": S,
        "layout": {"type": "string", "enum": list(LAYOUTS)},
        "title": S, "subtitle": S, "note": S,
        "groups": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "name", "color"],
                                              "properties": {"id": S, "name": S, "color": {"type": "string", "enum": COLORS}}}},
        "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                             "required": ["id", "group", "label", "text", "value", "value_text", "time", "empty", "emphasis", "icon", "fact_ids"],
                                             "properties": {"id": S, "group": {"type": ["string", "null"]}, "label": S, "text": S,
                                                            "value": {"type": ["number", "null"]}, "value_text": S, "time": {"type": ["string", "null"]},
                                                            "empty": B_, "emphasis": B_, "icon": {"type": ["string", "null"]}, "fact_ids": SL}}},
        "links": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["from", "to", "label", "color", "dashed", "both"],
                                             "properties": {"from": S, "to": S, "label": S, "color": {"type": "string", "enum": [*COLORS, "ink"]},
                                                            "dashed": B_, "both": B_}}},
        "marks": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["target", "label", "fact_ids"],
                                             "properties": {"target": S, "label": S, "fact_ids": SL}}},
        "grid_total": {"type": ["integer", "null"]},
        "grid_cols": {"type": ["integer", "null"]},
        "motion": {"type": "object", "additionalProperties": False, "required": ["in", "out", "order"], "properties": {
            "in": {"type": "string", "enum": MOTION_IN}, "out": {"type": "string", "enum": MOTION_OUT},
            "order": {"type": "string", "enum": MOTION_ORDER}}},
        "duration_seconds": {"type": ["number", "null"]},
        "over_video": B_,
    },
}
GRAMMAR = "\n".join([
    "[장면 설계 문법] draw_draft 의 scene 인자.",
    "- layout: 쓸 수 있는 배치 가운데 하나",
    "- title: 짙은 판 위 제목(짧게, 16자 안쪽). 없으면 \"\". subtitle: 제목 아래 한 줄(없으면 \"\"). note: 아래 한 줄 주석(없으면 \"\")",
    "- groups: 사람 · 편 · 칸 묶음 {id, name, color}. color 는 " + " ".join(COLORS) + " 가운데서",
    "- items: {id, group(묶음 id 또는 null), label(이름 · 제목, 짧게), text(설명 · 대사, 30자 안쪽, 없으면 \"\"), value(막대 · 격자의 숫자 또는 null), "
    "value_text(화면에 적을 값 글자, 예: '63명', 없으면 \"\"), time(시간표 · 대화의 시각 글자 또는 null), empty(빈 칸이면 true), emphasis(노란 강조), "
    "icon(아이콘 코드 또는 null), fact_ids(이 항목이 보여 주는 사실 id)}",
    "- links: {from, to(항목 id), label(짧게), color(색 이름 또는 ink), dashed, both} — 나란히 · 둘러앉기 · 시간표에서 카드 사이 화살표(나란히에서는 다른 묶음의 카드끼리 엇갈려 이어도 된다). motion.order 가 one_by_one 이면 화살표도 적은 차례대로 하나씩 나온다. 없으면 []",
    "- marks: {target(항목 id · 묶음 id · 시간표의 시각 글자 그대로), label(8자 안쪽), fact_ids} — 붉은 배지. 없으면 []",
    "- grid_total · grid_cols: 칸 격자일 때만 숫자(아니면 null)",
    "- motion: {in(" + " ".join(MOTION_IN) + "), out(" + " ".join(MOTION_OUT) + "), order(together 한꺼번에 · one_by_one 차례로 쌓임 · swap 큰 글자 판에서만: 같은 자리에서 하나씩 바뀜 — 글자 색과 찍히는 등장 효과는 코드가 붙인다)} — 정지 시안이면 in · out 모두 none",
    "- duration_seconds: 움직이는 장면의 길이(초 — 오프닝 3 · 전환 1.5 처럼). 정지면 null",
    "- over_video: 방송 화면 위에 얹는 그래픽이면 true(바탕 없이 투명), 화면을 꽉 채우는 판이면 false",
    "- memo: 이번 설계에서 무엇을 왜 그렇게 했는지(고칠 때는 무엇을 고쳤는지) 한두 문장 — 화면에는 안 나온다",
    "규칙: 사실에 없는 이름 · 숫자 · 시각 · 사건을 지어내지 않는다(제목 · 주석도 사실에서 끌어온 말만). 방송 자막처럼 짧게 쓰고, 한 판에 글을 많이 쌓지 않는다. "
    "좌표 · 크기 · 글자 크기는 정하지 않는다(코드가 정한다). 고칠 점을 받으면 그 점만 고치고 합격한 부분은 그대로 둔다.",
    ICON_LINE,
])
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["checks", "text_seen", "readability"],
    "properties": {
        "checks": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "ok", "why"],
                                              "properties": {"n": I_, "ok": B_, "why": S}}},
        "text_seen": SL, "readability": SL,
    },
}
JUDGE_SYSTEM = ("너는 방송 그래픽 시안을 검수하는 사람이다. 그림 한 장과, 참고로 주는 의뢰 원문의 사실만 보고 판정한다. 화면에서 바로 보이는 것만 근거로 삼고, "
                "원문과 견주라는 점은 참고 사실과 견준다. "
                "'크게' · '큼직하게' 같은 크기 조건은 함께 주는 글자 크기 실측을 근거로 엄하게 본다 — 가장 큰 글자 높이가 화면 세로의 12% 미만이면 '크게'가 아니다. "
                "길이 · 장수 · 바탕이 투명한지 같은 기술 조건은 그림이 아니라 '코드 실측' 줄로 판정한다. "
                "그림이 둘이면 첫째는 다 나온 순간이고, 둘째는 시간 순서(왼쪽 위 → 오른쪽 위 → 왼쪽 아래 → 오른쪽 아래)로 네 순간을 모은 검수판이다. 움직임에 관한 점은 검수판으로 본다. "
                "확인할 점마다 n(번호) · ok(예 true / 아니오 false) · why(근거 한 문장). 애매하면 아니오. "
                "text_seen: 그림에 적힌 글자를 위에서 아래로, 왼쪽에서 오른쪽으로 빠짐없이 그대로 옮겨 적는다(한 덩어리씩). "
                "readability: 글자가 겹치거나 잘리거나 너무 작아 읽기 어려운 곳만 적는다(없으면 빈 배열). 디자인 취향은 적지 않는다.")


# ---------------------------------------------------------------- 공통
def _norm(s: str) -> str:
    """글자 대조용: 띄어쓰기 · 문장 부호 · 따옴표를 지우고 숫자의 쉼표를 뗀다."""
    return re.sub(r"[\s\"'“”‘’`.,!?~…·・\-—–_()\[\]{}<>«»「」『』:;/|]", "", str(s or "")).lower()


def _seen(want: str, haystack: str) -> bool:
    """want 가 화면에서 읽혔는가 — 짧은 글은 그대로 들어 있어야 하고, 긴 글은 세 글자 조각의 75% 이상이 있어야 한다
    (줄바꿈 · 나란한 카드의 줄이 섞여 읽히는 것을 견딘다)."""
    w = _norm(want)
    if not w or w in haystack:
        return True
    if len(w) <= 8:
        return False
    grams = {w[i:i + 3] for i in range(len(w) - 2)}
    return sum(g in haystack for g in grams) / len(grams) >= 0.75


def _call(messages: list[dict], *, model: str, schema: dict | None = None, name: str = "", tools: list | None = None,
          max_tokens: int = 6000, chat=None, fallback: str | bool = True) -> dict:
    """라우터 · 전략은 liner.ROUTES 의 표를 따른다 — 도구 호출(메인 · 담당)은 'scene_agent', 검수는 'scene_judge'(둘 다 quality).
    LINER_DIRECT=on 이면 'scene_agent' 는 liner.DIRECT 가 claude-opus 로 직접 보낸다(그림이 든 물음을 라우터가 Sonnet 으로 고정하므로) — 안 되면 이 라우터로 돌아온다."""
    chat = chat or intake._liner().chat
    opts: dict = {"max_completion_tokens": max_tokens, "task": name or ("scene_agent" if tools else "")}
    if schema:
        opts["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    if tools:
        opts["tools"], opts["tool_choice"] = tools, "auto"
    return chat(messages, model=model, timeout=170, retries=1, fallback=fallback, **opts)


def picture(path: Path) -> dict:
    return {"type": "image_url", "image_url": {"url": proof.picture(path)}}


# ---------------------------------------------------------------- 메인
def parse_calls(message: dict) -> list[tuple[str, str, dict | None, str]]:
    """답의 도구 호출들 → [(호출 id, 도구 이름, 인자 또는 None, 인자 원문)]"""
    out = []
    for c in message.get("tool_calls") or []:
        fn = c.get("function") or {}
        raw = fn.get("arguments") or ""
        try:
            args = json.loads(raw) if raw.strip() else {}
            args = args if isinstance(args, dict) else None
        except ValueError:
            args = None
        out.append((c.get("id") or "", fn.get("name") or "", args, raw))
    return out


def _quoted(quote, text: str) -> bool:
    """근거(quote)가 그 글에 있는가 — 빈 근거는 '있다'로 넘기지 않는다(_seen 은 빈 글자를 있다고 본다)."""
    return bool(_norm(quote)) and _seen(quote, _norm(text))


def check_main(calls: list[tuple[str, str, dict | None, str]], request_text: str, found: str = "",
               sources: dict[str, str] | None = None, pictured: bool = False) -> tuple[dict, list[str]]:
    """메인의 도구 호출을 검사한다 → (정리한 결과 {brief, workers, unable}, 걸린 점).
    found = research 로 받은 자료(답 · 출처 제목 · 원문)를 이은 글 — 근거는 의뢰 글이나 이 글에 있어야 한다.
    sources = {출처 번호(r1-2): 그 출처의 제목 · 원문} — 사실이 출처 번호를 지목하면 근거가 바로 그 출처의 글에 있어야 한다
    (다른 출처에만 있는 근거에 그 사이트 이름이 붙지 않게)."""
    problems: list[str] = []
    briefs = [a for _, n, a, _ in calls if n == "write_brief"]
    workers = [a for _, n, a, _ in calls if n == "spawn_worker"]
    unable = [a for _, n, a, _ in calls if n == "report_unable"]
    early = sorted({n for _, n, _, _ in calls if n in ("redo_worker", "deliver")})
    if early:
        problems.append(f"담당 결과를 받기 전에는 {', '.join(early)} 를 부를 수 없다")
    unknown = sorted({n for _, n, _, _ in calls if n not in FIRST_TOOLS | LAST_TOOLS})
    if unknown:
        problems.append(f"없는 도구를 불렀다: {', '.join(unknown)}")
    if any(a is None for _, _, a, _ in calls):
        problems.append("도구 인자를 JSON 으로 읽지 못했다")
    if len(briefs) != 1 or briefs[0] is None:
        problems.append(f"write_brief 는 꼭 한 번 불러야 한다(지금 {len(briefs)}번)")
    brief = briefs[-1] if briefs and briefs[-1] else {}
    for k in ("checks", "must_not_assert", "open_questions"):      # 글자 목록 자리 — 제자리에서 목록으로(지시서 카드와 담당이 이 값을 그대로 쓴다)
        if k in brief:
            brief[k] = _strs(brief[k])
    facts = brief.get("facts") if isinstance(brief.get("facts"), list) else []
    for f in facts:     # id 를 글자로 — 제자리에서 바꾼다(같은 dict 가 지시서.json 의 brief · calls 에 담기고, Worker 가 이 값으로 맡은 사실을 고른다)
        if isinstance(f, dict):
            f["id"] = _txt(f.get("id"))
    ids = [f.get("id") for f in facts if isinstance(f, dict)]
    if briefs and not facts:
        problems.append("write_brief 의 facts 가 비었다")
    if len(set(ids)) != len(ids):
        problems.append("facts 의 id 가 겹친다")
    src, got = _norm(request_text), _norm(found)
    for f in facts:     # 빈 근거는 '원문에 있다'로 넘기지 않는다(_seen 은 빈 글자를 있다고 본다 — 화면 글자 대조에서는 그게 맞다)
        if not isinstance(f, dict):
            continue
        in_request = bool(_norm(f.get("quote"))) and _seen(f.get("quote"), src)
        in_found = bool(got) and bool(_norm(f.get("quote"))) and _seen(f.get("quote"), got)
        named = _txt(f.get("source")).strip()
        if pictured and named == PICTURED and _norm(f.get("quote")):
            continue        # 의뢰서 그림에서 읽은 사실(화살표가 어디서 어디로 · 그림 속 글자) — 글에는 없어 코드가 대조하지 못한다. 지시서 카드에서 사람이 본다
        if not in_request and not in_found:
            problems.append(f"{f.get('id')} 의 근거(quote)가 의뢰 원문" + (" · 받은 자료" if got else "") + f"에 없다: {f.get('quote')}")
        elif named in (sources or {}) and not _quoted(f.get("quote"), sources[named]):     # 지목한 출처의 원문에는 없는 근거
            if in_request:
                f["source"] = "의뢰"       # 근거가 의뢰 글에 있다 — 출처 번호를 잘못 적었어도 의뢰로 바로잡는다(제자리에서)
            else:
                problems.append(f"{f.get('id')} 의 근거(quote)가 지목한 출처 {named} 의 원문에 없다 — "
                                f"그 근거가 실제로 적힌 출처 번호를 적어라(답에만 있는 근거면 source 를 '자료'로 적는다): {f.get('quote')}")
        elif not in_request and _txt(f.get("source")).strip() in ("", "의뢰"):
            f["source"] = "자료"       # 근거가 자료에만 있다 — 의뢰라고 적었어도 자료로 바로잡는다(제자리에서)
    if briefs and not brief.get("checks"):
        problems.append("write_brief 의 checks(확인할 점)가 비었다")
    workers = [w for w in workers if w]
    for w in workers:   # 맡은 사실 id 도 글자로, 제자리에서(위 facts 와 같은 까닭)
        w["fact_ids"] = [_txt(i) for i in w["fact_ids"]] if isinstance(w.get("fact_ids"), list) else []
        w["checks"], w["layouts"] = _strs(w.get("checks")), _strs(w.get("layouts"))
    unable = [u for u in unable if u]
    for u in unable:
        if "needed" in u:
            u["needed"] = _strs(u["needed"])
    if not workers and not unable:
        problems.append("spawn_worker 나 report_unable 중 하나는 불러야 한다")
    covered: set[str] = set()
    for k, w in enumerate(workers, 1):
        if not str(w.get("role") or "").strip():
            problems.append(f"{k}번 담당의 role 이 비었다")
        bad = [i for i in w.get("fact_ids") or [] if i not in ids]
        if bad:
            problems.append(f"{k}번 담당이 없는 사실 id 를 맡았다: {', '.join(bad)}")
        if not w.get("fact_ids"):
            problems.append(f"{k}번 담당이 맡은 사실이 없다")
        lay = [x for x in w.get("layouts") or [] if x in LAYOUTS]
        if not lay:
            problems.append(f"{k}번 담당에게 쓸 수 있는 배치를 주지 않았다")
        if not w.get("checks"):
            problems.append(f"{k}번 담당의 checks(합격 기준)가 비었다")
        covered |= set(w.get("fact_ids") or [])
    for u in unable:
        if u.get("route") not in ROUTES:
            problems.append(f"report_unable 의 route 가 잘못됐다: {u.get('route')}")
    if workers and not unable:
        left = [i for i in ids if i not in covered]
        if left:
            problems.append(f"맡은 담당이 없는 사실: {', '.join(left)}")
    return {"brief": brief, "workers": workers, "unable": unable}, problems


# ---------------------------------------------------------------- 서브(임시 담당)
WORKER_TOOLS = [
    _fn("draw_draft", "장면 설계로 정지 시안 한 장을 그린다(과금 없음). 코드 검사 · 자동 검사(글자 넘침 · 작은 글자) 결과와 시안 그림이 돌아온다. 검수자 판정은 하지 않는다.",
        {"scene": SCENE_SCHEMA}),
    _fn("request_review", "가장 최근 시안을 검수자(Liner 그림 읽기)에게 보내 합격 기준을 하나씩 판정받는다. 한도가 있으니 자동 검사가 깨끗한 시안을 보낸다.",
        {"note": {**S, "description": "검수자에게 따로 알릴 것(없으면 \"\")"}}),
    _fn("submit", "일을 마치고 가장 최근 시안을 메인에게 돌려준다.",
        {"summary": {**S, "description": "무엇을 어떻게 만들었는지 한두 문장"}, "unresolved": {**SL, "description": "해결하지 못한 점(없으면 [])"}}),
    _fn("give_up", "주어진 배치 · 재료로는 이 일을 할 수 없을 때 메인에게 돌려준다.",
        {"reason": S, "needed": {**SL, "description": "무엇이 있으면 되는지"}}),
]


def worker_system(w: dict, brief: dict, draws: int, reviews: int) -> str:
    facts = {f["id"]: f for f in brief.get("facts") or [] if isinstance(f, dict)}
    mine = [facts[i] for i in w.get("fact_ids") or [] if i in facts]
    allowed = [x for x in w.get("layouts") or [] if x in LAYOUTS]
    lines = [
        "너는 메인 에이전트가 이 의뢰 하나를 위해 만든 임시 담당이다. 도구로만 일한다.",
        f"역할: {w.get('role')}",
        f"연출 목표: {w.get('goal')}",
        f"시청자가 알아야 할 것: {brief.get('viewer_point')}",
        "맡은 사실(모두 화면에 보여야 한다. 그 사실을 보여 주는 항목 · 표시의 fact_ids 에 id 를 적는다):",
        *[f"- {f['id']}: {f.get('fact')} (원문: \"{f.get('quote')}\"" + (f" · 출처 {f['source_name']}" if f.get("source_name") else "") + ")" for f in mine],
    ]
    if any(f.get("source_name") for f in mine):
        lines += ["출처가 붙은 사실을 화면에 쓰면 note(아래 한 줄 주석)에 '자료: 사이트 이름'을 적는다(방송 자막의 출처 표기)."]
    if brief.get("must_not_assert"):
        lines += ["단정하면 안 되는 것:", *[f"- {x}" for x in brief["must_not_assert"]]]
    lines += ["합격 기준(검수자가 그림과 원문만 보고 예/아니오로 판정한다):", *[f"{n}. {c}" for n, c in enumerate(w.get("checks") or [], 1)],
              "쓸 수 있는 배치:", *[f"- {x}: {LAYOUTS[x]}" for x in allowed], COMMON, "",
              "[도구]",
              "- draw_draft(scene): 시안을 그린다. 과금이 없다. 코드 검사 · 자동 검사 결과와 그림이 돌아온다.",
              "- request_review(note): 가장 최근 시안을 검수자에게 보낸다. 합격 기준마다 예/아니오와 근거, 화면에서 못 읽은 글자가 돌아온다.",
              "- submit(summary, unresolved): 마친다. 가장 최근 시안이 결과물이다. 남은 문제는 unresolved 에 적는다.",
              "- give_up(reason, needed): 이 배치 · 재료로는 안 될 때.",
              f"한도: 그리기 {draws}번 · 검수 {reviews}번. 무엇을 언제 몇 번 부를지는 네가 정한다. 검수를 통과하지 못한(또는 받지 않은) 시안을 내면 미해결로 남는다.",
              "합격 기준을 쓸 수 있는 배치 · 재료로 맞출 수 없으면(요구한 크기 · 표현을 낼 배치가 없는 등) 억지로 비슷하게 만들지 말고 give_up 으로 무엇이 필요한지 알린다.",
              "", GRAMMAR]
    return "\n".join(lines)


def _txt(v) -> str:
    return "" if v is None else str(v)


def _num(v):
    """숫자로. 숫자가 아니거나 무한대 · NaN 이면 None(그런 값은 장면 자료(JSON)에 쓸 수 없고 정수로도 못 바꾼다)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v if math.isfinite(v) else None
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        n = float(v.replace(",", ""))
    except ValueError:
        return None
    return n if math.isfinite(n) else None


def _strs(v) -> list[str]:
    """글자 목록 자리의 값을 글자 목록으로. 모델이 목록 대신 글자 하나("없음")나 다른 모양을 줘도 지시서 카드 · 담당이 고장 나지 않게 한다."""
    if isinstance(v, str):
        v = [v] if v.strip() and v.strip() not in ("없음", "-") else []
    if not isinstance(v, list):
        return []
    return [_txt(x) for x in v if isinstance(x, (str, int, float)) and not isinstance(x, bool) and _txt(x).strip()]


def normalize_scene(raw) -> dict:
    """도구 인자로 온 장면 설계를 그릴 수 있는 모양으로 채운다(빠진 칸은 기본값, 모양이 틀린 값은 버린다). 틀린 것은 check_scene 이 따로 알린다."""
    s = raw if isinstance(raw, dict) else {}
    lst = lambda k: [x for x in s.get(k) or [] if isinstance(x, dict)] if isinstance(s.get(k), list) else []
    groups = [{"id": _txt(g.get("id")), "name": _txt(g.get("name")), "color": g.get("color") if g.get("color") in COLORS else COLORS[i % len(COLORS)]}
              for i, g in enumerate(lst("groups"))]
    items = [{"id": _txt(it.get("id")), "group": None if it.get("group") in (None, "") else _txt(it.get("group")),
              "label": _txt(it.get("label")), "text": _txt(it.get("text")), "value": _num(it.get("value")), "value_text": _txt(it.get("value_text")),
              "time": None if it.get("time") in (None, "") else _txt(it.get("time")), "empty": bool(it.get("empty")), "emphasis": bool(it.get("emphasis")),
              "icon": it.get("icon") if isinstance(it.get("icon"), str) and it.get("icon") in plan.ICONS else None,
              "fact_ids": [_txt(x) for x in it.get("fact_ids") or []] if isinstance(it.get("fact_ids"), list) else []} for it in lst("items")]
    links = [{"from": _txt(l.get("from")), "to": _txt(l.get("to")), "label": _txt(l.get("label")),
              "color": l.get("color") if l.get("color") in [*COLORS, "ink"] else "ink", "dashed": bool(l.get("dashed")), "both": bool(l.get("both"))} for l in lst("links")]
    marks = [{"target": _txt(m.get("target")), "label": _txt(m.get("label")),
              "fact_ids": [_txt(x) for x in m.get("fact_ids") or []] if isinstance(m.get("fact_ids"), list) else []} for m in lst("marks")]
    whole = lambda v: int(_num(v)) if _num(v) is not None and _num(v) >= 1 else None      # noqa: E731 — "12.0" · "1,000" 같은 글자 숫자도 받는다
    mo = s.get("motion") if isinstance(s.get("motion"), dict) else {}
    dur = _num(s.get("duration_seconds"))
    dur = dur if dur and dur > 0 else None
    return {"memo": _txt(s.get("memo")), "layout": _txt(s.get("layout")), "title": _txt(s.get("title")), "subtitle": _txt(s.get("subtitle")),
            "note": _txt(s.get("note")), "groups": groups, "items": items, "links": links, "marks": marks,
            "grid_total": whole(s.get("grid_total")), "grid_cols": whole(s.get("grid_cols")),
            "motion": {"in": mo.get("in") if mo.get("in") in MOTION_IN else "none", "out": mo.get("out") if mo.get("out") in MOTION_OUT else "none",
                       "order": mo.get("order") if mo.get("order") in MOTION_ORDER else "together"},
            "duration_seconds": dur if dur is None else min(15.0, max(0.5, float(dur))), "over_video": bool(s.get("over_video"))}


class Worker:
    """메인이 spawn_worker 로 만든 임시 담당 하나. 자기 대화(지시문 = 메인이 정한 역할 · 목표 · 사실 · 배치 · 합격 기준)를 갖고,
    draw_draft · request_review · submit · give_up 도구를 언제 몇 번 부를지 스스로 정한다. 코드는 한도만 지킨다."""

    def __init__(self, wid: str, spec: dict, brief: dict, work: Path, profile: Path, tries_cap: int = MAX_TRIES, log=print, chat=None, judge_chat=None,
                 note: str = "", pictures=None):
        """note = 제작진이 지시서 카드에서 덧붙인 말(앱). 담당의 첫 지시에 붙인다.
        pictures = 항목 이름에 맞는 작은 그림을 마련해 주는 쪽(icons.for_labels). 앱이 넣어 준다 — 없으면 그림 없이 그린다(시험 · 명령줄)."""
        self.wid, self.spec, self.brief, self.profile, self.log, self.chat, self.judge_chat = wid, spec, brief, profile, log, chat, judge_chat
        self.pictures = pictures
        self.role = str(spec.get("role") or wid)
        self.dir = work / f"{wid}_{re.sub(r'[^0-9A-Za-z가-힣]+', '_', self.role)[:24]}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.checks = list(spec.get("checks") or []) + [f"화면이 '{x}' 를 단정하지 않는가?" for x in brief.get("must_not_assert") or []]
        self.facts = [f for f in brief.get("facts") or [] if isinstance(f, dict) and f.get("id") in (spec.get("fact_ids") or [])]
        tries = max(1, min(int(_num(spec.get("max_tries")) or MAX_TRIES), tries_cap))
        self.reviews_max, self.draws_max = tries, tries * 2 + 1
        self.msgs: list[dict] = [{"role": "system", "content": worker_system(spec, brief, self.draws_max, self.reviews_max)},
                                 {"role": "user", "content": "일을 시작해라." + (f"\n제작진이 덧붙인 말(따른다): {note}" if note else "")}]
        self.draws: list[dict] = []
        self.reviews: list[dict] = []
        self.order: list[str] = []
        self.turns, self.result, self.summary, self.unresolved, self.needed, self.redo = 0, None, "", [], [], None
        self.broken = {"그리기": 0, "검수": 0}      # 고장으로 끝난 도구 호출 — 한도를 쓴 것으로 센다(끝없이 다시 부르지 못하게)

    def left(self) -> dict:
        return {"그리기": self.draws_max - len(self.draws) - self.broken["그리기"], "검수": self.reviews_max - len(self.reviews) - self.broken["검수"]}

    def fault(self, what: str, e: Exception) -> dict:
        """그리기 · 검수 도구가 고장으로 끝났다(캡처 시간 초과 · Liner 시간 초과 · 깨진 검수 답) — 담당에게 오류로 돌려주고 한도를 한 번 쓴 것으로 센다.
        여기서 예외를 밖으로 내보내면 다른 담당이 이미 합격한 시안까지 의뢰 전체가 '제작 실패'로 버려진다."""
        self.broken[what] += 1
        why = intake.reason(e)
        self.log(f"    {self.wid} {self.role}: {what} 고장 — {why}")
        return {"오류": f"{what} 고장: {why}", "남은 한도": self.left()}

    def last_fails(self) -> list[str]:
        if not self.draws:
            return ["시안을 그리지 않았다"]
        d = self.draws[-1]
        if self.reviews and self.reviews[-1]["draft"] == d["n"]:
            return self.reviews[-1]["fails"]
        return [f"[코드 검사] {s}" for s in d["code"]] + [f"[자동 검사] {s}" for s in d["issues"]] + ["마지막 시안이 검수를 받지 않았다"]

    def handle(self, name: str, a: dict, pics: list[Path]) -> dict:
        self.order.append(name)
        if self.result:
            return {"오류": "이미 마쳤다"}
        if name == "draw_draft":
            if self.left()["그리기"] <= 0:
                return {"오류": f"그리기 한도({self.draws_max}번)를 다 썼다. submit 으로 마쳐라."}
            n = len(self.draws) + 1
            d = self.dir / f"시안{n}"
            try:        # 설계 정리 · 코드 검사도 이 안에서 — 모양이 틀린 값 하나로 난 예외가 의뢰 전체를 '제작 실패'로 만들지 않게
                scene = normalize_scene(a.get("scene"))
                code = check_scene(scene, self.spec)
                drawn = attach_pictures(scene, self.pictures)      # 아이콘 없는 항목에 Liner 그림(곰인형 · 기차 …) — 안 되면 그림 없이 간다
                for line in drawn["notes"]:
                    self.log(f"    {self.wid} {self.role}: {line}")
                install(d, scene)
                (d / "scene.json").write_text(json.dumps(scene, ensure_ascii=False, indent=1), encoding="utf-8")
                shot, issues, info = shoot(d, self.profile)
            except Exception as e:      # 고장 난 그리기는 draws 에 넣지 않는다(시안 그림이 없다) — 한도는 broken 으로 센다
                return self.fault("그리기", e)
            self.draws.append({"n": n, "scene": scene, "code": code, "issues": issues, "shot": shot, "info": info})
            pics += [shot] + ([info["sheet"]] if info.get("sheet") else [])
            self.log(f"    {self.wid} {self.role}: 그리기{n} {scene['layout']} · 코드 검사 {len(code)} · 자동 검사 {len(issues)}")
            extra = {"글자 실측": f"가장 큰 글자 높이 = 화면 세로의 {info['big'] * 100:.0f}%"} if info.get("big") else {}
            if drawn["names"]:
                extra["항목 그림"] = "아이콘이 없는 항목 이름에 맞춰 그림 모델이 그린 작은 그림을 붙였다: " + " · ".join(drawn["names"])
            if info.get("seconds"):
                extra["코드 실측"] = f"길이 {info['seconds']:g}초({info.get('frames')}장) · 바탕 {'투명' if info.get('clear') else '있음'}"
            if info.get("sheet") and info.get("swap"):
                extra["움직임"] = ("첫 항목이 자리 잡은 순간 한 장과, 항목마다 자리 잡은 순간을 모은 검수판(" + " · ".join(f"{t:g}초" for t in info["times"])
                                + ")을 함께 보낸다 — 찍힐 때 잠깐 붙는 등장 효과(원 고리 · 색 조각 · 평행 띠)는 이 순간들에는 걷혀 있어 그림에 안 보인다(영상에는 들어간다)")
            elif info.get("sheet") and info.get("steps"):
                extra["움직임"] = ("다 나온 순간 한 장과, " + ("화살표가" if info.get("step_kind", "links") == "links" else "차례로 나오는 것이")
                                + " 하나씩 더해지는 순간을 모은 검수판(" + " · ".join(f"{t:g}초" for t in info["times"]) + " — " + _steps_words(info) + ")을 함께 보낸다")
            elif info.get("sheet"):
                extra["움직임"] = "다 나온 순간 한 장과 네 순간 검수판(" + " · ".join(f"{t:g}초" for t in info["times"]) + ")을 함께 보낸다"
            return {"시안": n, "코드 검사": code or "걸린 것 없음", "자동 검사": issues or "걸린 것 없음", **extra, "남은 한도": self.left()}
        if name == "request_review":
            if not self.draws:
                return {"오류": "먼저 draw_draft 로 시안을 그려라."}
            if self.left()["검수"] <= 0:
                return {"오류": f"검수 한도({self.reviews_max}번)를 다 썼다. submit 으로 마쳐라."}
            d = self.draws[-1]
            try:
                verdict, model = judge(d["shot"], self.checks, self.facts, chat=self.judge_chat, info=d.get("info"))
                fails = failures(d["code"], d["issues"], verdict, self.checks, d["scene"])
            except Exception as e:
                return self.fault("검수", e)
            self.reviews.append({"draft": d["n"], "fails": fails, "verdict": verdict, "model": model})
            self.log(f"    {self.wid} {self.role}: 검수(시안{d['n']}) {'합격' if not fails else f'걸린 점 {len(fails)}'}")
            return {"시안": d["n"], "판정": "합격" if not fails else "걸린 점 있음", "검수자 판정": judged(verdict, self.checks), "걸린 점": fails,
                    "남은 한도": self.left()}
        if name == "submit":
            self.summary = _txt(a.get("summary"))
            passed = bool(self.draws) and bool(self.reviews) and self.reviews[-1]["draft"] == self.draws[-1]["n"] and not self.reviews[-1]["fails"]
            reviewed = bool(self.draws) and bool(self.reviews) and self.reviews[-1]["draft"] == self.draws[-1]["n"]
            self.result = "합격" if passed else "미해결 제출" if reviewed else "검수 없이 제출"
            self.unresolved = [] if passed else self.last_fails() + [f"(담당이 적음) {x}" for x in a.get("unresolved") or []]
            return {"받음": True}
        if name == "give_up":
            self.result, self.summary = "포기", _txt(a.get("reason"))
            self.needed = [_txt(x) for x in a.get("needed") or []]
            self.unresolved = [self.summary]
            return {"받음": True}
        return {"오류": f"없는 도구: {name}"}

    def run(self, instruction: str | None = None) -> dict:
        """도구를 부르며 일한다. instruction 이 있으면 메인의 다시 하기 지시로 이어서 일한다(한도를 조금 더 준다)."""
        if instruction:
            self.redo, self.result = instruction, None
            self.reviews_max, self.draws_max = self.reviews_max + 1, self.draws_max + 2
            self.msgs.append({"role": "user", "content": f"메인의 다시 하기 지시: {instruction}\n같은 도구로 이어서 일하고 마치면 submit 한다. 남은 한도: {self.left()}"})
        cap = self.turns + sum(max(0, n) for n in self.left().values()) + 3      # 남은 한도만큼만 — 다시 하기 때 전체 한도로 다시 열리지 않게
        while self.result is None and self.turns < cap:
            try:
                resp = _call(self.msgs, model=WORKER_MODEL, tools=WORKER_TOOLS, max_tokens=8000, chat=self.chat)
                choice = resp["choices"][0]
                msg, calls = choice["message"], parse_calls(choice["message"])
            except Exception as e:      # 담당을 부르지 못함(시간 초과 · 연결 · 깨진 답) — 이 담당만 '고장'으로 끝낸다. 다른 담당과 메인은 이어 간다
                self.summary = f"담당을 부르지 못했다: {intake.reason(e)}"
                self.result, self.unresolved = "고장", [self.summary, *self.last_fails()]
                self.log(f"    {self.wid} {self.role}: {self.summary}")
                break
            self.turns += 1
            self.msgs.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})})
            if not calls:
                self.order.append(f"(도구 없이 답함 · {choice.get('finish_reason')})")
                self.msgs.append({"role": "user", "content": "도구로만 일한다. draw_draft · request_review · submit · give_up 가운데 하나를 불러라."})
                continue
            pics: list[Path] = []
            for cid, name, args, _ in calls:
                out = self.handle(name, args or {}, pics) if args is not None else {"오류": "인자를 JSON 으로 읽지 못했다"}
                self.msgs.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(out, ensure_ascii=False)})
            if pics and self.result is None:
                self.msgs.append({"role": "user", "content": [{"type": "text", "text": "방금 그린 시안:"}, *[picture(p) for p in pics]]})
        if self.result is None:
            self.result, self.unresolved = "한도", self.last_fails()
        self.save()
        return self.for_main()

    def last_shot(self) -> Path | None:
        return self.draws[-1]["shot"] if self.draws else None

    def last_sheet(self) -> Path | None:
        return (self.draws[-1].get("info") or {}).get("sheet") if self.draws else None

    def for_main(self) -> dict:
        """메인에게 돌려주는 결과(spawn_worker · redo_worker 의 도구 결과)."""
        return {"worker": self.wid, "role": self.role, "result": self.result, "summary": self.summary, "unresolved": self.unresolved,
                "needed": self.needed, "order": self.order, "draws": len(self.draws), "reviews": len(self.reviews),
                **({"broken": dict(self.broken)} if any(self.broken.values()) else {})}      # 고장이 없을 때는 메인이 받는 글이 전과 같다

    def last_verdict(self) -> list[dict]:
        """마지막 시안의 검수 판정 [{check, ok, why}] — 마지막 시안이 검수를 받지 않았으면 []. 앱의 시안 카드에 그대로 싣는다."""
        if not (self.draws and self.reviews and self.reviews[-1]["draft"] == self.draws[-1]["n"]):
            return []
        got = {c.get("n"): c for c in (self.reviews[-1].get("verdict") or {}).get("checks") or []}
        return [{"check": c, "ok": bool((got.get(n) or {}).get("ok")), "why": _txt((got.get(n) or {}).get("why"))} for n, c in enumerate(self.checks, 1)]

    def record(self, work: Path) -> dict:
        shot = self.last_shot()
        return {**self.for_main(), "goal": self.spec.get("goal"), "layouts": self.spec.get("layouts"), "fact_ids": self.spec.get("fact_ids"),
                "checks": self.checks, "limits": {"그리기": self.draws_max, "검수": self.reviews_max}, "turns": self.turns, "redo": self.redo,
                "final_shot": Path(shot).resolve().relative_to(Path(work).resolve()).as_posix() if shot else None, "dir": self.dir.name,
                "layout": self.draws[-1]["scene"].get("layout") if self.draws else None, "verdict": self.last_verdict()}

    def save(self) -> None:
        log = {"spec": self.spec, "checks": self.checks, "order": self.order, "result": self.result, "summary": self.summary, "redo": self.redo,
               "draws": [{k: (str(v) if k == "shot" else {**v, "sheet": str(v.get("sheet") or "")} if k == "info" else v) for k, v in d.items()} for d in self.draws], "reviews": self.reviews,
               "system": self.msgs[0]["content"], "broken": self.broken}
        (self.dir / "기록.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


def check_scene(scene: dict, w: dict) -> list[str]:
    """장면 설계의 코드 검사: 허락한 배치 · 이름 겹침 · 가리키는 곳 · 맡은 사실을 다 덮었는지."""
    p: list[str] = []
    allowed = [x for x in w.get("layouts") or [] if x in LAYOUTS]
    if scene.get("layout") not in allowed:
        p.append(f"허락받지 않은 배치({scene.get('layout')}) — 쓸 수 있는 배치: {', '.join(allowed)}")
    groups, items = scene.get("groups") or [], scene.get("items") or []
    gids, iids = [g.get("id") for g in groups], [it.get("id") for it in items]
    if (scene.get("motion") or {}).get("order") == "swap":
        if scene.get("layout") != "headline":
            p.append("차례로 바꿔 보이기(order = swap)는 큰 글자 판(headline)에서만 쓴다")
        elif not 2 <= len(items) <= 6:
            p.append(f"차례로 바꿔 보이기(order = swap)는 항목이 2~6개여야 한다(지금 {len(items)}개)")
    if len(set(gids)) != len(gids):
        p.append("groups 의 id 가 겹친다")
    if len(set(iids)) != len(iids):
        p.append("items 의 id 가 겹친다")
    if set(gids) & set(iids):
        p.append("groups 와 items 가 같은 id 를 쓴다")
    for it in items:
        if it.get("group") is not None and it.get("group") not in gids:
            p.append(f"항목 {it.get('id')} 의 group({it.get('group')})이 groups 에 없다")
        if it.get("icon") and it["icon"] not in plan.ICONS:
            p.append(f"항목 {it.get('id')} 의 아이콘 {it['icon']} 은 목록에 없다")
    for it, k in undrawn(scene):     # 돈 드는 검수에서 '화면에서 읽히지 않음'으로 걸리기 전에, 그리기 단계에서 바로 알린다
        p.append(f"항목 {it.get('id')} 의 {k}('{str(it.get(k)).strip()[:20]}')는 {LAYOUT_LABELS.get(scene.get('layout'), scene.get('layout'))} 배치가 그리지 않는 칸이다 — "
                 "화면에 나와야 하면 그리는 칸(label · text 등)으로 옮기고, 아니면 지워라")
    for ln in scene.get("links") or []:
        if ln.get("from") not in iids or ln.get("to") not in iids:
            p.append(f"화살표의 끝이 항목이 아니다: {ln.get('from')} → {ln.get('to')}")
    times = {it.get("time") for it in items if it.get("time")}
    for m in scene.get("marks") or []:
        if m.get("target") not in set(iids) | set(gids) | times:
            p.append(f"표시({m.get('label')})의 target({m.get('target')})이 항목 · 묶음 · 시각 어디에도 없다")
    used = {i for it in items for i in it.get("fact_ids") or []} | {i for m in scene.get("marks") or [] for i in m.get("fact_ids") or []}
    mine = set(w.get("fact_ids") or [])
    left = sorted(mine - used)
    if left:
        p.append(f"맡은 사실이 화면에 없다(fact_ids 로 가리킨 항목 · 표시가 없음): {', '.join(left)}")
    stray = sorted(used - mine)
    if stray:
        p.append(f"맡지 않은 사실 id 를 적었다: {', '.join(stray)}")
    return p


# 배치마다 그리지 않는 항목 칸(templates/scene.html 의 그리기 코드와 같은 규칙) — 여기 든 칸에 넣은 글은 화면에 나오지 않는다.
# 영수증 · 격자 · 막대는 적지 않았다(그 배치들은 지금처럼 모든 칸을 읽혀야 할 글자로 센다)
NOT_DRAWN = {"rows": ("time",), "columns": ("time",), "lanes": ("value_text",), "ring": ("value_text", "time"),
             "chat": ("value_text",), "banner": ("value_text", "time"), "headline": ("time",)}


def undrawn(scene: dict) -> list[tuple[dict, str]]:
    """그 배치가 그리지 않는 칸에 글이 든 (항목, 칸 이름)들. 차례로 바꿔 보이기는 항목마다 큰 글자 하나(value_text, 없으면 text)만 그린다."""
    lay = scene.get("layout")
    swap = lay == "headline" and (scene.get("motion") or {}).get("order") == "swap"
    full = lambda it, k: bool(str(it.get(k) or "").strip())      # noqa: E731
    out = [(it, k) for it in scene.get("items") or [] for k in NOT_DRAWN.get(lay, ()) if full(it, k)]
    if swap:
        out += [(it, "text") for it in scene.get("items") or [] if full(it, "value_text") and full(it, "text")]
    return out


def wanted_texts(scene: dict) -> list[str]:
    """화면에 읽혀야 할 글자: 제목 · 부제 · 주석 · 묶음 이름 · 항목의 이름 · 설명 · 값 · 시각 · 화살표와 표시의 이름.

    그 배치가 실제로 그리는 것만 센다(templates/scene.html 과 같은 규칙) — 둘러앉기의 묶음은 카드 색으로만 나오고,
    막대 · 격자는 label 이 없을 때만 묶음 이름을 적고, 대화는 묶음이 있으면 묶음 이름이 보낸 사람이다.
    """
    items, lay = scene.get("items") or [], scene.get("layout")
    gids = {g.get("id") for g in scene.get("groups") or []}
    named = {it.get("group") for it in items if lay in ("rows", "lanes", "columns", "chat", "banner")
             or (lay in ("bars", "grid") and not str(it.get("label") or "").strip())}
    if lay == "wipe":
        first = next(iter(items), {})
        return [t for t in [str(first.get("text") or first.get("label") or first.get("value_text") or "").strip()] if t]
    out = [scene.get("title"), None if lay == "banner" else scene.get("subtitle"), scene.get("note")]
    out += [g.get("name") for g in scene.get("groups") or [] if g.get("id") in named]
    skip = {(id(it), k) for it, k in undrawn(scene)}       # 그 배치가 그리지 않는 칸은 세지 않는다(코드 검사 check_scene 이 따로 알린다)
    for it in items:
        label = None if lay == "chat" and it.get("group") in gids else it.get("label")
        out += [label] + [it.get(k) for k in ("text", "value_text", "time") if (id(it), k) not in skip]
    out += [ln.get("label") for ln in scene.get("links") or []] + [m.get("label") for m in scene.get("marks") or []]
    seen, res = set(), []
    for s in out:
        s = str(s or "").strip()
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res


def moving(scene: dict) -> bool:
    """움직이는 장면인가 — 덮는 전환이거나 들어오기 · 나가기가 있다."""
    mo = scene.get("motion") or {}
    return scene.get("layout") == "wipe" or mo.get("in", "none") != "none" or mo.get("out", "none") != "none" or bool(swap_count(scene))


def swap_count(scene: dict) -> int:
    """차례로 바꿔 보이기 장면이면 바뀌는 항목 수(아니면 0) — 큰 글자 판 + order = swap. 그리는 쪽(templates/scene.html)과 같이 6개까지."""
    if scene.get("layout") != "headline" or (scene.get("motion") or {}).get("order") != "swap":
        return 0
    return min(6, len(scene.get("items") or []))


def scene_duration(scene: dict) -> float:
    if scene.get("duration_seconds"):
        return float(min(15.0, max(0.5, scene["duration_seconds"])))
    if swap_count(scene):
        return float(swap_count(scene))        # 길이를 안 적으면 항목마다 1초
    return 1.5 if scene.get("layout") == "wipe" else 3.0 if moving(scene) else 2.0


PIC_LAYOUTS = ("rows", "columns")       # 항목 옆에 아이콘 자리가 있는 배치(templates/scene.html 의 iconOf 를 쓰는 곳과 같다)
PIC_WAIT = 75                           # 그림을 기다리는 한도(초). 넘으면 이번 시안은 그림 없이 그린다 — 그림은 뒤에서 끝까지 받아 보관돼 다음 시안에 붙는다
_PIC_LEAD = re.compile(r"^(?:[\s①-⑳㉑-㉟⓪❶-❿·:\-]|\(?\d+[.)](?!\d))+")       # 이름 앞의 번호(① · 1. · 2) …)는 물건 이름이 아니다
_PIC_WORD = re.compile(r"[가-힣A-Za-z]")


def _pic_word(it: dict) -> str:
    """그림을 물을 낱말 — 항목 이름에서 번호를 뗀 것. 이름이 숫자뿐이면(① 10000) 값 글자 · 설명(만원)에서 찾는다."""
    for key in ("label", "value_text", "text"):
        word = re.sub(r"\s+", " ", _PIC_LEAD.sub("", it.get(key) or "")).strip()      # 빈칸은 icons 쪽과 같은 모양으로(답을 낱말로 되찾는다)
        if word and _PIC_WORD.search(word) and len(word) <= 20:
            return word
    return ""


def attach_pictures(scene: dict, finder, wait: float = PIC_WAIT) -> dict:
    """아이콘이 없는 항목 이름(곰인형 · 기차 · 만원 …)에 맞는 작은 그림을 마련해 항목의 picture 칸에 파일 자리를 적는다.
    finder = icons.for_labels 같은 것(낱말들 → {'icons': {낱말: {'file', 'source'}}, 'notes'}). 없으면 아무것도 하지 않는다.
    그림은 덤이다 — 묻지 못했거나 늦으면 그림 없이 간다. 돌려주는 것: {'names': 그림이 붙은 항목 이름들, 'notes': 진행 기록에 적을 줄}"""
    out = {"names": [], "notes": []}
    if not finder or scene.get("layout") not in PIC_LAYOUTS:
        return out
    want: dict[str, list[dict]] = {}        # 물을 낱말 → 그 낱말을 쓰는 항목들
    for it in scene.get("items") or []:
        word = _pic_word(it)
        if word and not it.get("icon") and not it.get("empty"):
            want.setdefault(word, []).append(it)
    if not want:
        return out
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as Late
    liner = intake._liner()
    rid = getattr(liner._ctx, "request", None)

    def ask():      # 의뢰 번호는 스레드마다 따로라 넘겨 준다(호출 기록 · 카드의 Liner 줄에 같은 의뢰로 남게)
        if rid:
            with liner.request(rid):
                return finder(list(want))
        return finder(list(want))

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        got = ex.submit(ask).result(timeout=wait)
    except Late:
        out["notes"].append(f"항목 그림이 {wait:g}초 안에 오지 않아 이번 시안은 그림 없이 그립니다")
        return out
    except Exception as e:
        out["notes"].append(f"항목 그림 생략: {intake.reason(e, typed=False)[:120]}")
        return out
    finally:
        ex.shutdown(wait=False)
    icons_ = (got or {}).get("icons") or {}
    files: dict[str, bool] = {}     # 그림 파일 → 이번에 새로 그렸는가(같은 그림을 여러 항목이 쓴다 — 만원 · 2만원 …)
    for word, its in want.items():
        hit = icons_.get(word)
        if not isinstance(hit, dict) or not hit.get("file") or not Path(hit["file"]).is_file():
            continue
        f = Path(hit["file"])
        files[str(f)] = files.get(str(f), False) or hit.get("source") == "Liner"
        for it in its:      # 보관 폴더의 그림은 파일 이름만 적는다(설계가 모델에게 돌아갈 때 이 컴퓨터의 폴더 이름이 딸려 가지 않게)
            it["picture"] = f.name if f.parent == icons.STORE else str(f)
        out["names"].append(word)
    if out["names"]:
        new = sum(files.values())
        out["notes"].append(f"항목 그림 {len(files)}장(Liner 그림 모델 — 새로 {new} · 보관 {len(files) - new}): " + " · ".join(out["names"]))
    out["notes"] += [str(n) for n in (got or {}).get("notes") or []][:3]
    return out


def _pics(scene: dict) -> dict:
    """항목의 picture(그림 파일 자리)를 화면이 바로 그릴 수 있게 {항목 id: data URL} 로. 파일이 없으면 그 항목은 그림 없이 그린다."""
    out: dict[str, str] = {}
    for it in scene.get("items") or []:
        f = it.get("picture") if isinstance(it, dict) else None
        if not f or not it.get("id"):
            continue
        try:
            raw = (Path(f) if Path(f).is_absolute() else icons.STORE / Path(f).name).read_bytes()
        except OSError:
            continue
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) <= 2 * 1024 * 1024:
            out[it["id"]] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    return out


def install(job_dir: Path, scene: dict, art_style: str | None = None) -> None:
    """작업 폴더에 장면 화면을 넣는다 — 순위판과 같은 무대 · 붓 · 기준 묶음 + 방송 기본 묶음(안전 영역 · 글꼴 · 아이콘) + 화풍.
    art_style 이 없으면 방송 화풍(request.HOUSE_STYLE). job.json 은 길이 · 화풍 · 납품 기록용."""
    style = art_style or request.HOUSE_STYLE
    if style not in request.ART_STYLES:
        raise ValueError(f"모르는 화풍입니다: {style}")
    job_dir.mkdir(parents=True, exist_ok=True)
    dur = scene_duration(scene)
    job = {"kind": "scene", "art_style": style, "duration_seconds": dur, "layout": "full", "layout_rect": [0, 0, 1, 1],
           "output": {"width": 1920, "height": 1080, "fps": FPS}, "scene": scene, "pics": _pics(scene)}
    (job_dir / "job.js").write_text(build._js("JOB", job), encoding="utf-8")
    (job_dir / "job.json").write_text(json.dumps({"kind": "scene", "layout": scene.get("layout"), "duration_seconds": dur, "fps": FPS,
                                                  "moving": moving(scene), "swap": swap_count(scene), "over_video": bool(scene.get("over_video")), "art_style": style}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    build.write_art_page(PAGE.read_text(encoding="utf-8"), job_dir / "index.html", style, "boardskin.js", broadcast=True)
    for name in board.FILES:
        shutil.copyfile(build.TEMPLATES / name, job_dir / name)


SWAP_HOLD = 0.78     # 차례로 바뀌는 장면에서 항목마다 찍는 때(구간 길이에 대한 비율) — templates/scene.html 의 drawSwap 박자와 맞춘 값:
                     # 구간의 72% 까지는 등장 효과(고리 · 색 조각 · 평행 띠)가 남아 있고 85% 부터는 퇴장이 시작된다. 그 사이가 글자만 또렷한 순간이다


def shot_times(scene_or_job: dict) -> tuple[list[float], int]:
    """찍을 시각들과 그 가운데 '다 나온 순간'의 번호. 정지면 끝 무렵 한 장, 움직이면 네 순간(들어옴 · 다 나옴 · 나감)."""
    dur = scene_or_job.get("duration_seconds") or 2.0
    last = max(0.0, (round(dur * FPS) - 1) / FPS)
    is_moving = scene_or_job["moving"] if "moving" in scene_or_job else moving(scene_or_job)
    if not is_moving:
        return [round(last * 0.95, 3)], 0
    n = scene_or_job["swap"] if "swap" in scene_or_job else swap_count(scene_or_job)
    if n:           # 차례로 바뀌는 장면: 항목마다 한 순간씩(그 항목이 자리 잡고 등장 효과가 걷힌 때) — 검수가 모든 글자를 본다
        return [round(min(last, dur * (k + SWAP_HOLD) / n), 3) for k in range(n)], 0
    ks = (0.15, 0.35, 0.5, 0.8) if scene_or_job.get("layout") == "wipe" else (0.1, 0.3, 0.65, 0.95)
    return [round(last * k, 3) for k in ks], 2


def shoot(job_dir: Path, profile: Path) -> tuple[Path, list[str], dict]:
    """시안을 찍는다 → (시안.png = 다 나온 순간 한 장, 자동 검사 줄들, {big 가장 큰 글자 높이 / 화면 세로, sheet 움직임 검수판}).

    움직이는 장면이면 네 순간(화살표가 차례로 나오면 화살표가 하나씩 더해지는 순간)을 한 장에 모은 움직임.png 도 남긴다. 받은 그대로(아래에 자동 검사 줄)는 검수판.png 로 둔다.
    """
    from PIL import Image
    job_dir = job_dir.resolve()
    rec = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    times, hold = shot_times(rec)
    frames = job_dir / "frames"
    # panel: 안전 영역 점선 없이 그래픽만(검수자가 점선을 디자인으로 읽지 않게)
    _, got = export._capture(job_dir, "panel&times=" + ",".join(f"{t:g}" for t in times), frames, 1, profile, 120)
    raw = frames / "00000.png"
    # 차례로 나오는 장면(화살표 · 항목)은 화면(templates/scene.html 의 STEPS)이 하나씩 더해지는 순간으로 바꿔 찍고 그 시각을 알려 준다.
    # 다 나온 순간은 화면이 알려 준 장(hold) — 나가는 움직임이 있으면 그 뒤에 나가는 순간 한 장이 더 붙는다.
    # 알림이 이상하면(수 · 숫자가 안 맞음) 보낸 시각 그대로 읽는다
    steps = _steps(got)
    if steps:
        times, hold = steps["times"], steps["hold"]
    im = Image.open(raw)
    cols = min(2, len(times))
    fw, fh = 1920, 1080
    shot = job_dir / "시안.png"
    im.crop(((hold % cols) * fw, (hold // cols) * fh, (hold % cols + 1) * fw, (hold // cols + 1) * fh)).save(shot)
    sheet = None
    if len(times) > 1:
        sheet = job_dir / "움직임.png"
        im.crop((0, 0, cols * fw, ((len(times) + cols - 1) // cols) * fh)).save(sheet)
    im.close()
    raw.replace(job_dir / "검수판.png")     # 받은 한 장은 검수판으로 옮기고(지우지 않는다), 비어 있는 받는 폴더만 치운다
    try:
        frames.rmdir()
    except OSError:
        pass
    clear = rec.get("layout") in ("banner", "wipe") or bool(rec.get("over_video"))      # 바탕판을 그리지 않는 장면(templates/scene.html 의 PLATE 와 같은 규칙)
    return shot, list(got.get("issues") or []), {"big": got.get("big"), "sheet": sheet, "times": times, "seconds": rec.get("duration_seconds"),
                                                 "frames": export.frame_count(rec.get("duration_seconds") or 2.0, FPS), "clear": clear,
                                                 "swap": int(rec.get("swap") or 0), "steps": steps["shown"] if steps else [],
                                                 "step_kind": steps["kind"] if steps else "", "leave": bool(steps and steps["leave"])}


def _steps(got: dict) -> dict | None:
    """화면이 알려 준 '하나씩 더해지는 순간': {'times' 찍은 시각들, 'shown' 장마다 그때까지 나온 수, 'kind' 무엇을 세는지(links 화살표 · items 차례),
    'hold' 다 나온 순간의 장 번호, 'leave' 맨 끝에 나가는 순간 한 장이 붙었나}. 없거나 모양이 틀리면 None."""
    times, shown, leave = got.get("times"), got.get("steps"), got.get("leave") is True
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0      # noqa: E731
    if not (isinstance(times, list) and isinstance(shown, list) and 2 <= len(shown) and len(times) == len(shown) + leave and len(times) <= 8
            and all(map(num, [*times, *shown]))):
        return None
    return {"times": [round(float(t), 3) for t in times], "shown": [int(n) for n in shown], "kind": "items" if got.get("step_kind") == "items" else "links",
            "hold": len(shown) - 1, "leave": leave}


def _steps_words(info: dict) -> str:
    """검수판의 장마다 무엇이 얼마나 나와 있는지 한 줄로 — 담당과 검수자에게 같은 말로 알린다."""
    nums = " · ".join(str(n) for n in info["steps"])
    said = f"장마다 화살표 {nums}개째까지" if info.get("step_kind", "links") == "links" else f"장마다 차례 {nums}번째까지(제목 다음부터 세고, 묶음 이름표도 한 차례다)"
    return said + (", 맨 끝 한 장은 나가는 순간" if info.get("leave") else "")


def judge(shot: Path, checks: list[str], facts: list[dict] | None = None, chat=None, info: dict | None = None) -> tuple[dict, str]:
    """시안을 Liner 가 읽고 확인할 점을 판정한다(호출 1번). 원문과 견주는 점이 있어 맡은 사실(원문)을 참고로 주고,
    '크게' 같은 크기 조건을 너그럽게 넘기지 않게 글자 크기 실측을, 움직이는 장면이면 네 순간 검수판을 같이 준다."""
    info = info or {}
    ref = "".join(f"\n- {f.get('fact')} (원문: \"{f.get('quote')}\"" + (f" · 출처 {f['source_name']}" if f.get("source_name") else "") + ")"
                  for f in facts or [])
    head = ("참고 — 의뢰 원문의 사실:" + ref + "\n\n") if ref else ""
    if info.get("big"):
        head += f"실측 — 가장 큰 글자 높이: 화면 세로의 {info['big'] * 100:.0f}%\n"
    if info.get("seconds"):
        head += (f"코드 실측 — 길이 {info['seconds']:g}초({info.get('frames')}장 · {FPS}fps) · 바탕: "
                 + ("투명(방송 화면 위에 얹힘 — 그림의 검은 곳은 투명한 자리)" if info.get("clear") else "있음(화면을 꽉 채우는 판)") + "\n")
    if info.get("sheet"):
        head += "그림 2는 움직임 검수판(" + " · ".join(f"{t:g}초" for t in info.get("times") or []) + ")이다.\n"
        if info.get("steps"):
            links = info.get("step_kind", "links") == "links"
            head += (f"{'화살표가' if links else '항목이'} 차례로 나오는 장면 — 검수판은 네 순간이 아니라 {'화살표가' if links else '화면에'} 하나씩 더해지는 순간 {len(info['steps'])}장이다"
                     f"(시간 순서대로 {_steps_words(info)}. 더해지는 순간의 마지막 장이 그림 1과 같은 다 나온 순간). "
                     f"장마다 {'화살표가' if links else '보이는 것이'} 그만큼씩 늘어 있으면 차례로 나오는 것이다"
                     + ("" if links else " — 번호를 건너뛴 장 사이에는 그 사이 것들이 함께 늘어 있다") + ". 막 들어오는 것이 조금 옅거나 살짝 내려와 있는 것은 등장 효과다.\n")
        if info.get("swap"):
            head += (f"차례로 바뀌는 장면 — 검수판은 네 순간이 아니라 항목 {info['swap']}개가 저마다 자리 잡은 순간이다(그림 1은 그 첫째). "
                     "글자가 찍힐 때 잠깐 붙는 등장 효과(원 고리 · 색 조각 · 평행 띠)는 코드가 자동으로 넣는 장식이고, 이 순간들에는 걷혀 있어 그림에 보이지 않는 것이 정상이다.\n")
    pics = [picture(shot)] + ([picture(info["sheet"])] if info.get("sheet") else [])
    content = [{"type": "text", "text": head + "\n확인할 점:\n" + "\n".join(f"{n}. {c}" for n, c in enumerate(checks, 1))}, *pics]
    resp = _call([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": content}], model=JUDGE_MODEL,
                 schema=JUDGE_SCHEMA, name="scene_judge", max_tokens=4000, chat=chat,
                 fallback="liner-gemini-router")       # 그린 쪽(claude)이 아닌 세 번째 회사로 — 검수가 같은 회사 모델로 넘어가지 않게
    return intake._parse_json(resp["choices"][0]["message"]["content"]), resp.get("model") or ""


def export_scene(folder: Path, alpha: bool = False, log=print) -> dict:
    """납품 파일을 만든다 — 그래픽 MP4(30fps, 길이 × 30장)와, alpha 면 전체 프레임 투명 PNG 시퀀스. 장수와 투명도를 실제로 잰다.

    무거운 일이다(브라우저로 프레임을 다 그리고 ffmpeg 로 묶는다) — 이 PC 에서는 공용 대기열(heavyq)로 돌린다.
    """
    from PIL import Image
    folder = Path(folder).resolve()
    rec = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    dur, want = rec["duration_seconds"], export.frame_count(rec["duration_seconds"], FPS)
    got = export.export(folder, dur, FPS, alpha=alpha, size=(1920, 1080), name="납품", log=log)
    out = {"mp4": str(got["mp4"]), "frames": got["frames"], "want": want, "frames_ok": got["frames"] == want, "spec": got.get("spec"), "seconds": got.get("seconds")}
    if alpha:
        pngs = sorted(Path(got["alpha_dir"]).glob("*.png"))
        clear = 0
        for f in pngs:                                  # 모든 프레임: 투명한 곳이 있는가(바탕 없는 그래픽이면 있어야 한다)
            with Image.open(f) as im:
                clear += im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 255
        out.update({"alpha_frames": len(pngs), "alpha_frames_ok": len(pngs) == want, "alpha_with_clear": clear})
    log(json.dumps(out, ensure_ascii=False))
    return out


def judged(verdict: dict | None, checks: list[str]) -> str:
    """검수자가 확인할 점마다 낸 판정을 한 줄로 — 담당에게 '걸린 점'과 함께 돌려준다.

    '걸린 점'만 돌려주면, 걸린 것이 자동 검사뿐일 때 담당이 "검수자가 판정을 내지 않았다"고 잘못 적는다(11-24 앱 실측: 확인할 점 넷이
    모두 '예'였는데 납품 말에 '검수 미완료'가 올라갔다)."""
    got = {c.get("n"): c for c in (verdict or {}).get("checks") or [] if isinstance(c, dict)}
    seen = [got.get(n) for n in range(1, len(checks) + 1)]
    yes, none = sum(1 for v in seen if v and v.get("ok") is True), sum(1 for v in seen if not v)
    no = len(checks) - yes - none
    said = f"확인할 점 {len(checks)}개 — 예 {yes}" + (f" · 아니오 {no}" if no else "") + (f" · 판정 없음 {none}" if none else "")
    return said + ("(검수자는 모두 '예'로 판정했다 — 걸린 점이 있다면 코드 검사 · 자동 검사 · 글자 대조에서 나온 것이다)" if checks and yes == len(checks) else "")


def failures(code: list[str], issues: list[str], verdict: dict | None, checks: list[str], scene: dict) -> list[str]:
    out = [f"[코드 검사] {s}" for s in code] + [f"[자동 검사] {s}" for s in issues]
    if verdict:
        got = {c.get("n"): c for c in verdict.get("checks") or []}
        for n, c in enumerate(checks, 1):
            v = got.get(n)
            if not v:
                out.append(f"[검수] 확인할 점 {n}: 판정 없음 — {c}")
            elif not v.get("ok"):
                out.append(f"[검수] 확인할 점 {n}: 아니오 — {c} / 근거: {v.get('why')}")
        hay = _norm(" ".join(verdict.get("text_seen") or []))
        out += [f"[검수] 화면에서 읽히지 않음: {s}" for s in wanted_texts(scene) if not _seen(s, hay)]
        out += [f"[검수] 읽기 어려움: {s}" for s in verdict.get("readability") or []]
    return out


# ---------------------------------------------------------------- 한 의뢰(메인 ↔ 담당)
def _assistant(msg: dict) -> dict:
    return {"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})}


LAST_CALL = "이번이 마지막 차례다. redo_worker 는 더 받지 않는다 — deliver 를 불러 납품할 담당을 정해라(납품할 것이 없으면 accept 를 비워 부른다)."


def _say(msgs: list[dict], text: str) -> None:
    """대화 끝에 사용자 말 한 줄을 더한다. 끝이 user 면 그 메시지에 잇는다(글이면 줄을 바꿔, 시안 그림 묶음이면 글 조각으로) —
    user 메시지가 연달아 두 개 놓이지 않게. 끝이 user 가 아니면(도구 결과 등) 새 user 메시지로 붙인다."""
    end = msgs[-1] if msgs else None
    if end and end["role"] == "user" and isinstance(end["content"], str):
        end["content"] += "\n" + text
    elif end and end["role"] == "user" and isinstance(end["content"], list):
        end["content"].append({"type": "text", "text": text})
    else:
        msgs.append({"role": "user", "content": text})


def _tool(cid: str, out) -> dict:
    return {"role": "tool", "tool_call_id": cid, "content": json.dumps(out, ensure_ascii=False)}


def leftover_focus(handled: str, items: list[str]) -> str:
    """기존 담당이 본체를 맡은 의뢰에서, 그 틀로 못 그린 요청만 메인에게 보이는 덧붙임(plan_request 의 focus)."""
    return "\n".join([
        f"[이미 맡은 부분] 이 의뢰의 본체는 이미 있는 담당이 {handled}(으)로 만든다 — 다시 만들지 않고, 그 부분으로 report_unable 을 부르지도 않는다.",
        "[그 담당이 못 그린 요청 — 이것만 본다]", *[f"- {x}" for x in items],
        "남은 요청마다, 본체 영상 위에 따로 얹을 그래픽(over_video — 하단 띠 · 큰 글자 판 · 표시 등)으로 만들 수 있으면 spawn_worker, "
        "본체 그림 안에 그려야 하거나 재료로 안 되면 report_unable 로 까닭과 필요한 것을 적는다. "
        "facts 는 남은 요청에 필요한 것만 쓰고, quote 는 위 의뢰 원문에서 그대로 옮긴다."])


def _research_answer(n: int, question: str, got: dict) -> tuple[dict, dict, str]:
    """에이전트 답 → (메인에게 줄 도구 결과, 기록, 근거 대조용 글)."""
    refs = [r for r in got.get("references") or [] if isinstance(r, dict)][:5]
    rows = [{"번호": f"r{n}-{k}", "제목": _txt(r.get("title"))[:120], "사이트": _txt(r.get("hostname")), "날짜": _txt(r.get("date")),
             "원문": _txt(r.get("description"))[:400]} for k, r in enumerate(refs, 1)]
    answer = _txt(got.get("answer"))[:800]
    record = {"id": f"r{n}", "question": question, "answer": answer,
              "references": [{"n": row["번호"], "title": row["제목"], "hostname": row["사이트"], "date": row["날짜"],
                              "url": _txt(r.get("url")), "text": row["원문"]} for row, r in zip(rows, refs)]}
    text = " ".join([answer, *[f"{row['제목']} {row['원문']}" for row in rows]])
    return {"자료": f"r{n}", "답": answer, "출처": rows}, record, text


def _source_texts(found: list[dict]) -> dict[str, str]:
    """research 기록 → {출처 번호(r1-2): 그 출처의 제목 · 원문} — 사실이 지목한 출처에 근거가 있는지 볼 때 쓴다."""
    return {r["n"]: f"{r['title']} {r['text']}" for rec in found for r in rec["references"]}


def _liner_research(question: str) -> dict:
    return intake._liner().research(question)


MAIN_IMAGES = 3         # 메인이 함께 읽는 의뢰서 그림 수(접수 CG Director 와 같다)


def plan_request(text: str, log=print, chat=None, focus: str = "", research=None, images=None) -> dict:
    """1단계(앱의 '읽기'): 메인이 지시서를 쓰고 담당을 정의한다 — 담당은 아직 일하지 않는다. 코드가 검사해 걸리면 한 번 더 묻는다.

    focus = 의뢰 글 뒤에 붙는 덧붙임(예: leftover_focus — 기존 담당이 못 그린 요청만 보라). 근거(quote) 검사는 의뢰 글로만 한다.
    돌려주는 것: {main_ok, brief, workers(띄울 담당의 정의), unable, calls, msgs(시스템 지시를 뺀 메인 대화), rounds, problems}.
    전부 JSON 으로 저장할 수 있다 — 앱은 지시서.json 으로 남겼다가 사람이 '만들기'를 누르면 make_request 로 이어 간다."""
    # images = 의뢰서 그림(data URL) — 손으로 그린 의뢰서는 배치 · 화살표 · 순서가 그림에만 있다. 메인이 글과 함께 직접 본다
    pics = [u for u in images or [] if isinstance(u, str)][:MAIN_IMAGES]
    head = "[제작진 의뢰]\n" + (text.strip() or "(글 없이 의뢰서 그림만 왔다)") + (f"\n\n{focus}" if focus else "")
    if pics:
        head += (f"\n\n[의뢰서 그림 {len(pics)}장] 아래 그림도 의뢰 원문이다. 그림에 그려진 배치 · 화살표(어디서 어디로) · 순서 표시 · 글자를 읽어 지시서에 옮긴다. "
                 f"그림에만 있는 사실은 source 를 '{PICTURED}'으로 적는다.")
    msgs: list[dict] = [{"role": "system", "content": MAIN_SYSTEM},
                        {"role": "user", "content": [{"type": "text", "text": head}, *[{"type": "image_url", "image_url": {"url": u}} for u in pics]] if pics else head}]
    rounds: list[dict] = []
    got, problems, calls = {"brief": {}, "workers": [], "unable": []}, ["시작 전"], []
    found: list[dict] = []          # research 기록 [{id, question, answer, references}]
    found_text = ""
    research = research or _liner_research
    attempts, asks = 0, 0
    searched = 0                    # 이 의뢰에서 실제로 보낸 자료 찾기 횟수 — 실패도 센다(한도는 받은 자료 수가 아니라 이 값으로 본다)
    while attempts < 2 and asks < MAX_RESEARCH + 1:
        calls = _ask(msgs, rounds, "자료 찾기 뒤 지시서" if found and attempts == 0 else "지시서" if attempts == 0 else "지시서 다시", log, chat)
        if any(n == "research" for _, n, _, _ in calls):       # 자료를 먼저 받는다 — 같은 답의 다른 도구는 받은 뒤 다시 부르게
            asks += 1
            for cid, name, args, _ in calls:
                if name != "research":
                    out = {"받음": False, "까닭": "자료를 받은 뒤 write_brief 부터 다시 불러라"}
                elif searched >= MAX_RESEARCH:
                    out = {"오류": f"자료 찾기는 의뢰마다 {MAX_RESEARCH}번까지다. 받은 자료로 write_brief 부터 불러라."}
                else:
                    q = _txt((args or {}).get("question")).strip()[:300]
                    searched += 1       # 부르기 전에 센다 — 검색이 실패해도 한도에서 빠진다(장애 때 호출이 불어나지 않게)
                    try:
                        out, record, text_got = _research_answer(len(found) + 1, q, research(q))
                        found.append({**record, "why": _txt((args or {}).get("why"))[:200]})
                        found_text += " " + text_got
                        log(f"  메인 → 자료 찾기 r{len(found)}: {q[:60]} · 출처 {len(record['references'])}개")
                    except Exception as e:      # 찾지 못함 — 메인이 받은 것만으로 정하거나 '자료 필요'로
                        out = {"오류": f"자료를 찾지 못했다: {intake.reason(e)[:200]}"}
                        log(f"  메인 → 자료 찾기 실패: {q[:60]}")
                msgs.append(_tool(cid, out))
            rounds[-1]["research"] = [r["id"] for r in found]
            continue
        attempts += 1
        got, problems = check_main(calls, text, found_text, _source_texts(found), pictured=bool(pics))
        rounds[-1]["problems"] = problems
        if not problems:
            break
        for cid, *_ in calls:
            msgs.append(_tool(cid, {"받음": False, "까닭": "아래 걸린 점을 보고 다시 부른다"}))
        msgs.append({"role": "user", "content": "걸린 점:\n" + "\n".join(f"- {p}" for p in problems) + "\nwrite_brief 부터 필요한 도구를 전부 다시 불러라."})
    refs = {r["n"]: r for rec in found for r in rec["references"]}
    texts = _source_texts(found)
    for f in got["brief"].get("facts") or []:       # 사실마다 출처 이름 · 주소(담당 · 검수 · 지시서 카드가 쓴다) — 제자리에서
        if isinstance(f, dict):
            ref = refs.get(_txt(f.get("source")).strip())
            if ref:
                if _quoted(f.get("quote"), texts[ref["n"]]):       # 근거가 그 출처의 원문에 있는 것을 확인한 번호만 화면용 이름 · 주소로 쓴다
                    f["source_name"], f["source_url"] = ref["hostname"] or ref["title"], ref["url"]
            elif _txt(f.get("source")).strip() == "자료" and found:      # 번호 없이 자료에서 온 사실 — 근거가 있는 출처만, 답에만 있으면 받은 출처 전부
                hosts = [r["hostname"] for r in refs.values() if r["hostname"] and _quoted(f.get("quote"), texts[r["n"]])]
                f["source_name"] = " · ".join(dict.fromkeys(hosts or [r["hostname"] for r in refs.values() if r["hostname"]]))[:80]
    return {"main_ok": not problems, "brief": got["brief"], "unable": list(got["unable"]), "problems": problems, "rounds": rounds,
            "workers": [a for _, n, a, _ in calls if n == "spawn_worker"] if not problems else [],
            "calls": [[cid, n, a] for cid, n, a, _ in calls], "msgs": msgs[1:], "research": found}


def _ask(msgs: list[dict], rounds: list[dict], stage: str, log, chat) -> list:
    resp = _call(msgs, model=MAIN_MODEL, tools=MAIN_TOOLS, chat=chat)
    msg = resp["choices"][0]["message"]
    calls = parse_calls(msg)
    msgs.append(_assistant(msg))
    rounds.append({"stage": stage, "answered_model": resp.get("model"), "text": msg.get("content") or "",
                   "calls": [{"name": n, "args": a if a is not None else raw} for _, n, a, raw in calls]})
    log(f"  메인({stage}): 도구 {len(calls)}번 ({', '.join(n for _, n, _, _ in calls) or '없음'})")
    return calls


def make_request(planned: dict, work: Path, profile: Path, tries_cap: int = MAX_TRIES, log=print, chat=None, worker_chat=None, judge_chat=None,
                 drop: list[str] | None = None, note: str = "", pictures=None) -> dict:
    """2단계(앱의 '만들기'): 지시서 대로 담당들이 도구로 일하고 → 결과가 메인에게 돌아가고 → 메인이 다시 시키거나 납품한다.

    drop = 사람이 지시서 카드에서 뺀 담당 번호(w1 …) — 그 담당은 띄우지 않고 메인에게 '제작진이 뺐다'고 돌려준다.
    note = 사람이 덧붙인 말 — 담당마다 첫 지시에 붙는다."""
    msgs: list[dict] = [{"role": "system", "content": MAIN_SYSTEM}, *planned["msgs"]]
    rounds: list[dict] = []
    brief, unable, drop = planned["brief"], list(planned["unable"]), set(drop or [])
    workers: dict[str, Worker] = {}
    k = 0
    for cid, name, args in planned["calls"]:      # 각 spawn_worker 의 도구 결과로 담당의 결과가 메인에게 돌아간다
        if name != "spawn_worker":
            msgs.append(_tool(cid, {"받음": True}))
            continue
        k += 1
        wid = f"w{k}"
        if wid in drop:
            log(f"  {wid} 담당은 제작진이 빼서 띄우지 않음: {args.get('role')}")
            msgs.append(_tool(cid, {"worker": wid, "result": "제작진이 뺌", "summary": "제작진이 지시서 카드에서 이 담당을 빼고 만들라고 했다"}))
            continue
        log(f"  메인 → {wid} 담당을 만듦: {args.get('role')}")
        workers[wid] = Worker(wid, args, brief, work, profile, tries_cap, log=log, chat=worker_chat, judge_chat=judge_chat, note=note, pictures=pictures)
        msgs.append(_tool(cid, workers[wid].run()))
    delivery = None
    if not workers:
        delivery = {"accept": [], "message": " / ".join(_txt(u.get("reason")) for u in unable), "by": "담당 없음 — 못 함으로 끝"}

    def show(ids):
        parts: list[dict] = []
        for wid in ids:
            shot, sheet = workers[wid].last_shot(), workers[wid].last_sheet()
            if shot:
                parts += [{"type": "text", "text": f"{wid} ({workers[wid].role}) 마지막 시안:"}, picture(shot)]
            if sheet:
                parts += [{"type": "text", "text": f"{wid} 움직임 검수판(시간 순서):"}, picture(sheet)]
        if parts:
            msgs.append({"role": "user", "content": parts})

    # 메인이 결과를 보고 다시 시키거나(담당마다 한 번) 납품한다. 마지막 바퀴에는 납품을 재촉하고 다시 하기는 받지 않는다
    # (담당 셋을 한 바퀴에 하나씩 다시 시키면 deliver 차례가 남지 않아 합격한 시안도 '메인이 받지 않음'으로 나온다)
    show(list(workers))
    redone: set[str] = set()
    for k in range(3 if workers else 0):
        last = k == 2
        if last:
            _say(msgs, LAST_CALL)
        try:
            calls = _ask(msgs, rounds, "마무리", log, chat)
        except Exception as e:      # 메인을 부르지 못함 — 납품 판단만 없을 뿐, 담당들이 낸 시안은 그대로 돌려준다
            why = intake.reason(e)
            log(f"  메인(마무리) 호출 실패: {why}")
            delivery = {"accept": [], "message": f"마무리 판단을 하지 못했습니다({why}) — 담당들이 낸 시안을 그대로 보여 드립니다.", "by": "메인 호출 실패"}
            break
        if not calls:
            msgs.append({"role": "user", "content": "도구로만 답한다. redo_worker 나 deliver 를 불러라."})
            continue
        again: list[str] = []
        for cid, name, args, _ in calls:
            a = args or {}
            if name == "redo_worker":
                wid = _txt(a.get("worker"))
                if last:        # 돌려도 그 결과를 읽을 차례가 없다 — 담당을 돌리지 않는다
                    out = {"오류": "마지막 차례다 — 다시 하기는 더 받지 않는다. deliver 를 불러라."}
                elif wid not in workers:
                    out = {"오류": f"없는 담당: {wid} (있는 담당: {', '.join(workers)})"}
                elif wid in redone:
                    out = {"오류": "다시 하기는 담당마다 한 번뿐이다"}
                else:
                    redone.add(wid)
                    log(f"  메인 → {wid} 다시 하기: {_txt(a.get('instruction'))[:60]}")
                    out = workers[wid].run(_txt(a.get("instruction")))
                    again.append(wid)
            elif name == "deliver":
                bad = [x for x in a.get("accept") or [] if x not in workers]
                out = {"오류": f"없는 담당: {', '.join(bad)}"} if bad else {"받음": True}
                if not bad:
                    delivery = {"accept": list(a.get("accept") or []), "message": _txt(a.get("message")), "by": "메인"}
            elif name == "report_unable":
                unable.append(a)
                out = {"받음": True}
            else:
                out = {"오류": f"지금은 redo_worker · deliver · report_unable 만 부른다({name})"}
            msgs.append(_tool(cid, out))
        if delivery:
            break
        show(again)
    if workers and not delivery:
        delivery = {"accept": [], "message": "", "by": "메인이 deliver 를 부르지 않음"}
    return {"main_ok": True, "brief": brief, "unable": unable, "workers": [w.record(work) for w in workers.values()],
            "delivery": delivery, "rounds": rounds}


def run_request(text: str, work: Path, profile: Path, tries_cap: int = MAX_TRIES, log=print, chat=None, worker_chat=None, judge_chat=None) -> dict:
    """의뢰 하나를 두 단계 한 번에(명령줄 실험): 메인이 지시서를 쓰고 담당을 띄움 → 담당들이 도구로 일함 → 메인이 다시 시키거나 납품."""
    planned = plan_request(text, log=log, chat=chat)
    if not planned["main_ok"]:
        return {"main_ok": False, "brief": planned["brief"], "unable": planned["unable"], "workers": [], "delivery": None, "rounds": planned["rounds"]}
    out = make_request(planned, work, profile, tries_cap, log=log, chat=chat, worker_chat=worker_chat, judge_chat=judge_chat)
    return {**out, "rounds": planned["rounds"] + out["rounds"]}


def run(path: Path, tries_cap: int = MAX_TRIES, log=print) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    name = path.stem
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = OUT / f"{stamp}_{name}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "의뢰.txt").write_text(text, encoding="utf-8")
    rid = f"agent-{stamp}-{secrets.token_hex(2)}"
    liner = intake._liner()
    t0 = time.perf_counter()
    log(f"[{name}] 의뢰 번호 {rid}")
    with liner.request(rid):
        out = run_request(text, work, OUT / ".browser-profile", tries_cap, log=log)
    usage = liner.tally(rid)
    summary = {"name": name, "rid": rid, "dir": work.name, "seconds": round(time.perf_counter() - t0, 1), **out,
               "main_rounds": len(out["rounds"]), "calls": sum(v["calls"] for v in usage.values()),
               "tokens": sum(v["tokens"] for v in usage.values()), "usage": usage}
    (work / "메인.json").write_text(json.dumps(out["rounds"], ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "결과.json").write_text(json.dumps({k: v for k, v in summary.items() if k != "rounds"}, ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "결과.md").write_text(report(summary), encoding="utf-8")
    log(f"[{name}] 끝 — {verdict_line(summary)} · {summary['seconds']}초 · Liner {summary['calls']}번 · 토큰 {summary['tokens']:,}")
    return summary


def verdict_line(s: dict) -> str:
    if not s.get("main_ok"):
        return "메인 실패"
    d = s.get("delivery") or {}
    parts = [f"{w['worker']} {w['role']}: {w['result']}" + (" · 다시 함" if w.get("redo") else "") for w in s["workers"]]
    parts += [f"{u.get('route')}{'·' + u.get('existing') if u.get('existing') not in (None, '없음') else ''}" for u in s["unable"]]
    tail = f" → 납품 {', '.join(d['accept'])}" if d.get("accept") else (" → 납품 없음" if s["workers"] else "")
    return " / ".join(parts) + tail


def report(s: dict) -> str:
    b, d = s.get("brief") or {}, s.get("delivery") or {}
    out = [f"# {s['name']} — 에이전트 실험 결과", "",
           f"- 판정: {verdict_line(s)}",
           f"- 걸린 시간 {s['seconds']}초 · Liner {s['calls']}번 · 토큰 {s['tokens']:,} · 메인 호출 {s['main_rounds']}번",
           f"- 시청자가 알아야 할 것: {b.get('viewer_point', '')}"]
    if d:
        out += [f"- 메인이 제작진에게: {d.get('message')} ({d.get('by')})"]
    out += ["", "## 제작 지시서(메인)", ""]
    out += [f"- {f.get('id')}: {f.get('fact')} — \"{f.get('quote')}\"" for f in b.get("facts") or []]
    if b.get("must_not_assert"):
        out += ["", "단정하지 않을 것: " + " / ".join(b["must_not_assert"])]
    if b.get("open_questions"):
        out += ["", "물을 것: " + " / ".join(b["open_questions"])]
    for u in s["unable"]:
        out += ["", f"## 못 함 — {u.get('route')} ({u.get('existing')})", "", f"- 까닭: {u.get('reason')}", f"- 필요한 것: {' / '.join(u.get('needed') or [])}"]
    for w in s["workers"]:
        out += ["", f"## {w['worker']} {w['role']} — {w['result']} (그리기 {w['draws']} · 검수 {w['reviews']} / 한도 {w['limits']})", "",
                f"- 목표: {w['goal']}", f"- 허락한 배치: {', '.join(w['layouts'] or [])} · 맡은 사실: {', '.join(w['fact_ids'] or [])}",
                "- 확인할 점: " + " / ".join(w["checks"]), "- 일한 순서(담당이 고름): " + " → ".join(w["order"]),
                f"- 담당의 말: {w['summary']}", f"- 마지막 시안: `{w['final_shot']}`"]
        if w.get("redo"):
            out += [f"- 메인의 다시 하기 지시: {w['redo']}"]
        if w["unresolved"]:
            out += ["- 미해결:", *[f"  - {f}" for f in w["unresolved"]]]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 보기 그림(과금 없음)
def _it(i, group=None, label="", text="", value=None, value_text="", time_=None, empty=False, emphasis=False, icon=None):
    return {"id": i, "group": group, "label": label, "text": text, "value": value, "value_text": value_text, "time": time_,
            "empty": empty, "emphasis": emphasis, "icon": icon, "fact_ids": []}


def _sc(layout, title="", items=(), groups=(), links=(), marks=(), note="", subtitle="", grid_total=None, grid_cols=None,
        motion=None, duration=None, over_video=False):
    return {"memo": "보기", "layout": layout, "title": title, "subtitle": subtitle, "note": note, "groups": list(groups), "items": list(items),
            "links": list(links), "marks": list(marks), "grid_total": grid_total, "grid_cols": grid_cols,
            "motion": {"in": "none", "out": "none", "order": "together", **(motion or {})}, "duration_seconds": duration, "over_video": over_video}


def _g(i, name, color):
    return {"id": i, "name": name, "color": color}


def _ln(a, b, label="", color="ink", dashed=False, both=False):
    return {"from": a, "to": b, "label": label, "color": color, "dashed": dashed, "both": both}


SAMPLES = {
    "rows": _sc("rows", "오늘의 미션 순서", [_it("a", label="1라운드", text="물풍선 피하기", value_text="10분", icon="A01"), _it("b", label="2라운드", text="퀴즈 버저 대결", value_text="15분", icon="A12"),
                                       _it("c", label="3라운드", text="보물찾기", value_text="20분", emphasis=True, icon="A11")], note="시간은 예정입니다"),
    "receipt": _sc("receipt", "간식 정산", [_it("a", label="떡볶이", value_text="8,000원"), _it("b", label="김밥 2줄", value_text="7,000원"), _it("c", label="음료", value_text="3,500원"),
                                         _it("d", label="합계", value_text="18,500원", emphasis=True)]),
    "grid": _sc("grid", "관객 선택", [_it("a", "r", "노랑 팀", value=41, value_text="41명"), _it("b", "b", "초록 팀", value=59, value_text="59명", emphasis=True)],
                [_g("r", "노랑 팀", "yellow"), _g("b", "초록 팀", "green")], grid_total=100, grid_cols=10),
    "bars": _sc("bars", "미션 기록", [_it("a", label="가람", value=42, value_text="42초"), _it("b", label="나래", value=35, value_text="35초", emphasis=True), _it("c", label="다온", value=51, value_text="51초")]),
    "lanes": _sc("lanes", "세 사람의 저녁", [_it("a1", "a", "부엌", "냄비를 올렸어요", time_="7시"), _it("b1", "b", "현관", "택배를 받았어요", time_="7시"),
                                         _it("a2", "a", "거실", "TV를 봤어요", time_="7시 30분", emphasis=True), _it("c2", "c", "거실", "혼자 있었어요", time_="7시 30분", emphasis=True)],
                 [_g("a", "가람", "blue"), _g("b", "나래", "pink"), _g("c", "다온", "orange")], marks=[{"target": "7시 30분", "label": "엇갈림", "fact_ids": []}],
                 note="누구 말이 맞는지는 아직 모릅니다"),
    "columns": _sc("columns", "가방 비교", [_it("a1", "h", "손전등", icon="A06"), _it("a2", "h", "지도"), _it("b1", "n", "손전등"), _it("b2", "n", "나침반", empty=True)],
                   [_g("h", "가진 것", "blue"), _g("n", "필요한 것", "orange")], links=[_ln("a1", "b1", "있음", "green")]),
    "ring": _sc("ring", "누가 누구 편?", [_it("a", "g1", "가람", "1팀"), _it("b", "g1", "나래", "1팀"), _it("c", "g2", "다온", "2팀"), _it("d", "g2", "라온", "2팀")],
                [_g("g1", "1팀", "blue"), _g("g2", "2팀", "red")], links=[_ln("a", "b", "믿음", "blue"), _ln("b", "a", "의심", "red", dashed=True), _ln("c", "d", "동맹", "green", both=True), _ln("d", "a", "오해", "purple")]),
    "chat": _sc("chat", "단체방 다시 보기", [_it("a", "x", text="정답은 3번 같아요", time_="21:02"), _it("b", "y", text="저는 1번이요", time_="21:03"),
                                         _it("c", "x", text="최종 3번으로 낼게요", time_="21:05", emphasis=True)], [_g("x", "가람", "blue"), _g("y", "나래", "pink")]),
    "banner": _sc("banner", "상황 설명", [_it("a", "b", text="원래는 경쟁 팀"), _it("b", None, "오늘만", "이번 미션은 한 팀으로 뜁니다", emphasis=True)], [_g("b", "파랑 · 빨강", "blue")]),
    "headline": _sc("headline", "", [_it("a", None, "다음 라운드", value_text="3분 뒤", emphasis=True), _it("b", None, "", "물풍선 피하기")]),
    "headline_over": _sc("headline", "", [_it("a", None, "남은 시간", value_text="5초")], over_video=True),
    "opening": _sc("headline", "", [_it("a", None, "오늘의 코너", "물풍선 대작전"), _it("b", None, "", "세 팀의 마지막 승부")],
                   motion={"in": "pop", "out": "fade", "order": "one_by_one"}, duration=3),
    "countdown": _sc("headline", "", [_it("a", value_text="5"), _it("b", value_text="4"), _it("c", value_text="3"), _it("d", value_text="2"), _it("e", value_text="1")],
                     motion={"order": "swap"}, duration=3, over_video=True),       # 5개 · 3초 = 하나에 0.6초(빠른 카운트다운)
    "wipe": _sc("wipe", "", [_it("a", None, "", "다음 라운드")], [_g("p", "", "purple"), _g("y", "", "yellow"), _g("a", "", "aqua")], duration=1.5),
}


def samples(log=print) -> Path:
    d = OUT / "_배치보기"
    for k, sc in SAMPLES.items():
        install(d / k, sc)
        shot, issues, _ = shoot(d / k, OUT / ".browser-profile")
        log(f"  {k}: 자동 검사 {len(issues)}건" + ("" if not issues else " — " + " / ".join(issues)))
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 실험: 메인이 임시 담당을 스스로 꾸려 시안(정지 · 움직임)을 만드는지 본다")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("request")
    r.add_argument("--tries", type=int, default=MAX_TRIES)
    b = sub.add_parser("batch")
    b.add_argument("folder")
    b.add_argument("--tries", type=int, default=MAX_TRIES)
    sub.add_parser("samples")
    s = sub.add_parser("shoot")
    s.add_argument("scene")
    e = sub.add_parser("export", help="시안 폴더(시안K)의 납품 파일: MP4 + (--alpha) 투명 PNG 시퀀스. 무거운 일 — 공용 대기열(heavyq)로 돌린다")
    e.add_argument("folder")
    e.add_argument("--alpha", action="store_true")
    a = ap.parse_args(argv)
    intake._liner().load_env()
    if a.cmd == "samples":
        print("보기 그림:", samples())
    elif a.cmd == "shoot":
        p = Path(a.scene)
        d = p.parent / f"다시_{time.strftime('%H%M%S')}"
        install(d, json.loads(p.read_text(encoding="utf-8")))
        shot, issues, _ = shoot(d, OUT / ".browser-profile")
        print(shot, "자동 검사:", issues)
    elif a.cmd == "export":
        export_scene(Path(a.folder), a.alpha)
    elif a.cmd == "run":
        run(Path(a.request), a.tries)
    else:
        rows = [run(p, a.tries) for p in sorted(Path(a.folder).glob("*.txt"))]
        table = ["| 의뢰 | 판정 | 시간(초) | Liner 호출 | 토큰 | 폴더 |", "|---|---|---|---|---|---|"]
        table += [f"| {s['name']} | {verdict_line(s)} | {s['seconds']} | {s['calls']} | {s['tokens']:,} | {s['dir']} |" for s in rows]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (OUT / f"결과표_{stamp}.md").write_text("\n".join(table) + "\n", encoding="utf-8")
        print("\n".join(table))
    return 0


if __name__ == "__main__":
    sys.exit(main())
