"""Signed QR codes.

QR content: ``NWP:<ticket_uuid>:<signature>`` where
``signature = HMAC-SHA256(QR_SECRET_KEY, uuid)`` truncated to 16 hex chars.
Staff cards use ``NWS:<card_uuid>:<signature>`` with a different signed message
(``staff:<uuid>``), so a ticket QR can never pass as a staff card or the reverse.
The QR holds no personal data and cannot be forged without the secret.
"""
import hashlib
import hmac
import io
import re
import uuid as uuid_lib

import qrcode
from django.conf import settings
from qrcode.constants import ERROR_CORRECT_H

PREFIX = "NWP"
STAFF_PREFIX = "NWS"
STAFF_CODE_PREFIX = "STF"
SIGNATURE_LENGTH = 16
CODE_RE = re.compile(r"^[A-Z]{3}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def sign(uuid_value, purpose: str = "") -> str:
    key = settings.QR_SECRET_KEY.encode("utf-8")
    message = f"{purpose}{str(uuid_value).lower()}".encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:SIGNATURE_LENGTH]


def build_qr_payload(uuid_value) -> str:
    return f"{PREFIX}:{str(uuid_value).lower()}:{sign(uuid_value)}"


def build_staff_payload(uuid_value) -> str:
    return f"{STAFF_PREFIX}:{str(uuid_value).lower()}:{sign(uuid_value, 'staff:')}"


def _parse(value: str, prefix: str, purpose: str):
    parts = (value or "").strip().split(":")
    if len(parts) != 3 or parts[0].upper() != prefix:
        return None
    try:
        parsed = uuid_lib.UUID(parts[1])
    except ValueError:
        return None
    if not hmac.compare_digest(parts[2].lower(), sign(parsed, purpose)):
        return None
    return parsed


def parse_qr_payload(value: str):
    """Return the ticket UUID if `value` is a valid signed ticket payload, else None."""
    return _parse(value, PREFIX, "")


def parse_staff_payload(value: str):
    """Return the staff card UUID if `value` is a valid signed staff payload, else None."""
    return _parse(value, STAFF_PREFIX, "staff:")


def normalize_code(value: str, prefix: str = PREFIX) -> str | None:
    """'nwp 7xk4 92qd' / 'NWP7XK492QD' → 'NWP-7XK4-92QD' (or None). Staff: prefix='STF'."""
    compact = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if len(compact) != 11 or not compact.startswith(prefix):
        return None
    code = f"{compact[:3]}-{compact[3:7]}-{compact[7:]}"
    return code if CODE_RE.match(code) else None


def make_qr_image(data: str, box_size: int = 20, border: int = 2):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")


def make_qr_png(data: str, size: int = 720) -> bytes:
    image = make_qr_image(data)
    image = image.resize((size, size), resample=0)  # nearest: keeps modules sharp
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
