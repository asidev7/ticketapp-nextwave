import logging

from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import dateformat, timezone, translation

from tickets.models import Order, Ticket
from tickets.qr import STAFF_CODE_PREFIX, STAFF_PREFIX, normalize_code, parse_qr_payload, parse_staff_payload

from .models import ScanLog, StaffCard

logger = logging.getLogger("scanner")


def _en_time(value) -> str:
    if not value:
        return ""
    with translation.override("en"):
        return dateformat.format(timezone.localtime(value), "M j · g:i:s A")


def _user_label(user) -> str:
    if not user:
        return ""
    return user.get_full_name() or user.email or user.get_username()


def event_counts(event) -> dict:
    if not event:
        return {"checked_in": 0, "total": 0}
    tickets = Ticket.objects.filter(order__event=event, order__status=Order.Status.PAID)
    return {
        "checked_in": tickets.filter(status=Ticket.Status.USED).count(),
        "total": tickets.exclude(status=Ticket.Status.CANCELLED).count(),
    }


def scan_ticket(raw_value: str, user, event, device_info: str = "") -> dict:
    """Validate a scanned QR / typed code and check the ticket in.

    The ticket row is locked (SELECT … FOR UPDATE) so the same ticket scanned
    at the same moment on two phones is accepted only once.
    """
    value = (raw_value or "").strip()[:255]
    if value.upper().startswith(f"{STAFF_PREFIX}:") or normalize_code(value, STAFF_CODE_PREFIX):
        return scan_staff_card(value, user, event, device_info)
    lookup = {}
    if value.upper().startswith("NWP:"):
        ticket_uuid = parse_qr_payload(value)  # None if the signature is wrong
        if ticket_uuid:
            lookup = {"uuid": ticket_uuid}
    else:
        code = normalize_code(value)
        if code:
            lookup = {"code": code}

    first_scan_at = first_scanned_by = ""
    with transaction.atomic():
        ticket = None
        if lookup:
            ticket = (
                Ticket.objects.select_for_update(of=("self",))
                .select_related("order", "ticket_type")
                .filter(**lookup)
                .first()
            )

        if ticket is None:
            result = ScanLog.Result.INVALID
        elif not event or ticket.order.event_id != event.pk:
            result = ScanLog.Result.WRONG_EVENT
        elif ticket.status == Ticket.Status.CANCELLED or ticket.order.status != Order.Status.PAID:
            result = ScanLog.Result.CANCELLED
        elif ticket.status == Ticket.Status.USED:
            result = ScanLog.Result.ALREADY_USED
            first_scan_at = _en_time(ticket.checked_in_at)
            first_scanned_by = _user_label(ticket.checked_in_by)
        else:
            result = ScanLog.Result.ACCEPTED
            ticket.status = Ticket.Status.USED
            ticket.checked_in_at = timezone.now()
            ticket.checked_in_by = user
            ticket.save(update_fields=["status", "checked_in_at", "checked_in_by"])

        ScanLog.objects.create(
            ticket=ticket,
            raw_value=value,
            scanned_by=user,
            device_info=(device_info or "")[:255],
            result=result,
        )

    logger.info("Scan %s by %s: %s", value[:40], user, result)
    payload = {
        "result": result,
        "ticket": None,
        "first_scan_at": first_scan_at,
        "first_scanned_by": first_scanned_by,
        **event_counts(event),
    }
    if ticket is not None and result != ScanLog.Result.INVALID:
        tt = ticket.ticket_type
        payload["ticket"] = {
            "code": ticket.code,
            "holder_name": ticket.holder_name,
            "category": tt.tr("name"),
            "zone": tt.zone_label,
            "color": tt.color,
            "order": ticket.order.reference,
            "photo_url": reverse("scanner:photo", args=[ticket.uuid]) if ticket.holder_photo else "",
        }
    return payload


def scan_staff_card(value: str, user, event, device_info: str = "") -> dict:
    """Staff badge: accepted at every passage while the card is active."""
    lookup = {}
    if value.upper().startswith(f"{STAFF_PREFIX}:"):
        card_uuid = parse_staff_payload(value)
        if card_uuid:
            lookup = {"uuid": card_uuid}
    else:
        lookup = {"code": normalize_code(value, STAFF_CODE_PREFIX)}

    with transaction.atomic():
        card = StaffCard.objects.select_for_update().filter(**lookup).first() if lookup else None
        if card is None:
            result = ScanLog.Result.INVALID
        elif not event or card.event_id != event.pk:
            result = ScanLog.Result.WRONG_EVENT
        elif not card.is_active:
            result = ScanLog.Result.CANCELLED
        else:
            result = ScanLog.Result.STAFF
            StaffCard.objects.filter(pk=card.pk).update(scan_count=F("scan_count") + 1, last_scan_at=timezone.now())
        ScanLog.objects.create(
            staff_card=card, raw_value=value, scanned_by=user, device_info=(device_info or "")[:255], result=result,
        )

    logger.info("Staff scan %s by %s: %s", value[:40], user, result)
    payload = {"result": result, "ticket": None, "staff": True, "first_scan_at": "", "first_scanned_by": "", **event_counts(event)}
    if card is not None and result != ScanLog.Result.INVALID:
        payload["ticket"] = {
            "code": card.code,
            "holder_name": card.full_name,
            "category": card.get_role_display(),
            "zone": card.title,
            "color": card.color,
            "order": "",
            "photo_url": reverse("scanner:staff_photo", args=[card.uuid]) if card.photo else "",
        }
    return payload
