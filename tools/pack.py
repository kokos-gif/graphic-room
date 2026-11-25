"""제출 묶음 만들기 — 깃이 관리하는 파일만 zip 으로 묶고, 들어가면 안 되는 것이 없는지 확인한다.

폴더를 통째로 압축하면 `.env`(키) · `output`(영상과 프레임 수십 GB) · `시험결과` · `data` 가 같이 들어간다.
이 도구는 `git archive` 로 커밋된 파일만 묶으므로 그런 것들이 들어갈 길이 없고, 그래도 한 번 더 이름을 검사한다.

  python tools/pack.py            제출/mapcg_제출_날짜.zip 을 만든다
  python tools/pack.py --test     만든 묶음을 빈 폴더에 풀어 전체 시험을 돌려 본다(묶음만으로 도는지 확인)

커밋하지 않은 변경은 묶음에 들어가지 않는다 — 있으면 알려 준다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIG = 20 * 1024 * 1024      # 이보다 큰 파일은 제출물에 맞는지 눈으로 보라고 알린다
# 제출물에 들어가면 안 되는 것: 키 파일, 만든 결과물, 받아 둔 자료, 시험 결과, 깃 내부
_BANNED_DIRS = ("output/", "data/", "시험결과/", "제출/", ".git/")
_BANNED_NAMES = (".env",)


def forbidden(names) -> list[str]:
    """묶음 속 이름들 가운데 들어가면 안 되는 것."""
    bad = []
    for n in names:
        last = n.rstrip("/").rsplit("/", 1)[-1]
        if n.startswith(_BANNED_DIRS) or last in _BANNED_NAMES or (last.startswith(".env.") and last != ".env.example"):
            bad.append(n)
    return bad


def quarantine(out: Path) -> Path:
    """들어가면 안 되는 것이 든 묶음의 이름을 바꿔 둔다 — 정상 묶음과 같은 이름으로 남아 있으면 나중에 그대로 제출할 수 있다."""
    blocked = out.with_name(out.stem + "_제출금지.zip")
    os.replace(out, blocked)
    return blocked


def _git(*args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def main(argv=None) -> int:
    # 출력을 파일 · 다른 프로그램으로 넘기면 한국어 윈도우는 cp949 로 찍는다 — 못 찍는 글자('—' 등)가 있어도 묶다가 죽지 않게
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    out_dir = ROOT / "제출"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"mapcg_제출_{time.strftime('%Y%m%d-%H%M')}.zip"
    changed = [line for line in _git("status", "--porcelain").splitlines() if line and not line.startswith("??")]
    if changed:
        print(f"주의: 커밋하지 않은 변경 {len(changed)}개는 묶음에 들어가지 않습니다 — 먼저 커밋하세요.")
        for line in changed[:8]:
            print("   ", line)
    head = _git("log", "-1", "--format=%h %s").strip()
    subprocess.run(["git", "archive", "--format=zip", "-o", str(out), "HEAD"], cwd=ROOT, check=True)
    with zipfile.ZipFile(out) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        bad = forbidden(i.filename for i in infos)
        big = [i for i in infos if i.file_size > BIG]
    print(f"묶음: {out}")
    print(f"  기준 커밋 {head}")
    print(f"  파일 {len(infos)}개 · 압축 {out.stat().st_size / 1048576:.1f}MB · 풀면 {sum(i.file_size for i in infos) / 1048576:.1f}MB")
    for i in big:
        print(f"  큰 파일: {i.filename} ({i.file_size / 1048576:.0f}MB) — 제출물에 필요한지 확인하세요")
    if bad:
        blocked = quarantine(out)
        print("  들어가면 안 되는 것이 있습니다:")
        for n in bad:
            print("   ", n)
        print(f"  이 묶음은 제출하면 안 됩니다 — 이름을 바꿔 두었습니다: {blocked.name}")
        return 1
    print("  키 파일(.env) · output · data · 시험결과 없음 확인")
    if "--test" in args:
        with tempfile.TemporaryDirectory(prefix="mapcg_pack_") as tmp:
            with zipfile.ZipFile(out) as z:
                z.extractall(tmp)
            print("  묶음을 빈 폴더에 풀어 전체 시험을 돌립니다 …")
            run = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                                 cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
            tail = [line for line in (run.stderr or "").splitlines() if line.strip()][-3:]
            print("   ", " / ".join(tail))
            if run.returncode:
                print("  묶음만으로는 시험이 통과하지 않습니다 — 깃에 올리지 않은 파일에 기대는 곳이 있습니다.")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
