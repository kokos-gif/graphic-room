"""방송그래픽 의뢰 메신저(지금 만드는 종류는 지도) — 내 컴퓨터에서만 도는 작은 웹 서버.

의뢰(글·JPG·PDF)를 받아 읽고, 사람이 확인한 뒤 지도를 만들고, 영상으로 내보내는 과정을 대화로 보여 준다.
127.0.0.1 에만 열고, 다른 사이트에서 온 요청은 받지 않는다. 대화는 data/messenger 에 저장한다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import audit as auditor
from . import board, build, cuts, export, facesheet, icons, intake, mapdata, navi, plan, proof, quiz, quiz_build, request, revise, scene, transcribe, voice
from . import director as cg_director
from .geo import ATTRIBUTION, Geo, GeoError, write_atomic

WEB = Path(__file__).resolve().parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4", ".pdf": "application/pdf",
         ".svg": "image/svg+xml", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg",
         ".webm": "audio/webm", ".flac": "audio/flac"}       # 음성 의뢰는 대화의 첨부 고리에서 바로 들어 볼 수 있게
SHOWABLE = {"image/png", "image/jpeg", "image/webp", "image/gif"}     # 브라우저가 바로 보여 주는 그림
MAX_TEXT = 4000         # 의뢰 글의 길이 한도(자). 넘는 글은 받지 않는다 — 전문이 Liner 로 여러 번 가고, 글에서 구간을 찾는 일이 길이의 제곱으로 느려진다
MAX_BODY = 80 * 1024 * 1024
MAX_FILE = 20 * 1024 * 1024
MAX_IMAGE = 6 * 1024 * 1024
MAX_PAGES = 3       # 첨부 하나에서 읽는 쪽 수(PDF 의 앞쪽부터)
MAX_READ = 4        # 한 번의 의뢰에서 읽기·감사에 넘기는 그림 수(첨부 순서대로)


def coverage(files: list[dict], limit: int = MAX_READ, *, read: bool = True, audit: str | None = "done") -> str:
    """첨부를 실제로 어디까지 읽었는지 한 문장으로(_coverage). 화면이 그림으로 바꾸지 못한 첨부('failed')는 따로 적는다 —
    쪽수를 모르는 첨부가 '다 읽음'처럼 조용히 넘어가지 않게."""
    note = _coverage(files, limit, read=read, audit=audit)
    broken = [f["name"] for f in files if f.get("failed")]
    if broken:
        note = (note + " " if note else "") + f"그림으로 바꾸지 못해 읽지 못한 첨부: {', '.join(broken)} — 내용은 직접 확인해 주세요."
    return note


def _coverage(files: list[dict], limit: int = MAX_READ, *, read: bool = True, audit: str | None = "done") -> str:
    """첨부를 실제로 어디까지 읽고 감사했는지 한 문장으로 적는다. 첨부를 다 읽고 감사까지 했으면 빈 문자열.

    files = [{'name', 'sent' 그림으로 받은 쪽 수, 'total' 그 첨부의 전체 쪽 수}]. 읽지 않은 쪽을 조용히 넘기지 않으려는 것이다.
    read  = 모델이 첨부 그림을 실제로 읽었는가(글만 읽는 길로 갔으면 False).
    audit = 판독 감사의 실제 결과: 'done' 했음 · 'skipped' 안 함 · 'failed' 실패 · None 감사 단계가 없는 의뢰(순위판).
    읽기와 감사가 끝난 뒤에 부른다 — 넘길 수 있었던 범위가 아니라 실제로 한 일을 적어야 하기 때문이다.
    """
    span = lambda a, b: f"{a}쪽" if a == b else f"{a}~{b}쪽"      # noqa: E731
    left, passed, skipped, whole = limit, [], [], []
    for f in files:
        take = min(f["sent"], left)
        left -= take
        if take:
            passed.append(f"{f['name']} {span(1, take)}")
        if f["total"] > take:
            skipped.append(f"{f['name']} {span(take + 1, f['total'])}")
        if f["total"]:
            whole.append(f"{f['name']} {span(1, f['total'])}")
    if not whole:
        return ""
    if not read:        # 글만 읽었다 — 첨부는 한 쪽도 읽지 않았다
        checked = {"done": f"판독 감사는 {', '.join(passed)}만 대조했습니다." if passed else "판독 감사도 첨부를 보지 않았습니다.",
                   "skipped": "판독 감사도 하지 않았습니다.", "failed": "판독 감사는 실패했습니다."}.get(audit, "판독 감사도 하지 않았습니다.")
        return f"첨부는 읽지 않았습니다(글만 읽음) — 읽지 않은 쪽: {', '.join(whole)}. {checked} 첨부의 내용은 직접 확인해 주세요."
    checked = {"done": "판독 감사도 읽은 쪽만 대조했습니다.", "skipped": "판독 감사는 하지 않았습니다.",
               "failed": "판독 감사는 실패했습니다."}.get(audit, "")
    if skipped:
        return (f"첨부를 일부만 읽었습니다 — 읽은 쪽: {', '.join(passed) or '없음'} / 읽지 않은 쪽: {', '.join(skipped)}. "
                f"{checked} 읽지 않은 쪽의 내용은 직접 확인해 주세요.".replace(".  ", ". "))
    if audit in ("skipped", "failed"):      # 다 읽었지만 다른 모델의 대조는 받지 못했다
        return f"첨부({', '.join(passed)})는 읽었지만 {checked} 읽은 값을 의뢰서와 직접 견줘 주세요."
    return ""
SPANS = (1200, 2400, 4800)      # 지점 고르기 창의 가로 범위(미터)


# 첨부 없이 온 글이 앞 의뢰 · 그 결과를 가리키는 말인지(수정 요청) — 이런 글에는 앞서 보낸 의뢰서를 함께 읽힌다
FOLLOW_UP = re.compile(r"의뢰서|의뢰지|다시|안 ?나[왔와오]|안 ?보[이여]|없는데|없잖|없다|왜 |아니[다야라]|아닌데|아니고|수정|고쳐|고치|바꿔|바꾸|느낌|아까|방금|그대로|말고|틀렸|틀린|잘못|빠졌|빠진|위 ?(?:의|에|처럼)|앞 ?(?:의|에서|처럼)")


class DeskError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


def _safe_name(name) -> str:
    # 맥의 브라우저는 한글 파일 이름을 자모로 풀어서(NFD) 넘긴다 — 합친 글자로 바꾼 뒤에 걸러야 '가-힣' 에 맞는다(안 그러면 이름과 확장자가 뭉개진다)
    name = unicodedata.normalize("NFC", Path(str(name or "")).name)
    name = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", name).strip("._")[-80:]
    while len(name.encode("utf-8")) > 180:      # 저장할 때 앞에 시각 · 번호가 붙는다 — 맥 · 리눅스의 파일 이름 한도(255바이트) 안에 들게 앞에서부터 줄인다(확장자는 남는다)
        name = name[1:]
    return name.strip("._") or "file"


def _data_url(url, limit: int) -> tuple[str, bytes]:
    m = re.match(r"data:([\w/+.-]*);base64,(.*)$", url if isinstance(url, str) else "", re.S)
    if not m:
        raise ValueError("파일 내용이 없습니다")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except ValueError:
        raise ValueError("파일 내용을 풀지 못했습니다") from None
    if len(raw) > limit:
        raise ValueError(f"{limit // (1024 * 1024)}MB 를 넘습니다")
    return m.group(1), raw


class Desk:
    """대화 한 방과 그 안에서 도는 작업들."""

    def __init__(self, project_dir: Path, out_root: Path | None = None, *, offline: bool = False, analyze=None,
                 liner: bool | None = None, audit=None, director=None, transcriber=None, pictures=None):
        self.project = Path(project_dir)
        # 맞춤 그래픽의 항목 이름(곰인형 · 기차 …)에 맞는 작은 그림을 Liner 그림 모델로 마련하는 쪽(icons.for_labels). 앱(serve)이 넣어 준다 — 없으면 그림 없이 그린다
        self.pictures = pictures
        # 의뢰를 맨 먼저 받아 일을 나누는 CG Director(director.run). 앱(serve)이 넣어 준다 — 없으면 예전 순서(순위판 판별 → 지도 읽기)로 읽는다
        self.director = director
        # 의뢰서 그림을 다른 읽기보다 먼저 받아 적는 쪽(transcribe.run). 앱(serve)이 넣어 준다 — 없으면 그림만으로 읽는다(시험에서 만드는 Desk)
        self.transcriber = transcriber
        self._sheets: dict[str, str] = {}       # 그림 묶음 → 받아 적은 글(답을 붙여 다시 접수할 때 같은 그림을 또 받아 적지 않게)
        # MAPCG_ROOM 을 주면 대화방 · 첨부 · 결과를 그 폴더 아래에 따로 둔다(시연 녹화용 — 원래 대화방을 건드리지 않는다)
        room = Path(os.environ["MAPCG_ROOM"]) if os.environ.get("MAPCG_ROOM", "").strip() else None
        self.records = room or self.project / "data"     # 못 만든 의뢰 기록도 녹화방이면 그쪽에(원래 자료에 녹화 의뢰가 섞이지 않게)
        self.out = Path(out_root) if out_root else (room / "output" if room else self.project / "output")
        self.store = room / "messenger" if room else self.project / "data" / "messenger"
        self.uploads = room / "uploads" if room else self.project / "data" / "uploads"
        self.offline = offline
        self.analyze = analyze or intake.analyze
        self.audit = audit or auditor.check     # 판독 감사 — 읽은 것을 다른 모델이 원문과 다시 대조한다
        # 의뢰서를 읽을 수 있는 쪽(Liner 모델 · Claude). 켤 때 한 번 본다 — 키를 넣었으면 다시 실행해야 한다
        self.readers = intake.readers() if liner is None else {"liner": bool(liner), "claude": False}
        self._fixed_readers = liner is not None     # 시험에서 정해 준 값은 다시 살피지 않는다
        if offline and liner is None:
            # 오프라인으로 켰으면 키가 있어도 밖으로 아무것도 보내지 않는다 — 읽기·감사·지명 묻기·검색 모두(지도 서버도 같은 설정을 따른다)
            self.readers, self._fixed_readers = {"liner": False, "claude": False}, True
            if analyze is None:
                self.analyze = lambda text, images: intake.analyze(text, images, order=[])
        self.liner = self.readers["liner"]
        self.busy = 0                               # 돌고 있는 작업 수(읽기·지도 만들기·내보내기)
        self.lock = threading.RLock()
        self.work = threading.Lock()      # 지도 만들기·영상 내보내기는 한 번에 하나씩(브라우저와 CPU 를 같이 쓰므로)
        self.msgs: list[dict] = []
        self.rev = 0
        self.next_id = 1
        self.room = secrets.token_hex(4)
        self.picks: dict[str, dict] = {}
        self.pending: dict | None = None  # 접수에서 확인을 요청해 답을 기다리는 의뢰(글 · 그림) — 다음 글이 오면 붙여서 다시 접수한다
        self.last: dict | None = None     # 이 대화에서 가장 최근에 읽은 의뢰(글 · 그림) — 첨부 없이 온 수정 요청에 붙여 함께 읽힌다
        self.making: set[str] = set()     # 지금 만드는 중인 맞춤 그래픽 지시서(폴더 이름) — 같은 지시서의 제작이 겹쳐 돌지 않게
        self._tl = threading.local()      # 작업 스레드마다 그 작업이 속한 대화(room)
        self._load()

    # ---- 저장 ----
    def _load(self) -> None:
        path = self.store / "대화.json"
        if not path.is_file():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.msgs, self.room, self.next_id = saved["msgs"], saved["room"], saved["next_id"]
        except (OSError, ValueError, KeyError):
            return      # 읽지 못한 파일은 그대로 두고 빈 대화로 시작한다
        # 켤 때마다 번호를 하나 올린다 — 열려 있던 화면이 아래에서 바꾼 내용을 반드시 다시 받게
        try:
            last = int(saved.get("rev", 0))
        except (TypeError, ValueError):
            last = 0
        self.rev = max(last, max((m.get("rev", 0) for m in self.msgs), default=0)) + 1
        for m in self.msgs:     # 앱이 꺼지며 끊긴 작업 표시
            if m.get("state") == "run":
                m.update(state="fail", error="앱이 꺼지면서 중단됐습니다.", rev=self.rev)
            if m.get("exporting"):
                m.update(exporting=False, export_error="앱이 꺼지면서 중단됐습니다.", rev=self.rev)
            if m.get("proofing"):       # 영상은 이미 나왔다 — 검수만 끊겼다
                m.update(proofing=False, proof={"state": "검수 못 함", "note": "앱이 꺼지면서 화면 검수가 중단됐습니다.", "problems": [], "more": 0,
                                                "opinions": [], "notes": [], "shots": [], "model": "", "seconds": None}, rev=self.rev)

    def _save(self) -> None:
        self.store.mkdir(parents=True, exist_ok=True)
        write_atomic(self.store / "대화.json", json.dumps(
            {"room": self.room, "next_id": self.next_id, "rev": self.rev, "msgs": self.msgs}, ensure_ascii=False))

    def _check_room(self, room: str | None) -> None:
        """화면이 보고 있던 대화(room)가 지금 대화와 같은지 본다. None = 대조하지 않음(앱 안에서 직접 부를 때).
        새 대화에서는 메시지 번호가 1부터 다시 붙으므로, 오래 열린 화면의 번호만으로는 대상을 가릴 수 없다."""
        if room is not None and room != self.room:
            raise DeskError("대화가 바뀌었습니다(새 대화가 시작됨). 화면을 새로 고친 뒤 다시 해 주세요.", 409)

    def _spawn(self, fn, *args, fail=None) -> None:
        """작업을 따로 돌린다. 도는 동안은 busy 로 세어 '새 대화'가 끼어들지 못하게 한다.

        진행 표시를 남기는 쪽과 같은 임계구역(self.lock 을 잡은 채)에서 부른다 — 진행 표시 기록과 busy 예약 사이에
        '새 대화'가 끼면 예전 대화가 진행 중으로 보관되고 완료 카드가 새 대화에 붙는다.
        fail(사유) = 작업이 예외로 끝났거나 스레드를 띄우지 못했을 때 실패를 기록하는 함수. busy 는 어느 경우든 정확히 한 번 돌려받는다.
        """
        def run():
            self._tl.room = room        # 이 작업이 속한 대화 — add() 가 다른 대화에 결과를 붙이지 않게
            try:
                fn(*args)
            except Exception as e:      # 작업 쪽이 잡지 못한 고장 — 진행 표시가 '도는 중'으로 남지 않게
                if fail:
                    try:
                        fail(intake.reason(e))
                    except Exception:
                        pass
            finally:
                with self.lock:
                    self.busy -= 1

        with self.lock:
            room = self.room
            self.busy += 1
            try:
                threading.Thread(target=run, daemon=True).start()
            except Exception as e:      # 스레드를 띄우지 못함(자원 부족 등)
                self.busy -= 1
                if fail:
                    fail(f"작업을 시작하지 못했습니다({type(e).__name__})")
                raise DeskError("작업을 시작하지 못했습니다. 잠시 뒤 다시 해 주세요.", 503) from None

    def _progress_fail(self, p: dict):
        """진행 표시 p 를 실패로 바꾸는 함수(이미 끝난 표시는 그대로 둔다)."""
        def fail(why: str) -> None:
            if p.get("state") == "run":
                self.touch(p, state="fail", error=f"작업이 멈췄습니다: {why}")
        return fail

    def _start(self, room: str | None, title: str, fn, *args) -> None:
        """방 대조 · 진행 표시 기록 · busy 예약을 한 임계구역에서 한다(읽기·수정·제작이 같이 쓰는 예약 규칙)."""
        with self.lock:
            self._check_room(room)
            p = self.add("desk", "progress", title=title, lines=[], state="run", error="")
            self._spawn(fn, p, *args, fail=self._progress_fail(p))

    # ---- 메시지 ----
    # 카드에 싣는 Liner 한 줄의 일 이름(호출 기록의 task) — 어느 일을 어느 모델이 했는지
    LINER_TASKS = {"map_request": "읽기", "rank_board_request": "읽기", "info_board_plan": "읽기", "card_quiz_request": "읽기",
                   "voice_transcription": "받아쓰기", "reading_audit": "감사", "place_names": "지명", "place_search": "지명 검색",
                   "place_pick": "지명 검색", "web_search": "웹 검색", "map_revision": "수정", "board_revision": "수정",
                   "icon_subjects": "그림 주제", "scene_agent": "에이전트", "scene_judge": "시안 검수", "screen_proof": "화면 검수",
                   "research_quick_answer": "자료 찾기", "research_search": "자료 찾기", "director": "CG Director"}
    LINER_CARDS = {"card", "board", "plan", quiz.KIND, "unknown", scene.KIND, "result", cg_director.KIND}

    def _liner_line(self, rid: str) -> str:
        """의뢰 번호의 Liner 사용 한 줄: 'Liner 3번 · 9초 · 약 $0.012 — 읽기 gpt-6-sol · 감사 claude-sonnet-5.5'. 호출이 없으면 빈 글."""
        if not rid:         # 번호 없는 호출(점검 등)을 한데 세지 않게
            return ""
        try:
            s = intake._liner().summary(rid)
        except Exception:       # 기록을 못 읽어도 카드는 낸다
            return ""
        if not s.get("calls"):
            return ""
        jobs: dict[str, list[str]] = {}
        for task, model in s["models"]:
            label = self.LINER_TASKS.get(task) or task or "모델"
            short = model.split("/")[-1]
            if short not in jobs.setdefault(label, []):
                jobs[label].append(short)
        who = " · ".join(f"{label} {', '.join(ms)}" for label, ms in jobs.items())
        fail = f" · 실패 {s['failed']}" if s.get("failed") else ""
        return f"Liner {s['calls']}번{fail} · {s['seconds']:.0f}초 · 약 ${s['usd']:.3f}" + (f" — {who}" if who else "")

    def add(self, who: str, kind: str, **fields) -> dict:
        if kind in self.LINER_CARDS and "liner" not in fields:      # 의뢰 번호가 있는 카드에는 그 의뢰가 쓴 Liner 를 한 줄로
            rid = fields.get("rid") or (fields.get("draft") or {}).get("rid") or (fields.get("request") or {}).get("rid")
            line = self._liner_line(str(rid)) if rid else ""
            if line:
                fields["liner"] = line
        with self.lock:
            room = getattr(self._tl, "room", None)
            if room is not None and room != self.room:      # 작업이 시작된 대화는 이미 보관됐다 — 결과를 새 대화에 붙이지 않는다
                return {"id": 0, "who": who, "kind": kind, **fields}
            self.rev += 1
            m = {"id": self.next_id, "rev": self.rev, "who": who, "kind": kind, "time": time.strftime("%H:%M"), **fields}
            self.next_id += 1
            self.msgs.append(m)
            self._save()
            return m

    def touch(self, m: dict, **fields) -> None:
        with self.lock:
            self.rev += 1
            m.update(fields, rev=self.rev)
            self._save()

    def _line(self, m: dict, text: str) -> None:
        self.touch(m, lines=[*m.get("lines", []), str(text)])

    def state(self, rev: int = -1, room: str = "") -> dict:
        with self.lock:
            head = {"room": self.room, "rev": self.rev}
            if rev == self.rev and room == self.room:
                return head
            return {**head, "messages": json.loads(json.dumps(self.msgs)), "liner": self.liner,
                    "offline": self.offline,     # 화면도 오프라인을 따른다 — PDF 를 그림으로 바꿀 때 글자 표 · 글꼴을 밖에서 받지 않는다
                    "readers": self.readers, "route": intake.route(self.readers), "reader_labels": intake.READER_LABELS,
                    "reader_notes": intake.reader_notes(),
                    "face_modes": request.FACE_MODES, "layouts": request.LAYOUT_LABELS, "layout_rects": request.LAYOUTS, "vehicles": request.VEHICLES, "cameras": request.CAMERAS,
                    "styles": request.STYLES, "max_faces": request.MAX_FACES, "attribution": ATTRIBUTION,
                    "art_styles": request.ART_STYLES, "default_art_style": request.HOUSE_STYLE,
                    "orders": board.ORDERS, "max_rows": board.MAX_ROWS, "scene_layouts": scene.LAYOUT_LABELS}

    def reset(self, room: str | None = None) -> None:
        """지금 대화를 보관하고 새 대화를 연다. 지운다기보다 옮긴다 — 만든 지도 파일도 그대로 둔다.
        room = 화면이 보고 있던 대화. 다르면 이미 다른 화면이 새 대화를 열었으니 그 대화를 또 보관하지 않는다."""
        with self.lock:
            self._check_room(room)
            if self.busy:       # 도는 작업의 결과가 새 대화에 끼어들지 않게, 끝난 뒤에만 넘긴다
                raise DeskError("아직 도는 작업이 있습니다. 끝난 뒤에 새 대화를 시작해 주세요.", 409)
            path = self.store / "대화.json"
            if path.is_file() and self.msgs:
                keep = self.store / "지난대화"
                keep.mkdir(parents=True, exist_ok=True)
                path.rename(keep / f"대화_{time.strftime('%Y%m%d-%H%M%S')}_{self.room}.json")
            self.msgs, self.next_id, self.room = [], 1, secrets.token_hex(4)
            self.pending = None     # 답을 기다리던 의뢰는 지난 대화의 것이다
            self.last = None        # 수정 요청에 붙이려고 쥐고 있던 의뢰도
            self.rev += 1

    # ---- 의뢰 받기 ----
    def send(self, text: str, files: list, room: str | None = None, prefer: str = "") -> None:
        """prefer = '틀이 없음' 카드의 단추로 사람이 고른 틀(intake.NEAREST 의 키). 주면 종류를 가리지 않고 그 틀로 읽는다."""
        text = (text or "").strip()
        prefer = prefer if prefer in intake.NEAREST else ""
        files = [f for f in (files or []) if isinstance(f, dict)][:6]
        if not text and not files:
            raise DeskError("보낼 내용이 없습니다.")
        if len(text) > MAX_TEXT:
            raise DeskError(f"의뢰 글이 너무 깁니다({len(text):,}자 · 한도 {MAX_TEXT:,}자). 그래픽에 필요한 부분만 적어 주세요.")
        with self.lock:
            self._check_room(room)      # 첨부를 저장하기 전에 한 번(아래에서 등록할 때 한 번 더 본다)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(2)}"
        saved, images, cover, voices = [], [], [], []
        self.uploads.mkdir(parents=True, exist_ok=True)
        for n, f in enumerate(files, 1):
            name = _safe_name(f.get("name"))
            try:
                mime, raw = _data_url(f.get("data"), MAX_FILE)
                pages = [_data_url(u, MAX_IMAGE) for u in (f.get("images") or [])[:MAX_PAGES]]
            except ValueError as e:
                raise DeskError(f"첨부 '{name}' 을(를) 받지 못했습니다: {e}.") from None
            path = self.uploads / f"{stamp}_{n}_{name}"
            path.write_bytes(raw)
            item = {"name": name, "url": f"/uploads/{path.name}", "thumb": f"/uploads/{path.name}" if mime in SHOWABLE else ""}
            if not item["thumb"] and pages and pages[0][0] == "image/jpeg":     # PDF 는 첫 쪽 그림을 작게 보여 준다
                thumb = self.uploads / f"{stamp}_{n}_{name}_쪽1.jpg"
                thumb.write_bytes(pages[0][1])
                item["thumb"] = f"/uploads/{thumb.name}"
            saved.append(item)
            # 음성 의뢰: 파일 내용의 머리글자로 가린다(이름·형식 표시만으로는 믿지 않는다). 받아쓰기는 작업 스레드에서 한다(Liner 를 기다리는 동안 대화를 잡지 않게)
            # 화면이 음성으로 고른 첨부만, 그리고 내용도 음성일 때만 받아쓴다. 머리글자만 보면 MP4 영상 · HEIC 사진도 음성 그릇으로 보여,
            # 화면이 '보관만 한다'고 한 참고 영상이나 사진 의뢰서가 Liner 받아쓰기로 나간다
            kind = voice.sniff(raw) if f.get("state") == "voice" else None
            if kind:
                item["voice"] = True
                voices.append({"name": name, "data": raw})
                continue
            if f.get("state") == "voice":       # 화면은 음성이라고 했는데 알아볼 수 있는 형식이 아니다
                voices.append({"name": name, "data": b""})
                continue
            usable = [u for u, (pm, _) in zip(f.get("images") or [], pages) if pm in SHOWABLE]
            images += usable
            try:        # 화면 쪽이 알려 준 전체 쪽수(PDF 는 앞의 몇 쪽만 그림으로 바꿔 보낸다)
                total = int(f.get("pages") or 0)
            except (TypeError, ValueError):
                total = 0
            # failed = 화면이 판독용 그림으로 바꾸지 못한 첨부(PDF 변환 실패·그림 형식 못 읽음). 보관만 하는 첨부('keep')와 구분한다
            cover.append({"name": name, "sent": len(usable), "total": max(total, len(usable)), "failed": f.get("state") == "failed"})
        with self.lock:
            self._check_room(room)
            self.add("requester", "text", text=text, files=saved, **({"read_as": intake.NEAREST[prefer][0]} if prefer else {}))
            title = "음성을 받아쓰는 중" if voices else f"{intake.NEAREST[prefer][0]}(으)로 읽는 중" if prefer else "의뢰 내용을 읽는 중"
            # 접수에서 확인을 요청해 기다리던 의뢰가 있으면, 이번 글은 그 답이다 — 원래 의뢰(글 · 그림)에 질문과 답을 붙여 다시 접수한다.
            # 틀을 골라 다시 보낸 것(prefer)은 답이 아니라 새 지시이므로 붙이지 않는다
            waiting, self.pending = self.pending, None
            if waiting and waiting["room"] == self.room and not prefer:
                tail = "\n\n[접수 때 확인을 요청한 것]\n" + "\n".join(f"- {q}" for q in waiting["ask"]) + f"\n[의뢰자의 답]\n{text}"
                text = (waiting["text"][:max(0, MAX_TEXT - len(tail))] + tail).strip()
                images, cover = [*waiting["images"], *images], [*waiting["cover"], *cover]
                if not voices:
                    title = "답을 받아 의뢰를 이어서 읽는 중"
            elif (not prefer and not images and not voices and self.last and self.last["room"] == self.room and FOLLOW_UP.search(text)):
                # 첨부 없이 온 글이 앞 의뢰나 그 결과를 고쳐 달라는 말이다("이 느낌이 아니다 · 의뢰서를 다시 볼 것") —
                # 앞서 보낸 의뢰(글 · 의뢰서 그림)를 함께 읽힌다. 안 그러면 접수가 "의뢰서 그림이 없다"고 되묻기만 한다
                head = "[앞서 보낸 의뢰 — 아래 '이어서 온 글'이 이 의뢰나 그 결과를 고쳐 달라는 말이면 함께 읽고, 전혀 다른 새 의뢰이면 무시한다]\n"
                tail = f"\n\n[이어서 온 글]\n{text}"
                body = self.last["text"][:max(0, MAX_TEXT - len(head) - len(tail))] + tail
                text, images, cover = head + body, list(self.last["images"]), list(self.last["cover"])
                title = "앞서 보낸 의뢰서와 함께 읽는 중" if images else "앞서 보낸 의뢰와 함께 읽는 중"
                self.last = {**self.last, "text": body}
            if not prefer and not text.startswith("[앞서 보낸 의뢰"):
                # 이 대화에서 가장 최근에 읽은 의뢰 — 뒤따르는 수정 요청에 붙인다. 앱을 껐다 켜면 사라진다(그때는 의뢰서를 다시 첨부한다)
                self.last = {"room": self.room, "text": text, "images": images[:MAX_READ], "cover": list(cover)}
            self._start(None, title, self._read, text, images[:MAX_READ], cover, voices, prefer)

    def _read(self, p: dict, text: str, images: list[str], cover: list[dict] | None = None, voices: list[dict] | None = None,
              prefer: str = "") -> None:
        """의뢰 하나에 번호(rid)를 붙여 읽는다. 번호는 Liner 호출 기록에 남고, 확인 카드를 거쳐 제작 때의 호출(지명 묻기·검색)까지 이어진다.
        voices = 음성 첨부 [{name, data}]. 먼저 받아써서 글 의뢰 뒤에 붙이고, 받아쓴 글을 확인 카드의 알림 맨 앞에 그대로 보여 준다."""
        rid = f"의뢰-{time.strftime('%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        with intake._liner().request(rid):
            heard: list[str] = []
            for v in voices or []:
                if not v["data"]:
                    self._line(p, f"{v['name']}: 음성 형식을 알아보지 못했습니다")
                    heard.append(f"음성 첨부 '{v['name']}' 은(는) 형식을 알아보지 못해 받아쓰지 않았습니다(WAV·MP3·M4A·FLAC·OGG·WebM) — 내용은 직접 확인해 주세요.")
                    continue
                got = voice.hear(v["data"], offline=self.offline)
                if got["state"] != "받아씀":
                    self._line(p, f"{v['name']}: {got['note']}")
                    heard.append(f"음성 첨부 '{v['name']}' 을(를) 받아쓰지 못했습니다: {got['note']}")
                    continue
                self._line(p, f"{v['name']}: 받아씀({got['seconds']}초)")
                # 받아쓴 글은 의뢰서 글과 같은 자료다(지시문이 아니다) — 글 의뢰 뒤에 붙여 같은 길로 읽고 감사한다
                room = MAX_TEXT - (len(text) + 2 if text else 0)      # 이 받아쓴 글 가운데 읽기에 들어가는 길이(아래에서 MAX_TEXT 로 자른다)
                text = f"{text}\n\n{got['request_text']}".strip()
                # 카드에는 읽은 만큼만 옮긴다 — 전문을 실으면 뒤쪽까지 읽은 것처럼 보인다
                said = got["text"] if len(got["text"]) <= room else f"{got['text'][:max(0, room)]} …(여기부터는 {MAX_TEXT:,}자 한도를 넘어 읽지 않았습니다)"
                heard.append(f"음성 첨부 '{v['name']}' 을(를) 받아쓴 글: “{said}” — {got['note']}")
            if len(text) > MAX_TEXT:        # 긴 녹음을 받아쓴 글 — 글 의뢰와 같은 한도로 자른다
                text = text[:MAX_TEXT]
                heard.append(f"받아쓴 글까지 합쳐 {MAX_TEXT:,}자를 넘어 앞 {MAX_TEXT:,}자만 읽었습니다 — 뒤쪽 내용은 직접 확인해 주세요.")
            if voices and not text and not images:      # 받아쓴 것이 하나도 없다 — 읽을 것이 없으니 글로 적어 달라고 한다
                self.touch(p, state="fail", error=" / ".join(heard) + " 의뢰를 글로 적어 주세요.")
                return
            if voices:
                self.touch(p, title="의뢰 내용을 읽는 중")
            self._read_in(p, text, images, cover, rid, heard, prefer)

    def _read_in(self, p: dict, text: str, images: list[str], cover: list[dict] | None, rid: str, heard: list[str] | None = None,
                 prefer: str = "") -> None:
        """cover = 첨부마다 받은 쪽 수와 전체 쪽 수(coverage 의 files). 읽기·감사가 끝난 뒤 실제로 한 범위를 확인 카드의 알림 맨 앞에 적는다.
        heard = 음성 첨부를 받아쓴 글·받아쓰지 못한 사유(확인 카드의 알림 맨 앞에 그대로 둔다 — 잘못 들린 지명·숫자를 사람이 견줘 보게)."""
        # 의뢰서 그림을 먼저 받아 적는다. 받아 적은 글(seen = 의뢰 글 + 받아쓰기)은 메인 에이전트 · 의뢰서 읽기에 그림과 함께 넘기고,
        # 판독 감사 · 다시 보내기 칸 · 답변 대기에는 원래 글(text)을 쓴다 — 감사는 받아쓰기에 기대지 않고 그림을 따로 읽어야 한다
        sheet, sheet_note = self._transcribe(p, images, rid)
        seen = transcribe.attach(text, sheet)

        def lead(source: str, audit: str | None) -> list[str]:
            note = coverage(cover or [], MAX_READ, read=source != "simple", audit=audit)
            return [*(heard or []), *([sheet_note] if sheet_note else []), *([note] if note else [])]
        # CG Director 가 먼저 읽고 일을 나눈다(앱으로 켰을 때 · Liner 가 될 때). 나눈 대로 아래의 지금 코드가 그대로 실행한다.
        # 못 나누면 예전 순서로. 사람이 '틀 없음' 카드에서 틀을 골라 다시 보낸 의뢰(prefer)는 그 선택을 따른다.
        directed, skip_board = None, False
        if not prefer and self.director and self.readers.get("liner") and not self.offline and (text.strip() or images):
            directed = self._direct(p, seen, images, rid)
            if directed and directed.get("waiting"):
                # 답변 대기: 이 의뢰(글 · 그림)를 쥐고 있다가, 다음에 오는 글을 답으로 붙여 처음부터 다시 접수한다(send).
                # 앱을 껐다 켜면 쥐고 있던 것은 사라진다 — 그때는 의뢰를 다시 보내야 한다(질문 카드는 대화에 남아 있다)
                with self.lock:
                    self.pending = {"room": self.room, "rid": rid, "text": text, "images": list(images), "cover": list(cover or []),
                                    "ask": directed["ask"]}
                return
            if directed and not directed["assign"]:      # 맞는 틀이 없다 — 메인 에이전트가 의뢰 전체의 지시서를 쓴다
                first = directed["custom"][0]
                self._scene_plan(p, {"other_kind": first["name"], "nearest": "none", "unmade": [], "notes": []}, rid, text, list(images),
                                 lead("liner", None), read_text=seen)
                return
            if directed:
                prefer = directed["assign"]["prefer"]
                skip_board = not prefer     # 지도 담당 — 순위판 판별을 거치지 않고 지도로 읽는다
        # 먼저 순위판 의뢰인지 본다(글에 표가 있거나 '순위판'이라고 적었을 때). 아니면 지도 의뢰로 읽는다.
        try:
            b = None if skip_board else board.read(seen, images, available=self.readers, **({"prefer": prefer} if prefer else {}))
        except board.Unsure as e:       # 그림이 순위판인지 묻다가 실패했다 — 같은 그림을 지도 읽기로 다시 보내지 않는다(호출 기록에는 남아 있다)
            if not (directed and prefer):
                self.touch(p, state="fail", error=str(e))
                return
            # CG Director 가 고른 틀(정보판 · 퀴즈 …)로 읽다가 실패했다 — 의뢰를 버리지 않고 예전 순서(틀을 정하지 않은 판별)로 한 번 더
            self._line(p, f"{cg_director.LABEL} 가 고른 틀로 읽지 못해 예전 순서로 다시 읽습니다({str(e)[:120]})")
            try:
                b = board.read(seen, images, available=self.readers)
            except board.Unsure as again:
                self.touch(p, state="fail", error=str(again))
                return
            except Exception:
                b = None
        except Exception:       # 순위판 쪽 읽기가 고장 나도 지도 의뢰는 그대로 받는다
            b = None
        if b and isinstance(b.get("draft"), dict) and "text" in b["draft"]:
            b["draft"]["text"] = text.strip()       # 카드에 남는 원문은 의뢰자가 쓴 글만 — 받아 적은 글은 읽기에만 쓴다
        if b and b.get("kind") == quiz.KIND:
            self.touch(p, state="ok", title="카드 퀴즈 의뢰서를 읽었습니다")
            self.add("desk", quiz.KIND, draft={**b["draft"], "rid": rid}, source=b["source"],
                     notes=[*lead(b["source"], None), *b["notes"]])
            self._follow(rid, seen, "카드 퀴즈", "카드 퀴즈", [], directed, images)
            return
        if b and b.get("kind") == plan.KIND:       # 틀이 없는 정보판(VS 대결판·설명판) — Liner 가 설계도를 냈다
            self.touch(p, state="ok", title=f"의뢰 내용을 읽었습니다 — {plan.KIND_LABEL}({plan.SHAPES[b['draft']['shape']]}) · Liner 가 설계도를 냈습니다")
            self.add("desk", "plan", draft={**b["draft"], "rid": rid}, source=b["source"], notes=[*lead(b["source"], None), *b["notes"]])
            self._unmade(rid, "반영 못 한 요청", plan.SHAPES[b["draft"]["shape"]], b.get("unmade") or [])
            shape = plan.SHAPES[b["draft"]["shape"]]
            self._follow(rid, seen, f"{plan.KIND_LABEL}({shape})", shape, b.get("unmade") or [], directed, images)
            return
        if b:
            how = "글에서 표를 찾았습니다(모델 미사용)" if b["source"] == "simple" else f"{intake.READER_LABELS[b['source']]} 이 읽었습니다"
            self.touch(p, state="ok", title=f"의뢰 내용을 읽었습니다 — {board.KIND_LABEL} · {how}")
            self.add("desk", "board", draft={**b["draft"], "rid": rid}, source=b["source"], notes=[*lead(b["source"], None), *b["notes"]])
            self._follow(rid, seen, board.KIND_LABEL, board.KIND_LABEL, [], directed, images)
            return
        try:
            r = self.analyze(seen, images)
        except Exception as e:      # 읽기가 통째로 실패해도 대화는 이어 간다
            self.touch(p, state="fail", error=f"의뢰 내용을 읽지 못했습니다: {intake.reason(e, typed=False)}")
            return
        if not self._fixed_readers:     # 불러 보니 못 쓰게 된 쪽(로그인 만료 등)을 화면 표시에 반영한다
            self.readers = intake.readers()
        d = r["draft"]
        if r.get("other_kind") and not d["from"]["name"] and not d["to"]["name"] and not d.get("cuts"):
            # 지도도 순위판·정보판도 아닌 종류(대진표 · 그래프 …) — 빈 지도 카드를 내지 않는다. 감사할 지도 값도 없으니 감사도 부르지 않는다.
            # Liner 가 있으면 메인 에이전트가 이 의뢰의 지시서를 쓰고 맡을 임시 담당을 정의한다(맞춤 그래픽). 안 되면 '틀 없음' 카드
            if self.readers.get("liner") and not self.offline and seen.strip():
                self._scene_plan(p, r, rid, text, list(images), lead(r["source"], None), read_text=seen)
            else:
                self._unknown(p, r, rid, text, bool(images), lead(r["source"], None))
            return
        how = ("간이 해석으로 읽었습니다(모델 미사용)" if r["source"] == "simple"
               else f"{intake.READER_LABELS[r['source']]} 이 읽었습니다")
        # 확인 카드를 내기 전에 판독을 감사한다. 감사가 안 되거나 실패해도 카드는 낸다(그 사실을 카드에 적어서).
        self.touch(p, title=f"판독을 감사하는 중 — {how}")
        try:
            a = self.audit(text, images, r, available=self.readers)
        except Exception as e:      # 감사 쪽의 예상 못 한 고장 — 카드는 내고, 무엇이 났는지 원문을 붙여 둔다
            a = {"state": "failed", "reason": f"감사 중 예상하지 못한 오류가 났습니다({intake.reason(e)[:140]})"}
        if not isinstance(a, dict):
            a = {"state": "failed", "reason": "감사 결과를 알아볼 수 없습니다"}
        if not self._fixed_readers:
            self.readers = intake.readers()
        # 어긋난 곳이 없어도 가리지 못한 항목이 남았으면 '통과'라고 하지 않는다
        tail = {"skipped": "감사 안 함", "failed": "감사 실패"}.get(a.get("state")) or (
            f"감사에서 확인할 곳 {a['flags']}" if a.get("flags")
            else f"감사가 가리지 못한 곳 {a['unverified']}" if a.get("unverified") else "감사 통과")
        self.touch(p, state="ok", title=f"의뢰 내용을 읽었습니다 — {how} · {tail}")
        self.add("desk", "card", draft={**r["draft"], "rid": rid}, missing=r["missing"], source=r["source"], notes=[*lead(r["source"], a.get("state")), *r["notes"]], audit=a)
        self._unmade(rid, "반영 못 한 요청", "지도", r.get("unmade") or [])
        self._follow(rid, seen, f"지도 영상({d['from']['name'] or '출발'} → {d['to']['name'] or '도착'} 경로 이동)", "지도", r.get("unmade") or [], directed, images)

    # ---- 의뢰서 받아쓰기 ----
    SHEET_LINES = 40        # 진행 표시에 옮겨 보이는 받아쓰기 줄 수 한도

    def _transcribe(self, p: dict, images: list[str], rid: str) -> tuple[str, str]:
        """의뢰서 그림을 다른 읽기보다 먼저 받아 적는다(transcribe.run). 돌려주는 것: (받아 적은 글, 확인 카드에 적을 알림).
        받아쓰기를 붙이지 않은 Desk(시험) · Liner 가 없거나 오프라인 · 그림이 없으면 ('', '') — 예전처럼 그림만으로 읽는다."""
        pics = [u for u in images or [] if isinstance(u, str) and u.startswith("data:image/")]
        if not (self.transcriber and pics and self.readers.get("liner") and not self.offline):
            return "", ""
        key = hashlib.sha1("\n".join(pics).encode("ascii", "ignore")).hexdigest()
        if key in self._sheets:         # 질문에 답을 붙여 다시 접수한 의뢰 — 같은 그림을 또 받아 적지 않는다
            self._line(p, "의뢰서 받아쓰기: 앞서 받아 적은 글을 그대로 씁니다")
            return self._sheets[key], "의뢰서 그림을 먼저 받아 적은 글(앞서 받아 적은 것)을 읽기에 함께 넘겼습니다."
        self.touch(p, title="의뢰서 그림의 글씨를 먼저 받아 적는 중")
        try:
            got = self.transcriber(pics)
        except Exception as e:      # 받아쓰기 쪽의 예상 못 한 고장 — 의뢰는 그림만으로 그대로 읽는다
            got = {"ok": False, "why": intake.reason(e)[:160]}
        if not got.get("ok"):
            self._line(p, f"의뢰서 받아쓰기를 하지 못했습니다({got.get('why') or '까닭 모름'}) — 그림만으로 읽습니다")
            return "", f"의뢰서 그림 받아쓰기를 하지 못해({str(got.get('why') or '까닭 모름')[:80]}) 그림만으로 읽었습니다."
        sheet = got["text"]
        with self.lock:
            self._sheets[key] = sheet
        shown = [ln for ln in sheet.splitlines() if ln.strip()]
        more = len(shown) - self.SHEET_LINES
        self.touch(p, lines=[*p.get("lines", []), f"의뢰서 받아쓰기({got.get('model') or 'Liner'} · {got.get('seconds', 0):g}초):",
                             *[f"  {ln}" for ln in shown[:self.SHEET_LINES]], *([f"  …(아래 {more}줄 더)"] if more > 0 else [])])
        self.touch(p, title="의뢰 내용을 읽는 중")
        return sheet, (f"의뢰서 그림을 먼저 받아 적어({got.get('model') or 'Liner'}) 그 글을 읽기에 함께 넘겼습니다 — 받아 적은 글은 위 진행 표시에 있습니다. "
                       "판독 감사는 받아쓰기에 기대지 않고 그림을 따로 읽어 대조합니다.")

    # ---- CG Director(접수) ----
    def _direct(self, p: dict, text: str, images: list[str], rid: str) -> dict | None:
        """CG Director 를 불러 접수 판단 카드를 낸다. 나눈 일이 있으면 그 계획을, 못 나눴으면 None(예전 순서로 읽는다)."""
        self.touch(p, title=f"{cg_director.LABEL} 가 의뢰를 나누는 중")
        try:
            d = self.director(text, images, log=lambda s: self._line(p, s.strip()))
        except Exception as e:      # 연결 · 한도 · 예상 못 한 고장 — 의뢰는 예전 순서로 그대로 받는다
            d = {"ok": False, "why": intake.reason(e)[:160]}
        if not d.get("ok") and d.get("waiting") and d.get("ask"):
            # 일은 나누지 않고 질문만 돌아왔다 — 질문을 보이고 여기서 멈춘다. 답 없이 예전 순서로 읽으면 묻던 것을 짐작으로 채우게 된다
            self.add("desk", cg_director.KIND, rid=rid, summary=d.get("summary") or "", assign=None, custom=[], ask=d["ask"],
                     applied=d.get("applied") or [], searches=d.get("searches") or [], seconds=d.get("seconds"), waiting=True)
            self.touch(p, state="ok", title=f"{cg_director.LABEL} 가 확인을 요청했습니다 — 답을 보내 주시면 이어서 읽습니다")
            return {"waiting": True, "ask": list(d["ask"])}
        if not d.get("ok"):
            self._line(p, f"{cg_director.LABEL} 를 부르지 못해 예전 순서로 읽었습니다({d.get('why') or '나눈 일이 없음'})")
            self.touch(p, title="의뢰 내용을 읽는 중")
            return None
        self.add("desk", cg_director.KIND, rid=rid, summary=d["summary"], assign=d["assign"], custom=d["custom"], ask=d["ask"],
                 applied=d["applied"], searches=d["searches"], seconds=d.get("seconds"))
        a = d["assign"]
        self.touch(p, title=f"{cg_director.LABEL} 가 나눈 대로 읽는 중 — " + (f"{a['specialist']} 담당" if a else "임시 담당")
                   + (f" · 임시 담당 {len(d['custom'])}" if a and d["custom"] else ""))
        return d

    def _follow(self, rid: str, text: str, handled: str, label: str, items: list[str], directed: dict | None, images=None) -> None:
        """전문 담당이 본체를 맡은 뒤 그 틀로 못 그린 요청이 남았으면 메인 에이전트에게 보인다(_scene_leftover) — 조용히 빼지 않는다.
        CG Director 가 임시 담당에게 맡긴 부분이 있으면 그것을(같은 요청에 지시서 카드가 두 장 나오지 않게 — 메인을 한 번만 부른다),
        없으면 읽기가 못 그린 요청(items)을 넘긴다. 예전에는 이 경우 알림 한 줄만 남기고 버렸다(11-23 노들역 의뢰서의 '잠시정차 · 지도표시').
        images = 의뢰서 그림 — 메인이 손그림의 배치 · 화살표를 직접 본다."""
        parts = [c["part"] for c in directed["custom"]] if directed else []
        self._scene_leftover(rid, text, handled, label, parts or items, images)

    def _unknown(self, p: dict, r: dict, rid: str, text: str, had_images: bool, notes: list[str]) -> None:
        """앱에 틀이 없는 종류: 그 사실과 가장 가까운 대안을 알린다. 단추를 누르면 같은 글을 입력칸에 채우고, 보낼 때 고른 틀(prefer)을 같이 실어
        그 틀로 다시 읽는다(보내는 것은 사람이 확인한 뒤)."""
        kind, near = r["other_kind"], r.get("nearest") or "none"
        alt = intake.NEAREST.get(near)
        self.touch(p, state="ok", title=f"의뢰 내용을 읽었습니다 — '{kind}' 은(는) 아직 틀이 없습니다")
        self.add("desk", "unknown", what=kind, nearest=near if alt else "none", alt=alt[0] if alt else "", alt_does=alt[1] if alt else "",
                 retry=text.strip() if alt else "", had_images=had_images,
                 notes=[*notes, *[n for n in r.get("notes") or [] if n.startswith("반영하지 못한 요청")]])
        self._unmade(rid, "모르는 종류", kind, r.get("unmade") or [], near)

    # ---- 맞춤 그래픽(메인 에이전트 → 임시 담당) ----
    SCENE_NOTE = 300        # 지시서 카드에서 덧붙이는 말의 길이 한도(자)

    def _scene_plan(self, p: dict, r: dict, rid: str, text: str, images, notes: list[str], read_text: str | None = None) -> None:
        """앱에 틀이 없는 종류: 메인 에이전트가 제작 지시서를 쓰고 이 의뢰를 맡을 임시 담당을 스스로 정의한다(아직 만들지 않는다 — Liner 호출 1~2번).
        지시서 카드를 사람이 보고 '만들기'를 누르면 담당들이 일한다(make → _make_scene). 메인이 지시서를 못 쓰면 예전처럼 '틀 없음' 카드를 낸다."""
        kind = str(r.get("other_kind") or "새 종류")
        # images = 의뢰서 그림(data URL 목록) — 메인 에이전트가 글과 함께 읽는다(손그림 의뢰서의 배치 · 화살표 · 순서는 그림에만 있다). 예전 모양(참 · 거짓)도 받는다
        pics = [u for u in images if isinstance(u, str)] if isinstance(images, list) else []
        had_images = bool(images)
        self.touch(p, title=f"'{kind}' 은(는) 틀이 없어 메인 에이전트가 제작 지시서를 쓰는 중")
        try:
            with intake._liner().request(rid):
                # read_text = 의뢰 글 + 의뢰서 받아쓰기(있으면). 다시 보내기 칸 · 의뢰.txt 에는 원래 글(text)을 남긴다
                got = scene.plan_request(read_text or text, log=lambda s: self._line(p, s.strip()), images=pics)
        except Exception as e:      # 메인을 부르지 못함(연결 · 한도) — 틀 없음 카드로 간다
            got = {"main_ok": False, "problems": [intake.reason(e)]}
        if not got["main_ok"]:
            why = " / ".join(got.get("problems") or [])[:300]
            self._unknown(p, r, rid, text, had_images, [*notes, f"메인 에이전트가 지시서를 쓰지 못했습니다: {why}"])
            return
        near = r.get("nearest") or "none"
        alt = intake.NEAREST.get(near)
        n = self._scene_card(got, rid, text, kind, nearest=near if alt else "none", alt=alt[0] if alt else "", alt_does=alt[1] if alt else "",
                             retry=text.strip() if alt else "", had_images=had_images,
                             notes=[*notes, *(["붙인 의뢰서 그림도 메인 에이전트가 함께 읽었습니다 — 그림에서 읽은 사실(배치 · 화살표 · 순서)은 코드가 대조하지 못하니 아래 '사실'에서 맞는지 봐 주세요."] if pics
                                              else ["붙인 그림은 메인 에이전트가 읽지 않았습니다 — 지시서는 의뢰 글로만 썼습니다."] if had_images else [])])
        self.touch(p, state="ok", title=f"'{kind}' — 메인 에이전트가 지시서를 썼습니다 · 담당 {n['workers']}" + (f" · 못 함 {n['unable']}" if n["unable"] else ""))
        self._unmade(rid, "모르는 종류", kind, r.get("unmade") or [], near)

    def _scene_card(self, got: dict, rid: str, text: str, what: str, **fields) -> dict:
        """메인이 쓴 지시서를 작업 폴더(지시서.json · 의뢰.txt)에 남기고 지시서 카드(kind scene)를 낸다. 돌려주는 것: 담당 · 못 함 수."""
        slug = re.sub(r"[^0-9A-Za-z가-힣]+", "_", what).strip("_")[:20] or "의뢰"
        folder = self.out / f"{time.strftime('%Y%m%d-%H%M%S')}_맞춤_{slug}_{secrets.token_hex(2)}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "의뢰.txt").write_text(text, encoding="utf-8")
        write_atomic(folder / "지시서.json", json.dumps(got, ensure_ascii=False, indent=1))
        workers = [{"wid": f"w{n}", "role": scene._txt(w.get("role")), "goal": scene._txt(w.get("goal")), "fact_ids": list(w.get("fact_ids") or []),
                    "layouts": [x for x in w.get("layouts") or [] if x in scene.LAYOUTS], "checks": [scene._txt(c) for c in w.get("checks") or []]}
                   for n, w in enumerate(got["workers"], 1)]
        unable = [{k: (list(u.get(k) or []) if k == "needed" else scene._txt(u.get(k))) for k in ("route", "existing", "reason", "needed")} for u in got["unable"]]
        research = [{"id": r.get("id"), "question": scene._txt(r.get("question")), "answer": scene._txt(r.get("answer"))[:400],
                     "refs": [{**{k: scene._txt(x.get(k)) for k in ("n", "title", "hostname", "date")}, "url": cg_director._web_url(x.get("url"))}
                              for x in r.get("references") or []]}
                    for r in got.get("research") or []]       # 메인이 Liner 에이전트로 찾은 자료(지시서 카드에 출처와 함께)
        self.add("desk", scene.KIND, plan=folder.name, rid=rid, what=what, brief=got["brief"], workers=workers, unable=unable,
                 research=research, **fields)
        return {"workers": len(workers), "unable": len(unable)}

    def _scene_leftover(self, rid: str, text: str, handled: str, label: str, items: list[str], images=None) -> None:
        """기존 종류(지도 · 정보판)로 읽었지만 그 틀로 못 그린 요청이 남은 의뢰: 확인 카드는 이미 냈고, 남은 요청만 메인 에이전트에게 보인다.
        메인이 본체 위에 따로 얹을 그래픽(하단 띠 · 큰 글자 판 …)으로 만들 담당을 정의하면 지시서 카드를 하나 더 낸다(만드는 것은 사람이 누른 뒤).
        못 만드는 것만 남으면 그 까닭을 진행 표시에 한 줄로 적는다. 확인 카드는 이것과 상관없이 그대로 쓴다.
        text = 의뢰 글 + 의뢰서 받아쓰기, images = 의뢰서 그림 — 글 없이 그림만 온 의뢰도 넘긴다(메인이 그림을 직접 본다).
        '한 화면 지도의 지나는 곳'(예전 기록의 말)은 이제 지도가 그리므로 넘기지 않는다."""
        items = [x for x in items if x and not x.startswith("한 화면 지도의 지나는 곳")]
        pics = [u for u in images or [] if isinstance(u, str) and u.startswith("data:image/")]
        if not items or not self.readers.get("liner") or self.offline or not (text.strip() or pics):
            return
        p = self.add("desk", "progress", title=f"반영 못 한 요청 {len(items)}개 — 메인 에이전트가 따로 얹을 그래픽으로 만들 수 있는지 보는 중",
                     lines=[], state="run", error="")
        try:
            with intake._liner().request(rid):
                got = scene.plan_request(text, log=lambda s: self._line(p, s.strip()), focus=scene.leftover_focus(handled, items), images=pics)
        except Exception as e:
            got = {"main_ok": False, "problems": [intake.reason(e)]}
        if not got["main_ok"]:
            self.touch(p, state="fail", error="남은 요청은 따로 만들지 못했습니다(메인 에이전트: " + " / ".join(got.get("problems") or [])[:200]
                       + ") — 위 확인 카드는 그대로 쓰면 됩니다.")
            return
        if not got["workers"]:
            why = " / ".join(f"{scene._txt(u.get('route'))}: {scene._txt(u.get('reason'))}" for u in got["unable"])[:300]
            self.touch(p, state="ok", title=f"반영 못 한 요청 {len(items)}개 — 메인 에이전트: 지금 재료로는 따로 만들 수 없습니다", lines=[*p["lines"], why])
            return
        n = self._scene_card(got, rid, text, f"{label}의 남은 요청", leftover=items, handled=handled, notes=[])
        self.touch(p, state="ok", title=f"반영 못 한 요청 {len(items)}개 — 메인 에이전트가 지시서를 썼습니다 · 담당 {n['workers']}"
                   + (f" · 못 함 {n['unable']}" if n["unable"] else ""))

    def _scene_request(self, raw: dict, rid: str) -> dict:
        """지시서 카드가 돌려보낸 만들기 요청을 가린다: 지시서 폴더(이 앱이 만든 것만) · 뺄 담당 · 덧붙일 말."""
        name = str(raw.get("plan") or "")
        if not re.fullmatch(r"[0-9A-Za-z가-힣_-]{1,80}", name) or not (self.out / name / "지시서.json").is_file():
            raise DeskError("지시서를 찾지 못했습니다. 의뢰를 다시 보내 주세요.", 404)
        planned = json.loads((self.out / name / "지시서.json").read_text(encoding="utf-8"))
        all_w = [f"w{n}" for n in range(1, len(planned.get("workers") or []) + 1)]
        drop = [w for w in raw.get("drop") or [] if w in all_w]
        if not all_w:
            raise DeskError("이 의뢰에는 띄울 담당이 없습니다 — 카드의 '못 함' 까닭을 봐 주세요.")
        if len(drop) == len(all_w):
            raise DeskError("담당을 하나 이상 남겨 주세요.")
        note = str(raw.get("note") or "").strip()
        if len(note) > self.SCENE_NOTE:
            raise DeskError(f"덧붙일 말은 {self.SCENE_NOTE}자까지 적어 주세요.")
        return {"kind": scene.KIND, "plan": name, "rid": rid, "drop": drop, "note": note, "what": str(raw.get("what") or "")[:40], "planned": planned}

    def _make_scene_once(self, p: dict, req: dict) -> None:
        """_make_scene 을 돌리고, 끝나면(성공 · 실패 · 고장 모두) 그 지시서의 '만드는 중' 표시를 푼다."""
        try:
            self._make_scene(p, req)
        finally:
            with self.lock:
                self.making.discard(req["plan"])

    def _make_scene(self, p: dict, req: dict) -> None:
        """지시서대로 담당들이 도구로 일하고(시안 그리기 · Liner 검수 · 스스로 고치기), 메인이 결과를 보고 다시 시키거나 납품한다.
        담당이 그린 마지막 시안마다 시안 카드(result)를 낸다 — 메인이 납품한 것 먼저. 영상 내보내기는 다른 결과 카드와 같다."""
        work = self.out / req["plan"] / f"제작_{time.strftime('%H%M%S')}_{secrets.token_hex(2)}"
        t0 = time.perf_counter()
        with self.work, intake._liner().request(req["rid"]):
            try:
                out = scene.make_request(req["planned"], work, self.out / ".browser-profile", log=lambda s: self._line(p, s.strip()),
                                         drop=req["drop"], note=req["note"], **({"pictures": self.pictures} if self.pictures else {}))
            except Exception as e:
                self.touch(p, state="fail", error=f"{scene.KIND_LABEL} 제작 실패: {intake.reason(e, typed=False)}")
                return
        seconds = round(time.perf_counter() - t0, 1)
        try:
            write_atomic(work / "결과.json", json.dumps({**out, "seconds": seconds}, ensure_ascii=False, indent=1))
        except OSError:
            pass
        d = out.get("delivery") or {}
        accept = set(d.get("accept") or [])
        drawn = [w for w in out["workers"] if w.get("final_shot")]
        if drawn:
            self.touch(p, state="ok", title=f"{scene.KIND_LABEL} 완성: {req['what'] or '맞춤 의뢰'} · 시안 {len(drawn)}장 · 납품 {len(accept)} · {seconds}초")
        else:       # 전원 고장 · 포기 — '완성'이라고 쓰지 않는다(까닭은 아래 메인 에이전트의 글 카드에)
            self.touch(p, state="fail", error=f"{scene.KIND_LABEL}: 시안을 한 장도 만들지 못했습니다 — 아래 메인 에이전트의 말을 봐 주세요.")
        gave = [f"{w['worker']} {w['role']}: {w['result']} — {w.get('summary') or ''}".strip(" —") for w in out["workers"] if not w.get("final_shot")]
        say = [x for x in [d.get("message") or "", *gave] if x]
        if say:
            self.add("desk", "text", text="메인 에이전트: " + "\n".join(say))
        for w in sorted(drawn, key=lambda w: w["worker"] not in accept):
            job = f"{req['plan']}/{work.name}/{Path(w['final_shot']).parent.as_posix()}"
            try:
                rec = json.loads((self.out / job / "job.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rec = {}
            r = {"kind": scene.KIND, "rid": req["rid"], "title": w["role"], "layout": "full", "scene_layout": w.get("layout") or rec.get("layout") or "",
                 "duration_seconds": rec.get("duration_seconds") or 2.0, "output": {"width": 1920, "height": 1080, "fps": scene.FPS},
                 "moving": bool(rec.get("moving")), "over_video": bool(rec.get("over_video")), "art_style": rec.get("art_style") or request.HOUSE_STYLE}
            info = {k: w.get(k) for k in ("worker", "role", "goal", "result", "summary", "unresolved", "order", "draws", "reviews", "redo", "verdict", "limits")}
            self.add("desk", "result", job=job, request=r, scene={**info, "accepted": w["worker"] in accept, "by": d.get("by") or ""},
                     warnings=[], seconds=seconds, video=None, exporting=False, export_lines=[], export_error="")

    def _scene_restyle_source(self, raw: dict) -> dict:
        """다시 그릴 시안 카드 — 화면이 보낸 경로를 믿지 않고, 지금 대화에 있는 맞춤 그래픽 시안 카드의 작업 폴더만 받는다."""
        job = str(raw.get("restyle_from") or "")
        with self.lock:
            src = next((m for m in self.msgs if m.get("kind") == "result" and m.get("job") == job
                        and (m.get("request") or {}).get("kind") == scene.KIND), None)
        if src is None or not (self.out / job / "scene.json").is_file():
            raise DeskError("다시 그릴 시안을 찾지 못했습니다.", 404)
        return src

    def _restyle_scene(self, p: dict, src: dict, style: str) -> None:
        """같은 장면 설계(scene.json)를 다른 화풍으로 그리기만 한다 — Liner 를 부르지 않는다(글 · 자리 · 움직임은 그대로, 색 · 선 · 재질 · 바탕만 바뀐다).
        새 시안은 원래 시안 옆 폴더에 두고 시안 카드를 하나 더 낸다. 검수 결과는 원래 화풍 시안의 것이라고 카드에 적는다."""
        t0 = time.perf_counter()
        from_dir = self.out / src["job"]
        dest = from_dir.parent / f"{from_dir.name}_{style}_{time.strftime('%H%M%S')}_{secrets.token_hex(2)}"
        with self.work:
            try:
                design = json.loads((from_dir / "scene.json").read_text(encoding="utf-8"))
                scene.install(dest, design, style)
                (dest / "scene.json").write_text(json.dumps(design, ensure_ascii=False, indent=1), encoding="utf-8")
                _, issues, _ = scene.shoot(dest, self.out / ".browser-profile")
            except Exception as e:
                self.touch(p, state="fail", error=f"다시 그리지 못했습니다: {intake.reason(e, typed=False)}")
                return
        seconds = round(time.perf_counter() - t0, 1)
        self.touch(p, state="ok", title=p["title"].replace("다시 그리는 중", "다시 그림"))
        warn = [f"화풍만 바꿔 다시 그렸습니다({request.ART_STYLES[style]}) — 아래 검수 결과는 원래 화풍 시안의 것입니다. 글 · 자리 · 움직임은 같은 장면 설계입니다.",
                *[f"자동 검사: {x}" for x in issues]]
        self.add("desk", "result", job=dest.relative_to(self.out).as_posix(), request={**src["request"], "art_style": style}, scene=dict(src.get("scene") or {}),
                 warnings=warn, seconds=seconds, video=None, exporting=False, export_lines=[], export_error="")

    UNMADE_LOG = "못만든_의뢰.jsonl"

    def _unmade(self, rid: str, what: str, kind: str, items: list[str], nearest: str = "") -> None:
        """못 만든 의뢰(모르는 종류) · 반영 못 한 요청을 data/못만든_의뢰.jsonl 에 한 줄씩 남긴다 — 다음에 만들 틀을 고르는 자료.
        의뢰 글 · 이름 · 전화번호는 적지 않는다(종류 이름과 못 그린 요청만). 기록을 못 써도 의뢰는 그대로 간다."""
        if what != "모르는 종류" and not items:
            return
        line = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "rid": rid, "what": what, "kind": kind, "nearest": nearest, "items": list(items)[:12]}
        try:
            path = self.records / self.UNMADE_LOG
            path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock, path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---- 수정 지시 ----
    def revise(self, raw: dict, text: str, room: str | None = None, sheet: dict | None = None) -> None:
        """결과 카드에 감독이 적은 고칠 점을 받아, 바뀐 곳을 표시한 확인 카드를 다시 낸다(다시 만드는 것은 사람이 확정한 뒤).
        sheet: 같이 붙인 출연진 시트 그림({'name', 'data' data URL}) — 붙이면 글 속의 경로 대신 이 그림에서 얼굴을 고른다."""
        text = (text or "").strip()
        if not text:
            raise DeskError("고칠 점을 적어 주세요.")
        if len(text) > revise.MAX_TEXT:
            raise DeskError(f"고칠 점은 {revise.MAX_TEXT}자까지 적어 주세요.")
        if not isinstance(raw, dict) or not raw:
            raise DeskError("고칠 결과를 찾지 못했습니다.")
        if raw.get("kind") == quiz.KIND:
            raise DeskError("카드 퀴즈는 확인 카드에서 카드 순서와 정답을 직접 수정할 수 있습니다.")
        if raw.get("kind") == scene.KIND:
            raise DeskError("맞춤 그래픽은 아직 고칠 점을 받지 않습니다 — 지시서 카드에 덧붙일 말을 적어 다시 만들어 주세요.")
        raw = dict(raw)
        rid = re.sub(r"[^0-9A-Za-z가-힣_-]", "", str(raw.pop("rid", "") or ""))[:40] or f"수정-{time.strftime('%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        with self.lock:
            self._check_room(room)      # 붙인 그림을 저장하기 전에 한 번(등록할 때 _start 에서 한 번 더 본다)
        sheet_file, files = None, []
        if isinstance(sheet, dict) and sheet.get("data"):
            name = _safe_name(sheet.get("name"))
            try:
                mime, data = _data_url(sheet.get("data"), MAX_FILE)
            except ValueError as e:
                raise DeskError(f"출연진 시트 '{name}' 을(를) 받지 못했습니다: {e}.") from None
            if mime not in ("image/png", "image/jpeg", "image/webp"):
                raise DeskError(f"출연진 시트 '{name}' 은 그림 파일(PNG · JPG · WEBP)이어야 합니다.")
            self.uploads.mkdir(parents=True, exist_ok=True)
            sheet_file = self.uploads / f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(2)}_시트_{name}"
            sheet_file.write_bytes(data)
            files = [{"name": name, "url": f"/uploads/{sheet_file.name}", "thumb": f"/uploads/{sheet_file.name}"}]
        with self.lock:
            self._check_room(room)
            self.add("requester", "text", text=f"수정 지시: {text}", files=files)
            self._start(None, "수정 지시를 읽는 중", self._revise, raw, text, rid, sheet_file)

    def _revise(self, p: dict, raw: dict, text: str, rid: str, sheet_file: Path | None = None) -> None:
        is_board, is_plan = raw.get("kind") == board.KIND, raw.get("kind") == plan.KIND
        why = ("설계도 정보판은 아직 수정 지시를 받지 않아" if is_plan else "오프라인으로 켜서" if self.offline
               else "" if self.readers.get("liner") else "Liner 모델이 없어(.env 의 LINER_API_KEY)")
        r = None
        # 수정 지시에 출연진 시트 그림의 경로가 있으면 얼굴 칸을 먼저 찾아 둔다 — 모델은 '위에 3명 · 밑에 가운데' 같은 말을 칸으로 옮긴다
        sheet, sheet_note = (None, None) if (is_board or is_plan) else self._sheet(text, sheet_file)
        if not why:
            with intake._liner().request(rid):      # 같은 의뢰 번호로 — 호출 기록에서 이 의뢰의 수정 호출이 같이 세어진다
                try:
                    r = revise.revise(raw, text, sheet=sheet, cut=lambda picked: self._cut_sheet(sheet, rid, picked))
                except Exception as e:
                    why = f"Liner 가 수정 지시를 읽지 못해({intake.explain(e)})"
        if r is None:       # 수정 지시를 못 읽어도 지금 의뢰로 카드를 낸다 — 사람이 직접 고쳐서 다시 만들 수 있게
            d = revise._base_draft(raw)
            if is_board:
                d["kind"] = board.KIND
            r = {"board": is_board, "draft": d, "changed": [], "notes": [f"{why} 지금 의뢰 그대로 카드를 냈습니다 — 아래에서 직접 고쳐 주세요."]}
            self.touch(p, state="fail", error=f"{why} 수정 지시를 반영하지 못했습니다.")
        else:
            self.touch(p, state="ok", title=f"수정 지시를 읽었습니다 — {r['summary'][:160]}")
        if sheet_note:
            r["notes"].insert(0, f"확인 필요: {sheet_note}")
        draft = {**r["draft"], "rid": rid}
        if is_plan:     # 설계도 정보판: 지금 설계도 그대로 확인 카드를 낸다(사람이 고쳐서 다시 만든다)
            self.add("desk", "plan", draft={**raw, "rid": rid}, source="liner", notes=r["notes"])
        elif r["board"]:
            self.add("desk", "board", draft=draft, source="liner", notes=r["notes"], changed=r["changed"], revision=text)
        else:
            self.add("desk", "card", draft=draft, missing=[], source="liner", notes=r["notes"], changed=r["changed"], revision=text,
                     faces_preview=self._face_previews(draft.get("faces")),
                     audit={"state": "skipped", "reason": "수정 지시로 고친 카드라 의뢰서와 다시 대조하지 않았습니다"})

    def _sheet(self, text: str, attached: Path | None = None) -> tuple[dict | None, str | None]:
        """출연진 시트와 그 안의 얼굴 칸. (시트, 알림). 붙인 그림이 있으면 그것, 없으면 수정 지시 글 속의 경로(작업 폴더 = 앱 폴더의 위 폴더 안만)."""
        path, why = (attached, None) if attached else facesheet.find(text, self.project.parent)
        if path is None:
            return None, why
        try:
            rows = facesheet.detect(path)
        except Exception as e:
            return None, f"출연진 시트 '{path.name}' 을 읽지 못했습니다({type(e).__name__}: {e})."
        if not rows:
            return None, f"출연진 시트 '{path.name}' 에서 얼굴을 찾지 못했습니다."
        return {"path": path, "name": path.name, "rows": [len(r) for r in rows], "boxes": rows}, None

    def _cut_sheet(self, sheet: dict, rid: str, picked: list[tuple[int, int]]) -> list[str]:
        """시트에서 고른 칸의 얼굴을 오려 자산/얼굴/시트/<의뢰 번호-시각>/01.png ~ 에 넣고 그 경로(앱 폴더 기준)를 돌려준다.
        수정마다 새 폴더 — 앞서 낸 카드 · 결과가 쓰는 얼굴을 덮어쓰지 않는다."""
        name = f"{rid}-{time.strftime('%H%M%S')}-{secrets.token_hex(2)}"
        folder = self.project / intake.SHEET_FACE_DIR / name
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for i, im in enumerate(facesheet.cut_faces(sheet["path"], [sheet["boxes"][r][c] for r, c in picked]), 1):
            im.save(folder / f"{i:02d}.png")
            out.append(f"{intake.SHEET_FACE_DIR}/{name}/{i:02d}.png")
        return out

    def _face_previews(self, faces) -> list[str]:
        """확인 카드에 보일 작은 얼굴 그림(data URL). 시트에서 오린 얼굴일 때만."""
        if not intake.sheet_faces(faces, self.project):
            return []
        from io import BytesIO
        from PIL import Image
        out = []
        for f in faces:
            with Image.open(self.project / f) as src:
                im = src.convert("RGBA")
            im.thumbnail((72, 72))
            buf = BytesIO()
            im.save(buf, "PNG")
            out.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"))
        return out

    # ---- 지도 만들기 ----
    def make(self, raw: dict, room: str | None = None) -> None:
        with self.lock:
            self._check_room(room)      # 예전 대화의 카드면 의뢰를 따지기 전에 막는다(등록할 때 _start 에서 한 번 더 본다)
        raw = dict(raw) if isinstance(raw, dict) else {}
        # 의뢰 번호: 읽을 때 붙인 것이 카드를 거쳐 돌아온다(없으면 새로 붙인다) — 제작 때의 Liner 호출도 같은 번호로 남는다
        rid = re.sub(r"[^0-9A-Za-z가-힣_-]", "", str(raw.pop("rid", "") or ""))[:40] or f"의뢰-{time.strftime('%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        if raw.get("kind") == board.KIND:       # 순위판
            req = board.normalize(raw)          # 고칠 곳이 있으면 RequestError
            req["rid"] = rid
            self._start(room, f"{board.KIND_LABEL} 만드는 중: {req['title']}", self._make_board, req)
            return
        if raw.get("kind") == plan.KIND:        # 설계도 정보판
            req = plan.normalize(raw)
            req["rid"] = rid
            self._start(room, f"{plan.KIND_LABEL} 만드는 중: {req['title']} · {plan.SHAPES[req['shape']]}", self._make_info, req)
            return
        if raw.get("kind") == quiz.KIND:
            req = quiz.normalize(raw)
            req["rid"] = rid
            self._start(room, f"{quiz.KIND_LABEL} 만드는 중: {req['title']}", self._make_info, req)
            return
        if raw.get("kind") == scene.KIND and raw.get("restyle_from"):        # 시안 카드의 '다른 화풍으로 다시 만들기' — 같은 장면 설계를 다른 화풍으로 그리기만
            src = self._scene_restyle_source(raw)
            style = raw.get("art_style")
            if style not in request.ART_STYLES:
                raise request.RequestError([f"화풍(art_style)은 {', '.join(request.ART_STYLES)} 중에서 골라 주세요."])
            self._start(room, f"{scene.KIND_LABEL} 다시 그리는 중: {src['request'].get('title') or '시안'} · {request.ART_STYLES[style]}",
                        self._restyle_scene, src, style)
            return
        if raw.get("kind") == scene.KIND:       # 맞춤 그래픽: 메인이 쓴 지시서대로 담당들이 일한다(몇 분 걸린다)
            if not self.readers.get("liner") or self.offline:
                raise DeskError("맞춤 그래픽은 Liner 가 연결돼야 만듭니다(.env 의 LINER_API_KEY · 오프라인이 아닐 때).")
            req = self._scene_request(raw, rid)
            # 같은 지시서를 만드는 중에 단추를 또 누르면 제작(Liner 호출 · 시안 찍기)이 통째로 한 번 더 돈다 — 끝날 때까지는 받지 않는다
            with self.lock:
                if req["plan"] in self.making:
                    raise DeskError("이 지시서는 지금 만드는 중입니다. 끝난 뒤에 다시 눌러 주세요.", 409)
                self.making.add(req["plan"])
            try:
                self._start(room, f"{scene.KIND_LABEL} 만드는 중: {req['what'] or '맞춤 의뢰'} — 담당들이 시안을 그리고 검수받습니다(몇 분 걸립니다)", self._make_scene_once, req)
            except Exception:
                with self.lock:
                    self.making.discard(req["plan"])
                raise
            return
        try:
            count = int(raw.pop("face_count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        faces = raw.pop("faces", None)      # 출연진 시트에서 오려 둔 얼굴(수정 지시)만 그대로 쓰고, 그 밖에는 기본 얼굴 파일 01~
        raw["faces"] = faces if len(faces or []) == count and intake.sheet_faces(faces, self.project) else intake.face_files(count, self.project)
        # 그림 이동 아이콘도 앱 폴더 안의 파일만 받는다(보관해 둔 Liner 그림 등). 화면이 되돌려 보낸 경로를 그대로 믿으면
        # 컴퓨터의 다른 그림이 작업 폴더로 복사돼 화면에 나오고, 네트워크 공유 경로면 바깥으로 접속이 나간다
        if raw.get("vehicle_image") and not build.inside(str(raw["vehicle_image"]), [self.project]):
            raise DeskError("이동 아이콘 그림은 앱 폴더 안의 파일만 쓸 수 있습니다.")
        if raw.get("map_style") == navi.STYLE:      # 네비 화면: 브라우저 그림이 아니라 navi.py 가 그린다(한 화면만)
            if isinstance(raw.get("cuts"), list) and raw["cuts"]:
                raise request.RequestError(["네비 화면은 아직 컷 나눔을 하지 않습니다 — 컷 나눔을 빼거나 지도 모양을 '경로 이동 지도'로 바꿔 주세요."])
            req = request.normalize(raw)
            req["rid"] = rid
            self._start(room, f"{navi.LABEL} 만드는 중:{req['from']['name']} → {req['to']['name']}", self._make_navi, req)
            return
        if isinstance(raw.get("cuts"), list) and raw["cuts"]:       # 컷 나눔 지도
            spec = cuts.normalize(raw)      # 고칠 곳이 있으면 RequestError
            base = spec["base"]
            title = f"컷 나눔 지도 만드는 중:{base['from']['name']} → {base['to']['name']} · {len(spec['cuts'])}컷"
            self._start(room, title, self._make_cuts, spec, rid)
            return
        req = request.normalize(raw)        # 고칠 곳이 있으면 RequestError
        req["rid"] = rid
        title = f"지도 그래픽 만드는 중:{req['from']['name']} → {req['to']['name']} · {request.LAYOUT_LABELS[req['layout']]}"
        self._start(room, title, self._make, req)

    def _make(self, p: dict, req: dict) -> None:
        # 한 화면 지도도 의뢰 번호 안에서 만든다 — 이동 아이콘 그림(Liner 그림 모델) · 지명 검색 호출이 그 의뢰의 호출 기록에 묶인다
        # 지도에 없는 지명은 컷 나눔 지도처럼 Liner 웹 검색으로 정식 이름을 확인해 다시 찾는다(오프라인이면 검색하지 않는다)
        search = intake.searched_name if self.readers.get("liner") and not self.offline else None
        with self.work, intake._liner().request(req.get("rid") or f"제작-{time.strftime('%m%d-%H%M%S')}"):
            try:
                try:
                    job = build.make_job(req, project_dir=self.project, out_root=self.out, offline=self.offline,
                                         log=lambda s: self._line(p, s), searcher=search)
                except FileExistsError:     # 같은 초에 같은 이름으로 또 만들 때
                    time.sleep(1.1)
                    job = build.make_job(req, project_dir=self.project, out_root=self.out, offline=self.offline,
                                         log=lambda s: None, searcher=search)
            except GeoError as e:
                self.touch(p, state="fail", error=f"지도 데이터를 받지 못했습니다: {e}")
                return
            except Exception as e:
                self.touch(p, state="fail", error=f"지도를 만들지 못했습니다: {intake.reason(e)}")
                return
        self.touch(p, state="ok", title=p["title"].replace("만드는 중", "완성"))
        places = {k: {x: v[x] for x in ("lat", "lon", "display")} for k, v in job["places"].items()}
        self.add("desk", "result", job=job["dir"].name, request=req, places=places, route=job["route"],
                 warnings=job["warnings"], seconds=job["timing"]["build_seconds"],
                 video=None, exporting=False, export_lines=[], export_error="")

    def _make_navi(self, p: dict, req: dict) -> None:
        search = intake.searched_name if self.readers.get("liner") and not self.offline else None     # 한 화면 지도와 같다
        with self.work, intake._liner().request(req.get("rid") or f"제작-{time.strftime('%m%d-%H%M%S')}"):
            try:
                try:
                    job = navi.make_job(req, project_dir=self.project, out_root=self.out, offline=self.offline, log=lambda s: self._line(p, s),
                                        searcher=search)
                except FileExistsError:     # 같은 초에 같은 이름으로 또 만들 때
                    time.sleep(1.1)
                    job = navi.make_job(req, project_dir=self.project, out_root=self.out, offline=self.offline, log=lambda s: None,
                                        searcher=search)
            except GeoError as e:
                self.touch(p, state="fail", error=f"지도 데이터를 받지 못했습니다: {e}")
                return
            except Exception as e:
                self.touch(p, state="fail", error=f"{navi.LABEL}을 만들지 못했습니다: {intake.reason(e)}")
                return
        self.touch(p, state="ok", title=p["title"].replace("만드는 중", "완성"))
        places = {k: {x: v[x] for x in ("lat", "lon", "display")} for k, v in job["places"].items()}
        self.add("desk", "result", job=job["dir"].name, request=req, places=places, route=job["route"],
                 warnings=job["warnings"], seconds=job["timing"]["build_seconds"],
                 video=None, exporting=False, export_lines=[], export_error="")

    def _make_cuts(self, p: dict, spec: dict, rid: str = "") -> None:
        ask = self.readers.get("liner") and not self.offline       # 오프라인이면 키가 있어도 Liner 에 지명을 묻거나 검색하지 않는다
        with self.work, intake._liner().request(rid or f"제작-{time.strftime('%m%d-%H%M%S')}"):
            try:
                job = cuts.make(spec, project_dir=self.project, out_root=self.out, offline=self.offline, log=lambda s: self._line(p, s),
                                namer=intake.official_names if ask else None,
                                searcher=intake.searched_name if ask else None,
                                picker=intake.picked_place if ask else None)
            except GeoError as e:
                self.touch(p, state="fail", error=f"지도 데이터를 받지 못했습니다: {e}")
                return
            except request.RequestError as e:
                self.touch(p, state="fail", error=f"컷을 나누지 못했습니다: {e}")
                return
            except Exception as e:
                self.touch(p, state="fail", error=f"지도를 만들지 못했습니다: {intake.reason(e)}")
                return
        self.touch(p, state="ok", title=p["title"].replace("만드는 중", "완성"))
        places = {k: {x: v[x] for x in ("lat", "lon", "display")} for k, v in job["places"].items()}
        # 결과 카드의 의뢰는 '다시 만들기'가 그대로 다시 낸다 — 이번에 확정한 좌표·경유지·컷 구간·핀 자리까지 든 봉투(cuts.fixed_request)를 남긴다.
        # 화풍·배치만 바꿔 다시 내면 지명을 다시 읽거나 찾지 않고 같은 도로선·경계·핀으로 그린다
        state = [{"n": n, "source": c["map"].get("source", ""), "detail": c["map"].get("detail", ""), "roads": c["map"]["counts"].get("roads", 0),
                  "approx": c.get("approx", [])} for n, c in enumerate(job["cuts"], 1)]
        # 작업 기록의 의뢰는 전화번호를 가려 두었다 — 카드에는 제작진이 보낸 그대로(이름 · 전화번호 · 의뢰 글)를 싣는다
        mine = {k: spec[k] for k in ("requester", "text") if k in spec}
        self.add("desk", "result", job=job["dir"].name, request={**job["request"], **mine, "rid": rid}, places=places, cuts=state,
                 route=job["route"], warnings=job["warnings"], seconds=job["timing"]["build_seconds"],
                 video=None, exporting=False, export_lines=[], export_error="")

    def _make_board(self, p: dict, req: dict) -> None:
        with self.work:
            try:
                job = board.make_job(req, project_dir=self.project, out_root=self.out, log=lambda s: self._line(p, s))
            except Exception as e:
                self.touch(p, state="fail", error=f"{board.KIND_LABEL}을 만들지 못했습니다: {intake.reason(e)}")
                return
        self.touch(p, state="ok", title=p["title"].replace("만드는 중", "완성"))
        self.add("desk", "result", job=job["dir"].name, request=req, rows=job["board"]["rows"],
                 warnings=job["warnings"], seconds=job["timing"]["build_seconds"],
                 video=None, exporting=False, export_lines=[], export_error="")

    def _make_info(self, p: dict, req: dict) -> None:
        is_quiz = req["kind"] == quiz.KIND
        label = quiz.KIND_LABEL if is_quiz else plan.KIND_LABEL
        log = lambda line: self._line(p, line)
        with self.work:
            try:
                if is_quiz:
                    with intake._liner().request(req["rid"]):
                        job = quiz_build.make_job(req, project_dir=self.project, out_root=self.out, log=log, offline=self.offline)
                else:
                    job = plan.make_job(req, project_dir=self.project, out_root=self.out, log=log)
            except Exception as e:
                self.touch(p, state="fail", error=f"{label} 제작 실패: {intake.reason(e, typed=False)}")
                return
        self.touch(p, state="ok", title=p["title"].replace("만드는 중", "완성"))
        self.add("desk", "result", job=job["dir"].name, request=req, warnings=job["warnings"], seconds=job["timing"]["build_seconds"],
                 video=None, exporting=False, export_lines=[], export_error="")

    # ---- 영상 내보내기 ----
    def export(self, msg_id: int, alpha: bool, room: str | None = None) -> None:
        with self.lock:     # 방 대조 · 대상 찾기 · '내보내는 중' 표시 · busy 예약을 한 임계구역에서
            self._check_room(room)      # 새 대화에서도 번호가 1부터 다시 붙는다 — 예전 화면의 번호가 새 대화의 다른 지도를 가리키지 않게
            m = next((x for x in self.msgs if x["id"] == msg_id and x["kind"] == "result"), None)
            if m is None:
                raise DeskError("그 지도를 찾지 못했습니다.", 404)
            if m["exporting"]:
                raise DeskError("이미 내보내는 중입니다.", 409)
            job_dir = self.out / m["job"]
            if not (job_dir / "index.html").is_file():
                raise DeskError("작업 폴더가 없습니다. 지도를 다시 만들어 주세요.", 404)
            self.touch(m, exporting=True, export_lines=[], export_error="", proof=None, proofing=False)      # 다시 내보내면 앞 검수 결과는 지운다

            def fail(why: str) -> None:
                if m.get("exporting"):
                    self.touch(m, exporting=False, export_error=f"영상을 만들지 못했습니다: {why}")
            self._spawn(self._export, m, job_dir, alpha, fail=fail)

    def _export(self, m: dict, job_dir: Path, alpha: bool) -> None:
        req = m["request"]
        with self.work:
            try:
                size = request.panel_rect(req["layout"], req["output"]["width"], req["output"]["height"])[2:]
                log = lambda s: self.touch(m, export_lines=[*m["export_lines"], str(s)])      # noqa: E731
                kind = req.get("kind")
                if kind == cuts.KIND:
                    r = cuts.export_all(cuts.load(job_dir), req["output"]["fps"], size, alpha=alpha, log=log)
                elif req.get("map_style") == navi.STYLE:
                    r = navi.export(job_dir, log=log)
                else:
                    names = {board.KIND: board.KIND_LABEL, plan.KIND: plan.KIND_LABEL, quiz.KIND: quiz.KIND_LABEL, scene.KIND: scene.KIND_LABEL}
                    r = export.export(job_dir, req["duration_seconds"], req["output"]["fps"], alpha=alpha,
                                      size=size, name=names.get(kind, "지도"), log=log, keep_panel_frames=kind in (board.KIND, plan.KIND))
            except Exception as e:
                self.touch(m, exporting=False, export_error=f"영상을 만들지 못했습니다: {intake.reason(e, typed=False)}")
                return
        spec = r.get("spec")      # 만든 파일을 실제로 재서 의뢰 규격과 견준 결과
        self.touch(m, exporting=False, video={
            "url": f"/jobs/{m['job']}/{r['mp4'].name}", "path": str(r["mp4"]), "frames": r["frames"],
            "alpha": str(r["alpha_dir"]) if "alpha_dir" in r else "", "seconds": r.get("seconds"),
            "spec": export.spec_line(spec) if spec else "", "spec_state": spec["state"] if spec else ""})
        self._proof(m, job_dir)

    PROOF_SHOW = 12     # 결과 카드에 싣는 '다른 곳'의 수 한도(나머지는 화면검수.json 에 있다)

    def _proof(self, m: dict, job_dir: Path) -> None:
        """내보낸 완성 화면을 Liner 가 읽고 의뢰 값과 코드가 대조한다(호출 1번 · 의뢰와 같은 번호로 센다).
        영상은 이미 납품됐다 — 검수가 안 되거나 고장 나도 영상 카드는 그대로 두고 결과만 '같음 / 다른 곳 있음 / 검수 못 함 / 건너뜀'으로 싣는다."""
        if not self.readers.get("liner"):
            r = {"state": "건너뜀", "note": "Liner 가 연결되지 않아 화면 검수를 하지 않았습니다."}
        else:
            self.touch(m, proofing=True)
            rid = str(m["request"].get("rid") or "") or f"검수-{time.strftime('%m%d-%H%M%S')}"
            try:
                with intake._liner().request(rid):
                    r = proof.check(job_dir, offline=self.offline)
            except Exception as e:      # check 는 예외를 내지 않는다 — 그래도 여기서 멈추면 '검수 중'으로 남지 않게
                r = {"state": "검수 못 함", "note": f"화면 검수가 멈췄습니다({type(e).__name__})."}
        problems = list(r.get("problems") or [])
        self.touch(m, proofing=False, proof={
            "state": r.get("state") or "검수 못 함", "note": str(r.get("note") or "")[:200],
            "problems": problems[:self.PROOF_SHOW], "more": max(0, len(problems) - self.PROOF_SHOW),
            "opinions": list(r.get("opinions") or [])[:8], "notes": list(r.get("notes") or [])[:4],
            "shots": [str(s.get("label") or "") for s in r.get("shots") or []], "model": str(r.get("model") or ""), "seconds": r.get("seconds")})
        line = self._liner_line(str(m["request"].get("rid") or ""))     # 화면 검수 호출까지 더한 Liner 한 줄
        if line and line != m.get("liner"):
            self.touch(m, liner=line)

    # ---- 지점 고르기 창 ----
    def area(self, name: str, lat=None, lon=None, span=2400, pick=None, near: str = "") -> dict:
        """이름(또는 좌표) 둘레의 지도를 지점 고르기 화면(pick.html)용으로 준비한다. pick = 이미 고른 지점 {lat, lon}.
        near = 그 이름을 지도에서 못 찾을 때 대신 가운데로 삼을 장소(각자 이동의 '여의도 공원' — 공원 안의 지도에 없는 곳을 찍을 때)."""
        name = (name or "").strip()
        span = span if span in SPANS else 2400
        geo = Geo(self.project / "data" / "geo", offline=self.offline)
        cands: list[dict] = []
        if name:
            try:
                cands = geo.geocode(name)["candidates"]
            except GeoError:
                if lat is None and not near.strip():
                    raise
        if (lat is None or lon is None) and not cands and near.strip():
            for t in dict.fromkeys((near.strip(), re.sub(r"\s+", "", near))):      # '여의도 공원'은 없고 '여의도공원'은 있다
                try:
                    hit = geo.geocode(t)
                    lat, lon = hit["lat"], hit["lon"]
                    break
                except GeoError:
                    continue
        if lat is None or lon is None:
            if not cands:
                raise DeskError("지명을 먼저 적어 주세요.")
            lat, lon = cands[0]["lat"], cands[0]["lon"]
        lat, lon = float(lat), float(lon)
        w, h = float(span), span * 9 / 16
        plan = mapdata.plan_area(lat, lon, w, h)
        to_xy = plan["to_xy"]
        ppm = 960 / w
        osm = geo.osm(mapdata.view_bbox(plan), "foot" if ppm >= 0.6 else "minor" if ppm >= 0.25 else "major")      # 좁게 보면 산책로까지(공원 안을 찍을 때)
        data = mapdata.build(osm, plan, "", "", ppm)
        mark = None
        if isinstance(pick, dict):      # 이미 고른 지점은 범위를 바꿔도 제자리에 다시 표시한다
            try:
                mark = [round(v, 1) for v in to_xy(float(pick["lon"]), float(pick["lat"]))]
            except (KeyError, TypeError, ValueError):
                mark = None
        marks = []
        for i, c in enumerate(cands, 1):
            x, y = to_xy(c["lon"], c["lat"])
            c["short"] = ", ".join(s.strip() for s in c["display"].split(",")[:3])
            if abs(x) < w / 2 and abs(y) < h / 2:
                marks.append({"p": [round(x), round(y)], "n": i})
        info = {"lat0": lat, "lon0": lon, "kx": plan["kx"], "ky": plan["ky"], "candidates": marks, "mark": mark}
        token = secrets.token_hex(6)
        with self.lock:
            self.picks[token] = {"pick.js": build._js("PICK", info), "map-data.js": build._js("MAPDATA", data)}
            for old in list(self.picks)[:-8]:
                del self.picks[old]
        return {"url": f"/pick/{token}/index.html", "center": {"lat": lat, "lon": lon}, "span": span,
                "candidates": [{"lat": c["lat"], "lon": c["lon"], "short": c["short"]} for c in cands]}


# ---------- HTTP ----------
def make_handler(desk: Desk):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):       # 조용히
            pass

        def _guard(self) -> None:
            """다른 사이트가 이 화면을 제 페이지 안에 끼워 넣지 못하게(겹쳐 놓고 누르게 하는 속임수). 미리보기 · 지점 고르기는 같은 출처라 된다."""
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
            self.send_header("X-Content-Type-Options", "nosniff")

        def _send(self, status: int, ctype: str, body: bytes, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._guard()
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, TYPES[".json"], json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _file(self, path: Path, root: Path) -> None:
            try:
                path = path.resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    raise FileNotFoundError
                size = path.stat().st_size
            except (OSError, ValueError):
                return self._json({"error": "없는 파일입니다."}, 404)
            start, end, status = 0, size - 1, 200
            m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)) and size:     # 영상 넘겨 보기용 부분 전송
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                if start > end:
                    return self._send(416, "text/plain", b"", {"Content-Range": f"bytes */{size}"})
                status = 206
            ctype = TYPES.get(path.suffix.lower(), "application/octet-stream")
            if root == desk.uploads and path.suffix.lower() in (".svg", ".html", ".js", ".mjs"):
                ctype = "text/plain; charset=utf-8"     # 받은 첨부는 이 화면의 권한으로 실행되지 않게 글로만 보여 준다
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self._guard()
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with path.open("rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except OSError:
                pass

        def _local(self) -> bool:
            """이 컴퓨터의 이 화면에서 온 요청만 받는다(다른 사이트가 몰래 보내는 요청 차단).

            GET 은 브라우저가 Origin 을 붙이지 않으므로 Sec-Fetch-Site 도 본다: 다른 사이트(같은 컴퓨터의 다른 포트 포함)의
            그림 · 스크립트 · 끼워 넣기로 온 요청은 막고, 이 화면 안(same-origin)과 주소창 · 즐겨찾기(none)만 받는다.
            이 머리말을 안 보내는 프로그램(시험 · 명령줄)은 Host · Origin 검사만 거친다."""
            host = self.headers.get("Host", "")
            if host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
                return False
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site not in ("same-origin", "none"):
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlsplit(origin).netloc == host

        def do_GET(self):
            if not self._local():
                return self._json({"error": "허용되지 않은 요청입니다."}, 403)
            u = urlsplit(self.path)
            path = unquote(u.path)
            if path in ("/", "/index.html"):
                return self._file(WEB / "index.html", WEB)
            if path.startswith("/web/"):
                return self._file(WEB / path[5:], WEB)
            if path == "/api/state":
                q = parse_qs(u.query)
                try:
                    rev = int(q.get("rev", ["-1"])[0])
                except ValueError:
                    rev = -1
                return self._json(desk.state(rev, q.get("room", [""])[0]))
            if path.startswith("/jobs/"):
                return self._file(desk.out / path[6:], desk.out)
            if path.startswith("/uploads/"):
                return self._file(desk.uploads / path[9:], desk.uploads)
            m = re.match(r"/pick/([0-9a-f]+)/(index\.html|pick\.js|map-data\.js|" + "|".join(map(re.escape, build.SHARED)) + r")$", path)
            if m and m.group(1) in desk.picks:
                if m.group(2) == "index.html" or m.group(2) in build.SHARED:      # 고정 파일(화면 틀과 그리기 코드)
                    name = "pick.html" if m.group(2) == "index.html" else m.group(2)
                    return self._file(build.TEMPLATES / name, build.TEMPLATES)
                return self._send(200, TYPES[".js"], desk.picks[m.group(1)][m.group(2)].encode("utf-8"))
            return self._json({"error": "없는 주소입니다."}, 404)

        def do_POST(self):
            # 출처와 길이는 본문을 읽기 전에 본다. 읽은 뒤에 보면 다른 사이트가 보낸 큰 본문을 다 받아 놓고서야 거절하게 된다
            if not self._local():
                self.close_connection = True
                return self._json({"error": "허용되지 않은 요청입니다."}, 403)
            try:
                n = int(self.headers.get("Content-Length", 0))
            except ValueError:
                n = -1
            if n < 0:       # 길이가 숫자가 아니거나 음수 — 음수를 그대로 읽으면 연결이 끊길 때까지 끝없이 읽는다
                self.close_connection = True
                return self._json({"error": "요청을 읽지 못했습니다."}, 400)
            if n > MAX_BODY:
                self.close_connection = True
                return self._json({"error": "보낸 내용이 너무 큽니다."}, 413)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json({"error": "요청을 읽지 못했습니다."}, 400)
            path = urlsplit(self.path).path
            try:
                # 대화에 붙는 요청은 화면이 보고 있던 대화(room)를 함께 보낸다 — 없거나 다르면 409(예전 화면의 요청이 새 대화를 바꾸지 않게)
                room = str(body.get("room") or "")
                if path == "/api/send":
                    desk.send(str(body.get("text") or ""), body.get("files") or [], room=room, prefer=str(body.get("as") or ""))
                elif path == "/api/make":
                    desk.make(body.get("request") or {}, room=room)
                elif path == "/api/revise":
                    desk.revise(body.get("request") or {}, str(body.get("text") or ""), room=room, sheet=body.get("sheet"))
                elif path == "/api/export":
                    desk.export(int(body.get("id") or 0), bool(body.get("alpha")), room=room)
                elif path == "/api/area":
                    return self._json(desk.area(str(body.get("name") or ""), body.get("lat"), body.get("lon"),
                                                body.get("span"), body.get("pick"), str(body.get("near") or "")))
                elif path == "/api/reset":
                    desk.reset(room=room)
                else:
                    return self._json({"error": "없는 주소입니다."}, 404)
            except request.RequestError as e:
                return self._json({"error": "의뢰 내용에 고칠 곳이 있습니다.", "problems": e.problems}, 400)
            except DeskError as e:
                return self._json({"error": str(e)}, e.status)
            except GeoError as e:
                return self._json({"error": f"지도 데이터를 받지 못했습니다: {e}"}, 502)
            except (TypeError, ValueError) as e:
                return self._json({"error": f"요청 값이 맞지 않습니다: {e}"}, 400)
            except (AttributeError, KeyError, IndexError, OverflowError) as e:
                # 모양이 다른 값(이름 묶음 자리에 글자, 크기에 무한대 등) — 답 없이 연결이 끊기지 않게 알려 준다.
                # 앱 안의 고장도 같은 오류로 올 수 있다 — 입력 오류처럼 묻히지 않게 어디서 났는지 실행 창에 남긴다
                print(f"[{time.strftime('%H:%M:%S')}] {path} 처리 중 {type(e).__name__} — 요청 값이 이상하거나 앱 안의 고장입니다:", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                return self._json({"error": f"요청 값의 모양이 맞지 않습니다({type(e).__name__}) — 실행 창에 자세히 남겼습니다."}, 400)
            except Exception as e:      # 디스크 · 파일 쓰기 등 예상 못 한 고장 — 답 없이 연결이 끊기면 화면에는 까닭이 보이지 않는다
                print(f"[{time.strftime('%H:%M:%S')}] {path} 처리 중 {type(e).__name__}:", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                return self._json({"error": f"앱 안에서 고장이 났습니다({type(e).__name__}) — 실행 창에 자세히 남겼습니다."}, 500)
            return self._json({"ok": True})

    return Handler


def make_server(desk: Desk, port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(desk))
    server.daemon_threads = True
    return server


def director_on(env_path=None) -> bool:
    """CG Director 를 붙일지. .env 를 먼저 읽는다 — 키를 확인할 때까지 기다리면 .env 의 MAPCG_FRONT 가 아직 환경 변수에 없다."""
    intake._liner().load_env(env_path)
    return os.environ.get("MAPCG_FRONT", "").strip().lower() not in ("off", "0", "no", "false")


def store_of(project_dir: Path) -> Path:
    """대화를 저장하는 폴더 — Desk 가 쓰는 곳과 같다(MAPCG_ROOM 을 주면 그 아래)."""
    room = os.environ.get("MAPCG_ROOM", "").strip()
    return Path(room) / "messenger" if room else Path(project_dir) / "data" / "messenger"


def hold_store(store: Path):
    """대화 저장소를 한 번에 한 앱만 쓰게 잠근다. 돌려주는 것: (잠금 파일, '') 또는 (None, 먼저 연 앱의 주소 — 모르면 '').

    앱은 켤 때 대화를 한 번 읽고 그 뒤로는 메모리의 대화 전체를 같은 파일에 덮어쓴다. 같은 저장소를 두 앱이 열면(포트만 달리해서)
    나중에 쓴 쪽이 먼저 받은 대화를 지운다 — 그래서 두 번째 앱은 켜지 않는다.
    잠금은 운영체제가 쥔다: 앱이 비정상으로 꺼져도 풀리므로 다음 실행을 막지 않는다. 돌려받은 파일은 앱이 도는 동안 닫지 않는다."""
    store.mkdir(parents=True, exist_ok=True)
    f = open(store / "앱.lock", "a+")
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        try:
            other = (store / "앱주소.txt").read_text(encoding="utf-8").strip()
        except OSError:
            other = ""
        return None, other
    return f, ""


def serve(project_dir: Path, *, port: int = 8765, offline: bool = False, open_browser: bool = True) -> int:
    # 대화를 읽기 전에 저장소부터 잡는다 — 같은 대화방을 연 앱이 이미 있으면 여기서 멈춘다(두 앱이 서로의 대화를 덮어쓰지 않게)
    store = store_of(project_dir)
    held, other = hold_store(store)
    if held is None:
        print("이 대화방은 이미 다른 앱에서 열려 있습니다" + (f": {other}" if other else "") + ".")
        print("그 주소를 브라우저로 열어 쓰거나, 먼저 연 앱을 끈 뒤 다시 실행해 주세요(같은 대화방을 두 앱이 같이 쓰면 먼저 받은 대화가 지워집니다).")
        return 6
    try:
        return _serve(project_dir, store, port=port, offline=offline, open_browser=open_browser)
    finally:
        held.close()


def _serve(project_dir: Path, store: Path, *, port: int, offline: bool, open_browser: bool) -> int:
    # CG Director 는 앱으로 켤 때만 붙인다(.env 에 MAPCG_FRONT=off 를 적으면 예전 순서 — 순위판 판별 → 지도 읽기). 시험에서 만드는 Desk 에는 붙지 않는다
    front = director_on()
    # 의뢰서 받아쓰기도 앱으로 켤 때만 붙인다(.env 에 MAPCG_SHEET=off 를 적으면 그림만으로 읽는 예전 방식)
    sheet = os.environ.get("MAPCG_SHEET", "").strip().lower() not in ("off", "0", "no", "false")
    # 맞춤 그래픽의 항목 그림도 앱으로 켤 때만 붙인다(.env 에 MAPCG_SCENE_PICS=off 를 적으면 그림 없이 — 그림 한 장에 약 0.014달러 · 10초쯤)
    pics = os.environ.get("MAPCG_SCENE_PICS", "").strip().lower() not in ("off", "0", "no", "false")
    desk = Desk(project_dir, offline=offline, director=cg_director.run if front else None, transcriber=transcribe.run if sheet else None,
                pictures=icons.for_labels if pics and not offline else None)
    try:
        server = make_server(desk, port)
    except OSError as e:
        print(f"{port}번 포트를 열지 못했습니다({e}). 다른 프로그램이 쓰는 번호입니다 — --port 로 다른 번호를 주세요.")
        return 5
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        (store / "앱주소.txt").write_text(url, encoding="utf-8")      # 같은 대화방을 또 열려는 앱에 알려 줄 주소
    except OSError:
        pass
    print(f"방송그래픽 의뢰: {url}")
    print("Liner 모델: " + ("연결됨" if desk.readers["liner"] else "키 없음(.env 에 LINER_API_KEY)"))
    if desk.readers["liner"] and not offline:
        print(f"접수: {cg_director.LABEL} " + ("— 먼저 읽고 실무 확인 · Liner 검색 뒤 일을 나눕니다" if desk.director else "꺼짐(MAPCG_FRONT=off)"))
        print("의뢰서 받아쓰기: " + ("그림이 오면 먼저 글씨를 받아 적어 함께 읽힙니다" if desk.transcriber else "꺼짐(MAPCG_SHEET=off)"))
    if desk.readers["claude"]:
        print("Claude Opus(중간): claude 명령줄 도구 있음(보조)")
    order = intake.route(desk.readers)
    print("의뢰서 읽는 순서: " + " → ".join([intake.READER_LABELS[r] for r in order] + ["간이 해석(글만)"]))
    print("끄려면 Ctrl+C")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
