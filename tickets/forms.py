import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.constants import COUNTRY_CHOICES, NIGERIA, PHONE_PREFIX
from payments.models import PaymentGateway


def normalize_phone(raw: str, country: str) -> str:
    """'97 00 00 00' + BJ → '+22997000000' · '0803…' + NG → '+234803…'."""
    raw = (raw or "").strip()
    number = re.sub(r"\D", "", raw)
    if raw.startswith("+") or raw.startswith("00"):
        return "+" + number.removeprefix("00") if raw.startswith("00") else "+" + number
    prefix = PHONE_PREFIX[country]
    if number.startswith(prefix[1:]) and len(number) > 10:
        return "+" + number
    if country == NIGERIA:
        number = number.removeprefix("0")
    return prefix + number


class CheckoutForm(forms.Form):
    country = forms.ChoiceField(label=_("Country"), choices=COUNTRY_CHOICES, widget=forms.RadioSelect)
    full_name = forms.CharField(
        label=_("Full name"), max_length=120,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": _("e.g. Ade Johnson")}),
    )
    email = forms.EmailField(
        label=_("E-mail"),
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.com"}),
        help_text=_("Your tickets are sent to this address."),
    )
    phone = forms.CharField(
        label=_("Phone"), max_length=24,
        widget=forms.TextInput(attrs={"autocomplete": "tel-national", "inputmode": "tel", "placeholder": "97 00 00 00"}),
    )
    quantity = forms.TypedChoiceField(label=_("Number of tickets"), coerce=int)
    gateway = forms.ModelChoiceField(
        label=_("Payment method"), queryset=PaymentGateway.objects.none(),
        widget=forms.RadioSelect, empty_label=None,
        error_messages={"required": _("Please choose a payment method.")},
    )
    accept_terms = forms.BooleanField(
        label=_("I accept the terms and conditions"),
        error_messages={"required": _("Please accept the terms and conditions.")},
    )

    def __init__(self, *args, ticket_type, site, gateways, **kwargs):
        super().__init__(*args, **kwargs)
        self.ticket_type = ticket_type
        self.site = site
        self.max_quantity = max(1, min(site.max_tickets_per_order, ticket_type.available))
        self.fields["quantity"].choices = [(i, str(i)) for i in range(1, self.max_quantity + 1)]
        self.fields["gateway"].queryset = PaymentGateway.objects.filter(pk__in=[g.pk for g in gateways])

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name) < 3:
            raise ValidationError(_("Please enter your full name."))
        return name

    def clean(self):
        cleaned = super().clean()
        country = cleaned.get("country")
        gateway = cleaned.get("gateway")
        if country and gateway and gateway.country != country:
            self.add_error("gateway", _("This payment method is not available for the selected country."))

        if country and cleaned.get("phone"):
            phone = normalize_phone(cleaned["phone"], country)
            if not 11 <= len(phone) <= 16:
                self.add_error("phone", _("Please enter a valid phone number."))
            cleaned["phone"] = phone

        # Optional names for tickets 2…N
        quantity = cleaned.get("quantity") or 1
        cleaned["extra_holders"] = [
            " ".join(self.data.get(f"holder_name_{index}", "").split())[:120] for index in range(2, quantity + 1)
        ]
        return cleaned

    def build_holders(self) -> list[dict]:
        """The holder list stored on the order (no holder photos)."""
        names = [self.cleaned_data["full_name"]] + self.cleaned_data["extra_holders"]
        return [{"name": name or self.cleaned_data["full_name"], "photo": ""} for name in names]


class FindTicketForm(forms.Form):
    email = forms.EmailField(label=_("E-mail used for the purchase"), widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    reference = forms.CharField(
        label=_("Order reference or phone number"), max_length=40,
        widget=forms.TextInput(attrs={"placeholder": "NWP-2026-8F3K2Q"}),
    )
