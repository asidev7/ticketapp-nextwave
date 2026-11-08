import hashlib
import hmac
import json
import time
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from core.models import SiteSettings
from core.testing import configure_gateway, make_event, make_ticket_type
from tickets.forms import CheckoutForm
from tickets.models import Order, Ticket
from tickets.services import create_order

from .models import PaymentGateway
from .providers import PAID, VerificationResult
from .services import available_gateways


class CountryRoutingTests(TestCase):
    """Benin → FedaPay + FeexPay (XOF) · Nigeria → Paystack + Flutterwave (NGN)."""

    def setUp(self):
        for code in ("fedapay", "feexpay", "paystack", "flutterwave"):
            configure_gateway(code)

    def test_benin_gets_fedapay_and_feexpay(self):
        codes = {g.code for g in available_gateways("BJ")}
        self.assertEqual(codes, {"fedapay", "feexpay"})
        self.assertTrue(all(g.currency == "XOF" for g in available_gateways("BJ")))

    def test_nigeria_gets_paystack_and_flutterwave(self):
        codes = {g.code for g in available_gateways("NG")}
        self.assertEqual(codes, {"paystack", "flutterwave"})
        self.assertTrue(all(g.currency == "NGN" for g in available_gateways("NG")))

    def test_inactive_or_unconfigured_gateways_are_hidden(self):
        PaymentGateway.objects.filter(code="feexpay").update(is_active=False)
        flutterwave = PaymentGateway.objects.get(code="flutterwave")
        flutterwave.secret_key = ""
        flutterwave.save()
        self.assertEqual({g.code for g in available_gateways("BJ")}, {"fedapay"})
        self.assertEqual({g.code for g in available_gateways("NG")}, {"paystack"})

    def test_checkout_form_rejects_gateway_of_other_country(self):
        event = make_event()
        ticket_type = make_ticket_type(event)
        paystack = PaymentGateway.objects.get(code="paystack")
        form = CheckoutForm(
            data={
                "country": "BJ", "full_name": "Ade Johnson", "email": "ade@example.com",
                "phone": "97000000", "quantity": "1", "gateway": str(paystack.pk), "accept_terms": "on",
            },
            ticket_type=ticket_type, site=SiteSettings.load(), gateways=available_gateways(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("gateway", form.errors)

    def test_checkout_form_accepts_matching_gateway_and_normalises_phone(self):
        event = make_event()
        ticket_type = make_ticket_type(event)
        fedapay = PaymentGateway.objects.get(code="fedapay")
        form = CheckoutForm(
            data={
                "country": "BJ", "full_name": "Ade Johnson", "email": "ade@example.com",
                "phone": "97 00 00 00", "quantity": "1", "gateway": str(fedapay.pk), "accept_terms": "on",
            },
            ticket_type=ticket_type, site=SiteSettings.load(), gateways=available_gateways(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone"], "+22997000000")

    def test_keys_are_encrypted_at_rest(self):
        gateway = configure_gateway("paystack", secret_key="sk_test_supersecret1234")
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT secret_key FROM payments_paymentgateway WHERE id = %s", [gateway.pk])
            raw = cursor.fetchone()[0]
        self.assertNotIn("supersecret", raw)
        gateway.refresh_from_db()
        self.assertEqual(gateway.secret_key, "sk_test_supersecret1234")
        self.assertEqual(gateway.masked("secret_key"), "••••1234")


class PaystackWebhookIdempotencyTests(TestCase):
    SECRET = "sk_test_webhook_secret"

    def setUp(self):
        self.gateway = configure_gateway("paystack", secret_key=self.SECRET)
        event = make_event()
        self.ticket_type = make_ticket_type(event, capacity=10)
        self.order = create_order(
            event=event, ticket_type=self.ticket_type, quantity=2, country="NG",
            buyer_name="Chidi Okafor", buyer_email="chidi@example.com", gateway=self.gateway,
        )
        self.order.gateway_transaction_id = f"{self.order.reference}-AB12"
        self.order.save()
        self.url = reverse("payments:webhook", args=["paystack"])

    def _post(self, payload, signature=None):
        body = json.dumps(payload).encode()
        signature = signature or hmac.new(self.SECRET.encode(), body, hashlib.sha512).hexdigest()
        return self.client.post(self.url, data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)

    def _payload(self, amount=None):
        return {
            "event": "charge.success",
            "data": {
                "id": 987654,
                "reference": self.order.gateway_transaction_id,
                "status": "success",
                "amount": (amount if amount is not None else self.order.total_amount) * 100,
                "currency": "NGN",
            },
        }

    def test_same_webhook_twice_issues_tickets_once(self):
        first = self._post(self._payload())
        second = self._post(self._payload())
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(Ticket.objects.filter(order=self.order).count(), 2)
        self.ticket_type.refresh_from_db()
        self.assertEqual((self.ticket_type.sold, self.ticket_type.reserved), (2, 0))

    def test_invalid_signature_is_rejected(self):
        response = self._post(self._payload(), signature="0" * 128)
        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertFalse(Ticket.objects.filter(order=self.order).exists())

    def test_wrong_amount_does_not_issue_tickets(self):
        response = self._post(self._payload(amount=100))
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertIn("MISMATCH", self.order.note)
        self.assertFalse(Ticket.objects.filter(order=self.order).exists())


class BrowserCallbackIsNotTrustedTests(TestCase):
    def setUp(self):
        self.gateway = configure_gateway("fedapay")
        event = make_event()
        ticket_type = make_ticket_type(event)
        self.order = create_order(
            event=event, ticket_type=ticket_type, quantity=1, country="BJ",
            buyer_name="Koffi A", buyer_email="k@example.com", gateway=self.gateway,
        )
        self.order.gateway_transaction_id = "1234"
        self.order.save()

    def test_callback_with_approved_param_but_pending_api_issues_nothing(self):
        pending = VerificationResult(status="pending", transaction_id="1234")
        with mock.patch("payments.providers.fedapay.FedaPayProvider.verify", return_value=pending):
            self.client.get(reverse("payments:callback", args=["fedapay"]), {"id": "1234", "status": "approved"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_callback_verified_by_api_issues_tickets(self):
        paid = VerificationResult(status=PAID, transaction_id="1234", amount=self.order.total_amount, currency="XOF")
        with mock.patch("payments.providers.fedapay.FedaPayProvider.verify", return_value=paid):
            response = self.client.get(reverse("payments:callback", args=["fedapay"]), {"id": "1234", "status": "approved"})
        self.assertRedirects(response, self.order.get_absolute_url(), fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.tickets.count(), 1)

    def test_fedapay_webhook_signature(self):
        provider = self.gateway.get_provider()
        body = b'{"name":"transaction.approved","entity":{"id":1234}}'
        timestamp = str(int(time.time()))
        signature = hmac.new(b"extra-secret", f"{timestamp}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
        provider.verify_signature(body, f"t={timestamp},s={signature}")  # no exception
        from .providers import InvalidSignature

        with self.assertRaises(InvalidSignature):
            provider.verify_signature(body, f"t={timestamp},s={'0' * 64}")


class CheckoutFlowTests(TestCase):
    def test_checkout_creates_order_and_redirects_to_gateway(self):
        configure_gateway("fedapay")
        event = make_event()
        ticket_type = make_ticket_type(event)
        fedapay = PaymentGateway.objects.get(code="fedapay")
        with mock.patch("payments.providers.fedapay.FedaPayProvider.initialize", return_value="https://process.fedapay.com/pay/abc"):
            response = self.client.post(
                reverse("tickets:checkout", args=[ticket_type.slug]),
                {
                    "country": "BJ", "full_name": "Ade Johnson", "email": "ade@example.com", "phone": "97000000",
                    "quantity": "2", "gateway": fedapay.pk, "accept_terms": "on",
                    "holder_name_2": "Guest Two",
                },
            )
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        self.assertEqual(response["Location"], "https://process.fedapay.com/pay/abc")
        order = Order.objects.get()
        self.assertEqual(order.total_amount, 20000)
        self.assertEqual(order.holders[1]["name"], "Guest Two")
        self.assertEqual(order.holders[0], {"name": "Ade Johnson", "photo": ""})  # no holder photos
        # The buyer's browser can see its order page
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 200)

