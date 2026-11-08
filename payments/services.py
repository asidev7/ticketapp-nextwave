"""Gateway routing and payment confirmation.

Golden rule: tickets are issued only from a server-side verification
(signed webhook or the gateway's verify API), never from the browser's
return URL alone. Amount and currency are checked against the order.
"""
import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from tickets.models import Order
from tickets.services import fulfil_order, mark_order_failed

from .models import PaymentGateway
from .providers import FAILED, PAID, PaymentError, VerificationResult

logger = logging.getLogger("payments")

REFERENCE_RE = re.compile(r"(NWP-\d{4}-[A-Z0-9]{6})")


def available_gateways(country: str | None = None) -> list[PaymentGateway]:
    """Active gateways with keys filled in, optionally for one country.

    Benin (XOF) → FedaPay, FeexPay · Nigeria (NGN) → Paystack, Flutterwave.
    """
    gateways = PaymentGateway.objects.filter(is_active=True).order_by("order", "pk")
    if country:
        gateways = gateways.filter(country=country)
    return [g for g in gateways if g.is_configured]


def start_payment(order: Order, gateway: PaymentGateway) -> str:
    """Attach the gateway to the order and return the URL to redirect to."""
    if order.status != Order.Status.PENDING:
        raise PaymentError("Order is not awaiting payment")
    if gateway.country != order.country or gateway.currency != order.currency:
        raise PaymentError("This payment method is not available for the selected country")
    if not gateway.is_available:
        raise PaymentError("This payment method is not available")
    if order.gateway_id != gateway.pk:
        order.gateway = gateway
        order.gateway_transaction_id = ""
        order.save(update_fields=["gateway", "gateway_transaction_id", "updated_at"])
    return gateway.get_provider().initialize(order)


def find_order(gateway_code: str, *candidates: str) -> Order | None:
    """Locate an order from a gateway lookup key or anything containing our reference."""
    for value in candidates:
        if not value:
            continue
        order = Order.objects.filter(gateway__code=gateway_code, gateway_transaction_id=value).first()
        if order:
            return order
    for value in candidates:
        match = REFERENCE_RE.search(value or "")
        if match:
            order = Order.objects.filter(reference=match.group(1)).first()
            if order:
                return order
    return None


def amount_matches(order: Order, result: VerificationResult) -> bool:
    if result.amount is None:
        return False
    if result.currency and result.currency.upper() != order.currency:
        return False
    return Decimal(result.amount) >= Decimal(order.total_amount)


def apply_verification(order: Order, result: VerificationResult, gateway: PaymentGateway | None = None) -> Order:
    """Turn a verified gateway status into an order state change (idempotent)."""
    if result.status == PAID:
        if not amount_matches(order, result):
            logger.error(
                "Amount/currency mismatch for %s: expected %s %s, got %s %s",
                order.reference, order.total_amount, order.currency, result.amount, result.currency,
            )
            Order.objects.filter(pk=order.pk).update(
                note=f"{order.note}\nPAYMENT MISMATCH: {result.amount} {result.currency} (tx {result.transaction_id})".strip()
            )
            return Order.objects.get(pk=order.pk)
        order, _created = fulfil_order(
            order.pk, transaction_id=result.transaction_id, payload=result.raw, gateway=gateway or order.gateway
        )
        return order
    if result.status == FAILED and order.status == Order.Status.PENDING:
        mark_order_failed(order.pk, reason=f"Gateway status: {result.raw.get('status', 'failed')}")
    return Order.objects.get(pk=order.pk)


def refresh_order_status(order: Order, min_interval_seconds: int = 8) -> Order:
    """Polling fallback: ask the gateway directly when the webhook is late."""
    if order.status != Order.Status.PENDING or not order.gateway_id or not order.gateway_transaction_id:
        return order
    now = timezone.now()
    if order.last_verified_at and now - order.last_verified_at < timedelta(seconds=min_interval_seconds):
        return order
    Order.objects.filter(pk=order.pk).update(last_verified_at=now)
    try:
        result = order.gateway.get_provider().verify(order.gateway_transaction_id)
    except PaymentError as exc:
        logger.warning("Verification of %s failed: %s", order.reference, exc)
        return order
    return apply_verification(order, result, order.gateway)
