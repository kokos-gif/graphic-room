"""실무 확인 목록(mapcg/knowhow.json)을 저장소 맨 위 노하우_실무확인목록.md 로 옮긴다.

    python tools/knowhow_md.py            만들기
    python tools/knowhow_md.py --check    지금 파일이 JSON 과 같은지만 본다(다르면 1)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapcg import knowhow  # noqa: E402

OUT = ROOT / "노하우_실무확인목록.md"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = knowhow.markdown() + "\n"
    if "--check" in argv:
        # 줄바꿈은 보지 않는다 — Windows 에서 깃이 꺼내면 CRLF 가 된다(제출 묶음을 푼 폴더 · 다른 컴퓨터)
        same = OUT.is_file() and OUT.read_text(encoding="utf-8").replace("\r\n", "\n") == text
        print("같음" if same else f"다름 — python tools/knowhow_md.py 로 다시 만든다({OUT.name})")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
