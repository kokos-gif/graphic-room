"""시연 지역 지도 묶음 — 내려받은 서울 지역 자료(data/osm/seoul.sqlite)에서 시연 · README 예시 의뢰의 범위만 잘라
앱에 같이 싣는다(mapcg/packs/osm/*.sqlite, 깃 · 제출 묶음에 들어간다).

공개 지도 서버(Overpass)가 붐비면 지도 하나에 2분을 기다리고 큰길만 그려진다(2026-11-22 실측). 심사자 컴퓨터에는 326MB 짜리
서울 자료가 없으므로, 영상과 README 예시가 지나는 곳만 실어 심사자가 돌려도 영상과 같은 속도 · 같은 자세함으로 그려지게 한다.
자료: © OpenStreetMap contributors — ODbL. 잘라 낸 이 묶음도 ODbL 로 배포한다(화면 안 출처 표기는 그대로).

    python tools/osm_demo_pack.py                 data/osm/seoul.sqlite 에서 만든다
    python tools/osm_demo_pack.py --src <파일>     다른 지역 자료에서
    python tools/osm_demo_pack.py --only <이름>    묶음 하나만 다시
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapcg import osmlocal  # noqa: E402

# 이름 → (남, 서, 북, 동). 묶음은 앱이 청한 범위를 '통째로' 덮을 때만 쓰인다(Geo._local) — 경로 둘레가 아니라 앱이 실제로 청하는 범위로 잡는다.
# 네비 화면은 경로 둘레 ±0.02°(위도) · ±0.022°(경도)를 청하고(navi.make_job), 지도 영상은 화면 비율에 맞춰 경로보다 훨씬 넓게 청한다.
# 아래 범위는 2026-11-22 에 Geo._local 이 받은 범위를 재서 잡았다(tests/test_osm_demo_pack.py 의 ASKED 가 그 값이다)
AREAS = {
    "seoulstation_ddp": (37.535, 126.950, 37.585, 127.035),        # 서울역 · 명동 · 남산골한옥마을 · 동대문 · DDP(영상 사례 — 지도 영상)
    # 여의도공원 → 노량진역(영상 사례) · 노들섬 → 여의도(README 네비 예시)의 네비 화면: 도로 · 건물 범위를 둘 다 덮는다.
    # 처음 범위(37.500 ~ 37.545 · 126.900 ~ 126.975)는 네비 화면이 청하는 범위보다 좁아 도로에는 한 번도 쓰이지 않았다
    "yeouido_noryangjin": (37.485, 126.882, 37.555, 126.990),
    # 홍대 · 합정 · 마포 · 여의도 서쪽의 골목길. README 첫 예시(홍대 → 여의도 지도 영상)는 폭 10km 를 큰길만으로 청하므로
    # 이 묶음이 아니라 큰길 묶음(mapcg/packs/seoul_major.json.gz)이 그린다 — 이 묶음은 이 안에서 짧게 걷는 의뢰에만 쓰인다
    "hongdae_yeouido": (37.515, 126.900, 37.570, 126.950),
}


def build(src: Path, out_dir: Path, areas=AREAS) -> list[tuple[Path, int]]:
    db = sqlite3.connect(f"file:{quote(src.resolve().as_posix(), safe='/:')}?mode=ro", uri=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    made, done = time.strftime("%Y-%m-%d"), []
    source = dict(db.execute("SELECT key, value FROM meta")).get("source", "")
    for name, (s, w, n, e) in areas.items():
        w_ = osmlocal.Writer(out_dir / f"{name}.sqlite", (s, w, n, e), f"{src.name} 에서 시연 범위만 잘라 냄 · 원본 {source}", made)
        try:
            for layer, rank, body, a, b, c, d in db.execute(
                    "SELECT el.layer, el.rank, el.body, idx.minlat, idx.minlon, idx.maxlat, idx.maxlon FROM idx JOIN el ON el.id = idx.id "
                    "WHERE idx.maxlat >= ? AND idx.minlat <= ? AND idx.maxlon >= ? AND idx.minlon <= ? ORDER BY el.id", (s, n, w, e)):
                w_.n += 1
                w_.db.execute("INSERT INTO el VALUES (?, ?, ?, ?)", (w_.n, layer, rank, body))
                w_.db.execute("INSERT INTO idx VALUES (?, ?, ?, ?, ?)", (w_.n, a, c, b, d))
            path = w_.close()
        except BaseException:
            w_.abort()
            raise
        done.append((path, path.stat().st_size))
    db.close()
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=ROOT / "data" / "osm" / "seoul.sqlite")
    ap.add_argument("--out", type=Path, default=osmlocal.PACKED)
    ap.add_argument("--only", choices=sorted(AREAS), help="이 묶음 하나만 다시 만든다(나머지 파일은 건드리지 않는다)")
    args = ap.parse_args(argv)
    if not args.src.is_file():
        print(f"원본이 없습니다: {args.src} — tools/osm_local.py 로 먼저 만든다")
        return 2
    for path, size in build(args.src, args.out, {args.only: AREAS[args.only]} if args.only else AREAS):
        print(f"{path.relative_to(ROOT)}  {size / 1e6:.1f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
