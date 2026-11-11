import base64

from urllib.parse import quote

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import action

from core.admin import private_photo_widget
from core.models import Event
from core.utils import process_holder_photo, text_color_on
from tickets.admin import badge

from .badges import card_preview_png, ensure_card_files, render_card_png, render_cards_pdf
from .models import CheckIn, ScanLog, ScannerAccess, StaffCard, new_access_code

RESULT_TO_STATUS = {
    "accepted": "paid",
    "staff": "used",
    "already_used": "pending",
    "wrong_day": "pending",
    "invalid": "failed",
    "cancelled": "cancelled",
    "wrong_event": "failed",
}


@admin.register(ScanLog)
class ScanLogAdmin(ModelAdmin):
    list_display = ("created_at", "result_badge", "ticket", "holder", "scanner", "raw_short")
    list_filter = ("result", "scanner_access", "scanned_by", ("created_at", admin.DateFieldListFilter))
    search_fields = ("raw_value", "ticket__code", "ticket__holder_name", "staff_card__code", "staff_card__last_name")
    list_select_related = ("ticket", "staff_card", "scanned_by", "scanner_access")
    readonly_fields = ("created_at", "result", "ticket", "staff_card", "raw_value", "scanned_by", "scanner_access", "device_info")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Result"), ordering="result")
    def result_badge(self, obj):
        return badge(RESULT_TO_STATUS.get(obj.result, ""), obj.get_result_display())

    @admin.display(description=_("Holder"))
    def holder(self, obj):
        if obj.ticket:
            return obj.ticket.holder_name
        if obj.staff_card:
            return f"{obj.staff_card.full_name} ({obj.staff_card.get_role_display()})"
        return "—"

    @admin.display(description=_("Scanned value"))
    def raw_short(self, obj):
        return obj.raw_value[:28] + ("…" if len(obj.raw_value) > 28 else "")

    @admin.display(description=_("Scanner"))
    def scanner(self, obj):
        return obj.scanner_access or obj.scanned_by or "—"


@admin.register(CheckIn)
class CheckInAdmin(ModelAdmin):
    list_display = ("day", "scanned_at", "ticket", "serial", "holder", "category", "scanner")
    list_filter = ("day", "access", "ticket__ticket_type")
    search_fields = ("ticket__code", "ticket__holder_name", "ticket__order__reference")
    list_select_related = ("ticket__ticket_type", "access", "scanned_by")
    date_hierarchy = "scanned_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description=_("No."), ordering="ticket__serial")
    def serial(self, obj):
        return obj.ticket.serial_label

    @admin.display(description=_("Holder"))
    def holder(self, obj):
        return obj.ticket.holder_name or "—"

    @admin.display(description=_("Category"))
    def category(self, obj):
        return obj.ticket.ticket_type

    @admin.display(description=_("Scanner"))
    def scanner(self, obj):
        return obj.scanned_by_label or "—"


# --------------------------------------------------------------------------
# Scanner accesses (login + code for the entrance agents)
# --------------------------------------------------------------------------
class ScannerAccessForm(forms.ModelForm):
    class Meta:
        model = ScannerAccess
        fields = ("name", "login", "is_active", "expires_at")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_active"):
            others = ScannerAccess.objects.filter(is_active=True).exclude(pk=self.instance.pk).count()
            if others >= ScannerAccess.MAX_ACTIVE:
                raise ValidationError(
                    _("At most %(max)s scanners can be active at the same time: deactivate another one first.")
                    % {"max": ScannerAccess.MAX_ACTIVE}
                )
        return cleaned


def _scan_url() -> str:
    return f"{settings.SITE_URL}{reverse('scanner:login')}"


@admin.register(ScannerAccess)
class ScannerAccessAdmin(ModelAdmin):
    form = ScannerAccessForm
    list_display = ("name", "login", "code_display", "is_active", "expires_at", "scan_count", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("name", "login")
    readonly_fields = ("share", "scan_count", "last_used_at", "created_at")
    actions = ["activate", "deactivate"]
    actions_detail = ["new_code"]
    fieldsets = (
        (None, {
            "description": _("Give the agent the scan page link, the login and the access code. Up to %(max)s scanners "
                             "can be active at the same time; untick “active” to log a phone out.") % {"max": ScannerAccess.MAX_ACTIVE},
            "fields": ("name", "login", ("is_active", "expires_at")),
        }),
        (_("To send to the agent"), {"fields": ("share",)}),
        (_("Use"), {"fields": (("scan_count", "last_used_at"), "created_at")}),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        number = ScannerAccess.objects.count() + 1
        while ScannerAccess.objects.filter(login=f"scan{number}").exists():
            number += 1
        initial.setdefault("login", f"scan{number}")
        initial.setdefault("name", _("Gate %(n)s") % {"n": number})
        return initial

    @admin.display(description=_("Access code"))
    def code_display(self, obj):
        return format_html('<code style="font-weight:bold;letter-spacing:1px">{}</code>', obj.code)

    @admin.display(description=_("Scan page, login and code"))
    def share(self, obj):
        if not obj or not obj.pk:
            return _("Save first: the access code is created automatically.")
        url = _scan_url()
        message = _("NextWave scanner\nPage: %(url)s\nLogin: %(login)s\nCode: %(code)s") % {
            "url": url, "login": obj.login, "code": obj.code,
        }
        return format_html(
            '<div style="line-height:1.9">'
            '{}: <a href="{}" target="_blank" rel="noopener">{}</a><br>'
            '{}: <code style="font-size:16px;font-weight:bold">{}</code><br>'
            '{}: <code style="font-size:16px;font-weight:bold;letter-spacing:2px">{}</code><br>'
            '<a href="https://wa.me/?text={}" target="_blank" rel="noopener" style="font-weight:bold">{}</a>'
            '</div>',
            _("Page"), url, url,
            _("Login"), obj.login,
            _("Access code"), obj.code,
            quote(message), _("Send by WhatsApp"),
        )

    @action(description=_("New access code"), url_path="new-code", permissions=["change"])
    def new_code(self, request, object_id):
        access = get_object_or_404(ScannerAccess, pk=object_id)
        access.code = new_access_code()
        access.save(update_fields=["code"])
        messages.success(request, _("New code created: the phone using the old one is logged out."))
        return redirect(reverse("admin:scanner_scanneraccess_change", args=[object_id]))

    @action(description=_("Activate"))
    def activate(self, request, queryset):
        free = ScannerAccess.MAX_ACTIVE - ScannerAccess.objects.filter(is_active=True).count()
        ids = list(queryset.filter(is_active=False).values_list("pk", flat=True)[: max(free, 0)])
        count = ScannerAccess.objects.filter(pk__in=ids).update(is_active=True)
        messages.success(request, _("%(count)s scanner(s) activated.") % {"count": count})
        if count < queryset.filter(is_active=False).count() + count:
            messages.warning(request, _("At most %(max)s scanners can be active at the same time.") % {"max": ScannerAccess.MAX_ACTIVE})

    @action(description=_("Deactivate (logs the phone out)"))
    def deactivate(self, request, queryset):
        count = queryset.update(is_active=False)
        messages.success(request, _("%(count)s scanner(s) deactivated.") % {"count": count})


# --------------------------------------------------------------------------
# Staff cards
# --------------------------------------------------------------------------
class StaffCardForm(forms.ModelForm):
    class Meta:
        model = StaffCard
        fields = ("event", "first_name", "last_name", "role", "title", "photo", "language", "is_active")
        widgets = {"photo": private_photo_widget()}

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and "photo" in self.changed_data:
            # Real image check, EXIF stripped, square crop, new random name.
            return process_holder_photo(photo)
        return photo


def _filename(card: StaffCard, extension: str) -> str:
    return f"staff-{slugify(card.full_name) or 'card'}-{card.code}.{extension}"


@admin.register(StaffCard)
class StaffCardAdmin(ModelAdmin):
    form = StaffCardForm
    list_display = ("photo_thumb", "full_name", "role_badge", "title", "code", "is_active", "scan_count", "last_scan_at")
    list_filter = ("role", "is_active", "event")
    search_fields = ("first_name", "last_name", "code", "title")
    list_per_page = 50
    readonly_fields = ("card_preview", "code", "scan_count", "last_scan_at", "created_at")
    actions = ["download_cards_pdf", "activate_cards", "deactivate_cards"]
    actions_detail = ["download_png", "download_pdf"]
    fieldsets = (
        (None, {"fields": ("event", ("first_name", "last_name"), ("role", "title"), "photo", ("language", "is_active"))}),
        (_("Card"), {"fields": ("card_preview", "code", ("scan_count", "last_scan_at"), "created_at")}),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        event = Event.get_active()
        if event:
            initial.setdefault("event", event.pk)
        return initial

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        ensure_card_files(obj.pk, force=True)

    @admin.display(description=_("Photo"))
    def photo_thumb(self, obj):
        if not obj.photo:
            return "—"
        return format_html(
            '<img src="{}" alt="" style="width:44px;height:44px;object-fit:cover;border-radius:8px">',
            reverse("scanner:staff_photo", args=[obj.uuid]),
        )

    @admin.display(description=_("Name"), ordering="last_name")
    def full_name(self, obj):
        return obj.full_name

    @admin.display(description=_("Role"), ordering="role")
    def role_badge(self, obj):
        return format_html(
            '<span style="background:{};color:{};padding:2px 10px;border-radius:999px;font-weight:bold;font-size:12px">{}</span>',
            obj.color, text_color_on(obj.color), obj.get_role_display(),
        )

    @admin.display(description=_("Card preview"))
    def card_preview(self, obj):
        if not obj or not obj.pk:
            return _("Save first to see the card.")
        data = base64.b64encode(card_preview_png(obj)).decode("ascii")
        return format_html(
            '<img src="data:image/png;base64,{}" alt="" style="width:100%;max-width:540px;height:auto;border-radius:12px">',
            data,
        )

    # -- downloads ------------------------------------------------------------
    @action(description=_("Download card (PNG)"), url_path="download-png")
    def download_png(self, request, object_id):
        card = get_object_or_404(StaffCard, pk=object_id)
        response = HttpResponse(render_card_png(card), content_type="image/png")
        response["Content-Disposition"] = f'attachment; filename="{_filename(card, "png")}"'
        return response

    @action(description=_("Download card (PDF)"), url_path="download-pdf")
    def download_pdf(self, request, object_id):
        card = get_object_or_404(StaffCard, pk=object_id)
        response = HttpResponse(render_cards_pdf([card]), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{_filename(card, "pdf")}"'
        return response

    @action(description=_("Download the selected cards (one PDF, card size)"))
    def download_cards_pdf(self, request, queryset):
        cards = list(queryset.select_related("event"))
        response = HttpResponse(render_cards_pdf(cards), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="staff-cards-{timezone.now():%Y%m%d-%H%M}.pdf"'
        return response

    @action(description=_("Activate"))
    def activate_cards(self, request, queryset):
        count = queryset.update(is_active=True)
        messages.success(request, _("%(count)s card(s) activated.") % {"count": count})

    @action(description=_("Deactivate (refused at the entrance)"))
    def deactivate_cards(self, request, queryset):
        count = queryset.update(is_active=False)
        messages.success(request, _("%(count)s card(s) deactivated.") % {"count": count})
