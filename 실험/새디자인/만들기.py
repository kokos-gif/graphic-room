"""새 디자인 기준 화면(순위판 한 장)을 그림으로 뽑는다 — 방향을 확인받기 위한 실험이라 앱에는 연결하지 않았다.

실행(프로젝트 폴더에서):
  python 실험/새디자인/만들기.py                     output/실험_새디자인/순위판_기준.png (1920×1080)
  python 실험/새디자인/만들기.py 의뢰봉투.json        다른 순위판 의뢰로
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from mapcg import board, build, export, request  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "순위판_게임점수.json"
    raw = json.loads(src.read_text(encoding="utf-8-sig"))
    raw["output"] = {"width": 1920, "height": 1080, "fps": 30}        # 기준 화면은 방송 크기로 본다
    try:
        req = board.normalize(raw)
    except request.RequestError as e:
        print("의뢰 봉투에 고칠 곳이 있습니다: " + " / ".join(e.problems))
        return 2
    rows, notes = board.arrange(req)
    out = ROOT / "output" / "실험_새디자인"
    page = out / "page"
    page.mkdir(parents=True, exist_ok=True)
    job = {"kind": board.KIND, "program": req["program"], "title": req["title"], "unit": req["unit"],
           "rows": [{k: r[k] for k in ("id", "rank", "name", "show", "value")} for r in rows],
           "duration_seconds": req["duration_seconds"], "layout": "full", "layout_rect": list(request.LAYOUTS["full"]), "output": req["output"]}
    (page / "job.js").write_text(build._js("JOB", job), encoding="utf-8")
    shutil.copyfile(HERE / "rank.html", page / "index.html")
    for name in (build.STAGE, "brush.js"):
        shutil.copyfile(build.TEMPLATES / name, page / name)
    try:
        export.capture(page, "panel&times=6.5", out / "sheet", 1, out / ".browser-profile", timeout=120)
    except export.ExportError as e:
        print(f"실패: {e}")
        return 1
    shutil.copyfile(out / "sheet" / "00000.png", out / "순위판_기준.png")
    for n in notes:
        print(f"주의: {n}")
    print(f"기준 화면 → {out / '순위판_기준.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
