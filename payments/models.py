from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.constants import COUNTRY_CHOICES, CURRENCY_CHOICES
from core.crypto import mask
from core.fields import EncryptedTextField


class PaymentGateway(models.Model):
    class Code(models.TextChoices):
        FEDAPAY = "fedapay", "FedaPay"
        FEEXPAY = "feexpay", "FeexPay"
        PAYSTACK = "paystack", "Paystack"
        FLUTTERWAVE = "flutterwave", "Flutterwave"

    class Mode(models.TextChoices):
        SANDBOX = "sandbox", _("Sandbox (test)")
        LIVE = "live", _("Live")

    code = models.CharField(_("gateway"), max_length=20, choices=Code.choices, unique=True)
    display_name = models.CharField(_("display name"), max_length=60)
    description_en = models.CharField(_("subtitle (EN)"), max_length=120, blank=True, help_text=_("e.g. MTN, Moov, Celtiis, card"))
    description_fr = models.CharField(_("subtitle (FR)"), max_length=120, blank=True)
    logo = models.ImageField(_("logo"), upload_to="gateways/", blank=True)
    country = models.CharField(_("country"), max_length=2, choices=COUNTRY_CHOICES)
    currency = models.CharField(_("currency"), max_length=3, choices=CURRENCY_CHOICES)
    mode = models.CharField(_("mode"), max_length=10, choices=Mode.choices, default=Mode.SANDBOX)
    public_key = EncryptedTextField(_("public key"), blank=True)
    secret_key = EncryptedTextField(_("secret key"), blank=True)
    extra_key = EncryptedTextField(_("extra key"), blank=True)
    is_active = models.BooleanField(_("active"), default=False)
    order = models.PositiveIntegerField(_("position"), default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("payment gateway")
        verbose_name_plural = _("payment gateways")
        ordering = ["order", "pk"]

    def __str__(self):
        return self.display_name

    # -- helpers ----------------------------------------------------------
    @property
    def is_sandbox(self) -> bool:
        return self.mode == self.Mode.SANDBOX

    def get_provider(self):
        from .providers import get_provider

        return get_provider(self)

    @property
    def is_configured(self) -> bool:
        provider = self.get_provider()
        return all(getattr(self, field) for field in provider.required_keys)

    @property
    def is_available(self) -> bool:
        return self.is_active and self.is_configured

    def masked(self, field: str) -> str:
        return mask(getattr(self, field) or "")

    def tr_description(self) -> str:
        from django.utils import translation

        if (translation.get_language() or "en").startswith("fr") and self.description_fr:
            return self.description_fr
        return self.description_en

    @property
    def webhook_url(self) -> str:
        return settings.SITE_URL + self.get_provider().webhook_path()

    @property
    def callback_url(self) -> str:
        return settings.SITE_URL + reverse("payments:callback", args=[self.code])


class PaymentLog(models.Model):
    """Every request/response exchanged with a gateway (secrets redacted)."""

    class Direction(models.TextChoices):
        OUTGOING = "out", _("Request to gateway")
        CALLBACK = "callback", _("Browser callback")
        WEBHOOK = "webhook", _("Webhook received")

    gateway_code = models.CharField(_("gateway"), max_length=20, db_index=True)
    order = models.ForeignKey("tickets.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_logs", verbose_name=_("order"))
    direction = models.CharField(_("direction"), max_length=10, choices=Direction.choices)
    method = models.CharField(_("method"), max_length=8, blank=True)
    url = models.CharField(_("URL"), max_length=500, blank=True)
    status_code = models.PositiveSmallIntegerField(_("HTTP status"), null=True, blank=True)
    request_data = models.JSONField(_("request"), default=dict, blank=True)
    response_data = models.JSONField(_("response"), default=dict, blank=True)
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_at = models.DateTimeField(_("date"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("gateway log")
        verbose_name_plural = _("gateway logs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.gateway_code} {self.direction} {self.status_code or ''}"
