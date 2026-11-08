from django.db import migrations

GATEWAYS = [
    # code, name, subtitle EN, subtitle FR, country, currency, order
    ("fedapay", "FedaPay", "Mobile Money (MTN, Moov, Celtiis) & card", "Mobile Money (MTN, Moov, Celtiis) et carte", "BJ", "XOF", 1),
    ("feexpay", "FeexPay", "MTN, Moov, Celtiis & card", "MTN, Moov, Celtiis et carte", "BJ", "XOF", 2),
    ("paystack", "Paystack", "Card, bank transfer, USSD", "Carte, virement, USSD", "NG", "NGN", 3),
    ("flutterwave", "Flutterwave", "Card, bank transfer, USSD", "Carte, virement, USSD", "NG", "NGN", 4),
]


def create_gateways(apps, schema_editor):
    PaymentGateway = apps.get_model("payments", "PaymentGateway")
    for code, name, sub_en, sub_fr, country, currency, order in GATEWAYS:
        PaymentGateway.objects.get_or_create(
            code=code,
            defaults={
                "display_name": name,
                "description_en": sub_en,
                "description_fr": sub_fr,
                "country": country,
                "currency": currency,
                "order": order,
                "mode": "sandbox",
                "is_active": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("payments", "0002_initial")]

    operations = [migrations.RunPython(create_gateways, migrations.RunPython.noop)]
