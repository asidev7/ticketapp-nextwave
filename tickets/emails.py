import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from core.models import SiteSettings

from .models import Ticket
from .render import ensure_ticket_files, ticket_context
from .tokens import order_access_url, order_pdf_url

logger = logging.getLogger("tickets")


def send_order_email(order, force: bool = False) -> bool:
    """Bilingual (order language) e-mail with every ticket attached as PDF + PNG."""
    tickets = [ensure_ticket_files(t.pk) for t in order.tickets.exclude(status=Ticket.Status.CANCELLED)]
    if not tickets:
        return False
    if not force and all(t.email_sent_at for t in tickets):
        return False

    site = SiteSettings.load()
    event = order.event
    with translation.override(order.language or "en"):
        first = ticket_context(tickets[0])
        context = {
            "order": order,
            "event": event,
            "site": site,
            "tickets": [
                {"ticket": t, "category": t.ticket_type.tr("name"), "zone": t.ticket_type.zone_label}
                for t in tickets
            ],
            "date": first["date"],
            "doors": first["doors"],
            "show": first["show"],
            "access_url": order_access_url(order),
            "pdf_url": order_pdf_url(order, absolute=True),
            "practical_info": event.tr("practical_info"),
            "site_url": settings.SITE_URL,
        }
        subject = _("Your %(event)s tickets — %(reference)s") % {"event": event.name, "reference": order.reference}
        text_body = render_to_string("emails/order_tickets.txt", context)
        html_body = render_to_string("emails/order_tickets.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=site.smtp_from_email or settings.DEFAULT_FROM_EMAIL,
        to=[order.buyer_email],
        reply_to=[site.contact_email] if site.contact_email else None,
    )
    message.attach_alternative(html_body, "text/html")
    for ticket in tickets:
        with ticket.ticket_pdf.open("rb") as fh:
            message.attach(f"{ticket.code}.pdf", fh.read(), "application/pdf")
        with ticket.ticket_png.open("rb") as fh:
            message.attach(f"{ticket.code}.png", fh.read(), "image/png")
    message.send(fail_silently=False)

    Ticket.objects.filter(pk__in=[t.pk for t in tickets]).update(email_sent_at=timezone.now())
    logger.info("Tickets for %s e-mailed to %s", order.reference, order.buyer_email)
    return True
