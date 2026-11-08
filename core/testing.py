"""Small factories shared by the test suites."""
from datetime import timedelta

from django.utils import timezone

from core.models import Event
from payments.models import PaymentGateway
from tickets.models import TicketType


def make_event(**kwargs) -> Event:
    defaults = {
        "name": "NextWave Party",
        "edition": "1st Edition",
        "slug": f"nwp-{Event.objects.count() + 1}",
        "date_start": timezone.now() + timedelta(days=30),
        "venue_name": "Palais des Congrès",
        "venue_address": "Boulevard de la Marina",
        "city": "Cotonou",
        "is_active": True,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def make_ticket_type(event, **kwargs) -> TicketType:
    defaults = {
        "name_en": "Regular",
        "slug": f"regular-{TicketType.objects.count() + 1}",
        "price_xof": 10000,
        "price_ngn": 25000,
        "capacity": 10,
    }
    defaults.update(kwargs)
    return TicketType.objects.create(event=event, **defaults)


def configure_gateway(code: str, **keys) -> PaymentGateway:
    """Activate one of the gateways created by the data migration."""
    gateway = PaymentGateway.objects.get(code=code)
    gateway.is_active = True
    gateway.secret_key = keys.get("secret_key", "sk_test_secret")
    gateway.public_key = keys.get("public_key", "pk_test_public")
    gateway.extra_key = keys.get("extra_key", "extra-secret")
    gateway.save()
    return gateway
