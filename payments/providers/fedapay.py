"""FedaPay — Benin (XOF): Mobile Money MTN / Moov / Celtiis and cards.

Docs: https://docs.fedapay.com
* POST /v1/transactions            create the transaction
* POST /v1/transactions/{id}/token → {"token", "url"}: hosted payment page
* GET  /v1/transactions/{id}       status: approved | declined | canceled | pending…
* Webhook header X-FEDAPAY-SIGNATURE: "t=<ts>,s=<hex>" where
  s = HMAC-SHA256(webhook_secret, f"{t}.{raw_body}"), 5-minute tolerance.
"""
import hashlib
import hmac
import json
import time
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from .base import FAILED, PAID, PENDING, BasePaymentProvider, InvalidSignature, PaymentError, VerificationResult, WebhookEvent, split_name

TOLERANCE_SECONDS = 300


class FedaPayProvider(BasePaymentProvider):
    code = "fedapay"
    required_keys = ("secret_key",)
    key_labels = {
        "public_key": _("Public key (pk_sandbox_… / pk_live_…) — optional"),
        "secret_key": _("Secret key (sk_sandbox_… / sk_live_…)"),
        "extra_key": _("Webhook secret (wh_sandbox_… / wh_live_…)"),
    }

    @property
    def base_url(self) -> str:
        return "https://sandbox-api.fedapay.com/v1" if self.sandbox else "https://api.fedapay.com/v1"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.gateway.secret_key}", "Content-Type": "application/json"}

    @staticmethod
    def _unwrap(data: dict) -> dict:
        """FedaPay sometimes wraps the object as {"v1/transaction": {...}}."""
        for key in ("v1/transaction", "transaction"):
            if isinstance(data.get(key), dict):
                return data[key]
        return data

    def initialize(self, order) -> str:
        first, last = split_name(order.buyer_name)
        customer = {"firstname": first, "lastname": last, "email": order.buyer_email}
        if order.buyer_phone.startswith("+229"):
            customer["phone_number"] = {"number": order.buyer_phone, "country": "bj"}
        payload = {
            "description": f"{order.event.name} — {order.reference}",
            "amount": order.total_amount,
            "currency": {"iso": order.currency},
            "callback_url": self.callback_url(order),
            "customer": customer,
            "custom_metadata": {"order_reference": order.reference},
        }
        status, data = self.http("POST", f"{self.base_url}/transactions", json=payload, headers=self._headers(), order=order)
        transaction = self._unwrap(data)
        transaction_id = transaction.get("id")
        if status >= 300 or not transaction_id:
            raise PaymentError(self.error_message(data, "FedaPay refused the transaction"))

        status, token = self.http("POST", f"{self.base_url}/transactions/{transaction_id}/token", headers=self._headers(), order=order)
        if status >= 300 or not token.get("url"):
            raise PaymentError(self.error_message(token, "FedaPay did not return a payment link"))

        order.gateway_transaction_id = str(transaction_id)
        order.save(update_fields=["gateway_transaction_id", "updated_at"])
        return token["url"]

    def verify(self, lookup: str) -> VerificationResult:
        status, data = self.http("GET", f"{self.base_url}/transactions/{lookup}", headers=self._headers())
        if status == 404:
            return VerificationResult(FAILED, transaction_id=str(lookup), raw=data)
        if status >= 300:
            raise PaymentError(self.error_message(data, "FedaPay verification failed"))
        transaction = self._unwrap(data)
        state = (transaction.get("status") or "").lower()
        if state in ("approved", "transferred"):
            result = PAID
        elif state in ("declined", "canceled", "cancelled", "refunded", "expired"):
            result = FAILED
        else:
            result = PENDING
        currency = transaction.get("currency")
        iso = currency.get("iso", "") if isinstance(currency, dict) else ""
        amount = transaction.get("amount")
        return VerificationResult(
            status=result,
            transaction_id=str(transaction.get("id") or lookup),
            amount=Decimal(str(amount)) if amount is not None else None,
            currency=iso,
            raw={k: transaction.get(k) for k in ("id", "reference", "status", "amount", "mode", "approved_at")},
        )

    def callback_lookup(self, request) -> str:
        return request.GET.get("id", "")

    # -- webhook --------------------------------------------------------------
    def verify_signature(self, raw_body: bytes, header: str) -> None:
        secret = self.gateway.extra_key
        if not secret:
            raise InvalidSignature("FedaPay webhook secret is not configured")
        timestamp, signatures = None, []
        for part in (header or "").split(","):
            key, _sep, value = part.strip().partition("=")
            if key == "t":
                timestamp = value
            elif key == "s":
                signatures.append(value)
        if not timestamp or not signatures:
            raise InvalidSignature("Malformed X-FEDAPAY-SIGNATURE header")
        signed = f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, s) for s in signatures):
            raise InvalidSignature("FedaPay signature mismatch")
        try:
            if abs(time.time() - int(timestamp)) > TOLERANCE_SECONDS:
                raise InvalidSignature("FedaPay webhook timestamp outside tolerance")
        except ValueError as exc:
            raise InvalidSignature("Bad timestamp") from exc

    def parse_webhook(self, request) -> WebhookEvent:
        self.verify_signature(request.body, request.headers.get("X-FEDAPAY-SIGNATURE", ""))
        body = json.loads(request.body or b"{}")
        entity = body.get("entity") if isinstance(body.get("entity"), dict) else body.get("object")
        if not isinstance(entity, dict):
            entity = {}
        lookup = str(entity.get("id") or body.get("object_id") or "")
        # Status is always re-read from the API (result=None).
        return WebhookEvent(lookup=lookup)

    def test_connection(self) -> tuple[bool, str]:
        status, data = self.http("GET", f"{self.base_url}/transactions", params={"per_page": 1}, headers=self._headers())
        if status == 200:
            return True, f"FedaPay {self.gateway.get_mode_display()}: OK"
        return False, self.error_message(data, f"HTTP {status}")
