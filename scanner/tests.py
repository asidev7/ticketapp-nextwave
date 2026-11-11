import threading
import unittest
import uuid
from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import make_event, make_ticket_type
from tickets.models import Ticket
from tickets.qr import build_qr_payload
from tickets.services import create_order, fulfil_order

from .models import CheckIn, ScanLog, ScannerAccess
from .services import scan_ticket


def _issue_ticket(event, ticket_type):
    order = create_order(
        event=event, ticket_type=ticket_type, quantity=1, country="BJ",
        buyer_name="Scan Test", buyer_email="scan@example.com",
    )
    order, _created = fulfil_order(order.pk)
    return order.tickets.get()


class DoubleScanTests(TestCase):
    def setUp(self):
        self.event = make_event(date_start=timezone.now())
        self.ticket_type = make_ticket_type(self.event)
        self.ticket = _issue_ticket(self.event, self.ticket_type)
        self.user = User.objects.create_user("door1", "door1@example.com", "pass-12345")

    def test_second_scan_is_refused(self):
        first = scan_ticket(self.ticket.qr_token, self.user, self.event)
        second = scan_ticket(self.ticket.qr_token, self.user, self.event)
        self.assertEqual(first["result"], ScanLog.Result.ACCEPTED)
        self.assertEqual(second["result"], ScanLog.Result.ALREADY_USED)
        self.assertTrue(second["first_scan_at"])
        self.assertEqual(second["first_scanned_by"], "door1@example.com")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.USED)
        self.assertEqual(ScanLog.objects.count(), 2)

    def test_manual_code_entry(self):
        result = scan_ticket(self.ticket.code.lower().replace("-", " "), self.user, self.event)
        self.assertEqual(result["result"], ScanLog.Result.ACCEPTED)
        self.assertEqual(result["checked_in"], 1)

    def test_forged_qr_is_invalid(self):
        forged = f"NWP:{self.ticket.uuid}:{'a' * 16}"
        self.assertEqual(scan_ticket(forged, self.user, self.event)["result"], ScanLog.Result.INVALID)
        unknown = build_qr_payload(uuid.uuid4())  # well signed but no such ticket
        self.assertEqual(scan_ticket(unknown, self.user, self.event)["result"], ScanLog.Result.INVALID)

    def test_cancelled_ticket_is_refused(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(status=Ticket.Status.CANCELLED)
        self.assertEqual(scan_ticket(self.ticket.qr_token, self.user, self.event)["result"], ScanLog.Result.CANCELLED)

    def test_ticket_of_another_event_is_refused(self):
        other = make_event(slug="other-event", is_active=False)
        self.assertEqual(scan_ticket(self.ticket.qr_token, self.user, other)["result"], ScanLog.Result.WRONG_EVENT)

    def test_api_requires_scanner_group(self):
        url = reverse("scanner:api_scan")
        self.client.login(username="door1", password="pass-12345")
        self.assertEqual(self.client.post(url, {"value": "x"}, content_type="application/json").status_code, 403)
        self.user.groups.add(Group.objects.get(name="Scanner"))
        response = self.client.post(url, {"value": self.ticket.qr_token}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "accepted")
        again = self.client.post(url, {"value": self.ticket.qr_token}, content_type="application/json")
        self.assertEqual(again.json()["result"], "already_used")


@unittest.skipUnless(connection.vendor == "postgresql", "Row locks need PostgreSQL")
class ConcurrentScanTests(TransactionTestCase):
    """Same ticket scanned at the same moment on two phones: only one is accepted."""

    def test_simultaneous_scans(self):
        event = make_event(date_start=timezone.now())
        ticket = _issue_ticket(event, make_ticket_type(event))
        users = [User.objects.create_user(f"door{i}", f"door{i}@example.com", "x") for i in range(2)]
        results, barrier = [], threading.Barrier(2)

        def worker(user):
            barrier.wait()
            try:
                results.append(scan_ticket(ticket.qr_token, user, event)["result"])
            finally:
                connection.close()  # each thread has its own connection

        threads = [threading.Thread(target=worker, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["accepted", "already_used"])


def _at(day, hour):
    """Patch "now" to a local date and hour."""
    moment = timezone.make_aware(datetime.combine(day, datetime.min.time()) + timedelta(hours=hour))
    return mock.patch.object(timezone, "now", return_value=moment)


class TwoDayTicketTests(TestCase):
    """XMAS: one ticket = one entry on Dec 25 and one on Dec 26."""

    def setUp(self):
        self.event = make_event(date_start=timezone.make_aware(datetime(2026, 12, 25, 17, 0)), days=2)
        self.ticket = _issue_ticket(self.event, make_ticket_type(self.event))
        self.user = User.objects.create_user("door", "door@example.com", "pw")
        self.dec25, self.dec26 = self.event.entry_days

    def scan(self):
        return scan_ticket(self.ticket.qr_token, self.user, self.event)

    def test_one_entry_per_day(self):
        with _at(self.dec25, 18):
            first = self.scan()
            again = self.scan()
        self.assertEqual(first["result"], ScanLog.Result.ACCEPTED)
        self.assertEqual(first["entry"], {"day": 1, "days": 2, "used": 1})
        self.assertEqual(again["result"], ScanLog.Result.ALREADY_USED)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.VALID)  # day 2 still to come

        with _at(self.dec26, 20):
            second_day = self.scan()
            again = self.scan()
        self.assertEqual(second_day["result"], ScanLog.Result.ACCEPTED)
        self.assertEqual(second_day["entry"]["day"], 2)
        self.assertEqual(again["result"], ScanLog.Result.ALREADY_USED)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.USED)
        self.assertEqual(CheckIn.objects.filter(ticket=self.ticket).count(), 2)

    def test_night_after_midnight_counts_for_the_evening(self):
        with _at(self.dec25, 18):
            self.scan()
        with _at(self.dec26, 2):  # 2 AM on Dec 26 = still the Dec 25 party
            self.assertEqual(self.scan()["result"], ScanLog.Result.ALREADY_USED)

    def test_refused_outside_the_event_days(self):
        with _at(self.dec25 - timedelta(days=1), 18):
            before = self.scan()
        with _at(self.dec26 + timedelta(days=1), 18):
            after = self.scan()
        self.assertEqual(before["result"], ScanLog.Result.WRONG_DAY)
        self.assertEqual(after["result"], ScanLog.Result.WRONG_DAY)
        self.assertEqual(before["valid_days"], "25 DEC, 26 DEC")
        self.assertFalse(CheckIn.objects.exists())

    def test_counts_per_day(self):
        with _at(self.dec25, 18):
            counts = self.scan()
        self.assertEqual(counts["checked_in"], 1)
        self.assertEqual([d["count"] for d in counts["per_day"]], [1, 0])

    def test_dates_label(self):
        self.assertEqual(self.event.dates_label, "25/26 DEC 2026")
        self.assertEqual(self.event.time_label, "5PM")


class ScannerAccessTests(TestCase):
    """Entrance agents log in with the login + code given by the admin."""

    def setUp(self):
        self.event = make_event(date_start=timezone.now())
        self.ticket = _issue_ticket(self.event, make_ticket_type(self.event))
        self.access = ScannerAccess.objects.create(name="Gate 1", login="gate1")

    def login(self, login="gate1", code=None):
        return self.client.post(reverse("scanner:login"), {"mode": "code", "login": login, "code": code or self.access.code})

    def test_login_with_code_then_scan(self):
        self.assertEqual(self.client.get(reverse("scanner:scan")).status_code, 302)
        response = self.login(code=self.access.code.lower().replace("-", " "))
        self.assertRedirects(response, reverse("scanner:scan"))
        self.assertContains(self.client.get(reverse("scanner:scan")), "Gate 1")
        result = self.client.post(reverse("scanner:api_scan"), {"value": self.ticket.qr_token}, content_type="application/json")
        self.assertEqual(result.json()["result"], "accepted")
        self.assertEqual(CheckIn.objects.get().access, self.access)
        self.access.refresh_from_db()
        self.assertEqual(self.access.scan_count, 1)

    def test_wrong_code_or_login_is_refused(self):
        self.assertContains(self.login(code="AAAA-AAAA"), "Wrong login or access code")
        self.assertContains(self.login(login="gate2"), "Wrong login or access code")
        self.assertEqual(self.client.get(reverse("scanner:scan")).status_code, 302)

    def test_disabling_the_access_logs_the_phone_out(self):
        self.login()
        ScannerAccess.objects.filter(pk=self.access.pk).update(is_active=False)
        response = self.client.post(reverse("scanner:api_scan"), {"value": self.ticket.qr_token}, content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_new_code_logs_the_phone_out(self):
        self.login()
        ScannerAccess.objects.filter(pk=self.access.pk).update(code="ZZZZ-ZZZZ")
        self.assertEqual(self.client.get(reverse("scanner:scan")).status_code, 302)

    def test_expired_access_is_refused(self):
        ScannerAccess.objects.filter(pk=self.access.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertContains(self.login(), "Wrong login or access code")

    def test_at_most_six_active_scanners(self):
        from .admin import ScannerAccessForm

        for n in range(2, 7):
            ScannerAccess.objects.create(name=f"Gate {n}", login=f"gate{n}")
        form = ScannerAccessForm(data={"name": "Gate 7", "login": "gate7", "is_active": True})
        self.assertFalse(form.is_valid())
        form = ScannerAccessForm(data={"name": "Gate 7", "login": "gate7", "is_active": False})
        self.assertTrue(form.is_valid())


class StaffCardScanTests(TestCase):
    def setUp(self):
        from django.core.files.base import ContentFile

        from .models import StaffCard

        self.event = make_event()
        self.user = User.objects.create_user("door", "door@example.com", "pw")
        self.card = StaffCard(event=self.event, first_name="Tunde", last_name="Bello", role="security")
        self.card.photo.save("p.jpg", ContentFile(_jpeg()), save=False)
        self.card.save()

    def test_staff_card_is_accepted_at_every_passage(self):
        for expected_count in (1, 2):
            result = scan_ticket(self.card.qr_token, self.user, self.event)
            self.assertEqual(result["result"], ScanLog.Result.STAFF)
            self.assertEqual(result["ticket"]["holder_name"], "Tunde Bello")
            self.card.refresh_from_db()
            self.assertEqual(self.card.scan_count, expected_count)

    def test_manual_staff_code(self):
        self.assertEqual(scan_ticket(self.card.code.lower(), self.user, self.event)["result"], ScanLog.Result.STAFF)

    def test_deactivated_card_is_refused(self):
        self.card.is_active = False
        self.card.save()
        self.assertEqual(scan_ticket(self.card.qr_token, self.user, self.event)["result"], ScanLog.Result.CANCELLED)

    def test_forged_staff_qr_and_cross_use_are_refused(self):
        from tickets.qr import build_qr_payload

        forged = f"NWS:{self.card.uuid}:{'b' * 16}"
        self.assertEqual(scan_ticket(forged, self.user, self.event)["result"], ScanLog.Result.INVALID)
        # The ticket signature of the same UUID must not open a staff card (and vice versa).
        as_ticket = build_qr_payload(self.card.uuid)
        self.assertEqual(scan_ticket(as_ticket, self.user, self.event)["result"], ScanLog.Result.INVALID)
        swapped = self.card.qr_token.replace("NWS:", "NWP:")
        self.assertEqual(scan_ticket(swapped, self.user, self.event)["result"], ScanLog.Result.INVALID)

    def test_card_files_are_member_card_size(self):
        from PIL import Image

        from .badges import ensure_card_files

        card = ensure_card_files(self.card.pk, force=True)
        with card.card_png.open("rb") as fh:
            self.assertEqual(Image.open(fh).size, (1350, 852))
        with card.card_pdf.open("rb") as fh:
            self.assertEqual(fh.read(4), b"%PDF")


def _jpeg():
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 400), (10, 61, 145)).save(buffer, format="JPEG")
    return buffer.getvalue()
