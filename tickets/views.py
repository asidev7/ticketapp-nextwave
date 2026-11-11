import logging
import re

from django.conf import settings
from django.contrib import messages
from django.db.models import F
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from core.constants import BENIN, COUNTRY_CURRENCY, PHONE_PREFIX
from core.models import Event, SiteSettings
from core.tasks import dispatch
from payments.providers import PaymentError
from payments.services import available_gateways, refresh_order_status, start_payment

from .forms import CheckoutForm, FindTicketForm
from .models import Order, Ticket, TicketType
from .render import ensure_order_pdf, ensure_ticket_files
from .services import StockError, create_order, release_expired_orders
from .tasks import resend_order_email
from .tokens import (
    grant_order_access,
    has_order_access,
    links_expired,
    order_access_url,
    order_pdf_url,
    read_order_token,
    read_ticket_token,
    ticket_download_url,
)

logger = logging.getLogger("tickets")


def _client_ip(request) -> str | None:
    key = getattr(settings, "RATELIMIT_IP_META_KEY", None) or "REMOTE_ADDR"
    value = request.META.get(key) or request.META.get("REMOTE_ADDR")
    return (value or "").split(",")[0].strip() or None


@never_cache
@ratelimit(key="ip", rate=settings.CHECKOUT_RATE, method="POST", block=False)
@require_http_methods(["GET", "POST"])
def checkout(request, slug):
    event = Event.get_active()
    if not event:
        raise Http404
    release_expired_orders()  # cheap; keeps stock right even without Celery beat
    ticket_type = get_object_or_404(TicketType.objects.prefetch_related("benefits"), slug=slug, event=event, is_active=True)
    site = SiteSettings.load()
    gateways = available_gateways()
    on_sale = site.sales_open and ticket_type.is_on_sale()

    # Only the currencies this ticket is sold in (e.g. a Naira-only table).
    countries = [code for code in ("NG", BENIN) if ticket_type.price_for(COUNTRY_CURRENCY[code]) > 0] or [BENIN]
    country = request.GET.get("country", countries[0])
    if country not in countries:
        country = countries[0]

    form_kwargs = {"ticket_type": ticket_type, "site": site, "gateways": gateways}
    if request.method == "POST" and on_sale:
        form = CheckoutForm(request.POST, request.FILES, **form_kwargs)
        if getattr(request, "limited", False):
            form.add_error(None, _("Too many attempts. Please wait a few minutes and try again."))
        elif form.is_valid():
            data = form.cleaned_data
            holders = form.build_holders()
            try:
                order = create_order(
                    event=event,
                    ticket_type=ticket_type,
                    quantity=data["quantity"],
                    country=data["country"],
                    buyer_name=data["full_name"],
                    buyer_email=data["email"],
                    buyer_phone=data["phone"],
                    holders=holders,
                    gateway=data["gateway"],
                    language=request.LANGUAGE_CODE,
                    ip_address=_client_ip(request),
                )
            except StockError as exc:
                form.add_error(None, str(exc))
            else:
                grant_order_access(request, order)
                try:
                    return redirect(start_payment(order, data["gateway"]))
                except PaymentError as exc:
                    logger.error("Could not start payment for %s: %s", order.reference, exc)
                    messages.error(request, _("The payment could not be started. Please try again or choose another method."))
                    return redirect(order.get_absolute_url())
        country = form.data.get("country", country) if form.data.get("country") in countries else country
    else:
        form = CheckoutForm(initial={"country": country, "quantity": 1}, **form_kwargs)

    return render(
        request,
        "tickets/checkout.html",
        {
            "event": event,
            "ticket_type": ticket_type,
            "form": form,
            "gateways": gateways,
            "on_sale": on_sale,
            "country": country,
            "countries": countries,
            "prices": {"XOF": ticket_type.price_xof, "NGN": ticket_type.price_ngn},
            "fees": {"XOF": site.service_fee_xof, "NGN": site.service_fee_ngn},
            "phone_prefixes": PHONE_PREFIX,
            "max_quantity": form.max_quantity,
        },
    )


@never_cache
def order_detail(request, reference):
    order = get_object_or_404(Order.objects.select_related("event", "gateway"), reference=reference)
    if not has_order_access(request, order):
        return render(request, "tickets/order_locked.html", {"reference": reference}, status=403)

    if order.status == Order.Status.PENDING:
        order = refresh_order_status(order)

    tickets = []
    if order.status == Order.Status.PAID:
        for ticket in order.tickets.select_related("ticket_type").exclude(status=Ticket.Status.CANCELLED):
            tickets.append(
                {
                    "ticket": ticket,
                    "category": ticket.ticket_type.tr("name"),
                    "png_url": ticket_download_url(ticket, "png"),
                    "pdf_url": ticket_download_url(ticket, "pdf"),
                    "qr_url": ticket_download_url(ticket, "qr"),
                }
            )

    retry_gateways = []
    if order.status in (Order.Status.PENDING, Order.Status.FAILED):
        retry_gateways = available_gateways(order.country)

    first_item = order.items.select_related("ticket_type").first()
    return render(
        request,
        "tickets/order.html",
        {
            "order": order,
            "event": order.event,
            "tickets": tickets,
            "item": first_item,
            "order_pdf_url": order_pdf_url(order) if tickets else "",
            "share_url": order_access_url(order) if tickets else "",
            "retry_gateways": retry_gateways,
            "awaiting_push": order.gateway and order.gateway.code == "feexpay" and bool(order.gateway_transaction_id),
        },
    )


@never_cache
@require_GET
def order_status(request, reference):
    """JSON polled every 3 s by the waiting page."""
    order = get_object_or_404(Order, reference=reference)
    if not has_order_access(request, order):
        return JsonResponse({"error": "forbidden"}, status=403)
    order = refresh_order_status(order)
    return JsonResponse({"status": order.status, "paid": order.is_paid})


def _file_response(field, filename: str, inline: bool):
    content_type = "application/pdf" if filename.endswith(".pdf") else "image/png"
    response = FileResponse(field.open("rb"), as_attachment=not inline, filename=filename, content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Robots-Tag"] = "noindex"
    return response


@require_GET
def download_ticket(request, token, kind):
    """Signed link to one ticket file: ticket PNG, ticket PDF, or the QR alone (to show at the entrance)."""
    ticket_uuid = read_ticket_token(token)
    if not ticket_uuid or kind not in ("png", "pdf", "qr"):
        raise Http404
    ticket = get_object_or_404(Ticket.objects.select_related("order__event"), uuid=ticket_uuid)
    if ticket.order.status != Order.Status.PAID or ticket.status == Ticket.Status.CANCELLED or links_expired(ticket.order.event):
        raise Http404
    ticket = ensure_ticket_files(ticket.pk)
    inline = request.GET.get("inline") == "1" or kind == "qr"
    if not inline:
        Ticket.objects.filter(pk=ticket.pk).update(download_count=F("download_count") + 1)
    field = {"png": ticket.ticket_png, "pdf": ticket.ticket_pdf, "qr": ticket.qr_image}[kind]
    extension = "pdf" if kind == "pdf" else "png"
    return _file_response(field, f"{ticket.code}{'-qr' if kind == 'qr' else ''}.{extension}", inline)


@require_GET
def download_order(request, token):
    """Signed link to all tickets of an order in one PDF."""
    reference = read_order_token(token)
    order = get_object_or_404(Order.objects.select_related("event"), reference=reference or "")
    if order.status != Order.Status.PAID or links_expired(order.event):
        raise Http404
    order = ensure_order_pdf(order.pk)
    if not order.tickets_pdf:
        raise Http404
    order.tickets.exclude(status=Ticket.Status.CANCELLED).update(download_count=F("download_count") + 1)
    return _file_response(order.tickets_pdf, f"NextWave-{order.reference}.pdf", request.GET.get("inline") == "1")


@never_cache
@ratelimit(key="ip", rate=settings.FIND_TICKET_RATE, method="POST", block=False)
@require_http_methods(["GET", "POST"])
def find_ticket(request):
    form = FindTicketForm(request.POST or None)
    found = None
    if request.method == "POST":
        if getattr(request, "limited", False):
            form.add_error(None, _("Too many attempts. Please try again in an hour."))
        elif form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            value = form.cleaned_data["reference"].strip()
            orders = Order.objects.filter(buyer_email__iexact=email, status=Order.Status.PAID).select_related("event")
            if value.upper().startswith("NWP-"):
                found = orders.filter(reference=value.upper()).first()
            else:
                digits = re.sub(r"\D", "", value)
                found = orders.filter(buyer_phone__endswith=digits[-8:]).first() if len(digits) >= 8 else None
            if found:
                dispatch(resend_order_email, found.pk)
                grant_order_access(request, found)
            else:
                form.add_error(None, _("No paid order matches these details. Check the e-mail and reference."))
    return render(request, "tickets/find_ticket.html", {"form": form, "found": found})
