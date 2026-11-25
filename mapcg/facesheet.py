"""출연진 시트 — 여러 사람의 얼굴이 칸(줄 × 열)으로 놓인 그림 한 장에서 고른 칸의 얼굴을 동그라미용 정사각형으로 오린다.

바탕을 지운(투명) 시트가 기본이다: 칸 안의 불투명한 곳(머리 · 얼굴)을 찾아, 그 머리 전체가 동그라미 안에 들어가게
정사각형으로 오린다. 바탕은 투명한 채로 둔다 — 지도 그림이 동그라미를 단색으로 깔고 그 위에 얹는다(얼굴 양옆 번짐 없음).

바탕이 남아 있는(불투명) 시트는 얼굴 둘레의 바탕이 흐리게 번져 있는 경우가 많아, 얼굴 폭에 바짝 맞춰 오린다:
  1) 칸 안에서 '바탕과 다른 곳'(얼굴 · 머리카락)을 찾는다 — 칸 네 귀퉁이의 색을 바탕으로 보고, 그와 많이 다른 픽셀
  2) 그 영역의 눈높이 근처 폭(얼굴 폭)에 맞춰 한 변을 정하고, 가운데는 영역의 가로 가운데 · 세로는 이마~턱의 가운데
그 영역을 못 찾으면 칸 가운데를 칸의 62% 크기로 오린다.
"""
from __future__ import annotations

import re
from pathlib import Path

SIDE = 512          # 오린 얼굴 한 변(픽셀)
HEAD_PAD = 1.04     # 투명 시트: 머리(불투명한 곳) 긴 변 × 이 배율 = 정사각형 한 변 — 머리 둘레에 아주 조금 여유


def _alpha_box(cell) -> tuple[float, float, float] | None:
    """투명 시트의 칸: 불투명한 곳 중 가장 큰 덩어리(머리)의 가운데 (x, y)와 머리 전체가 들어가는 정사각형 한 변.
    칸이 투명하지 않거나 머리가 너무 작으면 None."""
    if cell.mode != "RGBA":
        return None
    import numpy as np
    from scipy import ndimage as nd
    a = np.asarray(cell.getchannel("A")) > 128
    if a.all() or a.sum() < a.size * .02:
        return None
    lab, n = nd.label(a)
    if n > 1:           # 지우다 남은 작은 부스러기는 빼고 가장 큰 덩어리만
        a = lab == (1 + int(np.argmax(nd.sum(a, lab, range(1, n + 1)))))
    ys, xs = np.where(a)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    return (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) * HEAD_PAD


def _crop_box(cell) -> tuple[float, float, float]:
    """칸 그림에서 얼굴 가운데 (x, y)와 정사각형 한 변. 칸 좌표."""
    got = _alpha_box(cell)
    if got:
        return got
    from PIL import ImageChops, ImageFilter, ImageStat
    cell = cell.convert("RGB")
    w, h = cell.size
    k = max(4, min(w, h) // 16)
    corners = [cell.crop(b) for b in ((0, 0, k, k), (w - k, 0, w, k), (0, h - k, k, h), (w - k, h - k, w, h))]
    bg = tuple(int(sum(ImageStat.Stat(c).mean[i] for c in corners) / 4) for i in range(3))
    flat = cell.copy()
    flat.paste(bg, (0, 0, w, h))
    diff = ImageChops.difference(cell, flat).convert("L").filter(ImageFilter.MedianFilter(5))
    # 번짐(바탕보다 조금 밝은 곳)은 빼고, 얼굴 · 머리카락처럼 바탕과 확실히 다른 곳만
    mask = diff.point(lambda v: 255 if v > 70 else 0).filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    box = mask.getbbox()
    if not box or (box[2] - box[0]) < w * .15 or (box[3] - box[1]) < h * .15:
        return w / 2, h / 2, min(w, h) * .62
    x0, y0, x1, y1 = box
    # 얼굴 폭: 영역의 가운데 높이(눈 ~ 광대) 줄들에서 가장 넓은 폭 — 어깨 · 목은 아래쪽이라 들어가지 않는다
    px = mask.load()
    widths = []
    for y in range(int(y0 + (y1 - y0) * .35), int(y0 + (y1 - y0) * .6), 3):
        xs = [x for x in range(x0, x1) if px[x, y]]
        if xs:
            widths.append((xs[-1] - xs[0], (xs[0] + xs[-1]) / 2))
    if not widths:
        return (x0 + x1) / 2, (y0 + y1) / 2, min(w, h) * .62
    span, cx = max(widths)
    side = span * 1.04                      # 얼굴(귀 · 옆머리 포함) 폭에 바짝 — 동그라미 양옆에 바탕 번짐이 들어가지 않게
    cy = y0 + side * .5 + (y1 - y0) * .04    # 정수리에서 한 변의 절반쯤 아래(이마가 잘리지 않게)
    return cx, cy, side


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_PARTICLES = ("에서", "으로", "에", "의", "을", "를", "은", "는", "이", "가", "로")
_ABS = re.compile(r"[A-Za-z]:[\\/]")
_REL = re.compile(r"(?<![\w:])[^\s\\/:]+[\\/]\S")


def find(text: str, root: Path) -> tuple[Path | None, str | None]:
    """수정 지시 글에서 출연진 시트 그림의 경로를 찾는다. 돌려주는 것: (그림 파일, 알림). 경로가 없으면 (None, None).

    경로는 'F:\\…\\출연진' 처럼 드라이브부터 적거나, 작업 폴더(root) 안의 '디자인의뢰지\\출연진' 처럼 적는다. 경로 뒤에 띄어 쓴 말이 붙어도
    (예: '…\\출연진 위에 3명') 실제로 있는 가장 긴 경로를 고른다. 폴더면 그 안의 그림이 한 장일 때 그것. 작업 폴더 밖은 읽지 않는다."""
    root = Path(root).resolve()
    starts = sorted({m.start() for m in _ABS.finditer(text or "")} | {m.start() for m in _REL.finditer(text or "")})
    tried = None
    for s in starts:
        words = (text or "")[s:].split()
        hit = None
        for k in range(1, min(len(words), 6) + 1):
            raw = " ".join(words[:k]).rstrip(".,;:)」』\"'")
            for cand in (raw, *[raw[: -len(p)] for p in _PARTICLES if raw.endswith(p) and len(raw) > len(p)]):
                p = Path(cand) if _ABS.match(cand) else root / cand
                if p.exists():
                    hit = p
                    break
        if hit is None:
            tried = tried or words[0]
            continue
        hit = hit.resolve()
        if hit != root and root not in hit.parents:
            return None, f"출연진 시트 경로 '{hit}' 는 작업 폴더({root}) 밖이라 읽지 않았습니다."
        if hit.is_dir():
            pics = sorted(f for f in hit.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS)
            if len(pics) != 1:
                return None, (f"'{hit.name}' 폴더에 그림이 {len(pics)}장이라 출연진 시트를 고르지 못했습니다 — 그림 파일 경로를 적어 주세요."
                              if pics else f"'{hit.name}' 폴더에 그림 파일이 없습니다.")
            hit = pics[0]
        if hit.suffix.lower() not in IMAGE_EXTS:
            return None, f"'{hit.name}' 은 그림 파일(PNG · JPG)이 아니라 출연진 시트로 읽지 않았습니다."
        return hit, None
    if tried and _ABS.match(tried):
        return None, f"수정 지시의 경로 '{tried}' 를 찾지 못했습니다."
    return None, None


def _open(sheet: Path):
    from PIL import Image
    with Image.open(sheet) as im:
        return im.convert("RGBA") if (im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info) else im.convert("RGB")


def _mask(im):
    """사람(머리 · 얼굴)인 곳. 투명 시트는 불투명한 곳, 불투명 시트는 네 귀퉁이 바탕색과 확실히 다른 곳(번짐은 빠진다)."""
    import numpy as np
    from scipy import ndimage as nd
    a = np.asarray(im)
    if im.mode == "RGBA" and (a[..., 3] < 128).mean() > .2:
        m = a[..., 3] > 128
    else:
        rgb = a[..., :3].astype(float)
        k = max(4, min(rgb.shape[:2]) // 40)
        bg = np.mean([rgb[:k, :k].mean((0, 1)), rgb[:k, -k:].mean((0, 1)), rgb[-k:, :k].mean((0, 1)), rgb[-k:, -k:].mean((0, 1))], 0)
        m = np.abs(rgb - bg).mean(2) > 70
        m = nd.binary_closing(nd.binary_opening(m, np.ones((5, 5))), np.ones((9, 9)))
    return nd.binary_fill_holes(m)


def detect(sheet: Path) -> list[list[tuple[int, int, int, int]]]:
    """시트에서 얼굴(사람 덩어리)을 찾아 줄별로 돌려준다 — 위 줄부터, 줄 안에서는 왼쪽부터. 칸마다 (x0, y0, x1, y1) 시트 좌표.
    지우다 남은 부스러기(그림 넓이의 1% 미만)는 뺀다."""
    import numpy as np
    from scipy import ndimage as nd
    m = _mask(_open(sheet))
    lab, n = nd.label(nd.binary_dilation(m, iterations=6))       # 머리카락 끝처럼 가까이 떨어진 조각은 한 사람으로
    boxes = []
    for k, sl in enumerate(nd.find_objects(lab), 1):
        part = (lab[sl] == k) & m[sl]
        if part.sum() < m.size * .01:
            continue
        ys, xs = np.where(part)
        boxes.append((int(sl[1].start + xs.min()), int(sl[0].start + ys.min()), int(sl[1].start + xs.max() + 1), int(sl[0].start + ys.max() + 1)))
    if not boxes:
        return []
    tall = sorted(b[3] - b[1] for b in boxes)[len(boxes) // 2]
    rows: list[list[tuple[int, int, int, int]]] = []
    for b in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        cy = (b[1] + b[3]) / 2
        if rows and abs(cy - sum((r[1] + r[3]) / 2 for r in rows[-1]) / len(rows[-1])) < tall * .5:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [sorted(r) for r in rows]


def cut_faces(sheet: Path, boxes: list[tuple[int, int, int, int]]) -> list:
    """detect 가 찾은 칸들의 얼굴을 SIDE × SIDE 그림(PIL, RGBA)으로 오린다."""
    from PIL import Image
    im = _open(sheet)
    W, H = im.size
    out = []
    for x0, y0, x1, y1 in boxes:
        px, py = (x1 - x0) * .2, (y1 - y0) * .2      # 머리 둘레에 조금 여유를 둔 칸(옆 사람은 들어오지 않을 만큼)
        ox, oy = max(0, round(x0 - px)), max(0, round(y0 - py))
        cell = im.crop((ox, oy, min(W, round(x1 + px)), min(H, round(y1 + py))))
        cx, cy, side = _crop_box(cell)
        out.append(im.crop((round(ox + cx - side / 2), round(oy + cy - side / 2), round(ox + cx + side / 2), round(oy + cy + side / 2)))
                   .convert("RGBA").resize((SIDE, SIDE), Image.LANCZOS))
    return out


def crop(sheet: Path, rows: int, cols: int, cells: list[tuple[int, int]]) -> list:
    """시트를 rows × cols 칸으로 나눠 cells([(줄, 열)], 1부터)의 얼굴을 SIDE × SIDE 그림(PIL, RGBA)으로 돌려준다.
    투명 시트면 바탕이 투명한 채로, 불투명 시트면 불투명한 채로."""
    from PIL import Image
    im = _open(sheet)
    W, H = im.size
    cw, ch = W / cols, H / rows
    out = []
    for r, c in cells:
        if not (1 <= r <= rows and 1 <= c <= cols):
            raise ValueError(f"출연진 시트에 {r}줄 {c}번째 칸이 없습니다({rows}줄 × {cols}칸).")
        ox, oy = (c - 1) * cw, (r - 1) * ch
        cell = im.crop((round(ox), round(oy), round(ox + cw), round(oy + ch)))
        cx, cy, side = _crop_box(cell)
        out.append(im.crop((round(ox + cx - side / 2), round(oy + cy - side / 2), round(ox + cx + side / 2), round(oy + cy + side / 2)))
                   .convert("RGBA").resize((SIDE, SIDE), Image.LANCZOS))
    return out
