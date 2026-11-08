"""Admin home dashboard (django-unfold DASHBOARD_CALLBACK)."""
from datetime import timedelta

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.utils import format_money, format_number

from .models import Event


def dashboard_callback(request, context):
    from payments.models import PaymentGateway
    from scanner.models import ScanLog
    from scanner.services import event_counts
    from tickets.models import Order, OrderItem, Ticket

    event = Event.get_active()
    paid = Order.objects.filter(status=Order.Status.PAID, is_complimentary=False)
    if event:
        paid = paid.filter(event=event)

    revenue = {row["currency"]: row for row in paid.values("currency").annotate(total=Sum("total_amount"), orders=Count("id"))}
    issued = Ticket.objects.filter(order__status=Order.Status.PAID).exclude(status=Ticket.Status.CANCELLED)
    if event:
        issued = issued.filter(order__event=event)

    categories, total_capacity = [], 0
    if event:
        for tt in event.ticket_types.all():
            items = OrderItem.objects.filter(ticket_type=tt, order__status=Order.Status.PAID)
            by_currency = {
                row["order__currency"]: row["amount"] or 0
                for row in items.values("order__currency").annotate(amount=Sum(F("quantity") * F("unit_price")))
            }
            total_capacity += tt.capacity
            percent = round(100 * tt.sold / tt.capacity) if tt.capacity else 0
            categories.append(
                {
                    "name": tt.name_en,
                    "color": tt.color,
                    "sold": format_number(tt.sold),
                    "reserved": tt.reserved,
                    "capacity": format_number(tt.capacity),
                    "percent": min(percent, 100),
                    "xof": format_money(by_currency.get("XOF", 0), "XOF"),
                    "ngn": format_money(by_currency.get("NGN", 0), "NGN"),
                }
            )

    # Tickets sold per day over the last 7 days (local time)
    today = timezone.localdate()
    start = today - timedelta(days=6)
    per_day = {
        row["day"]: row["tickets"]
        for row in issued.filter(order__paid_at__date__gte=start)
        .annotate(day=TruncDate("order__paid_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(tickets=Count("id"))
    }
    days = [start + timedelta(days=i) for i in range(7)]
    peak = max([per_day.get(d, 0) for d in days] + [1])
    chart = [
        {"label": d.strftime("%a %d"), "value": per_day.get(d, 0), "height": round(100 * per_day.get(d, 0) / peak)}
        for d in days
    ]

    gateways = []
    for row in paid.values("gateway__display_name", "currency").annotate(orders=Count("id"), total=Sum("total_amount")).order_by("-total"):
        gateways.append(
            {
                "name": row["gateway__display_name"] or "Manual",
                "orders": row["orders"],
                "total": format_money(row["total"] or 0, row["currency"]),
            }
        )

    counts = event_counts(event)
    context.update(
        {
            "nwp": {
                "event": event,
                "revenue_xof": format_money(revenue.get("XOF", {}).get("total") or 0, "XOF"),
                "revenue_ngn": format_money(revenue.get("NGN", {}).get("total") or 0, "NGN"),
                "orders_xof": revenue.get("XOF", {}).get("orders", 0),
                "orders_ngn": revenue.get("NGN", {}).get("orders", 0),
                "tickets_issued": format_number(issued.count()),
                "capacity": format_number(total_capacity),
                "pending": Order.objects.filter(status=Order.Status.PENDING).count(),
                "complimentary": issued.filter(order__is_complimentary=True).count(),
                "categories": categories,
                "chart": chart,
                "gateways": gateways,
                "gateways_ready": sum(1 for g in PaymentGateway.objects.filter(is_active=True) if g.is_configured),
                "checked_in": format_number(counts["checked_in"]),
                "checkin_total": format_number(counts["total"]),
                "recent_scans": ScanLog.objects.select_related("ticket", "scanned_by")[:8],
            }
        }
    )
    return context
