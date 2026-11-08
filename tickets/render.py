"""Ticket rendering: PNG 1080×1920 (Pillow) and PDF (WeasyPrint, Pillow fallback)."""
import base64
import io
import logging
import os
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
from .qr import make_qr_image, make_qr_png

logger = logging.getLogger("tickets")

W, H = 1080, 1920
WHITE = (255, 255, 255)
DARK = (17, 24, 39)
MUTED = (75, 85, 99)
BORDER = (229, 231, 235)
GREEN = (22, 163, 74)

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


def _circle(image: Image.Image, size: int) -> Image.Image:
    image = ImageOps.fit(image.convert("RGB"), (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def holder_avatar(ticket: Ticket, size: int, color_hex: str) -> Image.Image:
    """Round holder photo, or initials on the category colour."""
    if ticket.holder_photo:
        try:
            with ticket.holder_photo.open("rb") as fh:
                return _circle(Image.open(fh).copy(), size)
        except (OSError, ValueError):
            logger.warning("Holder photo missing for ticket %s", ticket.code)
    base = Image.new("RGB", (size, size), hex_to_rgb(color_hex))
    draw = ImageDraw.Draw(base)
    fnt = font(int(size * 0.38), True)
    initials = ticket.initials
    w = _text_width(draw, initials, fnt)
    draw.text(((size - w) / 2, size * 0.29), initials, font=fnt, fill=hex_to_rgb(text_color_on(color_hex)))
    return _circle(base, size)


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
# PNG
# --------------------------------------------------------------------------
def render_ticket_png(ticket: Ticket) -> bytes:
    """Portrait 1080×1920 ticket optimised for phone screens."""
    ctx = ticket_context(ticket)
    site = SiteSettings.load()
    event = ctx["event"]
    color = ctx["color"]
    band = hex_to_rgb(color)
    band_text = hex_to_rgb(text_color_on(color))

    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # Header band ------------------------------------------------------------
    draw.rectangle([0, 0, W, 262], fill=band)
    title_max = W - 120
    if site.logo:
        try:
            with site.logo.open("rb") as fh:
                logo = Image.open(fh).convert("RGBA")
                logo.thumbnail((150, 150))
                img.paste(logo, (W - 60 - logo.width, 50), logo)
                title_max = W - 120 - logo.width - 30
        except (OSError, ValueError):
            pass
    title_font = font(72, True)
    title = event.name.upper()
    while _text_width(draw, title, title_font) > title_max and title_font.size > 40:
        title_font = font(title_font.size - 4, True)
    draw.text((60, 48), title, font=title_font, fill=band_text)
    draw.text((60, 134), event.edition, font=font(40), fill=band_text)

    pill_font = font(34, True)
    pill_text = ctx["category"].upper()
    pill_w = _text_width(draw, pill_text, pill_font) + 56
    draw.rounded_rectangle([60, 192, 60 + pill_w, 244], radius=26, fill=WHITE)
    draw.text((88, 200), pill_text, font=pill_font, fill=DARK)

    # Holder -------------------------------------------------------------------
    avatar = holder_avatar(ticket, 220, color)
    img.paste(avatar, (60, 296), avatar)
    tx = 310
    draw.text((tx, 306), _("TICKET HOLDER"), font=font(28, True), fill=MUTED)
    y = 346
    for line in _wrap(draw, ticket.holder_name, font(52, True), W - tx - 60, 2):
        draw.text((tx, y), line, font=font(52, True), fill=DARK)
        y += 62
    detail = ctx["category"] + (f" · {ctx['zone']}" if ctx["zone"] else "")
    for line in _wrap(draw, detail, font(32), W - tx - 60, 2):
        draw.text((tx, y + 6), line, font=font(32), fill=DARK)
        y += 40

    draw.line([(60, 548), (W - 60, 548)], fill=BORDER, width=3)

    # Event info ---------------------------------------------------------------
    label_font, value_font = font(26, True), font(38, True)
    draw.text((60, 574), _("DATE"), font=label_font, fill=MUTED)
    draw.text((60, 608), ctx["date"], font=value_font, fill=DARK)
    draw.text((60, 676), _("DOORS · SHOW"), font=label_font, fill=MUTED)
    times = " · ".join(t for t in (ctx["doors"], ctx["show"]) if t)
    draw.text((60, 710), times, font=value_font, fill=DARK)

    vx, vw = 580, W - 580 - 60
    draw.text((vx, 574), _("VENUE"), font=label_font, fill=MUTED)
    vy = 608
    for line in _wrap(draw, event.venue_name, font(34, True), vw, 2):
        draw.text((vx, vy), line, font=font(34, True), fill=DARK)
        vy += 42
    for line in _wrap(draw, f"{event.venue_address}, {event.city}", font(26), vw, 3):
        draw.text((vx, vy + 4), line, font=font(26), fill=MUTED)
        vy += 34

    # Benefits -----------------------------------------------------------------
    benefits = ctx["benefits"][:4]
    if benefits:
        draw.text((60, 800), _("INCLUDED"), font=label_font, fill=MUTED)
        by = 840
        for text in benefits:
            _draw_check(draw, 62, by + 4, 26, GREEN)
            line = _wrap(draw, text, font(30), W - 170, 1)
            draw.text((108, by), line[0] if line else "", font=font(30), fill=DARK)
            by += 44

    # QR zone: black on white for a guaranteed read ----------------------------
    qr_size = 640
    qx, qy = (W - qr_size) // 2, 1052
    draw.rounded_rectangle([qx - 24, qy - 24, qx + qr_size + 24, qy + qr_size + 24], radius=24, fill=WHITE, outline=BORDER, width=3)
    qr = make_qr_image(ticket.qr_token, box_size=20, border=2).resize((qr_size, qr_size), Image.Resampling.NEAREST)
    img.paste(qr, (qx, qy))

    code_font = font(64, True)
    cw = _text_width(draw, ticket.code, code_font)
    draw.text(((W - cw) / 2, qy + qr_size + 44), ticket.code, font=code_font, fill=DARK)

    footer_font = font(28)
    ref = f"{_('Order')} {ctx['order'].reference}"
    fw = _text_width(draw, ref, footer_font)
    draw.text(((W - fw) / 2, 1830), ref, font=footer_font, fill=MUTED)
    notice = _("Non transferable — 1 entry only")
    nw = _text_width(draw, notice, font(28, True))
    draw.text(((W - nw) / 2, 1868), notice, font=font(28, True), fill=DARK)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def _png_data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _bytes_data_uri(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _weasyprint_pdf(tickets: list[Ticket]) -> bytes:
    from weasyprint import HTML

    site = SiteSettings.load()
    logo_uri = ""
    if site.logo:
        try:
            with site.logo.open("rb") as fh:
                logo_uri = _bytes_data_uri(fh.read(), "image/png")
        except OSError:
            logo_uri = ""
    pages = []
    for ticket in tickets:
        ctx = ticket_context(ticket)
        ctx["avatar_uri"] = _png_data_uri(holder_avatar(ticket, 300, ctx["color"]))
        ctx["qr_uri"] = _bytes_data_uri(make_qr_png(ticket.qr_token, size=800))
        ctx["band_text"] = text_color_on(ctx["color"])
        pages.append(ctx)
    html = render_to_string("tickets/ticket_pdf.html", {"pages": pages, "logo_uri": logo_uri, "site": site})
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


def _pillow_pdf(tickets: list[Ticket]) -> bytes:
    images = []
    for ticket in tickets:
        if ticket.ticket_png:
            with ticket.ticket_png.open("rb") as fh:
                images.append(Image.open(fh).convert("RGB"))
        else:
            images.append(Image.open(io.BytesIO(render_ticket_png(ticket))).convert("RGB"))
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:], resolution=200)
    return buffer.getvalue()


def render_tickets_pdf(tickets: list[Ticket]) -> bytes:
    """One page per ticket (A6 portrait)."""
    if not tickets:
        raise ValueError("No tickets to render")
    if settings.TICKET_PDF_ENGINE == "weasyprint":
        try:
            return _weasyprint_pdf(tickets)
        except Exception:  # missing system libs, font issue…
            logger.exception("WeasyPrint failed — falling back to Pillow PDF")
    return _pillow_pdf(tickets)


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
