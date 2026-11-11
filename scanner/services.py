import logging

from django.db import models, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import dateformat, timezone, translation

from core.models import event_day
from tickets.models import Order, Ticket
from tickets.qr import STAFF_CODE_PREFIX, STAFF_PREFIX, normalize_code, parse_qr_payload, parse_staff_payload

from .models import CheckIn, ScanLog, ScannerAccess, StaffCard

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


def _scanner_label(user, access) -> str:
    return access.name if access else _user_label(user)


def event_counts(event) -> dict:
    """Entries today (or on the last entry day once the event is over) out of the valid tickets."""
    if not event:
        return {"checked_in": 0, "total": 0, "day": 0, "days": 1, "per_day": []}
    tickets = Ticket.objects.filter(order__event=event, order__status=Order.Status.PAID)
    days = event.entry_days
    counts = {
        row["day"]: row["n"]
        for row in CheckIn.objects.filter(ticket__order__event=event).values("day").annotate(n=models.Count("id"))
    }
    today = event.day_number()
    shown = today or (len(days) if event_day(timezone.now()) > days[-1] else 0)
    return {
        "checked_in": counts.get(days[shown - 1], 0) if shown else 0,
        "total": tickets.exclude(status=Ticket.Status.CANCELLED).count(),
        "day": today,
        "days": len(days),
        "per_day": [{"n": i + 1, "label": event.day_label(d), "count": counts.get(d, 0)} for i, d in enumerate(days)],
    }


def scan_ticket(raw_value: str, user, event, device_info: str = "", access: ScannerAccess | None = None) -> dict:
    """Validate a scanned QR / typed code and record one entry for today.

    A ticket gives one entry per event day (a 2-day ticket: day 1 and day 2);
    outside the event days it is refused. The ticket row is locked (SELECT …
    FOR UPDATE) so the same ticket scanned at the same moment on two phones
    is accepted only once.
    """
    value = (raw_value or "").strip()[:255]
    if value.upper().startswith(f"{STAFF_PREFIX}:") or normalize_code(value, STAFF_CODE_PREFIX):
        return scan_staff_card(value, user, event, device_info, access)
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
    now = timezone.now()
    day_number = event.day_number(now) if event else 0
    entries_used = 0
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
        elif not day_number:
            result = ScanLog.Result.WRONG_DAY
        else:
            today = event.entry_days[day_number - 1]
            entries = list(CheckIn.objects.filter(ticket=ticket).select_related("scanned_by", "access"))
            done_today = next((entry for entry in entries if entry.day == today), None)
            entries_used = len(entries)
            if done_today:
                result = ScanLog.Result.ALREADY_USED
                first_scan_at = _en_time(done_today.scanned_at)
                first_scanned_by = done_today.scanned_by_label
            elif ticket.status == Ticket.Status.USED and not entries:
                # Checked in before daily entries existed.
                result = ScanLog.Result.ALREADY_USED
                first_scan_at = _en_time(ticket.checked_in_at)
                first_scanned_by = _user_label(ticket.checked_in_by)
            else:
                result = ScanLog.Result.ACCEPTED
                CheckIn.objects.create(ticket=ticket, day=today, scanned_at=now, scanned_by=user, access=access)
                entries_used += 1
                fields = []
                if not ticket.checked_in_at:
                    ticket.checked_in_at, ticket.checked_in_by = now, user
                    fields += ["checked_in_at", "checked_in_by"]
                if entries_used >= event.days:
                    ticket.status = Ticket.Status.USED
                    fields.append("status")
                if fields:
                    ticket.save(update_fields=fields)

        ScanLog.objects.create(
            ticket=ticket,
            raw_value=value,
            scanned_by=user,
            scanner_access=access,
            device_info=(device_info or "")[:255],
            result=result,
        )
        if access:
            ScannerAccess.objects.filter(pk=access.pk).update(scan_count=F("scan_count") + 1, last_used_at=now)

    logger.info("Scan %s by %s: %s", value[:40], user, result)
    payload = {
        "result": result,
        "ticket": None,
        "first_scan_at": first_scan_at,
        "first_scanned_by": first_scanned_by,
        "entry": {"day": day_number, "days": event.days if event else 1, "used": entries_used},
        "valid_days": ", ".join(event.day_label(d) for d in event.entry_days) if event else "",
        **event_counts(event),
    }
    if ticket is not None and result != ScanLog.Result.INVALID:
        tt = ticket.ticket_type
        payload["ticket"] = {
            "code": ticket.code,
            "serial": ticket.serial_label,
            "holder_name": ticket.holder_name,
            "category": tt.tr("name"),
            "zone": tt.zone_label,
            "color": tt.color,
            "order": ticket.order.reference,
            "photo_url": reverse("scanner:photo", args=[ticket.uuid]) if ticket.holder_photo else "",
        }
    return payload


def scan_staff_card(value: str, user, event, device_info: str = "", access: ScannerAccess | None = None) -> dict:
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
            staff_card=card, raw_value=value, scanned_by=user, scanner_access=access,
            device_info=(device_info or "")[:255], result=result,
        )
        if access:
            ScannerAccess.objects.filter(pk=access.pk).update(scan_count=F("scan_count") + 1, last_used_at=timezone.now())

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
