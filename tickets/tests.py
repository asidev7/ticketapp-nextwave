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


class TicketDesignTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.ticket_type = make_ticket_type(self.event)

    def _ticket(self):
        order = create_order(
            event=self.event, ticket_type=self.ticket_type, quantity=1, country="BJ",
            buyer_name="Design Test", buyer_email="d@example.com",
        )
        order, _created = fulfil_order(order.pk)
        return order.tickets.get()

    def test_default_ticket_is_landscape_with_stub(self):
        from PIL import Image

        from .render import H, W, ensure_ticket_files

        ticket = ensure_ticket_files(self._ticket().pk)
        with ticket.ticket_png.open("rb") as fh:
            self.assertEqual(Image.open(fh).size, (W, H))
        self.assertEqual((W, H), (2400, 900))

    def test_uploaded_design_is_used_as_background(self):
        import io

        from django.core.files.base import ContentFile
        from PIL import Image

        from .render import render_ticket_image, ticket_data

        buffer = io.BytesIO()
        Image.new("RGB", (1200, 450), (200, 0, 120)).save(buffer, format="PNG")
        self.ticket_type.design_image.save("design.png", ContentFile(buffer.getvalue()), save=True)
        image = render_ticket_image(ticket_data(self._ticket()))
        self.assertEqual(image.getpixel((5, 5)), (200, 0, 120))   # artwork kept
        # Default QR card: 504 px QR + 36 px padding + code and number lines, centred at (85 %, 50 %).
        self.assertEqual(image.getpixel((2040, 132)), (255, 255, 255))  # white QR card added on the artwork

    def test_uploaded_design_keeps_its_proportions(self):
        import io

        from django.core.files.base import ContentFile
        from PIL import Image

        from .render import render_ticket_image, ticket_data, ticket_page_mm

        buffer = io.BytesIO()
        Image.new("RGB", (1000, 1000), (10, 10, 10)).save(buffer, format="PNG")
        self.ticket_type.design_image.save("square.png", ContentFile(buffer.getvalue()), save=True)
        self.assertEqual(render_ticket_image(ticket_data(self._ticket())).size, (2400, 2400))
        self.assertEqual(ticket_page_mm(self.ticket_type), (200, 200.0))

    @override_settings(TICKET_PDF_ENGINE="pillow")  # Pillow writes the page box uncompressed
    def test_ticket_pdf_is_ticket_size(self):
        from .render import ensure_ticket_files

        ticket = ensure_ticket_files(self._ticket().pk)
        with ticket.ticket_pdf.open("rb") as fh:
            pdf = fh.read()
        self.assertTrue(pdf.startswith(b"%PDF"))
        # 200 × 75 mm = 566.9 × 212.6 pt
        self.assertRegex(pdf, rb"/MediaBox \[\s*0 0 566\.\d+ 212\.\d+")


class SerialNumberTests(TestCase):
    def test_serials_follow_each_other_per_event(self):
        event = make_event()
        ticket_type = make_ticket_type(event)
        serials = []
        for quantity in (2, 1):
            order = create_order(event=event, ticket_type=ticket_type, quantity=quantity, country="BJ",
                                 buyer_name="Serial Test", buyer_email="s@example.com")
            order, _created = fulfil_order(order.pk)
            serials += list(order.tickets.order_by("pk").values_list("serial", flat=True))
        self.assertEqual(serials, [1, 2, 3])
        self.assertEqual(Ticket.objects.get(serial=3).serial_label, "00003")

        other = make_event(slug="other", is_active=False)
        order = create_order(event=other, ticket_type=make_ticket_type(other), quantity=1, country="BJ",
                             buyer_name="Other", buyer_email="o@example.com")
        order, _created = fulfil_order(order.pk)
        self.assertEqual(order.tickets.get().serial, 1)  # numbering is per event


class PrintBatchTests(TestCase):
    def setUp(self):
        self.event = make_event(date_start=timezone.now(), days=2)
        self.ticket_type = make_ticket_type(self.event, capacity=20, physical_capacity=20)

    def test_batch_creates_numbered_tickets_and_a_pdf(self):
        from django.core import mail

        from .models import PrintBatch
        from .services import create_print_batch

        with self.captureOnCommitCallbacks(execute=True):  # the PDF is built after the commit
            batch = create_print_batch(ticket_type=self.ticket_type, quantity=5, per_page=5, note="Outlet A")
        batch.refresh_from_db()
        tickets = list(batch.order.tickets.order_by("serial"))
        self.assertEqual([t.serial for t in tickets], [1, 2, 3, 4, 5])
        self.assertTrue(all(t.channel == Ticket.Channel.PHYSICAL for t in tickets))
        self.assertEqual(tickets[0].serial_label, "P-00001")
        self.assertEqual(batch.serial_range, "P-00001 → P-00005")
        self.assertEqual(len({t.code for t in tickets}), 5)
        self.assertTrue(all(parse_qr_payload(t.qr_token) == t.uuid for t in tickets))
        self.assertEqual(batch.order.status, Order.Status.PAID)
        self.assertEqual(batch.order.total_amount, 0)  # no online revenue
        self.ticket_type.refresh_from_db()
        self.assertEqual((self.ticket_type.printed, self.ticket_type.physical_available), (5, 15))
        self.assertEqual((self.ticket_type.sold, self.ticket_type.available), (0, 20))  # online stock untouched
        self.assertEqual(mail.outbox, [])  # nothing e-mailed
        self.assertEqual(batch.status, PrintBatch.Status.READY)
        with batch.pdf.open("rb") as fh:
            self.assertEqual(fh.read(4), b"%PDF")

    def test_printed_ticket_is_valid_at_the_entrance(self):
        from django.contrib.auth.models import User

        from scanner.models import ScanLog
        from scanner.services import scan_ticket

        from .services import create_print_batch

        batch = create_print_batch(ticket_type=self.ticket_type, quantity=1)
        ticket = batch.order.tickets.get()
        user = User.objects.create_user("door", "door@example.com", "pw")
        result = scan_ticket(ticket.qr_token, user, self.event)
        self.assertEqual(result["result"], ScanLog.Result.ACCEPTED)
        self.assertEqual(result["ticket"]["serial"], "P-00001")
        self.assertTrue(result["ticket"]["channel"])  # the door sees it is a printed ticket

    def test_batch_cannot_exceed_the_physical_stock(self):
        from .services import create_print_batch

        with self.assertRaises(StockError):
            create_print_batch(ticket_type=self.ticket_type, quantity=21)
        no_physical = make_ticket_type(self.event, capacity=100, physical_capacity=0)
        with self.assertRaises(StockError):  # online stock is never used for printing
            create_print_batch(ticket_type=no_physical, quantity=1)

    def test_online_and_physical_stocks_and_numbers_are_separate(self):
        from .services import create_print_batch

        self.ticket_type.capacity, self.ticket_type.physical_capacity = 2, 3
        self.ticket_type.save()
        order = create_order(event=self.event, ticket_type=self.ticket_type, quantity=2, country="BJ",
                             buyer_name="Online", buyer_email="online@example.com")
        order, _created = fulfil_order(order.pk)
        batch = create_print_batch(ticket_type=self.ticket_type, quantity=3)
        with self.assertRaises(StockError):  # online sold out, printed tickets don't help
            create_order(event=self.event, ticket_type=self.ticket_type, quantity=1, country="BJ",
                         buyer_name="Late", buyer_email="late@example.com")
        with self.assertRaises(StockError):
            create_print_batch(ticket_type=self.ticket_type, quantity=1)
        self.assertEqual([t.serial_label for t in order.tickets.order_by("serial")], ["00001", "00002"])
        self.assertEqual([t.serial_label for t in batch.order.tickets.order_by("serial")], ["P-00001", "P-00002", "P-00003"])
        self.ticket_type.refresh_from_db()
        self.assertEqual((self.ticket_type.sold, self.ticket_type.printed), (2, 3))
        self.assertTrue(self.ticket_type.is_sold_out)  # on the website

    def test_cancel_batch_and_recount_use_the_physical_counter(self):
        from .services import create_print_batch, recount_stock

        order = create_order(event=self.event, ticket_type=self.ticket_type, quantity=1, country="BJ",
                             buyer_name="Online", buyer_email="online@example.com")
        fulfil_order(order.pk)
        batch = create_print_batch(ticket_type=self.ticket_type, quantity=4)
        self.ticket_type.refresh_from_db()
        self.ticket_type.sold = self.ticket_type.printed = 99
        self.ticket_type.save()
        recount_stock(self.ticket_type)
        self.assertEqual((self.ticket_type.sold, self.ticket_type.printed), (1, 4))
        cancel_order(batch.order.pk)
        self.ticket_type.refresh_from_db()
        self.assertEqual((self.ticket_type.sold, self.ticket_type.printed), (1, 0))

    def test_quotas_cannot_go_below_what_is_issued(self):
        from django.core.exceptions import ValidationError

        from .services import create_print_batch

        create_print_batch(ticket_type=self.ticket_type, quantity=5)
        self.ticket_type.refresh_from_db()
        self.ticket_type.physical_capacity = 4
        with self.assertRaises(ValidationError):
            self.ticket_type.full_clean()

    def test_printed_ticket_looks_different(self):
        from .printing import render_print_pdf
        from .render import render_ticket_image, sample_data
        from .services import create_print_batch

        online = render_ticket_image(sample_data(self.ticket_type))
        physical = render_ticket_image(sample_data(self.ticket_type, physical=True))
        self.assertEqual(online.getpixel((2390, 450)), (255, 255, 255))   # white stub online
        self.assertNotEqual(physical.getpixel((2390, 450)), (255, 255, 255))  # gold stub printed

        batch = create_print_batch(ticket_type=self.ticket_type, quantity=1)
        order = create_order(event=self.event, ticket_type=self.ticket_type, quantity=1, country="BJ",
                             buyer_name="Online", buyer_email="online@example.com")
        order, _created = fulfil_order(order.pk)
        with self.assertRaises(ValueError):  # one look per PDF
            render_print_pdf([batch.order.tickets.get(), order.tickets.get()])

    def test_printed_design_with_uploaded_artwork(self):
        import io

        from django.core.files.base import ContentFile
        from PIL import Image

        from .render import render_ticket_image, sample_data

        for field, color in (("design_image", (200, 0, 120)), ("physical_design_image", (0, 90, 200))):
            buffer = io.BytesIO()
            Image.new("RGB", (1200, 450), color).save(buffer, format="PNG")
            getattr(self.ticket_type, field).save(f"{field}.png", ContentFile(buffer.getvalue()), save=True)
        self.assertEqual(render_ticket_image(sample_data(self.ticket_type)).getpixel((5, 5)), (200, 0, 120))
        physical = render_ticket_image(sample_data(self.ticket_type, physical=True))
        self.assertEqual(physical.getpixel((5, 5)), (0, 90, 200))  # its own artwork
        self.assertNotEqual(physical.getpixel((2040, 132)), (255, 255, 255))  # gold QR card, not white

    def test_sheet_layouts(self):
        from .printing import sheet_grid

        four = sheet_grid(4, 900 / 2400)
        five = sheet_grid(5, 900 / 2400)
        self.assertEqual((four["cols"], four["rows"], four["page_w"]), (1, 4, 210))
        self.assertEqual((five["cols"], five["rows"]), (1, 5))
        self.assertGreater(four["ticket_w"], 180)
        self.assertLess(five["ticket_h"] * 5, 297 - 20)
        square = sheet_grid(4, 1.0)  # a square design goes 2 × 2
        self.assertEqual((square["cols"], square["rows"]), (2, 2))


class SaleDateTests(TestCase):
    def setUp(self):
        self.event = make_event()

    def test_ticket_not_on_sale_before_its_start_date(self):
        from django.urls import reverse

        ticket_type = make_ticket_type(self.event, sale_start=timezone.now() + timedelta(days=2))
        self.assertTrue(ticket_type.sale_not_started)
        self.assertFalse(ticket_type.is_on_sale())
        home = self.client.get(reverse("core:home")).content.decode()
        self.assertIn("On sale from", home)
        self.assertNotIn(ticket_type.get_checkout_url(), home)
        checkout = self.client.get(ticket_type.get_checkout_url()).content.decode()
        self.assertIn("Sales for this ticket open on", checkout)
        with self.assertRaises(StockError):
            create_order(event=self.event, ticket_type=ticket_type, quantity=1, country="BJ",
                         buyer_name="Early", buyer_email="e@example.com")

        ticket_type.sale_start = timezone.now() - timedelta(minutes=1)
        ticket_type.save()
        self.assertTrue(ticket_type.is_on_sale())
        self.assertIn(ticket_type.get_checkout_url(), self.client.get(reverse("core:home")).content.decode())

    def test_ticket_not_on_sale_after_its_end_date(self):
        ticket_type = make_ticket_type(self.event, sale_end=timezone.now() - timedelta(hours=1))
        self.assertTrue(ticket_type.sale_ended)
        self.assertFalse(ticket_type.is_on_sale())


class ResetSalesTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.ticket_type = make_ticket_type(self.event, capacity=20, physical_capacity=20)

    def _sell(self, ticket_type, quantity=1, event=None):
        order = create_order(event=event or self.event, ticket_type=ticket_type, quantity=quantity, country="BJ",
                             buyer_name="Test Buyer", buyer_email="t@example.com")
        return fulfil_order(order.pk)[0]

    def _reset(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("reset_sales", *args, stdout=out)
        return out.getvalue()

    def test_without_yes_nothing_changes(self):
        self._sell(self.ticket_type, 2)
        out = self._reset()
        self.assertIn("Nothing was changed", out)
        self.assertIn("1 orders: 1 paid online", out)
        self.assertEqual(Ticket.objects.count(), 2)
        self.ticket_type.refresh_from_db()
        self.assertEqual(self.ticket_type.sold, 2)

    def test_reset_clears_the_sales_and_restarts_the_numbers(self):
        from datetime import datetime

        from core.models import SiteSettings
        from core.storage import private_storage

        from .services import create_print_batch

        self._sell(self.ticket_type, 3)
        create_order(event=self.event, ticket_type=self.ticket_type, quantity=1, country="BJ",
                     buyer_name="Pending", buyer_email="p@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            batch = create_print_batch(ticket_type=self.ticket_type, quantity=2, per_page=4)
        batch.refresh_from_db()
        self.assertTrue(private_storage().exists(batch.pdf.name))
        other = make_event(slug="other", is_active=False)
        kept = self._sell(make_ticket_type(other), event=other)
        SiteSettings.objects.update_or_create(pk=1, defaults={"sales_open": False})

        out = self._reset("--yes", "--sale-start", "2030-01-15 10:00")
        self.assertIn("Done", out)
        self.assertFalse(Order.objects.filter(event=self.event).exists())
        self.assertFalse(Ticket.objects.filter(order__event=self.event).exists())
        self.assertTrue(Order.objects.filter(pk=kept.pk).exists())  # other events are not touched
        self.assertFalse(private_storage().exists(batch.pdf.name))
        self.ticket_type.refresh_from_db()
        self.assertEqual((self.ticket_type.sold, self.ticket_type.reserved, self.ticket_type.printed), (0, 0, 0))
        self.assertEqual((self.ticket_type.capacity, self.ticket_type.physical_capacity), (20, 20))  # quotas kept
        self.assertEqual(self.ticket_type.sale_start, timezone.make_aware(datetime(2030, 1, 15, 10, 0)))
        self.assertTrue(self.ticket_type.sale_not_started)
        self.assertTrue(SiteSettings.load().sales_open)

        TicketType = type(self.ticket_type)
        TicketType.objects.filter(pk=self.ticket_type.pk).update(sale_start=None)
        self.ticket_type.refresh_from_db()
        self.assertEqual(self._sell(self.ticket_type).tickets.get().serial_label, "00001")
        batch = create_print_batch(ticket_type=self.ticket_type, quantity=1, per_page=4)
        self.assertEqual(batch.serial_range, "P-00001 → P-00001")

    def test_bad_date_is_refused(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._reset("--sale-start", "20/11/2026")
