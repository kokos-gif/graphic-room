"""내려받아 둔 OpenStreetMap 자료(지역 하나의 SQLite)에서 지도 도형 · 건물 · 시설을 꺼낸다 — 공개 지도 서버(Overpass)를 부르지 않는다.

공개 Overpass 서버는 자주 꽉 찬다. 2026-11-22 실측: 4곳이 모두 실패했다("at capacity" · 무응답 · 504).
그래서 의뢰 하나가 지도 받기에만 133초를 쓰고, 결국 큰길만 있는 묶음으로 그려졌다.
tools/osm_local.py 가 Geofabrik 의 국가 자료(.osm.pbf)에서 한 지역을 뽑아 data/osm/<지역>.sqlite 로 만든다(그때만 pyosmium 을 쓴다).
이 파일은 표준 라이브러리(sqlite3 · R-tree)만 쓴다. 꺼내는 모양은 Overpass 응답('out geom')과 같아서 지도 그리기 코드는 그대로 쓴다.

층(layer): base(도로 · 철도 · 물 · 공원 · 숲 · 동네 이름 · 역) · building(건물) · feature(시설 — geo.features 와 같은 모양)
rank: base 의 도로 등급 — 0 큰길(major) · 1 골목길(minor) · 2 걷는 길(foot). 도로가 아닌 것은 0(어느 자세함에도 나온다).
자료: © OpenStreetMap contributors (ODbL).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from urllib.parse import quote

RANK = {"major": 0, "minor": 1, "foot": 2}
SCHEMA = 1
# 지도 그리기가 읽지 않고 크기만 키우는 태그 — 다른 나라 말 이름 · 출처 · 메모. 이름은 name · name:ko · name:en 만 남긴다
_DROP_PREFIX = ("name:", "alt_name", "old_name", "official_name", "short_name", "int_name", "loc_name", "source", "note", "fixme", "FIXME",
                "description", "wikipedia", "wikidata", "wikimedia", "mapillary", "check_date", "created_by", "image", "website", "url")
_KEEP = {"name:ko", "name:en"}


def slim_tags(tags: dict) -> dict:
    return {k: v for k, v in tags.items() if k in _KEEP or not k.startswith(_DROP_PREFIX)}


class Writer:
    """지역 자료 파일 만들기(tools/osm_local.py · 시험). 다 쓴 뒤 close() 에서 제자리로 옮긴다 — 쓰다 끊기면 있던 파일은 그대로다."""

    def __init__(self, path: Path, bbox, source: str, made: str):
        self.path = Path(path)
        self.tmp = self.path.with_name(self.path.name + ".만드는중")
        if self.tmp.exists():
            os.replace(self.tmp, self.path.with_name(self.path.name + ".지난시도"))      # 지우지 않고 옆으로(다음 시도가 덮어 쓴다)
        self.db = sqlite3.connect(self.tmp)
        self.db.executescript("""
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE el(id INTEGER PRIMARY KEY, layer TEXT NOT NULL, rank INTEGER NOT NULL, body TEXT NOT NULL);
            CREATE VIRTUAL TABLE idx USING rtree(id, minlat, maxlat, minlon, maxlon);
        """)
        meta = {"schema": SCHEMA, "bbox": [float(v) for v in bbox], "source": source, "made": made,
                "attribution": "© OpenStreetMap contributors (ODbL)"}
        self.db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
        self.n = 0

    def add(self, layer: str, rank: int, body: dict, bounds) -> None:
        """bounds = (최소 위도, 최소 경도, 최대 위도, 최대 경도)."""
        self.n += 1
        self.db.execute("INSERT INTO el VALUES (?, ?, ?, ?)", (self.n, layer, int(rank), json.dumps(body, ensure_ascii=False, separators=(",", ":"))))
        s, w, n, e = bounds
        self.db.execute("INSERT INTO idx VALUES (?, ?, ?, ?, ?)", (self.n, s, n, w, e))

    def close(self) -> Path:
        self.db.commit()
        self.db.close()
        try:
            os.replace(self.tmp, self.path)
        except PermissionError:
            raise PermissionError(f"{self.path.name} 을(를) 앱이 쓰고 있어 바꿔 넣지 못했습니다 — 앱(서버)을 끄고 다시 만들어 주세요. "
                                  f"새로 만든 것은 {self.tmp.name} 에 있습니다.") from None
        return self.path

    def abort(self) -> None:
        """만들다 실패했다 — 연결만 닫는다(있던 지역 자료는 그대로, 만들던 것은 '.만드는중' 으로 남아 다음 시도가 옆으로 치운다)."""
        try:
            self.db.close()
        except sqlite3.Error:
            pass


class Local:
    """지역 자료 파일 하나. 여러 스레드(서버의 의뢰들)가 같이 쓰므로 읽기마다 잠근다."""

    def __init__(self, path: Path):
        self.path = Path(path)
        # 읽기 전용 URI 로 연다 — 경로의 빈칸 · '#' · '%' · '?' · 한글은 퍼센트로 바꿔야 다른 파일로 읽히지 않는다
        self.db = sqlite3.connect(f"file:{quote(self.path.resolve().as_posix(), safe='/:')}?mode=ro", uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        try:
            meta = {k: json.loads(v) for k, v in self.db.execute("SELECT key, value FROM meta")}
            if meta.get("schema") != SCHEMA:
                raise ValueError(f"지역 지도 자료의 형식이 다릅니다({meta.get('schema')} ≠ {SCHEMA}): {self.path.name}")
            bbox = meta["bbox"]
        except BaseException:
            self.db.close()     # 못 쓰는 파일 — 연결을 남기면 윈도우에서 그 파일이 잠긴 채로 남는다
            raise
        self.bbox = tuple(bbox)
        self.made, self.source = meta.get("made", ""), meta.get("source", "")

    def covers(self, bbox) -> bool:
        s, w, n, e = bbox
        bs, bw, bn, be = self.bbox
        return bs <= s and bw <= w and bn >= n and be >= e

    def _rows(self, layer: str, bbox, rank: int = 0) -> list:
        s, w, n, e = bbox
        with self.lock:
            return [json.loads(b) for (b,) in self.db.execute(
                "SELECT el.body FROM idx JOIN el ON el.id = idx.id "
                "WHERE idx.maxlat >= ? AND idx.minlat <= ? AND idx.maxlon >= ? AND idx.minlon <= ? AND el.layer = ? AND el.rank <= ? "
                "ORDER BY el.id", (s, n, w, e, layer, rank))]

    def osm(self, bbox, detail: str = "major") -> dict:
        """geo.Geo.osm 과 같은 것(도로는 detail 까지) — Overpass 응답 모양 {'elements': [...]}."""
        return {"elements": self._rows("base", bbox, RANK.get(detail, 0))}

    def buildings(self, bbox) -> dict:
        return {"elements": self._rows("building", bbox)}

    def features(self, bbox) -> list[dict]:
        """geo.Geo.features 와 같은 모양: [{'name', 'tags', 'lat', 'lon', ('bounds'), ('ring')}]."""
        return self._rows("feature", bbox)


_opened: dict[tuple, Local | None] = {}      # (경로, 고친 시각, 크기) → 연 것. 파일을 다시 만들면 새로 연다
_open_lock = threading.Lock()
# 앱에 같이 싣는 시연 지역 묶음(tools/osm_demo_pack.py — 영상 · README 예시가 지나는 곳만). 심사자 컴퓨터에도 있다
PACKED = Path(__file__).resolve().parent / "packs" / "osm"
_APP_CACHE = Path(__file__).resolve().parent.parent / "data" / "geo"


def find(cache_dir: Path) -> list[Local]:
    """쓸 수 있는 지역 자료들. MAPCG_OSM_DB 에 파일을 주면 그것만, '0' 이면 쓰지 않는다. 없으면 <cache_dir 옆>/osm/*.sqlite,
    그리고 앱 자기 캐시(data/geo)를 쓸 때는 같이 싣는 시연 묶음(PACKED)까지 — 내려받은 자료가 먼저다(더 넓다)."""
    env = os.environ.get("MAPCG_OSM_DB", "").strip()
    if env == "0":
        return []
    paths = [Path(env)] if env else sorted((Path(cache_dir).parent / "osm").glob("*.sqlite"))
    if not env and Path(cache_dir).resolve() == _APP_CACHE:
        paths += sorted(PACKED.glob("*.sqlite"))
    out = []
    with _open_lock:
        for p in paths:
            try:
                st = p.stat()
            except OSError:
                continue
            key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
            if key not in _opened:
                try:
                    _opened[key] = Local(p)
                except (sqlite3.Error, ValueError, KeyError, OSError):
                    _opened[key] = None     # 깨졌거나 형식이 다르다 — 쓰지 않고 예전처럼 서버 · 묶음으로 간다
            if _opened[key]:
                out.append(_opened[key])
    return out
