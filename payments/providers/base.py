import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

import requests
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger("payments")

PAID = "paid"
PENDING = "pending"
FAILED = "failed"

SENSITIVE_KEY = re.compile(r"(secret|token|authori[sz]ation|password|card|cvv|pin|otp|key|signature|hash)", re.I)


class PaymentError(Exception):
    """The gateway refused the request or could not be reached."""


class InvalidSignature(Exception):
    """Webhook signature missing or wrong."""


@dataclass
class VerificationResult:
    """Normalised answer of a gateway's verify API (or a signed webhook)."""

    status: str  # paid | pending | failed
    transaction_id: str = ""
    amount: Decimal | None = None  # major units (XOF / NGN)
    currency: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    lookup: str  # matched against Order.gateway_transaction_id, or an order reference
    result: VerificationResult | None = None  # set only when the payload itself is signed & trusted


def redact(data, depth: int = 0):
    """Strip secrets and card data before logging."""
    if depth > 6:
        return "…"
    if isinstance(data, dict):
        return {k: ("***" if SENSITIVE_KEY.search(str(k)) else redact(v, depth + 1)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v, depth + 1) for v in data[:50]]
    if isinstance(data, str) and len(data) > 500:
        return data[:500] + "…"
    return data


def split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "Customer", "NextWave"
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


class BasePaymentProvider:
    """Common interface for every gateway.

    initialize(order)       → URL to redirect the buyer to
    verify(lookup)          → VerificationResult from the gateway API
    parse_webhook(request)  → WebhookEvent (raises InvalidSignature)
    callback_lookup(request)→ identifier found in the browser return URL
    test_connection()       → (ok, message) for the admin button
    """

    code = ""
    required_keys: tuple[str, ...] = ("secret_key",)
    key_labels: dict[str, str] = {}

    def __init__(self, gateway):
        self.gateway = gateway

    # -- URLs ---------------------------------------------------------------
    @property
    def sandbox(self) -> bool:
        return self.gateway.is_sandbox

    def callback_url(self, order) -> str:
        return f"{settings.SITE_URL}{reverse('payments:callback', args=[self.code])}?ref={order.reference}"

    def webhook_path(self) -> str:
        return reverse("payments:webhook", args=[self.code])

    # -- interface ----------------------------------------------------------
    def initialize(self, order) -> str:
        raise NotImplementedError

    def verify(self, lookup: str) -> VerificationResult:
        raise NotImplementedError

    def parse_webhook(self, request) -> WebhookEvent:
        raise NotImplementedError

    def callback_lookup(self, request) -> str:
        return ""

    def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError

    # -- HTTP + logging -----------------------------------------------------
    def http(self, method: str, url: str, *, order=None, **kwargs) -> tuple[int, dict]:
        kwargs.setdefault("timeout", settings.PAYMENT_HTTP_TIMEOUT)
        sent = kwargs.get("json") or kwargs.get("data") or kwargs.get("params") or {}
        try:
            response = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            self.log("out", method, url, None, sent, {"error": str(exc)}, order)
            raise PaymentError(f"{self.gateway.display_name}: network error ({exc.__class__.__name__})") from exc
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:1000]}
        if not isinstance(data, dict):
            data = {"data": data}
        self.log("out", method, url, response.status_code, sent, data, order)
        return response.status_code, data

    def log(self, direction, method, url, status_code, request_data, response_data, order=None, note=""):
        from payments.models import PaymentLog

        logger.info("[%s] %s %s %s → %s", self.code, direction, method, url, status_code)
        try:
            PaymentLog.objects.create(
                gateway_code=self.code,
                order=order,
                direction=direction,
                method=method or "",
                url=(url or "")[:500],
                status_code=status_code,
                request_data=redact(request_data if isinstance(request_data, dict) else {"data": request_data}),
                response_data=redact(response_data if isinstance(response_data, dict) else {"data": response_data}),
                note=note[:255],
            )
        except Exception:  # logging must never break a payment
            logger.exception("Could not store PaymentLog")

    @staticmethod
    def error_message(data: dict, default: str) -> str:
        for key in ("message", "error", "errors", "detail"):
            value = data.get(key) if isinstance(data, dict) else None
            if value:
                return str(value)[:200]
        return default
