"""Signed, expiring access tokens for orders and ticket files.

Links stay valid until two days after the event ends.
"""
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

ORDER_SALT = "nwp.order.access"
TICKET_SALT = "nwp.ticket.download"
SESSION_KEY = "nwp_orders"
GRACE = timedelta(days=2)


def make_order_token(order) -> str:
    return signing.dumps(order.reference, salt=ORDER_SALT)


def make_ticket_token(ticket) -> str:
    return signing.dumps(str(ticket.uuid), salt=TICKET_SALT)


def read_order_token(token: str) -> str | None:
    try:
        return signing.loads(token, salt=ORDER_SALT)
    except signing.BadSignature:
        return None


def read_ticket_token(token: str) -> str | None:
    try:
        return signing.loads(token, salt=TICKET_SALT)
    except signing.BadSignature:
        return None


def links_expired(event) -> bool:
    return timezone.now() > event.ends_at + GRACE


def order_access_url(order, absolute: bool = True) -> str:
    path = f"{reverse('tickets:order', args=[order.reference])}?t={make_order_token(order)}"
    return f"{settings.SITE_URL}{path}" if absolute else path


def ticket_download_url(ticket, kind: str, absolute: bool = False) -> str:
    path = reverse("tickets:download_ticket", args=[make_ticket_token(ticket), kind])
    return f"{settings.SITE_URL}{path}" if absolute else path


def order_pdf_url(order, absolute: bool = False) -> str:
    path = reverse("tickets:download_order", args=[make_order_token(order)])
    return f"{settings.SITE_URL}{path}" if absolute else path


def grant_order_access(request, order) -> None:
    refs = [r for r in request.session.get(SESSION_KEY, []) if r != order.reference]
    refs.append(order.reference)
    request.session[SESSION_KEY] = refs[-20:]


def has_order_access(request, order) -> bool:
    """The buyer's own browser (session) or a valid signed link from the e-mail."""
    if order.reference in request.session.get(SESSION_KEY, []):
        return True
    token = request.GET.get("t", "")
    if token and read_order_token(token) == order.reference and not links_expired(order.event):
        grant_order_access(request, order)
        return True
    return False
