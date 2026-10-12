"""Bilingual subtitle bitmaps (Pillow): Chinese on top (bold, wrapped into even lines of up to ~24 chars at nearby
punctuation), English below (wrapped by measured width), both outlined, anchored to the bottom of a 1920x1080 frame.
Rendered once per event and cached; the playout blends only the returned box. Placement is per stream (news
channels own the bottom ~200 px with their own lower third): see configure(). Fonts come from settings (obs.font_*)
or the first CJK font found on this system (Microsoft YaHei on Windows, Noto Sans CJK on Linux).
"""
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import settings

W, H = 1920, 1080
ZH_LINE, MARGIN_X, MARGIN_BOTTOM = 24, 80, 40
ZH_MAX = 30  # 58 px glyphs: the most one line holds inside the margins
BOX_ALPHA, BOX_PAD = 0.0, 16  # translucent backing box behind the subtitle block (0 = none)
TAG_CORNER = "tl"  # tl / tr


@lru_cache(maxsize=None)
def load_font(kind):
    """zh (bold, 58 px), en (36 px) or tag (26 px); loaded on first use, so importing this module needs no fonts."""
    src, size = {"zh": ("zh_bold", 58), "en": ("en", 36), "tag": ("zh", 26)}[kind]
    f = settings.font(src)
    if not f:
        raise RuntimeError(f"no font for the burnt-in subtitles ({src}): install a CJK font (e.g. Noto Sans CJK) "
                           f"or set obs.font_{src} in settings.json")
    return ImageFont.truetype(f, size)


def wrap_zh(t: str, n: int | None = None) -> list[str]:
    """n even lines (default: as many as ZH_LINE needs; two halves used to run off the frame). Each cut goes to the
    punctuation nearest its share, else the nearest spot outside a Latin word, within half a share and with no line
    over ZH_MAX."""
    if n is None:
        n = -(-len(t) // ZH_LINE)
    if n <= 1:
        return [t] if t else []
    share = len(t) / n
    ok = lambda i: abs(i - share) < share / 2 and i <= ZH_MAX and len(t) - i <= (n - 1) * ZH_MAX
    word = lambda c: c.isascii() and (c.isalnum() or c in ".-'")
    cuts = ([i + 1 for i, c in enumerate(t) if c in "，、；：,;" and ok(i + 1)]
            or [i for i in range(1, len(t)) if ok(i) and not (word(t[i - 1]) and word(t[i]))])  # not inside "UltraFast"
    cut = min(cuts, key=lambda i: abs(i - share)) if cuts else round(share)
    return [t[:cut].rstrip("，、；：,; ")] + wrap_zh(t[cut:].lstrip(), n - 1)


def wrap_en(t: str, width=W - 2 * MARGIN_X) -> list[str]:
    lines, cur = [], ""
    for w in t.split():
        nxt = f"{cur} {w}".strip()
        if cur and load_font("en").getlength(nxt) > width:
            lines.append(cur); cur = w
        else:
            cur = nxt
    return lines + ([cur] if cur else [])


def configure(bottom=None, box=None, tag_corner=None):
    """Per-stream placement: bottom margin in px, backing box opacity 0..1, tag corner "tl" / "tr"."""
    global MARGIN_BOTTOM, BOX_ALPHA, TAG_CORNER
    if bottom is not None:
        MARGIN_BOTTOM = int(bottom)
    if box is not None:
        BOX_ALPHA = float(box)
    if tag_corner:
        TAG_CORNER = tag_corner
    render.cache_clear(); render_tag.cache_clear()


@lru_cache(maxsize=8)  # a block is ~8 MB of float32; only the current and next few matter
def render(zh: str, en: str):
    """Returns (x0, y0, rgb float32 [h,w,3], alpha float32 [h,w,1]) for the subtitle block, or None."""
    lines = [(l, load_font("zh"), 3, (255, 255, 255)) for l in wrap_zh(zh.strip().rstrip("。"))]
    lines += [(l, load_font("en"), 2, (224, 224, 224)) for l in wrap_en(en)]
    if not lines:
        return None
    heights = [f.getbbox("国Ay")[3] + 10 for _, f, _, _ in lines]
    block_h = int(sum(heights)) + 8
    img = Image.new("RGBA", (W, block_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if BOX_ALPHA > 0:
        half = max(f.getlength(t) for t, f, _, _ in lines) / 2 + BOX_PAD
        d.rounded_rectangle((W / 2 - half, 0, W / 2 + half, block_h - 1), radius=10, fill=(0, 0, 0, int(255 * BOX_ALPHA)))
    y = 4
    for (text, font, stroke, color), h in zip(lines, heights):
        d.text((W / 2, y), text, font=font, fill=color + (255,), anchor="ma", stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += h
    a = np.asarray(img, dtype=np.float32) / 255.0
    ys, xs = np.nonzero(a[..., 3] > 0)
    x0, x1 = xs.min(), xs.max() + 1
    a = a[:, x0:x1]
    return int(x0), H - MARGIN_BOTTOM - block_h, a[..., :3] * 255.0, a[..., 3:4]


@lru_cache(maxsize=4)
def render_tag(text: str):
    tw = int(load_font("tag").getlength(text)) + 4
    img = Image.new("RGBA", (tw, 44), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 4), text, font=load_font("tag"), fill=(255, 255, 255, 190), stroke_width=1, stroke_fill=(0, 0, 0, 120))
    a = np.asarray(img, dtype=np.float32) / 255.0
    x0 = 36 if TAG_CORNER == "tl" else W - 36 - tw
    return x0, 26, a[..., :3] * 255.0, a[..., 3:4]


def blend(rgb: np.ndarray, item) -> None:
    """In-place alpha blend of a rendered item onto an HxWx3 uint8 frame."""
    if item is None:
        return
    x0, y0, color, alpha = item
    h, w = alpha.shape[:2]
    region = rgb[y0:y0 + h, x0:x0 + w].astype(np.float32)
    rgb[y0:y0 + h, x0:x0 + w] = (region * (1 - alpha) + color * alpha).astype(np.uint8)
