"""FeexPay — Benin (XOF): MTN, Moov, Celtiis Mobile Money and cards.

FeexPay's integration endpoints (as used by the official SDKs):
* POST {base}/api/transactions/requesttopay/integration     Mobile Money push (form data)
* POST {base}/api/transactions/card/inittransact/integration card → {"url", "transref"}
* GET  {base}/api/transactions/getrequesttopay/integration/{reference}
       status: SUCCESSFUL | FAILED | PENDING …
* GET  {base}/api/shop/{shop_id}/get_shop                    shop check
The API host is configurable with FEEXPAY_API_BASE in .env.

FeexPay webhooks are not signed, so the webhook URL carries a secret key
(?k=…) and the status is ALWAYS re-read from the FeexPay API.

Keys in the admin:
  secret_key = FeexPay API token
  extra_key  = Shop ID
"""
import hashlib
import hmac
import json
import secrets
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .base import FAILED, PAID, PENDING, BasePaymentProvider, InvalidSignature, PaymentError, VerificationResult, WebhookEvent, digits, split_name

NETWORKS = {
    "MTN": "MTN Mobile Money",
    "MOOV": "Moov Money",
    "CELTIIS": "Celtiis Cash",
}
CARD_TYPES = {"VISA": "Visa", "MASTERCARD": "Mastercard"}


class FeexPayProvider(BasePaymentProvider):
    code = "feexpay"
    required_keys = ("secret_key", "extra_key")
    key_labels = {
        "public_key": _("Not used by FeexPay"),
        "secret_key": _("API token (FeexPay dashboard → Developers)"),
        "extra_key": _("Shop ID"),
    }

    @property
    def base_url(self) -> str:
        return settings.FEEXPAY_API_BASE

    def initialize(self, order) -> str:
        # Mobile Money needs the network + number first: own small page.
        return settings.SITE_URL + reverse("payments:feexpay_pay", args=[order.reference])

    def _common(self, order) -> dict:
        first, last = split_name(order.buyer_name)
        return {
            "amount": order.total_amount,
            "token": self.gateway.secret_key,
            "shop": self.gateway.extra_key,
            "first_name": first,
            "last_name": last,
            "email": order.buyer_email,
            "description": f"{order.event.name} {order.reference}",
            "callback_info": order.reference,
        }

    def request_to_pay(self, order, network: str, phone: str) -> str:
        if network not in NETWORKS:
            raise PaymentError("Unknown network")
        payload = {**self._common(order), "phoneNumber": digits(phone), "reseau": network}
        status, data = self.http(
            "POST", f"{self.base_url}/api/transactions/requesttopay/integration", data=payload, order=order
        )
        reference = data.get("reference") or data.get("transref")
        if status >= 300 or not reference or str(data.get("status", "")).upper() == "FAILED":
            raise PaymentError(self.error_message(data, "FeexPay refused the payment request"))
        order.gateway_transaction_id = str(reference)
        order.save(update_fields=["gateway_transaction_id", "updated_at"])
        return str(reference)

    def card_payment(self, order, card_type: str) -> str:
        if card_type not in CARD_TYPES:
            raise PaymentError("Unknown card type")
        event = order.event
        payload = {
            **self._common(order),
            "phone": digits(order.buyer_phone),
            "reseau": card_type,
            "country": "Benin",
            "address1": event.venue_address[:100],
            "district": event.city,
            "currency": order.currency,
            "callback_url": self.callback_url(order),
        }
        status, data = self.http(
            "POST", f"{self.base_url}/api/transactions/card/inittransact/integration", data=payload, order=order
        )
        url = data.get("url")
        reference = data.get("transref") or data.get("reference")
        if status >= 300 or not url or not reference:
            raise PaymentError(self.error_message(data, "FeexPay card payment unavailable"))
        order.gateway_transaction_id = str(reference)
        order.save(update_fields=["gateway_transaction_id", "updated_at"])
        return url

    def verify(self, lookup: str) -> VerificationResult:
        status, data = self.http("GET", f"{self.base_url}/api/transactions/getrequesttopay/integration/{lookup}")
        if status == 404:
            return VerificationResult(PENDING, transaction_id=lookup, raw=data)
        if status >= 300:
            raise PaymentError(self.error_message(data, "FeexPay verification failed"))
        state = str(data.get("status", "")).upper()
        if state in ("SUCCESSFUL", "SUCCESS"):
            result = PAID
        elif state in ("FAILED", "TIMEOUT", "CANCELLED", "CANCELED", "REJECTED"):
            result = FAILED
        else:
            result = PENDING
        amount = data.get("amount")
        return VerificationResult(
            status=result,
            transaction_id=str(data.get("reference") or lookup),
            amount=Decimal(str(amount)) if amount not in (None, "") else None,
            currency=str(data.get("currency") or ""),
            raw={k: data.get(k) for k in ("reference", "status", "amount", "currency", "reseau")},
        )

    def callback_lookup(self, request) -> str:
        return request.GET.get("transref") or request.GET.get("reference") or ""

    # -- webhook --------------------------------------------------------------
    @staticmethod
    def webhook_key() -> str:
        return hmac.new(settings.SECRET_KEY.encode(), b"feexpay-webhook", hashlib.sha256).hexdigest()[:32]

    def webhook_path(self) -> str:
        return f"{super().webhook_path()}?k={self.webhook_key()}"

    def parse_webhook(self, request) -> WebhookEvent:
        if not secrets.compare_digest(request.GET.get("k", ""), self.webhook_key()):
            raise InvalidSignature("FeexPay webhook key mismatch")
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            body = request.POST.dict()
        lookup = str(body.get("reference") or body.get("transref") or body.get("callback_info") or "")
        return WebhookEvent(lookup=lookup)  # status re-read from the API

    def test_connection(self) -> tuple[bool, str]:
        status, data = self.http("GET", f"{self.base_url}/api/shop/{self.gateway.extra_key}/get_shop")
        if status == 200 and data.get("name"):
            return True, f"FeexPay shop: {data['name']}"
        return False, self.error_message(data, f"HTTP {status}")
