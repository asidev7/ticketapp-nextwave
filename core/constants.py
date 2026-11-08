from django.utils.translation import gettext_lazy as _

BENIN = "BJ"
NIGERIA = "NG"

COUNTRY_CHOICES = [
    (BENIN, _("Benin")),
    (NIGERIA, _("Nigeria")),
]

XOF = "XOF"
NGN = "NGN"

CURRENCY_CHOICES = [
    (XOF, "XOF (FCFA)"),
    (NGN, "NGN (Naira)"),
]

COUNTRY_CURRENCY = {BENIN: XOF, NIGERIA: NGN}
PHONE_PREFIX = {BENIN: "+229", NIGERIA: "+234"}
