"""Staff cards — member-card format 85.6 × 54 mm (CR80), rendered at 400 dpi.

Photo, first and last name, role band in the role colour, optional title,
and a signed QR (NWS:…) that the entrance scanner accepts as "STAFF".
"""
import io
import math

from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext as _
from PIL import Image, ImageDraw, ImageOps

from core.models import SiteSettings
from core.utils import hex_to_rgb, text_color_on
from tickets.render import _en_date, _fit_font, _png_bytes, _replace, _text_width, font, images_to_pdf
from tickets.qr import make_qr_image

from .models import StaffCard

CARD_MM = (85.6, 54.0)
W, H = 1350, 852
NAVY = (11, 27, 58)
BLUE = (10, 61, 145)
WHITE = (255, 255, 255)
SOFT = (203, 213, 225)  # light grey on navy: contrast > 10:1


def _rounded(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    image = ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", (size[0] * 4, size[1] * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] * 4 - 1, size[1] * 4 - 1], radius=radius * 4, fill=255)
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    result = Image.new("RGBA", size, (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def _brand_mark(size: int, accent: tuple) -> Image.Image:
    """The NextWave wave mark (used when no logo is uploaded)."""
    scale = 4
    mark = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mark)
    s = size * scale
    draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.29), fill=BLUE)
    for base, color in ((0.42, WHITE), (0.62, accent)):
        points = [(s * (0.12 + 0.76 * t / 60), s * (base - 0.07 * math.sin(2 * math.pi * 2 * t / 60))) for t in range(61)]
        draw.line(points, fill=color, width=int(s * 0.07), joint="curve")
    return mark.resize((size, size), Image.Resampling.LANCZOS)


def render_card_image(card: StaffCard) -> Image.Image:
    site = SiteSettings.load()
    event = card.event
    role_color = card.color
    accent = hex_to_rgb(site.accent_color or "#FF6B00")

    img = Image.new("RGB", (W, H), NAVY)

    # Decorative shapes (no text on them)
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(deco)
    d.ellipse([W - 420, -520, W + 620, 420], fill=(*BLUE, 255))
    d.ellipse([W - 300, -400, W + 500, 300], outline=(*accent, 90), width=6)
    d.ellipse([-260, H - 330, 200, H + 130], outline=(*hex_to_rgb(role_color), 110), width=10)
    img = Image.alpha_composite(img.convert("RGBA"), deco).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Header: logo/mark + event name
    x = 56
    logo = None
    if site.logo:
        try:
            with site.logo.open("rb") as fh:
                logo = Image.open(fh).convert("RGBA")
                logo.thumbnail((96, 96))
        except (OSError, ValueError):
            logo = None
    mark = logo or _brand_mark(84, accent)
    img.paste(mark, (x, 44 + (84 - mark.height) // 2), mark)
    tx = x + mark.width + 24
    draw.text((tx, 46), event.name.upper(), font=_fit_font(draw, event.name.upper(), 640, 44, 28), fill=WHITE)
    draw.text((tx, 100), f"{event.edition} · {_en_date(event.date_start, 'M j, Y')}", font=font(26), fill=SOFT)

    # Photo with a white frame
    frame = 344
    draw.rounded_rectangle([56, 186, 56 + frame, 186 + frame], radius=36, fill=WHITE)
    if card.photo:
        try:
            with card.photo.open("rb") as fh:
                photo = _rounded(Image.open(fh).copy(), (frame - 16, frame - 16), 30)
            img.paste(photo, (64, 194), photo)
        except (OSError, ValueError):
            pass

    # Names & title
    nx, n_width = 56 + frame + 40, W - (56 + frame + 40) - 56 - 300 - 32
    first = card.first_name
    last = card.last_name.upper()
    draw.text((nx, 198), first, font=_fit_font(draw, first, n_width, 60, 30), fill=WHITE)
    draw.text((nx, 270), last, font=_fit_font(draw, last, n_width, 60, 30), fill=WHITE)
    if card.title:
        draw.text((nx, 352), card.title, font=_fit_font(draw, card.title, n_width, 32, 20, bold=False), fill=SOFT)
    draw.text((nx, 452), card.code, font=font(28, True), fill=SOFT)

    # QR on a white square, top right
    box, qr_size = 300, 264
    bx, by = W - 56 - box, 186
    draw.rounded_rectangle([bx, by, bx + box, by + box], radius=24, fill=WHITE)
    qr = make_qr_image(card.qr_token, box_size=20, border=1).resize((qr_size, qr_size), Image.Resampling.NEAREST)
    img.paste(qr, (bx + (box - qr_size) // 2, by + (box - qr_size) // 2))

    # Role band
    band_top = 600
    draw.rectangle([0, band_top, W, H], fill=hex_to_rgb(role_color))
    on_band = hex_to_rgb(text_color_on(role_color))
    role = card.get_role_display().upper()
    role_font = _fit_font(draw, role, W - 56 - 56 - 260, 112, 54)
    draw.text((56, band_top + (H - band_top - role_font.size) / 2 - 10), role, font=role_font, fill=on_band)
    access = _("ACCESS")
    year = str(event.date_start.year)
    draw.text((W - 56 - _text_width(draw, access, font(30, True)), band_top + 70), access, font=font(30, True), fill=on_band)
    draw.text((W - 56 - _text_width(draw, year, font(56, True)), band_top + 110), year, font=font(56, True), fill=on_band)
    return img


def render_card_png(card: StaffCard) -> bytes:
    with translation.override(card.language or "en"):
        return _png_bytes(render_card_image(card))


def render_cards_pdf(cards: list[StaffCard]) -> bytes:
    """One card per page at the real card size (for PVC card printers or cutting)."""
    images = []
    for card in cards:
        with translation.override(card.language or "en"):
            images.append(render_card_image(card))
    return images_to_pdf(images, CARD_MM)


def ensure_card_files(card_id: int, force: bool = False) -> StaffCard:
    with transaction.atomic():
        card = StaffCard.objects.select_for_update().get(pk=card_id)
        if not force and card.card_png and card.card_pdf:
            return card
        _replace(card.card_png, f"{card.uuid.hex}.png", render_card_png(card))
        _replace(card.card_pdf, f"{card.uuid.hex}.pdf", render_cards_pdf([card]))
        card.save(update_fields=["card_png", "card_pdf"])
    return card


def card_preview_png(card: StaffCard, width: int = 675) -> bytes:
    with translation.override(card.language or "en"):
        image = render_card_image(card)
    image = image.resize((width, int(width * H / W)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

