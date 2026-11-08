"""QR code that opens the Buy Ticket section — for posters and flyers.

It encodes the short, stable URL ``{SITE_URL}/buy/``, which redirects to the
ticket section. The target can change later without reprinting anything.
Only admins can download it (see SiteSettingsAdmin).
"""
import io

import qrcode
from django.conf import settings
from django.urls import reverse
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.svg import SvgPathImage


def buy_page_url() -> str:
    return settings.SITE_URL + reverse("core:buy")


def _qr(box_size: int = 10) -> qrcode.QRCode:
    # High error correction: still scans on a creased or partly covered poster.
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=box_size, border=4)
    qr.add_data(buy_page_url())
    qr.make(fit=True)
    return qr


def buy_qr_png(size: int = 1200) -> bytes:
    """Black on white PNG, `size` pixels wide (1200 px ≈ 10 cm at 300 dpi)."""
    qr = _qr()
    modules = qr.modules_count + 2 * qr.border
    qr.box_size = max(1, size // modules)
    image = qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, dpi=(300, 300))
    return buffer.getvalue()


def buy_qr_svg() -> bytes:
    """Vector version for printers (scales to any size)."""
    return _qr().make_image(image_factory=SvgPathImage).to_string(encoding="unicode").encode("utf-8")
