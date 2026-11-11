"""The poster look for generated images (tickets, staff cards).

Deep red with a brighter centre, faint snowflakes, a few gold sparkles, white
torn-paper band — the same style as the event poster — and its fonts:
Anton (tall condensed: dates, prices, venue) and Montserrat (titles, text).
The fonts are bundled in static/fonts (SIL Open Font License).
"""
import math
import random
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

RED_BRIGHT = (228, 32, 38)
RED = (200, 16, 46)
RED_DEEP = (104, 7, 13)
GOLD = (245, 185, 33)
GOLD_LIGHT = (252, 224, 140)
WHITE = (255, 255, 255)
INK = (17, 17, 17)
GREY = (107, 114, 128)

FONT_DIR = Path(settings.BASE_DIR) / "static" / "fonts"
FONTS = {
    "display": "Anton-Regular.ttf",
    "black": "Montserrat-Black.ttf",
    "heavy": "Montserrat-ExtraBold.ttf",
    "bold": "Montserrat-Bold.ttf",
    "semibold": "Montserrat-SemiBold.ttf",
    "regular": "Montserrat-Medium.ttf",
}


@lru_cache(maxsize=128)
def poster_font(family: str, size: int):
    """Bundled poster font, or None if the file is missing (caller falls back)."""
    path = FONT_DIR / FONTS[family]
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return None


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def text_width(draw, text: str, fnt, tracking: float = 0) -> float:
    if not text:
        return 0
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=fnt)
    return right - left + tracking * (len(text) - 1)


def draw_tracked(draw, xy, text: str, fnt, fill, tracking: float = 0, anchor_center: bool = False) -> float:
    """Letter-spaced text (Pillow has no letter-spacing). Returns the width drawn."""
    x, y = xy
    width = text_width(draw, text, fnt, tracking)
    if anchor_center:
        x -= width / 2
    if not tracking:
        draw.text((x, y), text, font=fnt, fill=fill)
        return width
    for char in text:
        draw.text((x, y), char, font=fnt, fill=fill)
        x += draw.textlength(char, font=fnt) + tracking
    return width


def cap_offset(fnt) -> int:
    """Distance from the text origin to the top of capital letters, to align boxes."""
    return fnt.getbbox("H")[1]


# --------------------------------------------------------------------------
# Background
# --------------------------------------------------------------------------
def _snowflake(draw, cx: float, cy: float, r: float, fill, width: int, turn: float) -> None:
    for k in range(6):
        a = turn + k * math.pi / 3
        ex, ey = cx + r * math.cos(a), cy + r * math.sin(a)
        draw.line([(cx, cy), (ex, ey)], fill=fill, width=width)
        for at, length in ((0.5, 0.32), (0.75, 0.2)):
            bx, by = cx + r * at * math.cos(a), cy + r * at * math.sin(a)
            for side in (-1, 1):
                b = a + side * math.pi / 4
                draw.line([(bx, by), (bx + r * length * math.cos(b), by + r * length * math.sin(b))], fill=fill, width=width)


def _sparkle(draw, cx: float, cy: float, r: float, fill) -> None:
    """Four-point star, like the glints on the poster."""
    t = r * 0.18
    draw.polygon([(cx, cy - r), (cx + t, cy - t), (cx + r, cy), (cx + t, cy + t),
                  (cx, cy + r), (cx - t, cy + t), (cx - r, cy), (cx - t, cy - t)], fill=fill)


def red_background(size: tuple[int, int], glow: tuple[float, float] = (0.45, 0.38), seed: int = 25) -> Image.Image:
    """Red poster background: bright glow fading to deep red, snowflakes, sparkles."""
    w, h = size
    span = max(w, h * 2.2)
    mask = Image.radial_gradient("L").resize((int(span * 1.25), int(span * 0.9)), Image.Resampling.BILINEAR)
    left = int(mask.width / 2 - glow[0] * w)
    top = int(mask.height / 2 - glow[1] * h)
    mask = mask.crop((left, top, left + w, top + h)).point(lambda v: min(255, int(v * 1.15)))
    img = Image.composite(Image.new("RGB", size, RED_DEEP), Image.new("RGB", size, RED_BRIGHT), mask)

    rng = random.Random(seed)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    unit = h / 900
    for _ in range(max(4, int(w * h / 260_000))):  # large, very faint flakes
        _snowflake(draw, rng.uniform(0, w), rng.uniform(0, h), rng.uniform(110, 190) * unit,
                   (255, 255, 255, rng.randint(16, 26)), max(3, int(7 * unit)), rng.uniform(0, math.pi))
    for _ in range(max(10, int(w * h / 48_000))):  # small flakes
        _snowflake(draw, rng.uniform(0, w), rng.uniform(0, h), rng.uniform(14, 46) * unit,
                   (255, 255, 255, rng.randint(30, 70)), max(2, int(3 * unit)), rng.uniform(0, math.pi))
    for _ in range(max(3, int(w * h / 300_000))):  # a few glints, along the top edge
        color = GOLD_LIGHT if rng.random() < 0.6 else WHITE
        _sparkle(draw, rng.uniform(0, w), rng.uniform(0, h * 0.12), rng.uniform(7, 14) * unit, (*color, rng.randint(150, 230)))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def torn_band(img: Image.Image, top: int, x0: int = 0, x1: int | None = None, seed: int = 3, fill=WHITE) -> None:
    """White band with a torn-paper top edge from `top` to the bottom (poster footer)."""
    x1 = img.width if x1 is None else x1
    rng = random.Random(seed)
    unit = img.height / 900
    points = [(x0, img.height)]
    x = x0
    while x < x1:
        points.append((x, top + rng.uniform(-10, 10) * unit))
        x += rng.uniform(10, 34) * unit
    points += [(x1, top), (x1, img.height)]
    ImageDraw.Draw(img).polygon(points, fill=fill)
