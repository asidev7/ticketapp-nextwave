"""Ticket rendering — landscape concert ticket with a stub (2400 × 900 px, 8:3).

* Default design: the event poster look — red background with snowflakes,
  event title, red edition box, gold dates, white torn band with the venue —
  and a white stub with the QR code, ticket code, serial number and one box
  per entry day (a 2-day ticket shows DAY 1 · DAY 2).
* With an uploaded design (TicketType.design_image) the artwork is the
  background; the system adds the QR code, code and serial number (and, if
  wanted, the holder's photo and name) at the positions set in the admin.

The QR code, code and serial number are the only parts that change from one
ticket to the next: `qr_layout()` gives their position so the bulk print
sheets (printing.py) can draw them as vectors on a shared background.

PNG for phones and e-mail, PDF at the real ticket size (200 × 75 mm).
"""
import base64
import io
import logging
import os
import uuid
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import dateformat, timezone, translation
from django.utils.translation import gettext as _
from PIL import Image, ImageDraw, ImageFont, ImageOps

from core.poster import (
    GOLD,
    GOLD_LIGHT,
    GREY,
    INK,
    RED,
    RED_BRIGHT,
    RED_DEEP,
    WHITE,
    cap_offset,
    draw_tracked,
    poster_font,
    red_background,
    text_width,
    torn_band,
)
from core.utils import hex_to_rgb, text_color_on

from .models import Order, Ticket
from .qr import build_qr_payload, make_qr_image, make_qr_png

logger = logging.getLogger("tickets")

W, H = 2400, 900          # 8:3 landscape
STUB_X = 1700             # perforation; the stub (right part) holds the QR code
BAND_Y = 772              # top of the white torn band (venue)
PAGE_MM = (200, 75)       # printed ticket size

DARK = INK
MUTED = GREY
BORDER = (229, 231, 235)
PERFORATION = (190, 190, 190)

REGULAR_FONTS = [
    "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
BOLD_FONTS = [
    "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@lru_cache(maxsize=2)
def _font_path(bold: bool) -> str | None:
    configured = settings.TICKET_FONT_BOLD if bold else settings.TICKET_FONT_REGULAR
    for path in [configured, *(BOLD_FONTS if bold else REGULAR_FONTS)]:
        if path and os.path.exists(path):
            return path
    return None


@lru_cache(maxsize=128)
def font(size: int, bold: bool = False, family: str | None = None):
    """Poster fonts: family = display (Anton) · black · heavy · bold · semibold · regular (Montserrat).

    `bold` alone picks Montserrat Bold. Falls back to system fonts if the
    bundled files are missing.
    """
    family = family or ("bold" if bold else "regular")
    bundled = poster_font(family, size)
    if bundled is not None:
        return bundled
    path = _font_path(family != "regular")
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)


def _text_width(draw, text, fnt) -> int:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=fnt)
    return right - left


def _wrap(draw, text: str, fnt, max_width: int, max_lines: int) -> list[str]:
    words = (text or "").split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_width(draw, candidate, fnt) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        last = lines[-1]
        while last and _text_width(draw, last + "…", fnt) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _fit_font(draw, text: str, max_width: int, size: int, minimum: int, bold: bool = True, family: str | None = None,
              tracking: float = 0):
    family = family or ("bold" if bold else "regular")
    fnt = font(size, family=family)
    while text_width(draw, text, fnt, tracking) > max_width and size > minimum:
        size -= 2
        fnt = font(size, family=family)
    return fnt


def _cap_height(fnt) -> int:
    _left, top, _right, bottom = fnt.getbbox("H")
    return bottom - top


def _text_at_cap(draw, x: float, cap_top: float, text: str, fnt, fill, tracking: float = 0, center: bool = False) -> float:
    """Draw text so the top of its capitals sits at `cap_top` (boxes line up exactly)."""
    return draw_tracked(draw, (x, cap_top - cap_offset(fnt)), text, fnt, fill, tracking, anchor_center=center)


def _gradient_text(img: Image.Image, x: float, cap_top: float, text: str, fnt, top=GOLD_LIGHT, bottom=GOLD) -> float:
    """Gold text with a light-to-deep vertical gradient, like the poster dates."""
    draw = ImageDraw.Draw(img)
    origin = (round(x), round(cap_top - cap_offset(fnt)))
    left, t, right, b = (round(v) for v in draw.textbbox(origin, text, font=fnt))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text(origin, text, font=fnt, fill=255)
    gradient = Image.linear_gradient("L").resize((max(1, right - left), max(1, b - t)))
    fill = Image.composite(Image.new("RGB", gradient.size, bottom), Image.new("RGB", gradient.size, top), gradient)
    layer = Image.new("RGB", img.size, bottom)
    layer.paste(fill, (left, t))
    img.paste(layer, (0, 0), mask)
    return right - left


def _circle(image: Image.Image, size: int) -> Image.Image:
    image = ImageOps.fit(image.convert("RGB"), (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def _en_date(value, fmt: str) -> str:
    if not value:
        return ""
    with translation.override("en"):
        return dateformat.format(timezone.localtime(value), fmt)


def ticket_context(ticket: Ticket) -> dict:
    """Shared values for e-mails and the ticket image."""
    order = ticket.order
    event = order.event
    tt = ticket.ticket_type
    return {
        "ticket": ticket,
        "order": order,
        "event": event,
        "ticket_type": tt,
        "category": tt.tr("name"),
        "zone": tt.zone_label,
        "benefits": [b.tr("text") for b in tt.benefits.all()],
        "date": _en_date(event.date_start, "D, M j, Y"),
        "doors": _en_date(event.doors_open, "g:i A"),
        "show": _en_date(event.date_start, "g:i A"),
        "color": tt.color or "#C8102E",
    }


# --------------------------------------------------------------------------
# What goes on a ticket
# --------------------------------------------------------------------------
@dataclass
class TicketData:
    holder_name: str
    initials: str
    code: str
    qr_token: str
    reference: str
    ticket_type: object
    event: object
    serial: str = ""
    photo: Image.Image | None = None


def ticket_data(ticket: Ticket) -> TicketData:
    photo = None
    if ticket.holder_photo:
        try:
            with ticket.holder_photo.open("rb") as fh:
                photo = Image.open(fh).copy()
        except (OSError, ValueError):
            logger.warning("Holder photo missing for ticket %s", ticket.code)
    printed = hasattr(ticket.order, "print_batch")
    return TicketData(
        holder_name="" if printed else ticket.holder_name,
        initials=ticket.initials,
        code=ticket.code,
        qr_token=ticket.qr_token,
        reference="" if printed else ticket.order.reference,
        ticket_type=ticket.ticket_type,
        event=ticket.order.event,
        serial=ticket.serial_label,
        photo=photo,
    )


def sample_data(ticket_type) -> TicketData:
    """Fake holder for the admin preview (its QR is valid but matches no ticket)."""
    return TicketData(
        holder_name="Ade Johnson",
        initials="AJ",
        code="NWP-AB12-CD34",
        qr_token=build_qr_payload(uuid.UUID(int=0)),
        reference="NWP-2026-SAMPLE",
        ticket_type=ticket_type,
        event=ticket_type.event,
        serial="00042",
    )


def blank_data(ticket_type) -> TicketData:
    """Everything but the per-ticket parts: background of the bulk print sheets."""
    return TicketData(holder_name="", initials="", code="", qr_token="", reference="",
                      ticket_type=ticket_type, event=ticket_type.event)


def avatar(data: TicketData, size: int, color_hex: str) -> Image.Image:
    """Round holder photo, or initials on the category colour."""
    if data.photo is not None:
        return _circle(data.photo, size)
    base = Image.new("RGB", (size, size), hex_to_rgb(color_hex))
    draw = ImageDraw.Draw(base)
    fnt = font(int(size * 0.38), family="black")
    width = _text_width(draw, data.initials, fnt)
    draw.text(((size - width) / 2, size * 0.29), data.initials, font=fnt, fill=hex_to_rgb(text_color_on(color_hex)))
    return _circle(base, size)


def _qr(token: str, size: int) -> Image.Image:
    return make_qr_image(token, box_size=20, border=2).resize((size, size), Image.Resampling.NEAREST)


def _center_text(draw, text: str, fnt, cx: float, y: float, fill) -> None:
    draw.text((cx - _text_width(draw, text, fnt) / 2, y), text, font=fnt, fill=fill)


# --------------------------------------------------------------------------
# Size: the default design is 2400 × 900; an uploaded design keeps its own
# proportions (2400 px wide), so it is never cropped.
# --------------------------------------------------------------------------
def ticket_size(ticket_type) -> tuple[int, int]:
    if not ticket_type.design_image:
        return W, H
    try:
        width, height = ticket_type.design_image.width, ticket_type.design_image.height
    except (OSError, ValueError):
        return W, H
    if not width or not height:
        return W, H
    return W, int(min(max(W * height / width, 300), 4800))


def ticket_page_mm(ticket_type) -> tuple[float, float]:
    """PDF page of one ticket: 200 mm wide, height from the design's proportions."""
    width, height = ticket_size(ticket_type)
    return PAGE_MM[0], round(PAGE_MM[0] * height / width, 2)


# --------------------------------------------------------------------------
# Per-ticket parts: QR code, code, serial number (shared with printing.py)
# --------------------------------------------------------------------------
@dataclass
class QrLayout:
    qr: tuple[int, int, int]              # x, y, size (QR includes its white quiet zone)
    cx: float                             # centre line of the code and serial number
    code_top: float                       # top of the capitals
    code_size: int
    serial_top: float
    serial_size: int
    box: tuple[int, int, int, int] | None = None   # white card behind (uploaded designs)
    radius: int = 0
    serial_color: tuple = RED


def qr_layout(ticket_type) -> QrLayout:
    if not ticket_type.design_image:
        return QrLayout(qr=(1840, 98, 420), cx=STUB_X + (W - STUB_X) / 2, code_top=540, code_size=40,
                        serial_top=598, serial_size=54)
    width, height = ticket_size(ticket_type)
    qr_size = int(min(width, height) * ticket_type.qr_size / 100)
    pad = max(18, qr_size // 14)
    line = int(qr_size * 0.15)
    box_w, box_h = qr_size + 2 * pad, qr_size + pad + 2 * line + pad // 2
    x0 = int(min(max(width * ticket_type.qr_x / 100 - box_w / 2, 12), width - box_w - 12))
    y0 = int(min(max(height * ticket_type.qr_y / 100 - box_h / 2, 12), height - box_h - 12))
    size = max(18, int(line * 0.55))
    return QrLayout(
        qr=(x0 + pad, y0 + pad, qr_size), cx=x0 + box_w / 2,
        code_top=y0 + pad + qr_size + line * 0.22, code_size=size,
        serial_top=y0 + pad + qr_size + line * 1.18, serial_size=size,
        box=(x0, y0, x0 + box_w, y0 + box_h), radius=pad,
    )


def serial_text(serial: str) -> str:
    return f"{_('No.')} {serial}" if serial else ""


def _draw_per_ticket(img: Image.Image, data: TicketData, layout: QrLayout) -> None:
    draw = ImageDraw.Draw(img)
    if layout.box:
        draw.rounded_rectangle(layout.box, radius=layout.radius, fill=WHITE)
    x, y, size = layout.qr
    img.paste(_qr(data.qr_token, size), (x, y))
    code_font = font(layout.code_size, family="heavy")
    _text_at_cap(draw, layout.cx, layout.code_top, data.code, code_font, INK, tracking=1, center=True)
    if data.serial:
        _text_at_cap(draw, layout.cx, layout.serial_top, serial_text(data.serial),
                     font(layout.serial_size, family="display"), layout.serial_color, tracking=1, center=True)


# --------------------------------------------------------------------------
# Default design: the poster look
# --------------------------------------------------------------------------
def _perforate(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    for y in range(46, H - 40, 30):
        draw.line([(STUB_X + 3, y), (STUB_X + 3, y + 16)], fill=PERFORATION, width=4)
    for cy in (0, H):  # notches, like a real ticket
        draw.ellipse([STUB_X - 30, cy - 30, STUB_X + 30, cy + 30], fill=WHITE)


def _day_boxes(img: Image.Image, event, top: int, bottom: int) -> None:
    """One box per entry day in the stub (DAY 1 · 25 DEC …), for a tick at the door."""
    draw = ImageDraw.Draw(img)
    days = event.entry_days
    x0, x1 = STUB_X + 56, W - 56
    if len(days) > 4:
        _text_at_cap(draw, (x0 + x1) / 2, top + 20, _("1 ENTRY PER DAY"), font(28, family="heavy"), INK, center=True)
        _text_at_cap(draw, (x0 + x1) / 2, top + 70, event.dates_label, font(40, family="display"), INK, center=True)
        return
    gap = 16
    width = (x1 - x0 - gap * (len(days) - 1)) / len(days)
    label_font, date_font = font(22, family="bold"), font(44, family="display")
    for index, day in enumerate(days):
        bx = x0 + index * (width + gap)
        draw.rounded_rectangle([bx, top, bx + width, bottom], radius=16, outline=INK, width=3)
        label = _("DAY %(n)s") % {"n": index + 1} if len(days) > 1 else _("ENTRY")
        cx = bx + width / 2
        _text_at_cap(draw, cx, top + 20, label, label_font, GREY, tracking=3, center=True)
        _text_at_cap(draw, cx, top + 56, event.day_label(day), date_font, INK, center=True)


def _stub(img: Image.Image, data: TicketData) -> None:
    draw = ImageDraw.Draw(img)
    event = data.event
    cx = STUB_X + (W - STUB_X) / 2
    admit = _("ADMIT ONE")
    if event.is_multi_day:
        admit = f"{admit} · {_('%(days)s DAYS') % {'days': event.days}}"
    _text_at_cap(draw, cx, 44, admit, _fit_font(draw, admit, 600, 30, 20, family="heavy", tracking=5), INK,
                 tracking=5, center=True)
    if data.reference:
        _text_at_cap(draw, cx, 670, f"{_('Order')} {data.reference}", font(22, family="semibold"), GREY, center=True)
    _day_boxes(img, event, 712, 858)


def _render_default(data: TicketData) -> Image.Image:
    tt, event = data.ticket_type, data.event
    color = tt.color or "#C8102E"
    img = Image.new("RGB", (W, H), WHITE)
    img.paste(red_background((STUB_X, H), glow=(0.36, 0.36), seed=event.pk or 25), (0, 0))
    torn_band(img, BAND_Y, 0, STUB_X, seed=(event.pk or 3) + 11)
    draw = ImageDraw.Draw(img)
    x = 90

    # Left column: presenter · title · edition box · days · dates ---------------
    y = 70
    if event.presented_by:
        line = event.presented_by.upper()
        _text_at_cap(draw, x, y, line, _fit_font(draw, line, 1020, 28, 18, family="bold", tracking=3), WHITE, tracking=3)
        _text_at_cap(draw, x, y + 42, _("PRESENTS"), font(20, family="semibold"), GOLD_LIGHT, tracking=10)
        y += 96
    else:
        y = 96
    title = event.name.upper()
    title_font = _fit_font(draw, title, 1040, 186, 80, family="black")
    _text_at_cap(draw, x - 4, y, title, title_font, WHITE)
    y += _cap_height(title_font) + 30
    if event.edition:
        edition = event.edition.upper()
        ed_font = _fit_font(draw, edition, 940, 60, 30, family="black")
        box = [x, y, x + text_width(draw, edition, ed_font) + 56, y + _cap_height(ed_font) + 38]
        draw.rectangle([box[0] + 8, box[1] + 8, box[2] + 8, box[3] + 8], fill=RED_DEEP)
        draw.rectangle(box, fill=RED_BRIGHT)
        _gradient_text(img, x + 28, y + 19, edition, ed_font)
        y = box[3] + 34
    if event.is_multi_day:
        tag = _("[ %(days)s DAYS EVENT ]") % {"days": event.days}
        _text_at_cap(draw, x, y, tag, font(28, family="bold"), WHITE, tracking=9)

    date_top = 588
    date_font = _fit_font(draw, event.dates_label, 820, 116, 64, family="display")
    date_w = _gradient_text(img, x, date_top, event.dates_label, date_font)
    tx = x + date_w + 40
    time_font = font(54, family="display")
    _gradient_text(img, tx, date_top + 2, event.time_label, time_font)
    if event.is_multi_day:
        _gradient_text(img, tx, date_top + _cap_height(time_font) + 16, _("DAILY"), time_font)

    # Right column: category panel · holder · note ------------------------------
    rx, rw = 1196, 440
    label_font = font(22, family="semibold")
    name = tt.tr("name").upper()
    name_font = _fit_font(draw, name, rw - 64, 104, 48, family="display")
    zone_font = font(26, family="semibold")
    panel_h = 32 + _cap_height(label_font) + 22 + _cap_height(name_font) + (46 if tt.zone_label else 0) + 34
    holder_h = 150 if data.holder_name else 0
    note_h = 70
    top = int(70 + (BAND_Y - 70 - 40 - panel_h - holder_h - note_h - (40 if holder_h else 0) - 30) / 2)
    draw.rounded_rectangle([rx, top, rx + rw, top + panel_h], radius=18, outline=GOLD, width=4)
    _text_at_cap(draw, rx + 32, top + 32, _("TICKET"), label_font, GOLD_LIGHT, tracking=8)
    name_top = top + 32 + _cap_height(label_font) + 22
    _text_at_cap(draw, rx + 32, name_top, name, name_font, WHITE)
    if tt.zone_label:
        zone = _wrap(draw, tt.zone_label, zone_font, rw - 64, 1)
        _text_at_cap(draw, rx + 32, name_top + _cap_height(name_font) + 24, zone[0] if zone else "", zone_font, WHITE)
    y = top + panel_h + 40
    if data.holder_name:
        av = avatar(data, 128, color)
        ring = 6
        draw.ellipse([rx - ring, y - ring, rx + 128 + ring, y + 128 + ring], fill=WHITE)
        img.paste(av, (rx, y), av)
        hx = rx + 128 + 28
        _text_at_cap(draw, hx, y + 18, _("HOLDER"), label_font, GOLD_LIGHT, tracking=6)
        holder_font = font(36, family="heavy")
        for i, line in enumerate(_wrap(draw, data.holder_name.upper(), holder_font, rx + rw - hx, 2)):
            _text_at_cap(draw, hx, y + 58 + i * 46, line, holder_font, WHITE)
        y += holder_h + 40
    if event.is_multi_day:
        note, sub = _("1 TICKET COVERS %(days)s DAYS") % {"days": event.days}, _("One entry per day")
    else:
        note, sub = _("VALID FOR ONE ENTRY"), _("Keep your QR code private")
    _text_at_cap(draw, rx, y + 20, note, _fit_font(draw, note, rw, 28, 18, family="heavy"), WHITE)
    _text_at_cap(draw, rx, y + 62, sub, font(24, family="semibold"), GOLD_LIGHT)

    # White torn band: venue like the poster footer ----------------------------
    venue = event.venue_name.upper()
    venue_font = _fit_font(draw, venue, 760, 76, 44, family="display")
    band_cap = BAND_Y + 34
    venue_w = _text_at_cap(draw, x, band_cap, venue, venue_font, INK)
    address = f"{event.venue_address}, {event.city}".upper()
    address_x = x + venue_w + 34
    address_font = _fit_font(draw, address, STUB_X - 60 - address_x, 30, 18, family="heavy")
    address_cap = band_cap + (_cap_height(venue_font) - _cap_height(address_font)) / 2
    lines = _wrap(draw, address, address_font, STUB_X - 60 - address_x, 1)
    _text_at_cap(draw, address_x, address_cap, lines[0] if lines else "", address_font, INK)

    _stub(img, data)
    _perforate(img)
    if data.qr_token:
        _draw_per_ticket(img, data, qr_layout(tt))
    return img


# --------------------------------------------------------------------------
# Uploaded artwork + QR / holder added by the system
# --------------------------------------------------------------------------
def _render_on_artwork(data: TicketData) -> Image.Image:
    tt = data.ticket_type
    with tt.design_image.open("rb") as fh:
        art = ImageOps.exif_transpose(Image.open(fh)).convert("RGB")
    width, height = ticket_size(tt)
    img = art.resize((width, height), Image.Resampling.LANCZOS)

    # Holder: photo + name + category on a light panel.
    if tt.holder_overlay != "hidden" and data.holder_name:
        draw = ImageDraw.Draw(img)
        color = tt.color or "#C8102E"
        name_font, detail_font = font(46, family="heavy"), font(30)
        detail = tt.tr("name") + (f" · {tt.zone_label}" if tt.zone_label else "")
        text_w = min(760, max(_text_width(draw, data.holder_name, name_font), _text_width(draw, detail, detail_font)))
        panel_w, panel_h = 30 + 150 + 28 + text_w + 36, 190
        px = 40
        py = 40 if tt.holder_overlay == "top_left" else height - 40 - panel_h
        panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle([px, py, px + panel_w, py + panel_h], radius=28, fill=(255, 255, 255, 236))
        img = Image.alpha_composite(img.convert("RGBA"), panel).convert("RGB")
        draw = ImageDraw.Draw(img)
        av = avatar(data, 150, color)
        img.paste(av, (px + 30, py + 20), av)
        tx = px + 30 + 150 + 28
        name = _wrap(draw, data.holder_name, name_font, text_w, 1)
        draw.text((tx, py + 42), name[0] if name else "", font=name_font, fill=DARK)
        detail_lines = _wrap(draw, detail, detail_font, text_w, 1)
        draw.text((tx, py + 106), detail_lines[0] if detail_lines else "", font=detail_font, fill=MUTED)

    layout = qr_layout(tt)
    if data.qr_token:
        _draw_per_ticket(img, data, layout)
    else:  # print background: the white card only, the rest is drawn on top
        ImageDraw.Draw(img).rounded_rectangle(layout.box, radius=layout.radius, fill=WHITE)
    return img


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def render_ticket_image(data: TicketData) -> Image.Image:
    """Full ticket; with `blank_data()` the shared background of the print sheets."""
    if data.ticket_type.design_image:
        try:
            return _render_on_artwork(data)
        except (OSError, ValueError):
            logger.exception("Ticket design unreadable for %s — using the default design", data.ticket_type)
    return _render_default(data)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_ticket_png(ticket: Ticket) -> bytes:
    return _png_bytes(render_ticket_image(ticket_data(ticket)))


def render_preview_png(ticket_type, width: int = 1200) -> bytes:
    """Sample ticket for the admin (fake holder)."""
    image = render_ticket_image(sample_data(ticket_type))
    image = image.resize((width, int(width * image.height / image.width)), Image.Resampling.LANCZOS)
    return _png_bytes(image)


# --------------------------------------------------------------------------
# PDF: one page per ticket, at the real ticket size (200 mm wide)
# --------------------------------------------------------------------------
def _ticket_image(ticket: Ticket) -> Image.Image:
    if ticket.ticket_png:
        with ticket.ticket_png.open("rb") as fh:
            return Image.open(fh).convert("RGB")
    return render_ticket_image(ticket_data(ticket))


def _weasyprint_pdf(images: list[Image.Image], size_mm: tuple[float, float]) -> bytes:
    from weasyprint import HTML

    pages = ["data:image/png;base64," + base64.b64encode(_png_bytes(image)).decode("ascii") for image in images]
    html = render_to_string("tickets/ticket_pdf.html", {"pages": pages, "width_mm": size_mm[0], "height_mm": size_mm[1]})
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


def _pillow_pdf(images: list[Image.Image], size_mm: tuple[float, float]) -> bytes:
    buffer = io.BytesIO()
    dpi = images[0].width / (size_mm[0] / 25.4)
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:], resolution=dpi)
    return buffer.getvalue()


def images_to_pdf(images: list[Image.Image], size_mm: tuple[float, float]) -> bytes:
    """One image per page, each page exactly `size_mm` (tickets, staff cards)."""
    if settings.TICKET_PDF_ENGINE == "weasyprint":
        try:
            return _weasyprint_pdf(images, size_mm)  # lossless PNG inside the PDF
        except Exception:  # missing system libs…
            logger.exception("WeasyPrint failed — falling back to Pillow PDF")
    return _pillow_pdf(images, size_mm)


def render_tickets_pdf(tickets: list[Ticket]) -> bytes:
    if not tickets:
        raise ValueError("No tickets to render")
    return images_to_pdf([_ticket_image(ticket) for ticket in tickets], ticket_page_mm(tickets[0].ticket_type))


# --------------------------------------------------------------------------
# Persisted files
# --------------------------------------------------------------------------
def _replace(field, name: str, data: bytes) -> None:
    if field:
        try:
            field.storage.delete(field.name)
        except OSError:
            pass
    field.save(name, ContentFile(data), save=False)


def ensure_ticket_files(ticket_id: int, force: bool = False) -> Ticket:
    """Generate QR, PNG and PDF for a ticket (once, unless `force`)."""
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().get(pk=ticket_id)
        if not force and ticket.qr_image and ticket.ticket_png and ticket.ticket_pdf:
            return ticket
        with translation.override(ticket.order.language or "en"):
            png = render_ticket_png(ticket)
            _replace(ticket.qr_image, f"{ticket.uuid.hex}.png", make_qr_png(ticket.qr_token))
            _replace(ticket.ticket_png, f"{ticket.uuid.hex}.png", png)
            _replace(ticket.ticket_pdf, f"{ticket.uuid.hex}.pdf", render_tickets_pdf([ticket]))
        ticket.save(update_fields=["qr_image", "ticket_png", "ticket_pdf"])
    return ticket


def ensure_order_pdf(order_id: int, force: bool = False) -> Order:
    """All tickets of an order in one multi-page PDF."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.tickets_pdf and not force:
            return order
        tickets = [ensure_ticket_files(t.pk) for t in order.tickets.exclude(status=Ticket.Status.CANCELLED)]
        if not tickets:
            return order
        with translation.override(order.language or "en"):
            _replace(order.tickets_pdf, f"{order.reference}.pdf", render_tickets_pdf(tickets))
        order.save(update_fields=["tickets_pdf"])
    return order
