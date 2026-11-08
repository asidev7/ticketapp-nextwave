import json
import logging

from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.constants import PHONE_PREFIX
from tickets.models import Order
from tickets.tokens import has_order_access

from .models import PaymentGateway
from .providers import InvalidSignature, PaymentError
from .providers.feexpay import CARD_TYPES, NETWORKS
from .services import apply_verification, find_order, start_payment

logger = logging.getLogger("payments")


def _gateway(code: str) -> PaymentGateway:
    gateway = PaymentGateway.objects.filter(code=code).first()
    if not gateway:
        raise Http404
    return gateway


@require_POST
def init_payment(request, gateway, order_ref):
    """POST /payments/<gateway>/init/<order_ref>/ — (re)start a payment."""
    gateway = _gateway(gateway)
    order = get_object_or_404(Order, reference=order_ref)
    if not has_order_access(request, order):
        raise Http404
    try:
        return redirect(start_payment(order, gateway))
    except PaymentError as exc:
        logger.warning("Payment init failed for %s: %s", order.reference, exc)
        messages.error(request, _("The payment could not be started. Please try again or choose another method."))
        return redirect(order.get_absolute_url())


@require_GET
def callback(request, gateway):
    """Browser return URL. Never trusted on its own: the status is re-verified."""
    gateway = _gateway(gateway)
    provider = gateway.get_provider()
    lookup = provider.callback_lookup(request)
    order = find_order(gateway.code, lookup, request.GET.get("ref", ""))
    provider.log("callback", "GET", request.path, None, request.GET.dict(), {}, order)
    if not order:
        raise Http404
    if order.status == Order.Status.PENDING and order.gateway_transaction_id:
        try:
            apply_verification(order, provider.verify(order.gateway_transaction_id), gateway)
        except PaymentError as exc:
            logger.warning("Callback verification failed for %s: %s", order.reference, exc)
    return redirect(order.get_absolute_url())


@csrf_exempt
@require_POST
def webhook(request, gateway):
    """Server-to-server notification. Signature check is mandatory."""
    gateway = _gateway(gateway)
    provider = gateway.get_provider()
    try:
        event = provider.parse_webhook(request)
    except InvalidSignature as exc:
        provider.log("webhook", "POST", request.path, 400, {}, {}, None, note=f"Rejected: {exc}")
        return HttpResponseBadRequest("invalid signature")
    except (ValueError, json.JSONDecodeError):
        return HttpResponseBadRequest("invalid payload")

    order = find_order(gateway.code, event.lookup)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        body = {}
    provider.log("webhook", "POST", request.path, 200, body, {}, order)

    if not order or order.status == Order.Status.PAID:
        return HttpResponse("ok")  # unknown or already processed: idempotent

    try:
        result = event.result or provider.verify(order.gateway_transaction_id or event.lookup)
    except PaymentError as exc:
        logger.warning("Webhook verification failed for %s: %s", order.reference, exc)
        return HttpResponse("retry later", status=503)
    apply_verification(order, result, gateway)
    return HttpResponse("ok")


@require_http_methods(["GET", "POST"])
def feexpay_pay(request, order_ref):
    """FeexPay step: choose MTN / Moov / Celtiis (USSD push) or card."""
    order = get_object_or_404(Order, reference=order_ref, gateway__code="feexpay")
    if not has_order_access(request, order):
        raise Http404
    if order.status != Order.Status.PENDING:
        return redirect(order.get_absolute_url())
    provider = order.gateway.get_provider()
    error = ""
    phone = order.buyer_phone or PHONE_PREFIX["BJ"]

    if request.method == "POST":
        method = request.POST.get("method", "")
        phone = request.POST.get("phone", phone).strip()
        try:
            if method in CARD_TYPES:
                return redirect(provider.card_payment(order, method))
            if method in NETWORKS:
                if len("".join(ch for ch in phone if ch.isdigit())) < 10:
                    raise PaymentError("phone")
                provider.request_to_pay(order, method, phone)
                messages.info(request, _("Confirm the payment on your phone (enter your Mobile Money PIN)."))
                return redirect(order.get_absolute_url())
            error = _("Please choose a payment method.")
        except PaymentError as exc:
            logger.warning("FeexPay payment failed for %s: %s", order.reference, exc)
            error = (
                _("Please enter a valid Mobile Money number.")
                if str(exc) == "phone"
                else _("The payment could not be started. Please try again.")
            )

    return render(
        request,
        "payments/feexpay_pay.html",
        {"order": order, "networks": NETWORKS, "cards": CARD_TYPES, "phone": phone, "error": error},
    )
