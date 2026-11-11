"""Print sheets for tickets printed in bulk.

Each ticket type's design is rendered once (everything but the QR code, code
and serial number) and shared by every ticket of the sheet; the per-ticket
parts are added on top (sharp QR code, real text): a small PDF even for
hundreds of tickets.

* "a4": A4 sheets with N tickets per page (4 or 5…), as large as the page
  allows for the design's proportions, with crop marks.
* "ticket": one ticket per page at the real ticket size (200 mm wide), for a
  print shop that lays out the sheets itself.
"""
import base64
import io
import logging
import math
import tempfile
from pathlib import Path

import qrcode
from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import translation
from PIL import Image
from qrcode.constants import ERROR_CORRECT_H

from core.poster import FONT_DIR

from .models import PrintBatch, Ticket
from .render import (
    _replace,
    blank_data,
    font,
    qr_layout,
    render_ticket_image,
    serial_text,
    ticket_data,
    ticket_page_mm,
    ticket_size,
)

logger = logging.getLogger("tickets")

A4_MM = (210, 297)
MARGIN_MM = 12  # room for the crop marks


def sheet_grid(per_page: int, ratio: float) -> dict:
    """Best A4 layout for `per_page` tickets of proportions `ratio` (height / width):
    portrait or landscape sheet, columns × rows, largest possible tickets."""
    best = None
    for page_w, page_h in (A4_MM, A4_MM[::-1]):
        avail_w, avail_h = page_w - 2 * MARGIN_MM, page_h - 2 * MARGIN_MM
        for cols in range(1, per_page + 1):
            rows = math.ceil(per_page / cols)
            width = min(avail_w / cols, avail_h / rows / ratio)
            if best is None or width > best["ticket_w"] + 0.01:
                best = {"page_w": page_w, "page_h": page_h, "cols": cols, "rows": rows, "ticket_w": width}
    best["ticket_w"] = math.floor(best["ticket_w"] * 10) / 10
    best["ticket_h"] = round(best["ticket_w"] * ratio, 2)
    return best


def qr_data_uri(data: str) -> str:
    """QR code as a tiny 1-bit PNG (one pixel per module, 2-module quiet zone).

    Printed without smoothing (image-rendering: pixelated), so it is as sharp
    as a vector at any size, and much faster to lay out than SVG paths.
    """
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=2, box_size=1)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image().get_image().convert("1")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _css_top(cap_top: float, family: str, size: int) -> float:
    """Top of a `line-height: 1` CSS box whose capitals start at `cap_top` (same as Pillow)."""
    fnt = font(size, family=family)
    ascent, descent = fnt.getmetrics()
    return cap_top - fnt.getbbox("H")[1] - (size - ascent - descent) / 2


def _type_style(ticket_type, physical: bool) -> dict:
    """Positions (% of the ticket) and font sizes (em of 1 design pixel) of the per-ticket parts."""
    width, height = ticket_size(ticket_type, physical)
    layout = qr_layout(ticket_type, physical)
    x, y, size = layout.qr
    half = min(330, width / 2)
    text = {"left": 100 * (layout.cx - half) / width, "width": 100 * 2 * half / width}
    return {
        "qr": {"left": 100 * x / width, "top": 100 * y / height, "width": 100 * size / width, "height": 100 * size / height},
        "code": {**text, "top": 100 * _css_top(layout.code_top, "heavy", layout.code_size) / height, "size": layout.code_size},
        "serial": {**text, "top": 100 * _css_top(layout.serial_top, "display", layout.serial_size) / height,
                   "size": layout.serial_size, "color": "#%02X%02X%02X" % layout.serial_color},
    }


def _html(tickets: list[Ticket], paper: str, per_page: int, workdir: Path) -> str:
    """All tickets must share one ticket type and one channel (one design, one size)."""
    tt, physical = tickets[0].ticket_type, tickets[0].is_physical
    background = workdir / f"type-{tt.pk}.jpg"
    render_ticket_image(blank_data(tt, physical)).save(background, format="JPEG", quality=94, subsampling=0)
    style = {"pk": tt.pk, "background": background.as_uri(), **_type_style(tt, physical)}
    items = [{"qr": qr_data_uri(t.qr_token), "code": t.code, "serial": serial_text(t.serial_label)} for t in tickets]

    width_px, height_px = ticket_size(tt, physical)
    if paper == PrintBatch.Paper.A4:
        grid = sheet_grid(per_page, height_px / width_px)
    else:
        page_w, page_h = ticket_page_mm(tt, physical)
        grid = {"page_w": page_w, "page_h": page_h, "cols": 1, "rows": 1, "ticket_w": page_w, "ticket_h": page_h}
    cols, rows = grid["cols"], grid["rows"]
    width, height = grid["ticket_w"], grid["ticket_h"]
    left0 = round((grid["page_w"] - cols * width) / 2, 2)
    top0 = round((grid["page_h"] - rows * height) / 2, 2)
    slots = per_page if paper == PrintBatch.Paper.A4 else 1

    pages = []
    for start in range(0, len(items), slots):
        chunk = items[start : start + slots]
        placed = []
        for index, item in enumerate(chunk):
            row, col = divmod(index, cols)
            placed.append((round(left0 + col * width, 2), round(top0 + row * height, 2), item))
        marks = []
        if paper == PrintBatch.Paper.A4:  # crop marks in the margins, on every cut line
            used_rows = math.ceil(len(chunk) / cols)
            bottom = round(top0 + used_rows * height, 2)
            right = round(left0 + cols * width, 2)
            for r in range(used_rows + 1):
                y = round(top0 + r * height, 2)
                marks += [("h", f"top:{y}mm;left:{left0 - 10}mm"), ("h", f"top:{y}mm;left:{right + 2}mm")]
            for c in range(cols + 1):
                x = round(left0 + c * width, 2)
                marks += [("v", f"top:{top0 - 10}mm;left:{x}mm"), ("v", f"top:{bottom + 2}mm;left:{x}mm")]
        pages.append({"tickets": placed, "marks": marks})
    return render_to_string("tickets/print_sheet.html", {
        "pages": pages,
        "type": style,
        "page_w": grid["page_w"], "page_h": grid["page_h"],
        "ticket_w": width, "ticket_h": height,
        "unit_mm": width / width_px,  # 1 design pixel in mm
        "font_dir": FONT_DIR.as_uri(),
    })


def _pillow_sheets(tickets: list[Ticket], paper: str, per_page: int) -> bytes:
    """Fallback without WeasyPrint: raster pages (150 dpi), written page by page."""
    dpi = 150
    px = lambda mm: round(mm / 25.4 * dpi)  # noqa: E731
    tt, physical = tickets[0].ticket_type, tickets[0].is_physical
    width_px, height_px = ticket_size(tt, physical)
    if paper == PrintBatch.Paper.A4:
        grid = sheet_grid(per_page, height_px / width_px)
    else:
        page_w, page_h = ticket_page_mm(tt, physical)
        grid = {"page_w": page_w, "page_h": page_h, "cols": 1, "rows": 1, "ticket_w": page_w, "ticket_h": page_h}
        per_page = 1
    size = (px(grid["ticket_w"]), px(grid["ticket_h"]))
    left0 = px((grid["page_w"] - grid["cols"] * grid["ticket_w"]) / 2)
    top0 = px((grid["page_h"] - grid["rows"] * grid["ticket_h"]) / 2)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        for start in range(0, len(tickets), per_page):
            page = Image.new("RGB", (px(grid["page_w"]), px(grid["page_h"])), "white")
            for index, ticket in enumerate(tickets[start : start + per_page]):
                row, col = divmod(index, grid["cols"])
                image = render_ticket_image(ticket_data(ticket)).resize(size, Image.Resampling.LANCZOS)
                page.paste(image, (left0 + col * size[0], top0 + row * size[1]))
            page.save(tmp.name, format="PDF", resolution=dpi, append=start > 0)
        return Path(tmp.name).read_bytes()


def render_print_pdf(tickets: list[Ticket], paper: str = "a4", per_page: int = 4) -> bytes:
    """Print PDF for tickets of one ticket type and one channel (online or printed)."""
    if not tickets:
        raise ValueError("No tickets to print")
    if len({(t.ticket_type_id, t.channel) for t in tickets}) > 1:
        raise ValueError("Tickets of one ticket type and one channel per PDF")
    if settings.TICKET_PDF_ENGINE == "weasyprint":
        try:
            from weasyprint import HTML

            with tempfile.TemporaryDirectory() as workdir:
                html = _html(tickets, paper, per_page, Path(workdir))
                return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
        except Exception:  # missing system libs…
            logger.exception("WeasyPrint failed — falling back to Pillow print sheets")
    return _pillow_sheets(tickets, paper, per_page)


def batch_tickets(batch: PrintBatch) -> list[Ticket]:
    return list(
        batch.order.tickets.exclude(status=Ticket.Status.CANCELLED)
        .select_related("ticket_type__event", "order__event")
        .order_by("serial")
    )


def build_batch_files(batch_id: int) -> PrintBatch:
    """Build (or rebuild) the print PDF of a batch, in its paper format."""
    batch = PrintBatch.objects.select_related("order", "ticket_type").get(pk=batch_id)
    PrintBatch.objects.filter(pk=batch.pk).update(status=PrintBatch.Status.GENERATING)
    tickets = batch_tickets(batch)
    try:
        with translation.override(settings.LANGUAGE_CODE):
            pdf = render_print_pdf(tickets, batch.paper, batch.per_page)
    except Exception:
        PrintBatch.objects.filter(pk=batch.pk).update(status=PrintBatch.Status.FAILED)
        raise
    with transaction.atomic():
        batch = PrintBatch.objects.select_for_update().get(pk=batch.pk)
        suffix = f"{batch.per_page}-per-a4" if batch.paper == PrintBatch.Paper.A4 else "ticket-size"
        _replace(batch.pdf, f"tickets-{batch.first_serial:05d}-{batch.last_serial:05d}-{suffix}.pdf", pdf)
        batch.status = PrintBatch.Status.READY
        batch.save(update_fields=["pdf", "status"])
    return batch
