"""python -m mapcg <명령> — 프로젝트 폴더 안에서 실행한다."""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import request

PROJECT_DIR = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser(prog="mapcg", description="Graphic Room — 방송그래픽 의뢰를 받아 지도·순위판·정보판을 코드로 그린다(실행 명령의 mapcg 는 예전 이름)")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="의뢰 봉투 하나로 지도와 영상을 만든다")
    m.add_argument("request", type=Path, help="의뢰 봉투 JSON 파일")
    m.add_argument("--layout", choices=list(request.LAYOUTS), help="배치를 바꿔서 만든다(의뢰 봉투 값 대신)")
    m.add_argument("--art-style", choices=request.ART_STYLES, help="화풍을 고른다(안 주면 의뢰 봉투의 값, 그것도 없으면 claude = V1)")
    m.add_argument("--out", type=Path, default=PROJECT_DIR / "output", help="결과 폴더(기본 output)")
    m.add_argument("--no-video", action="store_true", help="영상은 만들지 않고 브라우저용 지도만")
    m.add_argument("--sheet", action="store_true", help="영상 대신 검수판(네 시각의 화면을 한 장에 모은 그림)을 만든다")
    m.add_argument("--alpha", action="store_true", help="전체 프레임 투명 배경 PNG 시퀀스도 같이")
    m.add_argument("--offline", action="store_true", help="저장해 둔 지도 데이터만 쓴다(인터넷 안 씀)")
    m.add_argument("--open", action="store_true", help="끝나면 브라우저로 미리보기를 연다")

    s = sub.add_parser("serve", help="방송그래픽 의뢰 화면을 연다(내 컴퓨터에서만)")
    s.add_argument("--port", type=int, default=8765, help="포트 번호(기본 8765)")
    s.add_argument("--offline", action="store_true", help="저장해 둔 지도 데이터만 쓴다(인터넷 안 씀)")
    s.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않는다")
    k = sub.add_parser("pack", help="자주 쓰는 지역의 바탕 지도를 미리 받아 앱에 넣어 둔다(지도 서버가 될 때 한 번)")
    k.add_argument("name", choices=["seoul"], help="지역 이름")
    c = sub.add_parser("check", help="Liner 연결을 점검한다(키를 넣은 뒤 한 번)")
    c.add_argument("--image", type=Path, help="이 의뢰지 그림을 앱과 같은 길로 실제로 읽혀 본다")

    args = p.parse_args(argv)

    if args.cmd == "pack":
        from . import packs
        from .geo import GeoError
        try:
            packs.build(args.name, PROJECT_DIR / "data" / "geo")
        except GeoError as e:
            print(f"묶음을 만들지 못했습니다: {e}")
            return 3
        return 0

    if args.cmd == "check":
        from . import check
        return check.run(args.image)

    if args.cmd == "serve":
        from . import server
        return server.serve(PROJECT_DIR, port=args.port, offline=args.offline, open_browser=not args.no_open)

    import time
    from . import board, build, cuts, export, intake, navi, plan, quiz
    from .geo import GeoError
    try:
        import json
        raw = json.loads(args.request.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise request.RequestError(["의뢰 봉투는 JSON 객체여야 합니다."])
        if args.layout:
            raw["layout"] = args.layout
        if args.art_style:
            raw["art_style"] = args.art_style
        # 의뢰 봉투의 kind 로 종류를 가린다(없으면 지도) — 메신저(server.Desk.make)와 같은 갈래. 모르는 종류를 지도로 조용히 만들지 않는다
        kind = raw.get("kind") or None
        if kind == "scene":     # 맞춤 그래픽(mapcg/scene.py)은 봉투가 아니라 의뢰 글에서 시작한다 — 메인 에이전트가 지시서를 쓰고 담당을 띄운다
            raise request.RequestError(["맞춤 그래픽(kind: scene)은 의뢰 봉투로 만들지 않습니다 — 메신저에 의뢰 글을 보내거나, "
                                        "명령줄에서는 python tools/agent_lab.py run <의뢰.txt> 로 지시서부터 납품까지 한 번에 돌려 주세요."])
        if kind not in (None, "map", board.KIND, plan.KIND, quiz.KIND, cuts.KIND):
            raise request.RequestError([f"모르는 종류(kind: {kind})입니다 — {board.KIND}(순위판) · {plan.KIND}(정보판) · {quiz.KIND}(카드 퀴즈) · "
                                        f"{cuts.KIND}(컷 나눔) 가운데 하나이거나, 지도면 kind 를 빼 주세요."])
        is_board = kind == board.KIND
        is_info = kind in (plan.KIND, quiz.KIND)        # 설계도 정보판 · 카드 퀴즈
        is_navi = kind in (None, "map") and raw.get("map_style") == navi.STYLE      # 네비 화면: 브라우저가 아니라 navi.py 가 그린다
        if is_navi and isinstance(raw.get("cuts"), list) and raw["cuts"]:
            raise request.RequestError(["네비 화면은 아직 컷 나눔을 하지 않습니다 — 컷 나눔을 빼거나 지도 모양을 '경로 이동 지도'로 바꿔 주세요."])
        is_cuts = not (is_board or is_info or is_navi) and "cuts" in raw       # 컷 나눔 지도: 한 경로를 여러 컷으로 잘라 한 영상으로 잇는다
        req = (board.normalize(raw) if is_board else plan.normalize(raw) if kind == plan.KIND else quiz.normalize(raw) if kind == quiz.KIND
               else cuts.normalize(raw) if is_cuts else request.normalize(raw))
    except request.RequestError as e:
        print("의뢰 봉투에 고칠 곳이 있습니다:")
        for line in e.problems:
            print(f"  - {line}")
        return 2
    except (OSError, ValueError) as e:      # 파일이 없거나 JSON 이 아닐 때
        print(f"의뢰 봉투를 읽지 못했습니다: {e}")
        return 2

    if is_cuts:
        base = req["base"]
        try:
            # 명령줄 제작도 의뢰 번호로 묶는다 — 호출 기록에 번호가 남고, 지명 묻기가 키·잔액으로 거절되면 같은 의뢰의 검색은 보내지 않는다
            with intake._liner().request(f"명령줄-{time.strftime('%m%d-%H%M%S')}"):
                job = cuts.make(req, project_dir=PROJECT_DIR, out_root=args.out, request_dir=args.request.resolve().parent, offline=args.offline,
                                # --offline 이면 키가 있어도 Liner 에 지명을 묻거나 검색하지 않는다(밖으로 아무것도 보내지 않는다)
                                namer=intake.official_names if intake.has_key() and not args.offline else None,
                                searcher=intake.searched_name if intake.has_key() and not args.offline else None,
                                picker=intake.picked_place if intake.has_key() and not args.offline else None)
        except GeoError as e:
            print(f"지도 데이터를 받지 못했습니다: {e}")
            return 3
        except request.RequestError as e:
            print("의뢰 봉투에 고칠 곳이 있습니다:")
            for line in e.problems:
                print(f"  - {line}")
            return 2
        print(f"컷 나눔: {len(job['cuts'])}컷 · 길이 {base['duration_seconds']:g}초 · 경로 전체 {job['route']['distance_m'] / 1000:.1f}km")
        print(f"화풍 · {request.ART_STYLES[base['art_style']]}")
        for w in job["warnings"]:
            print(f"주의: {w}")
        for n, c in enumerate(job["cuts"], 1):      # 영상 규격이 맞아도 바탕이 빈 컷·어림으로 놓은 곳은 따로 알린다
            if c["map"].get("source") == "blank":
                print(f"컷 {n}: 바탕 지도 없음(도로·강을 받지 못했습니다)")
            if c.get("approx"):
                print(f"컷 {n}: 어림으로 놓은 곳 — {', '.join(c['approx'])}")
        try:
            if args.sheet:
                for p in cuts.sheets(job):
                    print(f"검수판: {p}")
            elif not args.no_video:
                size = request.panel_rect(base["layout"], base["output"]["width"], base["output"]["height"])[2:]
                r = cuts.export_all(job, base["output"]["fps"], size, alpha=args.alpha)
                print(f"영상: {r['mp4']} ({r['frames']}프레임 · 내보내는 데 {r['seconds']:g}초)")
                if "alpha_dir" in r:
                    print(f"투명 배경 시퀀스: {r['alpha_dir']}")
        except export.ExportError as e:
            print(f"영상을 만들지 못했습니다: {e}")
            return 4
        print(f"출처 표기: {job['attribution']}")
        return 0

    label = board.KIND_LABEL if is_board else plan.KIND_LABEL if kind == plan.KIND else quiz.KIND_LABEL if kind == quiz.KIND else navi.LABEL if is_navi else "지도"
    if is_board:
        job = board.make_job(req, project_dir=PROJECT_DIR, out_root=args.out)
        job_dir = job["dir"]
        print(f"{board.KIND_LABEL}: {req['title']} · 길이 {req['duration_seconds']:g}초")
        for r in job["board"]["rows"]:
            print(f"  {r['rank']}위 {r['name']} {r['show']}")
    elif is_info:
        try:
            if kind == quiz.KIND:       # 그림 카드는 Liner 그림 모델로 그린다 — 명령줄 제작도 의뢰 번호로 묶는다
                from . import quiz_build
                with intake._liner().request(f"명령줄-{time.strftime('%m%d-%H%M%S')}"):
                    job = quiz_build.make_job(req, project_dir=PROJECT_DIR, out_root=args.out, offline=args.offline)
            else:
                job = plan.make_job(req, project_dir=PROJECT_DIR, out_root=args.out)
        except Exception as e:      # 메신저와 같이: 제작 실패는 사유를 한 줄로
            print(f"{label} 제작 실패: {e}")
            return 3
        job_dir = job["dir"]
        print(f"{label}: {req.get('title') or '(제목 없음)'} · 길이 {req['duration_seconds']:g}초")
    elif is_navi:
        try:
            with intake._liner().request(f"명령줄-{time.strftime('%m%d-%H%M%S')}"):
                job = navi.make_job(req, project_dir=PROJECT_DIR, out_root=args.out, offline=args.offline,
                                    searcher=intake.searched_name if intake.has_key() and not args.offline else None)
        except GeoError as e:
            print(f"지도 데이터를 받지 못했습니다: {e}")
            return 3
        job_dir = job["dir"]
        print(f"{navi.LABEL}: {job['places']['from']['display']} → {job['places']['to']['display']} · 길이 {req['duration_seconds']:g}초 · "
              f"도로 거리 {job['route']['distance_m'] / 1000:.1f}km")
    else:
        try:
            # 지도에 없는 지명은 Liner 웹 검색으로 확인해 다시 찾는다(--offline 이면 밖으로 아무것도 보내지 않는다)
            with intake._liner().request(f"명령줄-{time.strftime('%m%d-%H%M%S')}"):
                job = build.make_job(req, project_dir=PROJECT_DIR, out_root=args.out,
                                     request_dir=args.request.resolve().parent, offline=args.offline,
                                     searcher=intake.searched_name if intake.has_key() and not args.offline else None)
        except GeoError as e:
            print(f"지도 데이터를 받지 못했습니다: {e}")
            return 3
        job_dir = job["dir"]
        print(f"배치: {request.LAYOUT_LABELS[req['layout']]} · 길이 {req['duration_seconds']:g}초 · "
              f"도로 거리 {job['route']['distance_m'] / 1000:.1f}km")
        print(f"출발: {job['places']['from']['display']}")
        print(f"도착: {job['places']['to']['display']}")
    if req.get("art_style") in request.ART_STYLES:
        print(f"화풍 · {request.ART_STYLES[req['art_style']]}")
    for w in job["warnings"]:
        print(f"주의: {w}")
    print(f"미리보기: {job_dir / 'index.html'} (만드는 데 {job['timing']['build_seconds']:g}초)")

    if args.sheet and is_navi:
        print("네비 화면은 검수판을 만들지 않습니다 — 미리보기나 영상으로 확인해 주세요.")
    elif args.sheet:
        try:
            print(f"검수판: {export.sheet(job_dir, req['duration_seconds'])}")
        except export.ExportError as e:
            print(f"검수판을 만들지 못했습니다: {e}")
            return 4
    elif not args.no_video:
        try:
            if is_navi:     # 검은 방송 화면 가운데에 세로 화면을 얹은 영상(투명 배경 시퀀스는 없다)
                r = navi.export(job_dir)
            else:
                size = request.panel_rect(req["layout"], req["output"]["width"], req["output"]["height"])[2:]
                r = export.export(job_dir, req["duration_seconds"], req["output"]["fps"], alpha=args.alpha, size=size,
                                  name=label, keep_panel_frames=kind in (board.KIND, plan.KIND))
        except export.ExportError as e:
            print(f"영상을 만들지 못했습니다: {e}")
            return 4
        print(f"영상: {r['mp4']} ({r['frames']}프레임" + (f" · 내보내는 데 {r['seconds']:g}초" if r.get("seconds") is not None else "") + ")")
        if "alpha_dir" in r:
            print(f"투명 배경 시퀀스: {r['alpha_dir']}")
    if job.get("attribution"):
        print(f"출처 표기: {job['attribution']}")
    if args.open:
        webbrowser.open((job_dir / "index.html").resolve().as_uri())      # 결과 폴더가 상대 경로여도 열리게 — 파일 주소는 절대 경로로만 만들 수 있다
    return 0


if __name__ == "__main__":
    sys.exit(main())
