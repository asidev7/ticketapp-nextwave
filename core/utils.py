import io
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

COUNTRY_NAMES = {
    "BJ": "Benin",
    "NG": "Nigeria",
    "GH": "Ghana",
    "TG": "Togo",
    "CI": "Côte d'Ivoire",
    "SN": "Senegal",
    "US": "United States",
    "GB": "United Kingdom",
    "FR": "France",
    "CM": "Cameroon",
    "ZA": "South Africa",
}


# --------------------------------------------------------------------------
# Colours & contrast (WCAG)
# --------------------------------------------------------------------------
def _channel(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def text_color_on(bg: str, dark: str = "#111827", light: str = "#FFFFFF") -> str:
    """Pick the more readable text colour (white or near-black) for a background."""
    if not bg or not HEX_RE.match(bg):
        return light
    return light if contrast_ratio(bg, light) >= contrast_ratio(bg, dark) else dark


def readable_on_white(color: str, fallback: str = "#111827") -> str:
    """Return `color` if it passes WCAG AA on white, else a safe fallback."""
    if color and HEX_RE.match(color) and contrast_ratio(color, "#FFFFFF") >= 4.5:
        return color
    return fallback


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = (hex_color or "#0A3D91").lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------
# Countries
# --------------------------------------------------------------------------
def flag_emoji(country_code: str) -> str:
    code = (country_code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


# --------------------------------------------------------------------------
# Number formatting (always English style)
# --------------------------------------------------------------------------
def format_number(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    return f"{number:,}"


def format_money(amount, currency: str) -> str:
    return f"{currency} {format_number(amount)}"


# --------------------------------------------------------------------------
# Uploaded photos
# --------------------------------------------------------------------------
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}


def process_holder_photo(uploaded, size: int = 600) -> ContentFile:
    """Validate and normalise a holder photo.

    - checks the real image type with Pillow (not the extension / browser MIME)
    - enforces the max size
    - applies EXIF orientation, then drops all metadata (EXIF, GPS…)
    - centre-crops to a square and resizes
    - returns a fresh JPEG with a random UUID file name
    """
    max_bytes = settings.HOLDER_PHOTO_MAX_BYTES
    if uploaded.size and uploaded.size > max_bytes:
        raise ValidationError(_("The photo is too large (max %(mb)s MB).") % {"mb": max_bytes // (1024 * 1024)})

    try:
        uploaded.seek(0)
        probe = Image.open(uploaded)
        probe.verify()
        uploaded.seek(0)
        image = Image.open(uploaded)
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValidationError(_("Please upload a valid JPG, PNG or WebP image."))

    if (image.format or "").upper() not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError(_("Please upload a valid JPG, PNG or WebP image."))

    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    image = ImageOps.fit(image, (size, size), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86, optimize=True)  # no exif= → metadata stripped
    return ContentFile(buffer.getvalue(), name=f"{uuid.uuid4().hex}.jpg")
