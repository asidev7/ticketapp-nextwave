from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from tickets.admin import badge

from .models import ScanLog

RESULT_TO_STATUS = {
    "accepted": "paid",
    "already_used": "pending",
    "invalid": "failed",
    "cancelled": "cancelled",
    "wrong_event": "failed",
}


@admin.register(ScanLog)
class ScanLogAdmin(ModelAdmin):
    list_display = ("created_at", "result_badge", "ticket", "holder", "scanned_by", "raw_short")
    list_filter = ("result", "scanned_by", ("created_at", admin.DateFieldListFilter))
    search_fields = ("raw_value", "ticket__code", "ticket__holder_name")
    list_select_related = ("ticket", "scanned_by")
    readonly_fields = ("created_at", "result", "ticket", "raw_value", "scanned_by", "device_info")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Result"), ordering="result")
    def result_badge(self, obj):
        return badge(RESULT_TO_STATUS.get(obj.result, ""), obj.get_result_display())

    @admin.display(description=_("Holder"))
    def holder(self, obj):
        return obj.ticket.holder_name if obj.ticket else "—"

    @admin.display(description=_("Scanned value"))
    def raw_short(self, obj):
        return obj.raw_value[:28] + ("…" if len(obj.raw_value) > 28 else "")
