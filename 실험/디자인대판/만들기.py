"""디자인대판 시험작을 영상으로 낸다 — 지도와 같은 내보내기(브라우저가 그린 프레임을 받아 ffmpeg 로 묶기)를 그대로 쓴다.

실행(프로젝트 폴더에서):
  python 실험/디자인대판/만들기.py            영상(대판_시험작.mp4)
  python 실험/디자인대판/만들기.py --sheet    검수판(여러 시각을 한 장에 모은 그림 + 자동 검사 결과)
"""
import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from mapcg import build, export, request  # noqa: E402

# 대판이 실제로 그릴 수 있는 화풍. V5 는 지도·순위판용이라 대판의 디자인 기준(skin.js)을 바꾸지 않는다 — 고르게 두면 이름만 V5 인 V1 결과가 나온다
STYLES = [k for k in request.ART_STYLES if k != "v5"]
SHEET_TIMES = "1.3,3.7,6.0,8.1,9.9,11.1,12.4,14.9,16.7"       # 장면마다 가운데와 끝 무렵
BRUSH = "../../mapcg/templates/brush.js"                       # index.html 이 부르는 작가 도구(앱 쪽 파일)


def stage(out: Path, art_style: str = request.DEFAULT_ART_STYLE) -> Path:
    """내보낼 묶음을 한 폴더에 모은다. 내보내기 서버는 폴더 하나만 보여 주므로, 앱의 작가 도구(brush.js)를 옆에 복사한다."""
    page = out / "page"
    page.mkdir(parents=True, exist_ok=True)
    for name in ("skin.js", "brief.js", "kit.js", "scenes.js"):
        shutil.copyfile(HERE / name, page / name)
    shutil.copyfile(ROOT / "mapcg" / "templates" / "brush.js", page / "brush.js")
    html = (HERE / "index.html").read_text(encoding="utf-8")
    if BRUSH not in html:
        raise export.ExportError(f"index.html 에서 작가 도구 경로({BRUSH})를 찾지 못했습니다.")
    # 화풍은 장면을 따로 두지 않는다 — 같은 장면에 화풍 파일(색·붓 설정)만 얹는다. 화풍 파일은 작가 도구 바로 뒤에서 부른다
    build.write_art_page(html.replace(BRUSH, "brush.js"), page / "index.html", art_style, "brush.js")
    return page


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="디자인대판 시험작 내보내기")
    ap.add_argument("--sheet", nargs="?", const=SHEET_TIMES, help="영상 대신 검수판 한 장을 만든다(쉼표로 나눈 시각들, 초)")
    ap.add_argument("--art-style", choices=STYLES, default=request.DEFAULT_ART_STYLE,
                    help="화풍 선택 (기본 claude). v5 는 지도·순위판에서만 됩니다")
    ap.add_argument("--out", type=Path, default=ROOT / "output", help="화풍별 결과 폴더를 만들 상위 폴더")
    args = ap.parse_args()
    folder = "실험_디자인대판" if args.art_style == request.DEFAULT_ART_STYLE else f"실험_디자인대판_{args.art_style}"
    out = args.out / folder
    profile = out / ".browser-profile"
    try:
        page = stage(out, args.art_style)
        if args.sheet:
            export.capture(page, f"sheet&times={args.sheet}", out / "sheet", 1, profile, timeout=120)
            shutil.copyfile(out / "sheet" / "00000.png", out / "검수판.png")
            print(f"검수판 → {out / '검수판.png'}")
        else:
            # 길이·fps·크기는 여기 적지 않는다 — 페이지(장면 길이의 합)가 알려 주는 계획을 그대로 따르고, 만든 파일을 재서 견준다
            plan = export.capture_planned(page, "panel", out / "frames", profile, timeout=300)
            mp4 = export.encode_mp4(out / "frames", plan["fps"], out / "대판_시험작.mp4", frames=plan["frames"])
            print(f"프레임 {plan['frames']}장 → {mp4}")
            print(export.spec_line(export.check_spec({k: plan[k] for k in export.SPEC_LABELS}, export.probe(mp4))))
    except export.ExportError as e:
        print(f"실패: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
