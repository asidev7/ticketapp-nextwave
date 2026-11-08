"""Ticket rendering — landscape concert ticket with a stub (2400 × 900 px, 8:3).

* With an uploaded design (TicketType.design_image) the artwork is the
  background; the system adds the QR code and ticket code (and, if wanted,
  the holder's photo and name) at the positions set in the admin.
* Without a design, the system draws a clean default ticket.

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

from core.models import SiteSettings
from core.utils import hex_to_rgb, text_color_on

from .models import Order, Ticket
from .qr import build_qr_payload, make_qr_image, make_qr_png

logger = logging.getLogger("tickets")

W, H = 2400, 900          # 8:3 landscape
STUB_X = 1700             # perforation; the stub (right part) holds the QR code
PAGE_MM = (200, 75)       # printed ticket size

WHITE = (255, 255, 255)
DARK = (17, 24, 39)
MUTED = (75, 85, 99)
BORDER = (229, 231, 235)
GREEN = (22, 163, 74)
PAGE_BG = (244, 246, 250)
PERFORATION = (156, 163, 175)

REGULAR_FONTS = [
    "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/arial.ttf",
    "/usr/share/fonts/TTF/arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    # Liberation Sans is metric-compatible with Arial (Docker image).
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
BOLD_FONTS = [
    "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/arialbd.ttf",
    "/usr/share/fonts/TTF/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
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


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    path = _font_path(bold)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


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


def _fit_font(draw, text: str, max_width: int, size: int, minimum: int, bold: bool = True):
    fnt = font(size, bold)
    while _text_width(draw, text, fnt) > max_width and size > minimum:
        size -= 2
        fnt = font(size, bold)
    return fnt


def _circle(image: Image.Image, size: int) -> Image.Image:
    image = ImageOps.fit(image.convert("RGB"), (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def _draw_check(draw, x: int, y: int, size: int, color) -> None:
    draw.line(
        [(x, y + size * 0.55), (x + size * 0.38, y + size * 0.9), (x + size, y + size * 0.1)],
        fill=color, width=max(4, size // 6), joint="curve",
    )


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
        "color": tt.color or "#0A3D91",
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
    photo: Image.Image | None = None


def ticket_data(ticket: Ticket) -> TicketData:
    photo = None
    if ticket.holder_photo:
        try:
            with ticket.holder_photo.open("rb") as fh:
                photo = Image.open(fh).copy()
        except (OSError, ValueError):
            logger.warning("Holder photo missing for ticket %s", ticket.code)
    return TicketData(
        holder_name=ticket.holder_name,
        initials=ticket.initials,
        code=ticket.code,
        qr_token=ticket.qr_token,
        reference=ticket.order.reference,
        ticket_type=ticket.ticket_type,
        event=ticket.order.event,
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
    )


def avatar(data: TicketData, size: int, color_hex: str) -> Image.Image:
    """Round holder photo, or initials on the category colour."""
    if data.photo is not None:
        return _circle(data.photo, size)
    base = Image.new("RGB", (size, size), hex_to_rgb(color_hex))
    draw = ImageDraw.Draw(base)
    fnt = font(int(size * 0.38), True)
    width = _text_width(draw, data.initials, fnt)
    draw.text(((size - width) / 2, size * 0.29), data.initials, font=fnt, fill=hex_to_rgb(text_color_on(color_hex)))
    return _circle(base, size)


def _qr(token: str, size: int) -> Image.Image:
    return make_qr_image(token, box_size=20, border=2).resize((size, size), Image.Resampling.NEAREST)


def _center_text(draw, text: str, fnt, cx: float, y: float, fill) -> None:
    draw.text((cx - _text_width(draw, text, fnt) / 2, y), text, font=fnt, fill=fill)


# --------------------------------------------------------------------------
# Default design (no artwork uploaded)
# --------------------------------------------------------------------------
def _perforate(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    for y in range(44, H - 44, 32):
        draw.line([(STUB_X, y), (STUB_X, y + 18)], fill=PERFORATION, width=4)
    for cy in (0, H):  # notches, like a real ticket
        draw.ellipse([STUB_X - 36, cy - 36, STUB_X + 36, cy + 36], fill=PAGE_BG)


def _draw_stub(img: Image.Image, data: TicketData) -> None:
    draw = ImageDraw.Draw(img)
    cx = STUB_X + (W - STUB_X) / 2
    _center_text(draw, _("ADMIT ONE"), font(30, True), cx, 44, MUTED)
    size = 500
    x0, y0 = int(cx - size / 2), 104
    draw.rounded_rectangle([x0 - 20, y0 - 20, x0 + size + 20, y0 + size + 20], radius=24, fill=WHITE, outline=BORDER, width=3)
    img.paste(_qr(data.qr_token, size), (x0, y0))
    _center_text(draw, data.code, font(50, True), cx, y0 + size + 42, DARK)
    _center_text(draw, _("Non transferable — 1 entry only"), font(24, True), cx, y0 + size + 112, DARK)
    _center_text(draw, f"{_('Order')} {data.reference}", font(24), cx, y0 + size + 152, MUTED)


def _render_default(data: TicketData) -> Image.Image:
    tt, event = data.ticket_type, data.event
    site = SiteSettings.load()
    color = tt.color or "#0A3D91"
    band, on_band = hex_to_rgb(color), hex_to_rgb(text_color_on(color))
    category = tt.tr("name")

    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # Header band ------------------------------------------------------------
    draw.rectangle([0, 0, STUB_X, 240], fill=band)
    x = 60
    if site.logo:
        try:
            with site.logo.open("rb") as fh:
                logo = Image.open(fh).convert("RGBA")
                logo.thumbnail((130, 130))
                img.paste(logo, (60, (240 - logo.height) // 2), logo)
                x = 60 + logo.width + 32
        except (OSError, ValueError):
            pass
    pill_font = font(40, True)
    pill_text = category.upper()
    pill_w = _text_width(draw, pill_text, pill_font) + 64
    draw.rounded_rectangle([STUB_X - 60 - pill_w, 84, STUB_X - 60, 156], radius=36, fill=WHITE)
    draw.text((STUB_X - 60 - pill_w + 32, 96), pill_text, font=pill_font, fill=DARK)
    title = event.name.upper()
    title_font = _fit_font(draw, title, STUB_X - x - pill_w - 120, 80, 44)
    draw.text((x, 54), title, font=title_font, fill=on_band)
    draw.text((x, 150), event.edition, font=font(38), fill=on_band)

    # Holder -------------------------------------------------------------------
    av = avatar(data, 210, color)
    img.paste(av, (60, 284), av)
    tx = 310
    draw.text((tx, 296), _("TICKET HOLDER"), font=font(26, True), fill=MUTED)
    name = _wrap(draw, data.holder_name, font(56, True), STUB_X - tx - 60, 1)
    draw.text((tx, 334), name[0] if name else "", font=font(56, True), fill=DARK)
    detail = category + (f" · {tt.zone_label}" if tt.zone_label else "")
    detail_lines = _wrap(draw, detail, font(34), STUB_X - tx - 60, 1)
    draw.text((tx, 412), detail_lines[0] if detail_lines else "", font=font(34), fill=DARK)

    draw.line([(60, 536), (STUB_X - 60, 536)], fill=BORDER, width=3)

    # Event info: date · doors/show · venue -------------------------------------
    label, value = font(24, True), font(36, True)
    draw.text((60, 562), _("DATE"), font=label, fill=MUTED)
    draw.text((60, 596), _en_date(event.date_start, "D, M j, Y"), font=value, fill=DARK)
    draw.text((580, 562), _("DOORS · SHOW"), font=label, fill=MUTED)
    times = " · ".join(t for t in (_en_date(event.doors_open, "g:i A"), _en_date(event.date_start, "g:i A")) if t)
    draw.text((580, 596), times, font=value, fill=DARK)
    vx, vw = 1040, STUB_X - 1040 - 60
    draw.text((vx, 562), _("VENUE"), font=label, fill=MUTED)
    venue = _wrap(draw, event.venue_name, font(34, True), vw, 1)
    draw.text((vx, 596), venue[0] if venue else "", font=font(34, True), fill=DARK)
    vy = 640
    for line in _wrap(draw, f"{event.venue_address}, {event.city}", font(26), vw, 2):
        draw.text((vx, vy), line, font=font(26), fill=MUTED)
        vy += 32

    # Benefits on one line -------------------------------------------------------
    bx, by = 60, 760
    benefit_font = font(28)
    for benefit in [b.tr("text") for b in tt.benefits.all()][:4]:
        width = 40 + _text_width(draw, benefit, benefit_font)
        if bx + width > STUB_X - 60:
            break
        _draw_check(draw, bx, by + 4, 26, GREEN)
        draw.text((bx + 40, by), benefit, font=benefit_font, fill=DARK)
        bx += width + 44

    draw.rectangle([0, H - 26, W, H], fill=band)
    _draw_stub(img, data)
    _perforate(img)
    return img


# --------------------------------------------------------------------------
# Uploaded artwork + QR / holder added by the system
# --------------------------------------------------------------------------
def _render_on_artwork(data: TicketData) -> Image.Image:
    tt = data.ticket_type
    with tt.design_image.open("rb") as fh:
        art = ImageOps.exif_transpose(Image.open(fh)).convert("RGB")
    img = ImageOps.fit(art, (W, H), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)

    # QR card: white box so it always scans, whatever the artwork behind it.
    qr_size = int(H * tt.qr_size / 100)
    pad = max(18, qr_size // 14)
    code_h = int(qr_size * 0.16)
    box_w, box_h = qr_size + 2 * pad, qr_size + 2 * pad + code_h
    x0 = int(min(max(W * tt.qr_x / 100 - box_w / 2, 12), W - box_w - 12))
    y0 = int(min(max(H * tt.qr_y / 100 - box_h / 2, 12), H - box_h - 12))
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=pad, fill=WHITE)
    img.paste(_qr(data.qr_token, qr_size), (x0 + pad, y0 + pad))
    code_font = _fit_font(draw, data.code, qr_size, max(24, int(code_h * 0.62)), 18)
    _center_text(draw, data.code, code_font, x0 + box_w / 2, y0 + pad + qr_size + (code_h - code_font.size) / 2 - 2, DARK)

    # Holder: photo + name + category on a light panel.
    if tt.holder_overlay != "hidden":
        color = tt.color or "#0A3D91"
        name_font, detail_font = font(46, True), font(30)
        detail = tt.tr("name") + (f" · {tt.zone_label}" if tt.zone_label else "")
        text_w = min(760, max(_text_width(draw, data.holder_name, name_font), _text_width(draw, detail, detail_font)))
        panel_w, panel_h = 30 + 150 + 28 + text_w + 36, 190
        px = 40
        py = 40 if tt.holder_overlay == "top_left" else H - 40 - panel_h
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
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
    return img


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def render_ticket_image(data: TicketData) -> Image.Image:
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
    image = image.resize((width, int(width * H / W)), Image.Resampling.LANCZOS)
    return _png_bytes(image)


# --------------------------------------------------------------------------
# PDF: one page per ticket, at the real ticket size (200 × 75 mm)
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
    return images_to_pdf([_ticket_image(ticket) for ticket in tickets], PAGE_MM)


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
