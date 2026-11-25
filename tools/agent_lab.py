"""맞춤 그래픽(에이전트) 명령줄 실험 — 본체는 mapcg/scene.py(앱의 맞춤 그래픽 갈래와 같은 코드)다. 결과는 output/에이전트실험/ 아래에만 쌓인다.

    python tools/agent_lab.py run 시험의뢰/에이전트_개발/04_진술대조.txt [--tries 4]     의뢰 하나(지시서 → 담당 → 납품을 한 번에)
    python tools/agent_lab.py batch 시험의뢰/에이전트_개발 [--tries 4]                 폴더의 .txt 를 차례로 + 결과표
    python tools/agent_lab.py samples                                                 배치 보기 그림(Liner 를 부르지 않는다)
    python tools/agent_lab.py shoot <장면.json>                                        장면 설계 하나를 다시 그리기만(Liner 를 부르지 않는다)
    python tools/agent_lab.py export <시안 폴더> [--alpha]                            납품: MP4(30fps) + 투명 PNG 시퀀스 — 무거운 일이라 공용 대기열로 돌린다
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapcg.scene import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
