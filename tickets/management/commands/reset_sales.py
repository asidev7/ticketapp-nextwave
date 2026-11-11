"""python manage.py reset_sales [--sale-start "2026-11-20 10:00"] [--yes]

Puts the sales of the active event back to zero before the launch: deletes its
orders (paid, pending, complimentary), tickets, print batches, check-ins, scan
logs and gateway logs, with their files, and sets every counter to 0, so the
next tickets are numbered 00001 and P-00001 again.

Kept: the event and its content, ticket types (prices, quotas, designs),
gateways, outlets, staff cards, scanner accesses and user accounts.

Without --yes nothing is changed: the command only shows what it would do.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Event, SiteSettings
from core.utils import format_money
from payments.models import PaymentLog
from scanner.models import CheckIn, ScanLog, StaffCard
from tickets.models import Order, PrintBatch, Ticket, TicketType

DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_local_datetime(value: str):
    if value.strip().lower() == "now":
        return timezone.now()
    for fmt in DATE_FORMATS:
        try:
            return timezone.make_aware(datetime.strptime(value.strip(), fmt))
        except ValueError:
            continue
    raise CommandError(f'Invalid date "{value}": use "YYYY-MM-DD HH:MM" (local time) or "now".')


def local(dt) -> str:
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M") if dt else "—"


class Command(BaseCommand):
    help = "Delete the orders and tickets of an event and set its sales counters back to zero (test data)."

    def add_arguments(self, parser):
        parser.add_argument("--event", help="Event slug (default: the active event).")
        parser.add_argument(
            "--sale-start",
            help='Also set the online sale start of every active ticket type, e.g. "2026-11-20 10:00" '
                 '(local time) or "now", and switch the online sales on.',
        )
        parser.add_argument("--yes", action="store_true", help="Really do it (otherwise only show what would happen).")

    def handle(self, *args, **options):
        event = Event.objects.filter(slug=options["event"]).first() if options["event"] else Event.get_active()
        if not event:
            raise CommandError("No event found." if options["event"] else "No active event.")
        sale_start = parse_local_datetime(options["sale_start"]) if options["sale_start"] else None
        apply = options["yes"]

        orders = Order.objects.filter(event=event)
        tickets = Ticket.objects.filter(order__event=event)
        batches = PrintBatch.objects.filter(event=event)
        checkins = CheckIn.objects.filter(ticket__order__event=event)
        scans = ScanLog.objects.filter(
            Q(ticket__order__event=event) | Q(staff_card__event=event) | Q(ticket__isnull=True, staff_card__isnull=True)
        )
        gateway_logs = PaymentLog.objects.filter(order__event=event)
        staff_cards = StaffCard.objects.filter(event=event).exclude(scan_count=0, last_scan_at__isnull=True)
        ticket_types = list(TicketType.objects.filter(event=event).order_by("order", "pk"))

        paid_online = orders.filter(status=Order.Status.PAID, is_complimentary=False, print_batch__isnull=True)
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(f"Event: {event}"))
        w(f"\n{'Will delete' if apply else 'Would delete'}:")
        w(f"  {orders.count()} orders: {paid_online.count()} paid online, "
          f"{orders.filter(is_complimentary=True).count()} complimentary, {batches.count()} print batches, "
          f"{orders.exclude(status=Order.Status.PAID).count()} unpaid / cancelled")
        for currency in ("XOF", "NGN"):
            total = paid_online.filter(currency=currency).aggregate(n=Sum("total_amount"))["n"]
            if total:
                w(f"  revenue {format_money(total, currency)}")
        w(f"  {tickets.filter(channel=Ticket.Channel.ONLINE).count()} online tickets, "
          f"{tickets.filter(channel=Ticket.Channel.PHYSICAL).count()} printed tickets, "
          f"{checkins.count()} check-ins, {scans.count()} scan logs, {gateway_logs.count()} gateway logs")
        for order in paid_online.order_by("paid_at")[:15]:
            w(f"    {order.reference}  {local(order.paid_at)}  {order.buyer_name} <{order.buyer_email}>  {order.total_display}")
        if paid_online.count() > 15:
            w(f"    … and {paid_online.count() - 15} more")

        w("\nCounters (online sold / reserved · printed):")
        for tt in ticket_types:
            start = sale_start if sale_start and tt.is_active else tt.sale_start
            window = f"sale from {local(start)}" if start else "sale open now"
            if tt.sale_end:
                window += f" until {local(tt.sale_end)}"
            w(f"  {tt.name_en}: {tt.sold} / {tt.reserved} · {tt.printed}  →  0 / 0 · 0   ({window}"
              f"{'' if tt.is_active else ', inactive'})")
            if tt.sale_end and tt.sale_end <= (start or timezone.now()):
                w(self.style.WARNING(f"    the sale end {local(tt.sale_end)} is before the start: change it in the admin"))
        site = SiteSettings.load()
        sales_open = True if sale_start else site.sales_open
        w(f"Online sales switch: {'on' if sales_open else 'OFF (Site settings → sales open)'}")

        if not apply:
            w(self.style.WARNING("\nNothing was changed. Back up the database, then run again with --yes."))
            return

        files = self._files(orders, tickets, batches)
        with transaction.atomic():
            deleted = scans.delete()[0] + gateway_logs.delete()[0]
            deleted += batches.delete()[0]  # PrintBatch.order is PROTECT: batches go first
            deleted += orders.delete()[0]  # cascades to items, tickets and check-ins
            TicketType.objects.filter(event=event).update(sold=0, reserved=0, printed=0)
            staff_cards.update(scan_count=0, last_scan_at=None)
            if sale_start:
                TicketType.objects.filter(event=event, is_active=True).update(sale_start=sale_start)
                site.sales_open = True
                site.save(update_fields=["sales_open"])
            transaction.on_commit(lambda: self._delete_files(files))

        w(self.style.SUCCESS(f"\nDone: {deleted} rows deleted, counters at zero, next numbers 00001 and P-00001."))
        if sale_start:
            w(self.style.SUCCESS(f"Online sale starts {local(sale_start)} for the active ticket types."))

    @staticmethod
    def _files(orders, tickets, batches) -> list:
        files = [order.tickets_pdf for order in orders.exclude(tickets_pdf="").only("tickets_pdf")]
        files += [batch.pdf for batch in batches.exclude(pdf="").only("pdf")]
        for ticket in tickets.only("holder_photo", "qr_image", "ticket_png", "ticket_pdf").iterator():
            files += [ticket.holder_photo, ticket.qr_image, ticket.ticket_png, ticket.ticket_pdf]
        return [(f.storage, f.name) for f in files if f]

    @staticmethod
    def _delete_files(files):
        for storage, name in files:
            try:
                storage.delete(name)
            except OSError:
                pass
