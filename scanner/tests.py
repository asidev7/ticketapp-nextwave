import threading
import unittest
import uuid

from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.testing import make_event, make_ticket_type
from tickets.models import Ticket
from tickets.qr import build_qr_payload
from tickets.services import create_order, fulfil_order

from .models import ScanLog
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
        self.event = make_event()
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
        event = make_event()
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
