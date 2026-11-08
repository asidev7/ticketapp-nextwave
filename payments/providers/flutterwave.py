"""Flutterwave — Nigeria (NGN): card, bank transfer, USSD.

Docs: https://developer.flutterwave.com/docs
* POST /v3/payments                                  hosted checkout → data.link
* GET  /v3/transactions/verify_by_reference?tx_ref=  status: successful | failed | pending
* Webhook header `verif-hash` must equal the "secret hash" set in the
  Flutterwave dashboard (stored here as extra_key). Status is re-verified.
Sandbox vs live is decided by the keys (FLWSECK_TEST-… / FLWSECK-…).
"""
import json
import secrets
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from core.models import SiteSettings

from .base import FAILED, PAID, PENDING, BasePaymentProvider, InvalidSignature, PaymentError, VerificationResult, WebhookEvent

BASE_URL = "https://api.flutterwave.com/v3"


class FlutterwaveProvider(BasePaymentProvider):
    code = "flutterwave"
    required_keys = ("secret_key",)
    key_labels = {
        "public_key": _("Public key (FLWPUBK_TEST-… / FLWPUBK-…) — optional"),
        "secret_key": _("Secret key (FLWSECK_TEST-… / FLWSECK-…)"),
        "extra_key": _("Webhook secret hash (Settings → Webhooks)"),
    }

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.gateway.secret_key}", "Content-Type": "application/json"}

    def initialize(self, order) -> str:
        site = SiteSettings.load()
        tx_ref = f"{order.reference}-{secrets.token_hex(2).upper()}"
        payload = {
            "tx_ref": tx_ref,
            "amount": str(order.total_amount),
            "currency": order.currency,
            "redirect_url": self.callback_url(order),
            "payment_options": "card,banktransfer,ussd",
            "customer": {
                "email": order.buyer_email,
                "phonenumber": order.buyer_phone,
                "name": order.buyer_name,
            },
            "customizations": {
                "title": site.site_name,
                "description": f"{order.event.name} — {order.reference}",
            },
            "meta": {"order_reference": order.reference},
        }
        status, data = self.http("POST", f"{BASE_URL}/payments", json=payload, headers=self._headers(), order=order)
        link = (data.get("data") or {}).get("link")
        if status >= 300 or data.get("status") != "success" or not link:
            raise PaymentError(self.error_message(data, "Flutterwave refused the transaction"))
        order.gateway_transaction_id = tx_ref
        order.save(update_fields=["gateway_transaction_id", "updated_at"])
        return link

    def verify(self, lookup: str) -> VerificationResult:
        status, data = self.http(
            "GET", f"{BASE_URL}/transactions/verify_by_reference", params={"tx_ref": lookup}, headers=self._headers()
        )
        if status in (400, 404) and data.get("status") == "error":
            # No transaction yet for this tx_ref (buyer still on the checkout).
            return VerificationResult(PENDING, transaction_id=lookup, raw=data)
        if status >= 300:
            raise PaymentError(self.error_message(data, "Flutterwave verification failed"))
        tx = data.get("data") or {}
        state = (tx.get("status") or "").lower()
        if state == "successful":
            result = PAID
        elif state in ("failed", "cancelled"):
            result = FAILED
        else:
            result = PENDING
        amount = tx.get("amount")
        return VerificationResult(
            status=result,
            transaction_id=str(tx.get("id") or lookup),
            amount=Decimal(str(amount)) if amount is not None else None,
            currency=(tx.get("currency") or "").upper(),
            raw={k: tx.get(k) for k in ("id", "tx_ref", "flw_ref", "status", "amount", "charged_amount", "currency", "payment_type")},
        )

    def callback_lookup(self, request) -> str:
        return request.GET.get("tx_ref", "")

    def parse_webhook(self, request) -> WebhookEvent:
        expected = self.gateway.extra_key or ""
        received = request.headers.get("verif-hash", "")
        if not expected or not received or not secrets.compare_digest(expected, received):
            raise InvalidSignature("Flutterwave verif-hash mismatch")
        body = json.loads(request.body or b"{}")
        data = body.get("data") or {}
        return WebhookEvent(lookup=str(data.get("tx_ref") or data.get("txRef") or ""))

    def test_connection(self) -> tuple[bool, str]:
        status, data = self.http("GET", f"{BASE_URL}/banks/NG", headers=self._headers())
        if status == 200 and data.get("status") == "success":
            return True, "Flutterwave: OK"
        return False, self.error_message(data, f"HTTP {status}")
