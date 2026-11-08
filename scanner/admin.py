import base64

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
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
from .models import ScanLog, StaffCard

RESULT_TO_STATUS = {
    "accepted": "paid",
    "staff": "used",
    "already_used": "pending",
    "invalid": "failed",
    "cancelled": "cancelled",
    "wrong_event": "failed",
}


@admin.register(ScanLog)
class ScanLogAdmin(ModelAdmin):
    list_display = ("created_at", "result_badge", "ticket", "holder", "scanned_by", "raw_short")
    list_filter = ("result", "scanned_by", ("created_at", admin.DateFieldListFilter))
    search_fields = ("raw_value", "ticket__code", "ticket__holder_name", "staff_card__code", "staff_card__last_name")
    list_select_related = ("ticket", "staff_card", "scanned_by")
    readonly_fields = ("created_at", "result", "ticket", "staff_card", "raw_value", "scanned_by", "device_info")

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
