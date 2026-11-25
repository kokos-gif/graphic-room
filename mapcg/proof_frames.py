"""완성 영상에서 검수할 프레임만 메모리에 읽는다. 이전 작업의 PNG도 지원한다."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import export


@dataclass(frozen=True)
class VideoFrame:
    video: Path
    index: int

    @property
    def name(self) -> str:
        return f"{self.video.name} #{self.index}"

    def read_bytes(self) -> bytes:
        result = subprocess.run(
            [export.find_ffmpeg(), "-v", "error", "-i", str(self.video), "-map", "0:v:0",
             "-vf", f"select=eq(n\\,{self.index})", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"],
            capture_output=True, timeout=120)
        if result.returncode or not result.stdout:
            raise ValueError(f"영상에서 검수할 프레임을 읽지 못했습니다({self.name}).")
        return result.stdout


@dataclass(frozen=True)
class _VideoFrames:
    video: Path
    count: int

    def __len__(self) -> int:
        return self.count

    def __getitem__(self, index: int) -> VideoFrame:
        return VideoFrame(self.video, range(self.count)[index])


def for_job(job_dir: Path, job: dict) -> _VideoFrames | list[Path]:
    delivery = job.get("delivery") or {}
    kind = job.get("kind") or (job.get("request") or {}).get("kind")
    name = delivery.get("mp4") or ("순위판.mp4" if kind == "rank_board" else "지도.mp4")
    if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.lower() != ".mp4":
        raise ValueError("작업 기록의 영상 파일명이 올바르지 않습니다.")
    movie = job_dir / name
    if not movie.is_file():
        return sorted((job_dir / "frames_panel").glob("*.png"))
    if movie.resolve().parent != job_dir.resolve():
        raise ValueError("작업 폴더 안에 있는 완성 영상만 검수할 수 있습니다.")
    count = delivery.get("frames")
    if type(count) is not int or count <= 0:
        count = (export.probe(movie) or {}).get("frames", 0)
    if not count:
        raise ValueError("완성 영상의 프레임 수를 읽지 못했습니다.")
    return _VideoFrames(movie, count)
