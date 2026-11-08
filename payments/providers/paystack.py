"""Paystack — Nigeria (NGN): card, bank transfer, USSD.

Docs: https://paystack.com/docs/api/
* POST /transaction/initialize       amount in kobo (×100) → authorization_url
* GET  /transaction/verify/:reference status: success | failed | abandoned…
* Webhook header x-paystack-signature = HMAC-SHA512(secret_key, raw_body)
Sandbox vs live is decided by the keys (sk_test_… / sk_live_…).
"""
import hashlib
import hmac
import json
import secrets
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from .base import FAILED, PAID, PENDING, BasePaymentProvider, InvalidSignature, PaymentError, VerificationResult, WebhookEvent

BASE_URL = "https://api.paystack.co"


class PaystackProvider(BasePaymentProvider):
    code = "paystack"
    required_keys = ("secret_key",)
    key_labels = {
        "public_key": _("Public key (pk_test_… / pk_live_…) — optional"),
        "secret_key": _("Secret key (sk_test_… / sk_live_…) — also signs webhooks"),
        "extra_key": _("Not used by Paystack"),
    }

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.gateway.secret_key}", "Content-Type": "application/json"}

    def initialize(self, order) -> str:
        reference = f"{order.reference}-{secrets.token_hex(2).upper()}"
        payload = {
            "email": order.buyer_email,
            "amount": order.total_amount * 100,
            "currency": order.currency,
            "reference": reference,
            "callback_url": self.callback_url(order),
            "metadata": {
                "order_reference": order.reference,
                "cancel_action": self.callback_url(order),
                "custom_fields": [
                    {"display_name": "Order", "variable_name": "order_reference", "value": order.reference},
                ],
            },
        }
        status, data = self.http("POST", f"{BASE_URL}/transaction/initialize", json=payload, headers=self._headers(), order=order)
        url = (data.get("data") or {}).get("authorization_url")
        if status >= 300 or not data.get("status") or not url:
            raise PaymentError(self.error_message(data, "Paystack refused the transaction"))
        order.gateway_transaction_id = reference
        order.save(update_fields=["gateway_transaction_id", "updated_at"])
        return url

    @staticmethod
    def _result_from(data: dict, lookup: str) -> VerificationResult:
        state = (data.get("status") or "").lower()
        if state == "success":
            result = PAID
        elif state in ("failed", "reversed"):
            result = FAILED
        else:  # abandoned, ongoing, pending, queued…
            result = PENDING
        amount = data.get("amount")
        return VerificationResult(
            status=result,
            transaction_id=str(data.get("id") or lookup),
            amount=(Decimal(str(amount)) / 100) if amount is not None else None,
            currency=(data.get("currency") or "").upper(),
            raw={k: data.get(k) for k in ("id", "reference", "status", "amount", "currency", "channel", "paid_at")},
        )

    def verify(self, lookup: str) -> VerificationResult:
        status, data = self.http("GET", f"{BASE_URL}/transaction/verify/{lookup}", headers=self._headers())
        if status == 404 or (status == 400 and "not found" in str(data.get("message", "")).lower()):
            return VerificationResult(PENDING, transaction_id=lookup, raw=data)
        if status >= 300 or not data.get("status"):
            raise PaymentError(self.error_message(data, "Paystack verification failed"))
        return self._result_from(data.get("data") or {}, lookup)

    def callback_lookup(self, request) -> str:
        return request.GET.get("reference") or request.GET.get("trxref") or ""

    def parse_webhook(self, request) -> WebhookEvent:
        signature = request.headers.get("x-paystack-signature", "")
        secret = self.gateway.secret_key or ""
        expected = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha512).hexdigest()
        if not secret or not signature or not hmac.compare_digest(expected, signature):
            raise InvalidSignature("Paystack signature mismatch")
        body = json.loads(request.body or b"{}")
        data = body.get("data") or {}
        lookup = str(data.get("reference") or "")
        if body.get("event") == "charge.success":
            # The payload is HMAC-signed with our secret key: it can be trusted.
            return WebhookEvent(lookup=lookup, result=self._result_from(data, lookup))
        return WebhookEvent(lookup=lookup)

    def test_connection(self) -> tuple[bool, str]:
        status, data = self.http("GET", f"{BASE_URL}/transaction", params={"perPage": 1}, headers=self._headers())
        if status == 200 and data.get("status"):
            mode = "test" if (self.gateway.secret_key or "").startswith("sk_test") else "live"
            return True, f"Paystack ({mode} keys): OK"
        return False, self.error_message(data, f"HTTP {status}")
