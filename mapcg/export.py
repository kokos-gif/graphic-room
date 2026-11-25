"""작업 폴더 → 영상. 브라우저를 창 없이 띄워 페이지가 프레임을 차례로 보내게 하고, ffmpeg 로 묶는다.

페이지가 캔버스에 직접 그리므로 화면 캡처가 아니라 그린 픽셀 그대로 받는다(투명 배경 포함).
"""
from __future__ import annotations

import functools
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext, suppress
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 크롬 계열 브라우저가 흔히 깔리는 자리(Windows · macOS · Linux). 맥은 앱 묶음 안의 실행 파일이라 PATH 로는 찾지 못한다
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge", "/snap/bin/chromium",
]
# ffmpeg 를 PATH 밖에 깐 맥(홈브루) · 리눅스에서 찾아볼 자리
FFMPEG_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]


class ExportError(RuntimeError):
    pass


class _BrowserDied(ExportError):
    pass


# ---- 그래픽 카드(GPU)로 그리기 ----
# 따라가는 카메라의 큰 바탕(가로 4000픽셀)과 매 장의 붓 선·종이 질감은 CPU 로만 그리면 느리다.
# MAPCG_GPU=0 이면 예전처럼 CPU 로만 그린다. 켠 채로 브라우저가 꺼지면 그 실행 동안은 저절로 CPU 로 돌아간다.
GPU_FLAGS = (["--use-angle=d3d11"] if os.name == "nt" else []) + ["--enable-gpu-rasterization", "--ignore-gpu-blocklist"]      # d3d11 은 Windows 에만 있다
CPU_FLAGS = ["--disable-gpu"]
_GPU_STATE = {"broken": False}


def use_gpu() -> bool:
    return not _GPU_STATE["broken"] and os.environ.get("MAPCG_GPU", "1").strip() != "0"


# ---- 여러 컷을 동시에 그리기 ----
# 브라우저 하나가 한 컷을 맡는다. 같은 프로필 폴더를 두 브라우저가 같이 쓰면 둘째가 바로 꺼지므로 컷마다 따로 둔다.
def workers(n: int) -> int:
    """동시에 띄울 브라우저 수. MAPCG_PARALLEL 로 정할 수 있다(1 = 차례로). 기본은 코어 4개당 하나, 많아야 4."""
    try:
        want = int(os.environ.get("MAPCG_PARALLEL", "0"))
    except ValueError:
        want = 0
    if want <= 0:
        want = min(4, max(1, (os.cpu_count() or 2) // 4))
    return max(1, min(n, want))


def profile_for(base: Path, i: int) -> Path:
    """i 번째(0부터) 브라우저의 프로필 폴더. 첫째는 예전과 같은 '.browser-profile'."""
    return base / (".browser-profile" if i == 0 else f".browser-profile-{i + 1}")


def find_browser() -> str:
    """크롬 계열 브라우저 실행 파일. 환경 변수 MAPCG_BROWSER 로 지정할 수 있다."""
    for c in [os.environ.get("MAPCG_BROWSER"), *BROWSERS,
              *(shutil.which(n) for n in ("chrome", "msedge", "chromium", "google-chrome", "google-chrome-stable", "chromium-browser", "microsoft-edge"))]:
        if c and Path(c).is_file():
            return c
    raise ExportError("크롬이나 엣지를 찾지 못했습니다. 크롬을 깔거나 MAPCG_BROWSER 에 실행 파일 경로를 넣어 주세요"
                      "(맥: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome).")


def find_ffmpeg() -> str:
    c = os.environ.get("MAPCG_FFMPEG") or shutil.which("ffmpeg") or next(
        (str(Path(d) / "ffmpeg") for d in FFMPEG_DIRS if (Path(d) / "ffmpeg").is_file()), None)
    if not c:
        raise ExportError("ffmpeg 를 찾지 못했습니다. 설치하거나(Windows: winget install Gyan.FFmpeg · 맥: brew install ffmpeg · 리눅스: apt install ffmpeg) MAPCG_FFMPEG 에 경로를 넣어 주세요.")
    return c


def frame_count(duration: float, fps: float) -> int:
    """영상의 프레임 수. 페이지(stage.js 의 Math.round)와 같은 반올림 — 반은 올린다(2.5초 × 25fps = 63장).

    파이썬의 round 는 반을 짝수 쪽으로 보내(62장) 페이지가 보내는 장수와 어긋난다.
    """
    return int(math.floor(duration * fps + 0.5))


def capture(job_dir: Path, view: str, frames_dir: Path, expected: int, profile_dir: Path, timeout: float = 600, *,
            frames: int | None = None, offset: int = 0) -> int:
    """페이지를 ?export=1 로 열어 프레임 PNG 를 frames_dir 에 받는다. 받은 장수를 돌려준다.

    frames = 페이지에 그릴 장수를 정해 줄 때(컷 나눔: 컷의 경계를 영상 전체의 누적 시간으로 잡는다).
    offset = 받은 프레임의 번호에 더할 값 — 여러 컷을 한 폴더에 번호를 이어서 바로 받는다(컷 폴더에 한 벌, 묶음 폴더에 또 한 벌 쓰지 않는다).
    """
    return _capture(job_dir, view, frames_dir, expected, profile_dir, timeout, frames, offset)[0]


def capture_planned(job_dir: Path, view: str, frames_dir: Path, profile_dir: Path, timeout: float = 600) -> dict:
    """장수를 페이지가 스스로 정하는 경우(장면 길이의 합처럼 페이지만 아는 값)에 쓴다.

    페이지가 프레임을 보내기 전에 /plan 으로 알려 준 계획 {frames, fps, width, height} 를 돌려준다.
    길이·크기를 파이썬 쪽에 한 번 더 적어 두지 않으려는 것이다 — 두 군데에 적으면 한쪽만 고쳤을 때 영상이 잘리거나 남는다.
    """
    n, plan = _capture(job_dir, view, frames_dir, None, profile_dir, timeout)
    if not {"fps", "width", "height"} <= plan.keys():
        raise ExportError("페이지가 계획(fps·크기)을 알려 주지 않았습니다.")
    return {**plan, "frames": n}


def _capture(job_dir: Path, view: str, frames_dir: Path, expected: int | None, profile_dir: Path, timeout: float,
             frames: int | None = None, offset: int = 0) -> tuple[int, dict]:
    frames_dir.mkdir(parents=True, exist_ok=True)

    def save(i: int, body: bytes) -> None:
        (frames_dir / f"{i + offset:05d}.png").write_bytes(body)

    return _retry_browser(lambda gpu: _capture_once(job_dir, view, expected, profile_dir, timeout, frames, gpu, save))


def _retry_browser(run):
    gpu = use_gpu()
    try:
        return run(gpu)
    except _BrowserDied:
        if not gpu:
            raise
        _GPU_STATE["broken"] = True
        return run(False)    # 처음부터 다시 그린다. MP4도 새 인코더에서 시작한다.


def _capture_once(job_dir: Path, view: str, expected: int | None, profile_dir: Path, timeout: float,
                  frames: int | None, gpu: bool, write_frame: Callable[[int, bytes], None], *,
                  ordered: bool = False) -> tuple[int, dict]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    done = threading.Event()
    lock = threading.Lock()
    got: set[int] = set()
    failed: list[str] = []
    plan: dict = {}

    class Handler(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"   # 연결 하나로 프레임을 이어 받는다(프레임마다 새로 접속하면 가끔 전송이 끊겼다)

        def log_message(self, *args):   # 조용히
            pass

        def handle(self):               # 다 받고 브라우저를 끌 때 열려 있던 연결이 끊기는 것은 정상이다
            try:
                super().handle()
            except OSError:
                pass

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length < 0 or (ordered and length > 64 * 1024 * 1024):
                    raise ValueError("프레임 전송 크기가 잘못됐습니다.")
                body = self.rfile.read(length)
                if len(body) != length:
                    self.close_connection = True   # 페이지가 동일 번호를 다시 보낼 수 있다.
                    return
                with lock:
                    if not done.is_set():
                        self.receive(body)
            except (OSError, ValueError, ExportError) as error:
                failed.append(str(error))
                done.set()
                self.send_error(400)
                return
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def receive(self, body):
            if self.path.startswith("/frame/"):
                i = int(self.path.rsplit("/", 1)[1])
                if i in got:              # 앞선 요청이 성공한 뒤 응답만 유실되어 재전송된 경우
                    return
                if ordered and (i != len(got) or i >= plan.get("frames", expected)):
                    raise ExportError(f"프레임 순서가 잘못됐습니다({i}, 다음 번호 {len(got)}).")
                write_frame(i, body)
                got.add(i)
            elif self.path == "/plan":      # 페이지가 스스로 정한 장수·fps·크기
                announced = json.loads(body)
                if not isinstance(announced, dict):
                    raise ExportError("페이지가 보낸 프레임 계획을 읽지 못했습니다.")
                if ordered:
                    count = announced.get("frames")
                    # 현재 의뢰 한도는 60초 × 60fps. 예전 컷 페이지는 ?frames 대신 자기 장수를 알린다.
                    if type(count) is not int or not 1 <= count <= max(expected, 60 * 60):
                        raise ExportError("페이지가 보낸 프레임 수가 범위를 벗어났습니다.")
                    if (plan or got) and plan != announced:
                        raise ExportError("전송 도중 페이지의 프레임 계획이 바뀌었습니다.")
                plan.update(announced)
            elif self.path == "/done":
                done.set()
            elif self.path == "/fail":      # 페이지가 다시 보내기까지 해 보고도 못 보냈을 때
                failed.append(body.decode("utf-8", errors="replace")[:200])
                done.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(job_dir)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html?view={view}&export=1" + (f"&frames={int(frames)}" if frames else "")

    def browse(gpu: bool) -> None:
        proc = subprocess.Popen(
            # 셋째 줄: 창이 가려진 것으로 처리돼도(화면 잠금, 다른 창에 덮임) 페이지가 멈추거나 느려지지 않게 한다
            [find_browser(), "--headless=new", *(GPU_FLAGS if gpu else CPU_FLAGS),
             "--hide-scrollbars", "--mute-audio", "--no-first-run",
             "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
             "--disable-features=CalculateNativeWinOcclusion",
             f"--user-data-dir={profile_dir}", "--window-size=1280,720", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            waited = 0.0
            while not done.wait(0.5):
                waited += 0.5
                if proc.poll() is not None:
                    raise _BrowserDied("브라우저가 프레임을 다 보내기 전에 꺼졌습니다.")
                if waited >= timeout:
                    raise ExportError(f"프레임 받기가 {timeout:.0f}초를 넘겼습니다({len(got)}/{expected or plan.get('frames', '?')}장).")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    try:
        browse(gpu)
    finally:
        server.shutdown()
        server.server_close()
    if failed:
        raise ExportError(f"페이지가 프레임을 보내지 못했습니다({len(got)}/{expected}장): {failed[0]}")
    # 장수는 페이지가 알려 준 계획을 따른다(그리는 쪽이 정한 것을 받는 쪽·묶는 쪽·규격 확인이 같이 쓴다).
    # 계획을 알려 주지 않는 예전 작업 폴더는 여기서 셈한 장수(expected)로 받는다.
    want = plan.get("frames") if isinstance(plan.get("frames"), int) else expected
    if want is None:
        raise ExportError("페이지가 프레임 수를 알려 주지 않았습니다.")
    if len(got) != want:
        raise ExportError(f"프레임 수가 맞지 않습니다({len(got)}/{want}장).")
    return len(got), plan


_H264 = ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
         "-movflags", "+faststart"]


def capture_mp4(job_dir: Path, out_path: Path, fps: int, expected: int, profile_dir: Path, *,
                frames: int | None = None) -> int:
    """브라우저가 보낸 PNG를 FFmpeg에 바로 쓴다. 성공한 MP4만 최종 이름으로 교체한다."""
    ffmpeg = find_ffmpeg()
    with tempfile.NamedTemporaryFile(prefix=".render-", suffix=".mp4", dir=out_path.parent, delete=False) as temp:
        partial = Path(temp.name)

    def render(gpu: bool) -> int:
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-xerror", "-f", "image2pipe", "-framerate", str(fps),
               "-c:v", "png", "-i", "pipe:0", *_H264, str(partial)]
        # 긴 오류 로그 때문에 프레임을 받는 파이프가 막히지 않게 한다.
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                def write(_i: int, body: bytes) -> None:
                    if not body.startswith(b"\x89PNG\r\n\x1a\n") or not body.endswith(b"IEND\xaeB\x60\x82"):
                        raise ExportError("프레임 PNG를 온전히 받지 못했습니다.")
                    # 쓰기가 막혀도 캡처 제한 시간이 적용되도록 핸들러에서 모두 보낸다.
                    remaining = memoryview(body)
                    while remaining:
                        sent = proc.stdin.write(remaining)
                        if not sent:
                            raise ExportError("FFmpeg가 프레임을 받지 못했습니다.")
                        remaining = remaining[sent:]

                n, _ = _capture_once(job_dir, "panel", expected, profile_dir, 600, frames, gpu, write, ordered=True)
                proc.stdin.close()
                if proc.wait(timeout=300) or not partial.stat().st_size:
                    raise ExportError("FFmpeg가 MP4를 완성하지 못했습니다.")
                return n
            except (ExportError, OSError, subprocess.TimeoutExpired) as error:
                if proc.poll() is not None and proc.returncode:
                    errors.seek(0)
                    raise ExportError(f"FFmpeg 실패: {errors.read(500).decode('utf-8', errors='replace').strip()}") from error
                if isinstance(error, ExportError):
                    raise
                raise ExportError(f"MP4 저장 실패: {error}") from error
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                with suppress(OSError):
                    proc.stdin.close()

    try:
        n = _retry_browser(render)
        os.replace(partial, out_path)
        return n
    finally:
        partial.unlink(missing_ok=True)


def encode_mp4(frames_dir: Path, fps: int, out_path: Path, frames: int | None = None) -> Path:
    """프레임 PNG 를 MP4 로 묶는다. frames 를 주면 앞에서부터 그만큼만 쓴다(폴더에 예전의 더 긴 영상 프레임이 남아 있어도 섞이지 않게)."""
    cmd = [find_ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "%05d.png"),
           *(["-frames:v", str(frames)] if frames else []),
           *_H264, str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out_path.is_file():
        raise ExportError(f"ffmpeg 실패: {r.stderr.strip()[:400]}")
    return out_path


# ---- 납품 규격 확인: 만든 파일을 실제로 재서 의뢰와 견준다 ----
SPEC_LABELS = {"width": "가로", "height": "세로", "fps": "초당 프레임", "frames": "프레임 수"}


def find_ffprobe() -> str | None:
    """영상 규격을 재는 도구. 보통 ffmpeg 와 같은 폴더에 같이 깔린다. 없으면 규격 확인만 건너뛴다."""
    c = os.environ.get("MAPCG_FFPROBE") or shutil.which("ffprobe")
    if c and Path(c).is_file():
        return c
    try:
        ff = Path(find_ffmpeg())
    except ExportError:
        return None
    beside = ff.with_name("ffprobe" + ff.suffix)
    return str(beside) if beside.is_file() else None


def probe(path: Path, *, exe: str | None = None, run=subprocess.run) -> dict | None:
    """영상 파일을 실제로 재 본 규격 {width, height, fps, frames}. 잴 도구가 없거나 읽지 못하면 None."""
    exe = exe or find_ffprobe()
    if not exe:
        return None
    try:
        r = run([exe, "-v", "error", "-select_streams", "v:0", "-count_frames",
                 "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
        s = json.loads(r.stdout)["streams"][0]
        num, _, den = str(s["r_frame_rate"]).partition("/")
        return {"width": int(s["width"]), "height": int(s["height"]),
                "fps": round(float(num) / float(den or 1), 3), "frames": int(s["nb_read_frames"])}
    except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        return None


def check_spec(want: dict, got: dict | None) -> dict:
    """의뢰한 규격(want)과 실제로 잰 규격(got)을 견준다. state: ok 같음 · differs 다름 · unknown 재지 못함."""
    if got is None:
        return {"state": "unknown", "want": want, "got": None, "diffs": []}
    diffs = [f"{SPEC_LABELS[k]} {want[k]:g} → {got[k]:g}" for k in SPEC_LABELS if abs(got[k] - want[k]) > 1e-6]
    return {"state": "differs" if diffs else "ok", "want": want, "got": got, "diffs": diffs}


def spec_line(spec: dict) -> str:
    """규격 확인 결과를 한 줄로(화면·명령줄에 그대로 보여 준다)."""
    show = lambda v: f"{v['width']}×{v['height']} · {v['fps']:g}fps · {v['frames']}프레임({v['frames'] / v['fps']:.2f}초)"  # noqa: E731
    if spec["state"] == "unknown":
        return f"규격을 재지 못했습니다(ffprobe 가 없거나 파일을 읽지 못함) — 의뢰 규격은 {show(spec['want'])}"
    if spec["state"] == "ok":
        return f"규격 확인: {show(spec['got'])} — 의뢰와 같음"
    return f"규격이 의뢰와 다릅니다: {', '.join(spec['diffs'])} (만든 파일 {show(spec['got'])})"


def _record(job_dir: Path, result: dict) -> None:
    """납품 기록을 작업 기록(job.json)에 남긴다 — 만든 파일, 실제로 잰 규격, 걸린 시간. 기록 파일이 없으면 건너뛴다."""
    path = job_dir / "job.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    record["delivery"] = {
        "exported": time.strftime("%Y-%m-%d %H:%M:%S"), "mp4": result["mp4"].name, "frames": result["frames"],
        "alpha_dir": result["alpha_dir"].name if "alpha_dir" in result else None,
        "spec": result.get("spec"), "seconds": result["seconds"],
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def sheet(job_dir: Path, duration: float, profile: Path | None = None) -> Path:
    """검수판: 처음·중간 두 곳·끝, 네 시각의 화면(방송 화면 자리에 얹은 모습)을 한 장에 모은다. 영상을 다 뽑기 전에 눈으로 확인할 때 쓴다.

    profile = 브라우저 프로필 폴더(여러 검수판을 동시에 뽑을 때 따로 준다). 안 주면 작업 폴더 옆의 '.browser-profile'.
    """
    job_dir = Path(job_dir).resolve()       # 브라우저에 넘기는 폴더 경로는 전체 경로여야 한다(상대 경로면 브라우저가 바로 꺼진다)
    times =",".join(f"{duration * k:.2f}" for k in (0.04, 0.34, 0.63, 0.97))
    capture(job_dir, f"preview&times={times}", job_dir / "sheet", 1, profile or profile_for(job_dir.parent, 0), timeout=300)      # 따라가는 카메라의 큰 바탕은 CPU 로만 그리면 1분 가까이 걸린다
    return Path(shutil.copyfile(job_dir / "sheet" / "00000.png", job_dir / "검수판.png"))


def export(job_dir: Path, duration: float, fps: int, *, alpha: bool = False, size: tuple[int, int] | None = None,
           name: str = "지도", log=print, keep_panel_frames: bool = False) -> dict:
    """그래픽 자리만 담은 MP4(이름.mp4)를 만든다. alpha=True 면 전체 프레임 투명 배경 PNG 시퀀스도 같이 낸다.

    size=(가로, 세로) 를 주면 만든 영상을 실제로 재서 의뢰 규격과 견준 결과를 'spec' 에 담는다.
    걸린 시간('seconds')과 함께 작업 기록(job.json)의 delivery 에도 남긴다.
    """
    t0 = time.perf_counter()
    job_dir = Path(job_dir).resolve()
    n = frame_count(duration, fps)
    log(f"프레임 그리기 {n}장(그래픽 자리)")
    movie = job_dir / f"{name}.mp4"
    # 별도 앱이나 명령줄에서 이미 내보내는 중이어도 같은 브라우저 프로필을 열지 않는다.
    profile_dir = (nullcontext(job_dir.parent / ".browser-profile") if keep_panel_frames else
                   tempfile.TemporaryDirectory(prefix="mapcg-browser-", ignore_cleanup_errors=True))
    with profile_dir as path:
        profile = Path(path)
        if keep_panel_frames:
            n = capture(job_dir, "panel", job_dir / "frames_panel", n, profile)
            encode_mp4(job_dir / "frames_panel", fps, movie, frames=n)
        else:
            n = capture_mp4(job_dir, movie, fps, n, profile)
        result = {"mp4": movie, "frames": n}
        if size:
            result["spec"] = check_spec({"width": size[0], "height": size[1], "fps": fps, "frames": n}, probe(movie))
            log(spec_line(result["spec"]))
        if alpha:
            log(f"프레임 그리기 {n}장(전체 프레임, 투명 배경)")
            got = capture(job_dir, "stage", job_dir / "알파_시퀀스", n, profile)
            if got != n:
                raise ExportError(f"투명 시퀀스의 장수가 영상과 다릅니다({got}/{n}장).")
            result["alpha_dir"] = job_dir / "알파_시퀀스"
    result["seconds"] = round(time.perf_counter() - t0, 1)
    _record(job_dir, result)
    return result
