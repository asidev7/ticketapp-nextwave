"""Symmetric encryption (Fernet) for secrets stored in the database."""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        # Development fallback: derive a stable key from SECRET_KEY.
        # Production settings require FIELD_ENCRYPTION_KEY explicitly.
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error("Could not decrypt a stored secret: FIELD_ENCRYPTION_KEY changed?")
        return ""


def mask(value: str, visible: int = 4) -> str:
    """'sk_live_abcdef1234' -> '••••1234'."""
    if not value:
        return ""
    return "••••" + value[-visible:]
