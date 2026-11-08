import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from core.testing import make_event, make_ticket_type

from .models import Order, Ticket
from .qr import build_qr_payload, normalize_code, parse_qr_payload, sign
from .services import (
    StockError,
    cancel_order,
    create_order,
    fulfil_order,
    release_expired_orders,
)


class QRSignatureTests(TestCase):
    def test_roundtrip(self):
        ticket_uuid = uuid.uuid4()
        payload = build_qr_payload(ticket_uuid)
        self.assertTrue(payload.startswith(f"NWP:{ticket_uuid}:"))
        self.assertEqual(len(payload.split(":")[2]), 16)
        self.assertEqual(parse_qr_payload(payload), ticket_uuid)

    def test_tampered_signature_is_rejected(self):
        ticket_uuid = uuid.uuid4()
        payload = build_qr_payload(ticket_uuid)
        forged = payload[:-1] + ("0" if payload[-1] != "0" else "1")
        self.assertIsNone(parse_qr_payload(forged))

    def test_signature_of_another_ticket_is_rejected(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        self.assertIsNone(parse_qr_payload(f"NWP:{b}:{sign(a)}"))

    def test_secret_key_is_required_to_forge(self):
        ticket_uuid = uuid.uuid4()
        with override_settings(QR_SECRET_KEY="attacker-guess"):
            forged = build_qr_payload(ticket_uuid)
        self.assertIsNone(parse_qr_payload(forged))

    def test_garbage_is_rejected(self):
        for value in ("", "hello", "NWP:not-a-uuid:abc", "XYZ:" + str(uuid.uuid4()) + ":0000000000000000"):
            self.assertIsNone(parse_qr_payload(value))

    def test_qr_holds_no_personal_data(self):
        event = make_event()
        ticket_type = make_ticket_type(event)
        order = create_order(
            event=event, ticket_type=ticket_type, quantity=1, country="BJ",
            buyer_name="Ade Johnson", buyer_email="ade@example.com", buyer_phone="+22997000000",
        )
        order, _created = fulfil_order(order.pk)
        token = order.tickets.get().qr_token
        for personal in ("Ade", "ade@example.com", "97000000"):
            self.assertNotIn(personal, token)

    def test_normalize_code(self):
        self.assertEqual(normalize_code("nwp 7xk4 92qd"), "NWP-7XK4-92QD")
        self.assertEqual(normalize_code("NWP7XK492QD"), "NWP-7XK4-92QD")
        self.assertIsNone(normalize_code("ABC-1234-5678"))
        self.assertIsNone(normalize_code("NWP-123"))


class StockReservationTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.ticket_type = make_ticket_type(self.event, capacity=5)

    def _order(self, quantity=1, country="BJ"):
        return create_order(
            event=self.event, ticket_type=self.ticket_type, quantity=quantity, country=country,
            buyer_name="Test Buyer", buyer_email="buyer@example.com",
        )

    def test_order_reserves_stock_for_15_minutes(self):
        order = self._order(quantity=2)
        self.ticket_type.refresh_from_db()
        self.assertEqual(self.ticket_type.reserved, 2)
        self.assertEqual(self.ticket_type.available, 3)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertAlmostEqual(
            (order.reserved_until - timezone.now()).total_seconds(), 15 * 60, delta=5
        )
        self.assertEqual(order.total_amount, 20000)
        self.assertEqual(order.currency, "XOF")

    def test_nigeria_orders_use_naira_price(self):
        order = self._order(country="NG")
        self.assertEqual(order.currency, "NGN")
        self.assertEqual(order.total_amount, 25000)

    def test_cannot_oversell(self):
        self._order(quantity=4)
        with self.assertRaises(StockError):
            self._order(quantity=2)
        self.ticket_type.refresh_from_db()
        self.assertEqual(self.ticket_type.reserved, 4)

    def test_expired_reservations_are_released(self):
        order = self._order(quantity=3)
        Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(release_expired_orders(), 1)
        order.refresh_from_db()
        self.ticket_type.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(self.ticket_type.reserved, 0)
        self.assertEqual(self.ticket_type.available, 5)
        self.assertEqual(release_expired_orders(), 0)  # idempotent

    def test_payment_moves_reserved_to_sold_and_issues_tickets(self):
        order = self._order(quantity=2)
        order, created = fulfil_order(order.pk, transaction_id="tx-1")
        self.assertTrue(created)
        self.ticket_type.refresh_from_db()
        self.assertEqual((self.ticket_type.reserved, self.ticket_type.sold), (0, 2))
        self.assertEqual(order.tickets.count(), 2)
        codes = set(order.tickets.values_list("code", flat=True))
        self.assertEqual(len(codes), 2)

    def test_fulfil_is_idempotent(self):
        order = self._order(quantity=2)
        fulfil_order(order.pk)
        _order, created = fulfil_order(order.pk)
        self.assertFalse(created)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 2)
        self.ticket_type.refresh_from_db()
        self.assertEqual(self.ticket_type.sold, 2)

    def test_late_payment_after_expiry_is_honoured(self):
        order = self._order()
        Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now() - timedelta(minutes=1))
        release_expired_orders()
        order, created = fulfil_order(order.pk)
        self.assertTrue(created)
        self.assertEqual(order.status, Order.Status.PAID)
        self.ticket_type.refresh_from_db()
        self.assertEqual((self.ticket_type.reserved, self.ticket_type.sold), (0, 1))

    def test_cancel_paid_order_voids_tickets_and_frees_stock(self):
        order = self._order(quantity=2)
        fulfil_order(order.pk)
        self.assertTrue(cancel_order(order.pk, reason="test"))
        self.ticket_type.refresh_from_db()
        self.assertEqual(self.ticket_type.sold, 0)
        self.assertFalse(Ticket.objects.filter(order=order, status=Ticket.Status.VALID).exists())

    def test_holder_names_are_used(self):
        order = create_order(
            event=self.event, ticket_type=self.ticket_type, quantity=2, country="BJ",
            buyer_name="Buyer One", buyer_email="b@example.com",
            holders=[{"name": "Buyer One", "photo": ""}, {"name": "Guest Two", "photo": ""}],
        )
        fulfil_order(order.pk)
        self.assertEqual(
            list(order.tickets.order_by("pk").values_list("holder_name", flat=True)), ["Buyer One", "Guest Two"]
        )


class TicketRenderingTests(TestCase):
    def test_png_and_pdf_are_generated(self):
        from .render import ensure_order_pdf, ensure_ticket_files

        event = make_event()
        ticket_type = make_ticket_type(event)
        order = create_order(
            event=event, ticket_type=ticket_type, quantity=1, country="BJ",
            buyer_name="Render Test", buyer_email="r@example.com",
        )
        order, _created = fulfil_order(order.pk)
        ticket = ensure_ticket_files(order.tickets.get().pk)
        with ticket.ticket_png.open("rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        with ticket.ticket_pdf.open("rb") as fh:
            self.assertEqual(fh.read(4), b"%PDF")
        order = ensure_order_pdf(order.pk)
        self.assertTrue(order.tickets_pdf)
