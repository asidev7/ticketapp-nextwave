from django import forms
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget, UnfoldAdminPasswordWidget

from .models import PaymentGateway, PaymentLog
from .providers import PaymentError

SECRET_FIELDS = ("public_key", "secret_key", "extra_key")


class PaymentGatewayForm(forms.ModelForm):
    """Keys are write-only: never shown again after saving (only ••••1234)."""

    public_key = forms.CharField(label=_("Public key"), required=False, widget=UnfoldAdminPasswordWidget(render_value=False))
    secret_key = forms.CharField(label=_("Secret key"), required=False, widget=UnfoldAdminPasswordWidget(render_value=False))
    extra_key = forms.CharField(label=_("Extra key"), required=False, widget=UnfoldAdminPasswordWidget(render_value=False))
    clear_keys = forms.MultipleChoiceField(
        label=_("Delete stored keys"),
        required=False,
        choices=[("public_key", _("Public key")), ("secret_key", _("Secret key")), ("extra_key", _("Extra key"))],
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
    )

    class Meta:
        model = PaymentGateway
        fields = (
            "code", "display_name", "description_en", "description_fr", "logo", "country", "currency",
            "mode", "is_active", "public_key", "secret_key", "extra_key",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        labels = {}
        if instance and instance.code:
            labels = instance.get_provider().key_labels
        for field in SECRET_FIELDS:
            stored = instance.masked(field) if instance and instance.pk else ""
            status = _("Stored: %(mask)s — leave empty to keep it.") % {"mask": stored} if stored else _("Not set.")
            label = labels.get(field, "")
            self.fields[field].help_text = f"{label} · {status}" if label else status
            self.fields[field].widget.attrs["autocomplete"] = "new-password"

    def clean(self):
        cleaned = super().clean()
        to_clear = set(cleaned.get("clear_keys") or [])
        for field in SECRET_FIELDS:
            value = (cleaned.get(field) or "").strip()
            if field in to_clear:
                cleaned[field] = ""
            elif not value:
                cleaned[field] = getattr(self.instance, field, "") if self.instance.pk else ""
            else:
                cleaned[field] = value
        country, currency = cleaned.get("country"), cleaned.get("currency")
        if (country, currency) not in (("BJ", "XOF"), ("NG", "NGN")):
            self.add_error("currency", _("Benin uses XOF, Nigeria uses NGN."))
        return cleaned


@admin.register(PaymentGateway)
class PaymentGatewayAdmin(ModelAdmin):
    form = PaymentGatewayForm
    list_display = ("display_name", "country", "currency", "mode", "is_active", "configured")
    list_filter = ("country", "mode", "is_active")
    readonly_fields = ("key_status", "gateway_urls")
    ordering_field = "order"
    hide_ordering_field = True
    actions_detail = ["test_connection"]
    fieldsets = (
        (None, {"fields": ("code", "display_name", "description_en", "description_fr", "logo", "country", "currency")}),
        (_("Mode & activation"), {"fields": ("mode", "is_active")}),
        (_("API keys (encrypted)"), {"fields": ("key_status", "public_key", "secret_key", "extra_key", "clear_keys")}),
        (_("URLs to declare in the gateway dashboard"), {"fields": ("gateway_urls",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        return (*self.readonly_fields, "code") if obj else self.readonly_fields

    def has_add_permission(self, request):
        # The four gateways are created by a data migration.
        return PaymentGateway.objects.count() < len(PaymentGateway.Code.choices)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Ready"), boolean=True)
    def configured(self, obj):
        return obj.is_configured

    @admin.display(description=_("Stored keys"))
    def key_status(self, obj):
        if not obj.pk:
            return "—"
        parts = []
        for field in SECRET_FIELDS:
            parts.append(f"{obj._meta.get_field(field).verbose_name}: {obj.masked(field) or '—'}")
        return " · ".join(parts)

    @admin.display(description=_("URLs"))
    def gateway_urls(self, obj):
        if not obj.pk:
            return "—"
        return format_html(
            "<strong>Webhook:</strong> <code>{}</code><br><strong>Callback / redirect:</strong> <code>{}</code>",
            obj.webhook_url, obj.callback_url,
        )

    @action(description=_("Test connection"), url_path="test-connection")
    def test_connection(self, request, object_id):
        gateway = get_object_or_404(PaymentGateway, pk=object_id)
        if not gateway.is_configured:
            messages.error(request, _("Enter the keys first."))
        else:
            try:
                ok, message = gateway.get_provider().test_connection()
            except PaymentError as exc:
                ok, message = False, str(exc)
            (messages.success if ok else messages.error)(request, message)
        return redirect(reverse("admin:payments_paymentgateway_change", args=[object_id]))


@admin.register(PaymentLog)
class PaymentLogAdmin(ModelAdmin):
    list_display = ("created_at", "gateway_code", "direction", "method", "status_code", "order", "note")
    list_filter = ("gateway_code", "direction", "status_code")
    search_fields = ("order__reference", "url", "note")
    readonly_fields = [f.name for f in PaymentLog._meta.fields]
    list_select_related = ("order",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
