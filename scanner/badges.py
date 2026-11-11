"""Staff cards — member-card format 85.6 × 54 mm (CR80), rendered at 400 dpi.

Same look as the event poster: red background with snowflakes, white names,
gold title, photo in a white frame, a signed QR (NWS:…) that the entrance
scanner accepts as "STAFF", and a band in the role colour.
"""
import io

from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext as _
from PIL import Image, ImageDraw, ImageOps

from core.models import SiteSettings
from core.poster import GOLD, GOLD_LIGHT, WHITE, red_background
from core.utils import hex_to_rgb, text_color_on
from tickets.qr import make_qr_image
from tickets.render import (
    _cap_height,
    _fit_font,
    _gradient_text,
    _png_bytes,
    _replace,
    _text_at_cap,
    _text_width,
    font,
    images_to_pdf,
)

from .models import StaffCard

CARD_MM = (85.6, 54.0)
W, H = 1350, 852
BAND_TOP = 640


def _rounded(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    image = ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", (size[0] * 4, size[1] * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] * 4 - 1, size[1] * 4 - 1], radius=radius * 4, fill=255)
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    result = Image.new("RGBA", size, (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def render_card_image(card: StaffCard) -> Image.Image:
    site = SiteSettings.load()
    event = card.event
    img = red_background((W, H), glow=(0.4, 0.3), seed=card.pk or 9)
    draw = ImageDraw.Draw(img)

    # Header: logo (if any) + event title + edition in gold
    x = 56
    if site.logo:
        try:
            with site.logo.open("rb") as fh:
                logo = Image.open(fh).convert("RGBA")
                logo.thumbnail((84, 84))
            img.paste(logo, (x, 44 + (84 - logo.height) // 2), logo)
            x += logo.width + 24
        except (OSError, ValueError):
            pass
    title = event.name.upper()
    title_font = _fit_font(draw, title, W - x - 56 - 260, 64, 34, family="black")
    _text_at_cap(draw, x, 52, title, title_font, WHITE)
    sub = " · ".join(part for part in (event.edition.upper(), event.dates_label) if part)
    _gradient_text(img, x, 52 + _cap_height(title_font) + 18, sub, _fit_font(draw, sub, W - x - 56 - 260, 30, 20, family="heavy"))
    draw = ImageDraw.Draw(img)
    staff = _("STAFF PASS")
    _text_at_cap(draw, W - 56 - _text_width(draw, staff, font(24, family="heavy")) - 5 * (len(staff) - 1), 58, staff,
                 font(24, family="heavy"), GOLD_LIGHT, tracking=5)

    # Photo with a white frame
    frame, top = 330, 196
    draw.rounded_rectangle([56, top, 56 + frame, top + frame], radius=30, fill=WHITE)
    if card.photo:
        try:
            with card.photo.open("rb") as fh:
                photo = _rounded(Image.open(fh).copy(), (frame - 16, frame - 16), 24)
            img.paste(photo, (64, top + 8), photo)
        except (OSError, ValueError):
            pass

    # Names, title, code
    nx = 56 + frame + 40
    n_width = W - nx - 56 - 290 - 30
    first_font = _fit_font(draw, card.first_name.upper(), n_width, 54, 28, family="heavy")
    _text_at_cap(draw, nx, top + 12, card.first_name.upper(), first_font, WHITE)
    last = card.last_name.upper()
    last_font = _fit_font(draw, last, n_width, 86, 36, family="display")
    last_top = top + 12 + _cap_height(first_font) + 20
    _text_at_cap(draw, nx, last_top, last, last_font, WHITE)
    y = last_top + _cap_height(last_font) + 28
    if card.title:
        title_font = _fit_font(draw, card.title, n_width, 32, 20, family="bold")
        _text_at_cap(draw, nx, y, card.title, title_font, GOLD_LIGHT)
        y += _cap_height(title_font) + 26
    _text_at_cap(draw, nx, top + frame - 26, card.code, font(26, family="bold"), WHITE, tracking=2)

    # QR on a white square
    box, qr_size = 290, 258
    bx, by = W - 56 - box, top
    draw.rounded_rectangle([bx, by, bx + box, by + box], radius=22, fill=WHITE)
    qr = make_qr_image(card.qr_token, box_size=20, border=1).resize((qr_size, qr_size), Image.Resampling.NEAREST)
    img.paste(qr, (bx + (box - qr_size) // 2, by + (box - qr_size) // 2))

    # Role band (role colour, gold edge)
    role_color = card.color
    draw.rectangle([0, BAND_TOP - 8, W, BAND_TOP], fill=GOLD)
    draw.rectangle([0, BAND_TOP, W, H], fill=hex_to_rgb(role_color))
    on_band = hex_to_rgb(text_color_on(role_color))
    role = card.get_role_display().upper()
    role_font = _fit_font(draw, role, W - 56 - 56 - 280, 150, 60, family="display")
    _text_at_cap(draw, 56, BAND_TOP + (H - BAND_TOP - _cap_height(role_font)) / 2, role, role_font, on_band, tracking=2)
    access = _("ACCESS")
    year = str(event.date_start.year)
    access_font, year_font = font(28, family="heavy"), font(64, family="display")
    _text_at_cap(draw, W - 56 - _text_width(draw, access, access_font), BAND_TOP + 54, access, access_font, on_band)
    _text_at_cap(draw, W - 56 - _text_width(draw, year, year_font), BAND_TOP + 98, year, year_font, on_band)
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

