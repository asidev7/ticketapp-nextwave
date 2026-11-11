from celery import shared_task

from .models import Order


@shared_task(autoretry_for=(Exception,), retry_backoff=30, max_retries=5)
def process_paid_order(order_id: int) -> None:
    """Payment confirmed: build PNG + PDF for every ticket, then e-mail them."""
    from .emails import send_order_email
    from .render import ensure_order_pdf, ensure_ticket_files

    order = Order.objects.get(pk=order_id)
    if order.status != Order.Status.PAID:
        return
    for ticket in order.tickets.all():
        ensure_ticket_files(ticket.pk)
    ensure_order_pdf(order.pk)
    send_order_email(order)


@shared_task(autoretry_for=(Exception,), retry_backoff=30, max_retries=3)
def resend_order_email(order_id: int) -> None:
    from .emails import send_order_email

    order = Order.objects.get(pk=order_id)
    if order.status == Order.Status.PAID:
        send_order_email(order, force=True)


@shared_task(autoretry_for=(Exception,), retry_backoff=30, max_retries=3)
def regenerate_ticket_files(ticket_id: int) -> None:
    from .render import ensure_order_pdf, ensure_ticket_files

    ticket = ensure_ticket_files(ticket_id, force=True)
    ensure_order_pdf(ticket.order_id, force=True)


@shared_task(autoretry_for=(Exception,), retry_backoff=30, max_retries=2)
def build_print_batch(batch_id: int) -> None:
    """Print PDFs of a batch: A4 sheets (4 tickets per page) and ticket-size pages."""
    from .printing import build_batch_files

    build_batch_files(batch_id)


@shared_task
def release_expired_orders_task() -> int:
    from .services import release_expired_orders

    return release_expired_orders()
