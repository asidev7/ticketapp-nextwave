"""Order lifecycle: stock reservation, payment fulfilment, cancellation.

Stock rules
-----------
* `TicketType.reserved` — units held by pending orders (15 min).
* `TicketType.sold`     — units in paid orders.
* available = capacity - sold - reserved.
All changes happen inside transactions with row locks (select_for_update)
so two buyers can never take the last ticket at the same time.
"""
import logging
import secrets
from datetime import timedelta

import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import F, Max, Sum, Value
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.translation import gettext as _

from core.constants import BENIN, COUNTRY_CURRENCY
from core.models import Event, SiteSettings
from core.storage import private_storage
from core.tasks import dispatch

from .models import Order, OrderItem, PrintBatch, Ticket, TicketType
from .qr import build_qr_payload

logger = logging.getLogger("tickets")

# No 0/O/1/I to keep codes easy to read and type at the entrance.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class StockError(Exception):
    """Not enough tickets, sales closed, or ticket not on sale."""


def _random(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def generate_order_reference(year: int | None = None) -> str:
    year = year or timezone.now().year
    for _attempt in range(25):
        reference = f"NWP-{year}-{_random(6)}"
        if not Order.objects.filter(reference=reference).exists():
            return reference
    raise RuntimeError("Could not generate a unique order reference")


def generate_ticket_code() -> str:
    for _attempt in range(25):
        code = f"NWP-{_random(4)}-{_random(4)}"
        if not Ticket.objects.filter(code=code).exists():
            return code
    raise RuntimeError("Could not generate a unique ticket code")


def next_serials(event, count: int) -> range:
    """Reserve the next `count` serial numbers of an event.

    Must run inside a transaction: the event row is locked until commit, so
    two orders paid at the same moment never get the same numbers.
    """
    Event.objects.select_for_update().filter(pk=event.pk).first()
    last = Ticket.objects.filter(order__event_id=event.pk).aggregate(n=Max("serial"))["n"] or 0
    return range(last + 1, last + 1 + count)


# --------------------------------------------------------------------------
# Creation & reservation
# --------------------------------------------------------------------------
def create_order(
    *,
    event,
    ticket_type,
    quantity: int,
    country: str,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str = "",
    holders: list | None = None,
    gateway=None,
    language: str = "en",
    ip_address: str | None = None,
) -> Order:
    """Create a pending order and reserve its stock for ORDER_RESERVATION_MINUTES."""
    if quantity < 1:
        raise StockError(_("Please choose at least one ticket."))
    currency = COUNTRY_CURRENCY[country]
    site = SiteSettings.load()
    now = timezone.now()

    with transaction.atomic():
        ticket_type = TicketType.objects.select_for_update().get(pk=ticket_type.pk)
        if not site.sales_open or not ticket_type.is_on_sale(now):
            raise StockError(_("This ticket is not on sale right now."))
        if ticket_type.available < quantity:
            raise StockError(
                _("Only %(count)s ticket(s) left in this category.") % {"count": ticket_type.available}
            )
        unit_price = ticket_type.price_for(currency)
        if unit_price <= 0:
            raise StockError(_("This ticket is not sold in %(currency)s.") % {"currency": currency})

        subtotal = unit_price * quantity
        fees = site.service_fee(currency)
        order = Order.objects.create(
            reference=generate_order_reference(event.date_start.year),
            event=event,
            buyer_name=buyer_name.strip(),
            buyer_email=buyer_email.strip().lower(),
            buyer_phone=buyer_phone.strip(),
            country=country,
            currency=currency,
            subtotal=subtotal,
            fees=fees,
            total_amount=subtotal + fees,
            gateway=gateway,
            holders=holders or [],
            reserved_until=now + timedelta(minutes=settings.ORDER_RESERVATION_MINUTES),
            language=language[:2] if language else "en",
            ip_address=ip_address,
            stock_state=Order.StockState.RESERVED,
        )
        OrderItem.objects.create(order=order, ticket_type=ticket_type, quantity=quantity, unit_price=unit_price)
        TicketType.objects.filter(pk=ticket_type.pk).update(reserved=F("reserved") + quantity)
    return order


def _release_stock(order: Order) -> None:
    """Give reserved units back. Caller must hold the order row lock."""
    if order.stock_state != Order.StockState.RESERVED:
        return
    for item in order.items.all():
        TicketType.objects.filter(pk=item.ticket_type_id).update(
            reserved=Greatest(F("reserved") - item.quantity, Value(0))
        )
    order.stock_state = Order.StockState.RELEASED


def _delete_pending_photos(holders: list) -> None:
    storage = private_storage()
    for holder in holders or []:
        path = holder.get("photo")
        if path:
            try:
                storage.delete(path)
            except OSError:
                pass


def cancel_pending_order(order_id: int, status: str = Order.Status.CANCELLED, reason: str = "") -> bool:
    """pending → cancelled/failed, releasing the reservation. Idempotent."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.PENDING:
            return False
        order.status = status
        _release_stock(order)
        if reason:
            order.note = f"{order.note}\n{timezone.now():%Y-%m-%d %H:%M} {reason}".strip()
        order.save(update_fields=["status", "stock_state", "note", "updated_at"])
        holders = list(order.holders or [])
        transaction.on_commit(lambda: _delete_pending_photos(holders))
    logger.info("Order %s → %s (%s)", order.reference, status, reason)
    return True


def mark_order_failed(order_id: int, reason: str = "") -> bool:
    return cancel_pending_order(order_id, Order.Status.FAILED, reason or "Payment failed")


def release_expired_orders(now=None) -> int:
    """Cancel pending orders whose 15-minute reservation is over."""
    now = now or timezone.now()
    expired = Order.objects.filter(
        status=Order.Status.PENDING,
        stock_state=Order.StockState.RESERVED,
        reserved_until__lt=now,
    ).values_list("pk", flat=True)[:500]
    released = sum(1 for pk in list(expired) if cancel_pending_order(pk, reason="Reservation expired"))
    if released:
        logger.info("Released %s expired order(s)", released)
    return released


# --------------------------------------------------------------------------
# Payment confirmed → tickets
# --------------------------------------------------------------------------
def fulfil_order(order_id: int, *, transaction_id: str = "", payload=None, gateway=None, note: str = "") -> tuple[Order, bool]:
    """Mark an order paid and issue its tickets — exactly once.

    Called only after server-side verification (webhook signature and/or the
    gateway's verify API). Safe to call several times: the order row is
    locked and an already-paid order is returned untouched (`created=False`).
    A payment confirmed after the reservation expired is still honoured.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status == Order.Status.PAID:
            return order, False
        if order.status == Order.Status.REFUNDED:
            logger.warning("Ignoring payment confirmation for refunded order %s", order.reference)
            return order, False

        for item in order.items.all():
            if order.stock_state == Order.StockState.RESERVED:
                TicketType.objects.filter(pk=item.ticket_type_id).update(
                    reserved=Greatest(F("reserved") - item.quantity, Value(0)),
                    sold=F("sold") + item.quantity,
                )
            elif order.stock_state == Order.StockState.RELEASED:
                TicketType.objects.filter(pk=item.ticket_type_id).update(sold=F("sold") + item.quantity)
                logger.warning("Late payment for %s: stock re-allocated (check capacity)", order.reference)

        order.stock_state = Order.StockState.SOLD
        order.status = Order.Status.PAID
        order.paid_at = timezone.now()
        if gateway is not None:
            order.gateway = gateway
        order.gateway_payload = {"transaction_id": transaction_id, "data": payload or {}}
        if note:
            order.note = f"{order.note}\n{timezone.now():%Y-%m-%d %H:%M} {note}".strip()
        order.save()
        _create_tickets(order)

        from .tasks import process_paid_order

        order_pk = order.pk
        transaction.on_commit(lambda: dispatch(process_paid_order, order_pk))
    logger.info("Order %s paid — %s ticket(s) issued", order.reference, order.tickets.count())
    return order, True


def _create_tickets(order: Order) -> None:
    holders = order.holders or []
    items = list(order.items.select_related("ticket_type"))
    serials = iter(next_serials(order.event, sum(item.quantity for item in items)))
    index = 0
    for item in items:
        for _unit in range(item.quantity):
            holder = holders[index] if index < len(holders) else {}
            ticket = Ticket(
                order=order,
                ticket_type=item.ticket_type,
                code=generate_ticket_code(),
                serial=next(serials),
                holder_name=(holder.get("name") or order.buyer_name)[:120],
            )
            if holder.get("photo"):
                ticket.holder_photo.name = holder["photo"]
            ticket.save()
            index += 1


def issue_complimentary(
    *,
    event,
    ticket_type,
    quantity: int,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str = "",
    language: str = "en",
    holders: list | None = None,
    note: str = "",
) -> Order:
    """Free tickets (guests, press, artists): no payment, e-mailed right away."""
    with transaction.atomic():
        ticket_type = TicketType.objects.select_for_update().get(pk=ticket_type.pk)
        if ticket_type.available < quantity:
            raise StockError(_("Only %(count)s ticket(s) left in this category.") % {"count": ticket_type.available})
        order = Order.objects.create(
            reference=generate_order_reference(event.date_start.year),
            event=event,
            buyer_name=buyer_name.strip(),
            buyer_email=buyer_email.strip().lower(),
            buyer_phone=buyer_phone.strip(),
            country=BENIN,
            currency=COUNTRY_CURRENCY[BENIN],
            subtotal=0,
            fees=0,
            total_amount=0,
            holders=holders or [],
            language=language,
            is_complimentary=True,
            note=note,
            stock_state=Order.StockState.RESERVED,
        )
        OrderItem.objects.create(order=order, ticket_type=ticket_type, quantity=quantity, unit_price=0)
        TicketType.objects.filter(pk=ticket_type.pk).update(reserved=F("reserved") + quantity)
        order, _created = fulfil_order(order.pk, transaction_id="complimentary", note="Complimentary tickets")
    return order


# --------------------------------------------------------------------------
# Printed tickets (bulk)
# --------------------------------------------------------------------------
def _unique_codes(count: int) -> list[str]:
    codes: set[str] = set()
    while len(codes) < count:
        wanted = {f"NWP-{_random(4)}-{_random(4)}" for _ in range(count - len(codes))}
        codes |= wanted - set(Ticket.objects.filter(code__in=wanted).values_list("code", flat=True))
    return list(codes)


def create_print_batch(*, ticket_type, quantity: int, paper: str = PrintBatch.Paper.A4, per_page: int = 4,
                       outlet=None, note: str = "", user=None) -> PrintBatch:
    """Issue `quantity` bearer tickets for printing: each gets a signed QR code,
    a code and the next serial numbers. They count as sold (stock) but bring no
    online revenue; nothing is e-mailed. The print PDF is built afterwards."""
    if not 1 <= quantity <= PrintBatch.MAX_QUANTITY:
        raise StockError(_("Choose between 1 and %(max)s tickets.") % {"max": PrintBatch.MAX_QUANTITY})
    with transaction.atomic():
        ticket_type = TicketType.objects.select_for_update().select_related("event").get(pk=ticket_type.pk)
        if ticket_type.available < quantity:
            raise StockError(_("Only %(count)s ticket(s) left in this category.") % {"count": ticket_type.available})
        event = ticket_type.event
        now = timezone.now()
        order = Order.objects.create(
            reference=generate_order_reference(event.date_start.year),
            event=event,
            buyer_name=f"Print batch — {ticket_type.name_en}"[:120],
            buyer_email=(getattr(user, "email", "") or "").lower(),
            country=BENIN,
            currency=COUNTRY_CURRENCY[BENIN],
            status=Order.Status.PAID,
            stock_state=Order.StockState.SOLD,
            paid_at=now,
            note=note,
        )
        OrderItem.objects.create(order=order, ticket_type=ticket_type, quantity=quantity, unit_price=0)
        TicketType.objects.filter(pk=ticket_type.pk).update(sold=F("sold") + quantity)

        serials = next_serials(event, quantity)
        tickets = []
        for serial, code in zip(serials, _unique_codes(quantity)):
            ticket_uuid = uuid.uuid4()
            tickets.append(Ticket(
                order=order, ticket_type=ticket_type, uuid=ticket_uuid, code=code, serial=serial,
                qr_token=build_qr_payload(ticket_uuid),
            ))
        Ticket.objects.bulk_create(tickets)

        batch = PrintBatch.objects.create(
            event=event, ticket_type=ticket_type, quantity=quantity, outlet=outlet, note=note,
            paper=paper, per_page=per_page, order=order, first_serial=serials[0], last_serial=serials[-1], created_by=user,
        )
        from .tasks import build_print_batch

        transaction.on_commit(lambda: dispatch(build_print_batch, batch.pk))
    logger.info("Print batch %s: %s × %s (%s)", batch.pk, quantity, ticket_type, batch.serial_range)
    return batch


# --------------------------------------------------------------------------
# Admin operations
# --------------------------------------------------------------------------
def cancel_order(order_id: int, reason: str = "") -> bool:
    """Cancel a pending order (release stock) or a paid one (void its tickets)."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status == Order.Status.PENDING:
            order.status = Order.Status.CANCELLED
            _release_stock(order)
        elif order.status == Order.Status.PAID:
            for item in order.items.all():
                TicketType.objects.filter(pk=item.ticket_type_id).update(
                    sold=Greatest(F("sold") - item.quantity, Value(0))
                )
            order.tickets.exclude(status=Ticket.Status.USED).update(status=Ticket.Status.CANCELLED)
            order.status = Order.Status.CANCELLED
            order.stock_state = Order.StockState.RELEASED
        else:
            return False
        if reason:
            order.note = f"{order.note}\n{timezone.now():%Y-%m-%d %H:%M} {reason}".strip()
        order.save()
    return True


def recount_stock(ticket_type: TicketType) -> TicketType:
    """Rebuild sold/reserved counters from orders (safety net for the admin)."""
    items = OrderItem.objects.filter(ticket_type=ticket_type)
    sold = items.filter(order__stock_state=Order.StockState.SOLD).aggregate(n=Sum("quantity"))["n"] or 0
    reserved = (
        items.filter(order__status=Order.Status.PENDING, order__stock_state=Order.StockState.RESERVED)
        .aggregate(n=Sum("quantity"))["n"]
        or 0
    )
    TicketType.objects.filter(pk=ticket_type.pk).update(sold=sold, reserved=reserved)
    ticket_type.refresh_from_db()
    return ticket_type
