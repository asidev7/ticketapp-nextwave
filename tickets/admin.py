import base64
import csv

from django import forms
from django.contrib import admin, messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action
from unfold.widgets import (
    UnfoldAdminIntegerFieldWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
)

from core.admin import private_photo_widget, thumb
from core.models import Event
from core.storage import private_storage
from core.tasks import dispatch
from core.utils import format_money, format_number, process_holder_photo

from scanner.models import CheckIn

from .models import ComplimentaryOrder, Order, OrderItem, PrintBatch, Ticket, TicketBenefit, TicketType
from .printing import render_print_pdf
from .qr import make_qr_png
from .render import render_preview_png
from .services import StockError, cancel_order, create_print_batch, fulfil_order, issue_complimentary, recount_stock
from .tasks import build_print_batch, regenerate_ticket_files, resend_order_email
from .tokens import order_access_url, ticket_download_url

STATUS_COLORS = {
    "paid": ("#DCFCE7", "#14532D"),
    "valid": ("#DCFCE7", "#14532D"),
    "pending": ("#FEF3C7", "#78350F"),
    "used": ("#DBEAFE", "#1E3A8A"),
    "ready": ("#DCFCE7", "#14532D"),
    "generating": ("#FEF3C7", "#78350F"),
    "failed": ("#FEE2E2", "#7F1D1D"),
    "cancelled": ("#E5E7EB", "#111827"),
    "refunded": ("#E5E7EB", "#111827"),
}


def badge(value: str, label: str):
    bg, fg = STATUS_COLORS.get(value, ("#E5E7EB", "#111827"))
    return format_html(
        '<span style="background:{};color:{};padding:2px 10px;border-radius:999px;font-weight:bold;font-size:12px">{}</span>',
        bg, fg, label,
    )


# --------------------------------------------------------------------------
# Ticket types
# --------------------------------------------------------------------------
class TicketBenefitInline(TabularInline):
    model = TicketBenefit
    extra = 1
    fields = ("text_en", "text_fr", "order")
    ordering_field = "order"
    hide_ordering_field = True


@admin.register(TicketType)
class TicketTypeAdmin(ModelAdmin):
    list_display = ("name_en", "zone_label", "price_xof_display", "price_ngn_display", "stock", "is_active")
    list_filter = ("event", "is_active")
    search_fields = ("name_en", "name_fr", "zone_label")
    prepopulated_fields = {"slug": ("name_en",)}
    readonly_fields = ("sold", "reserved", "available_display", "seat_preview", "ticket_preview")
    inlines = [TicketBenefitInline]
    ordering_field = "order"
    hide_ordering_field = True
    actions = ["recount"]
    fieldsets = (
        (None, {"fields": ("event", "name_en", "name_fr", "slug", "color", "is_active")}),
        (_("Prices"), {"fields": ("price_xof", "price_ngn")}),
        (_("Stock"), {"fields": ("capacity", "sold", "reserved", "available_display", "sale_start", "sale_end")}),
        (_("Description"), {"fields": ("description_en", "description_fr")}),
        (_("Spot in the hall"), {"fields": ("zone_label", "seat_image", "seat_preview")}),
        (_("Ticket design"), {
            "description": _("Upload one copy of your ticket design for this type. On every ticket (online and printed) "
                             "the system adds the QR code, the ticket code and its number (No. 00042), valid for each "
                             "day of the event. Choose where the QR goes, then save to refresh the preview."),
            "fields": ("design_image", ("qr_x", "qr_y", "qr_size"), "holder_overlay", "ticket_preview"),
        }),
    )

    @admin.display(description=_("Price XOF"), ordering="price_xof")
    def price_xof_display(self, obj):
        return format_money(obj.price_xof, "XOF")

    @admin.display(description=_("Price NGN"), ordering="price_ngn")
    def price_ngn_display(self, obj):
        return format_money(obj.price_ngn, "NGN")

    @admin.display(description=_("Sold / capacity"))
    def stock(self, obj):
        return f"{format_number(obj.sold)} / {format_number(obj.capacity)} (+{obj.reserved} reserved)"

    @admin.display(description=_("Available"))
    def available_display(self, obj):
        return format_number(obj.available)

    @admin.display(description=_("Preview"))
    def seat_preview(self, obj):
        return thumb(obj.seat_image, 220)

    @admin.display(description=_("Ticket preview"))
    def ticket_preview(self, obj):
        if not obj or not obj.pk:
            return _("Save first to see the preview.")
        data = base64.b64encode(render_preview_png(obj, width=1200)).decode("ascii")
        return format_html(
            '<img src="data:image/png;base64,{}" alt="" style="width:100%;max-width:720px;height:auto;'
            'border:1px solid #E5E7EB;border-radius:8px">',
            data,
        )

    @action(description=_("Recount sold / reserved from orders"))
    def recount(self, request, queryset):
        for ticket_type in queryset:
            recount_stock(ticket_type)
        messages.success(request, _("Stock counters rebuilt."))


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("ticket_type", "quantity", "unit_price")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class TicketInline(TabularInline):
    model = Ticket
    extra = 0
    can_delete = False
    fields = ("code_link", "holder_name", "ticket_type", "status", "checked_in_at", "email_sent_at", "download_count")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Code"))
    def code_link(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:tickets_ticket_change", args=[obj.pk]), obj.code)


class OrderExportMixin:
    EXPORT_HEADERS = [
        "Reference", "Status", "Created", "Paid at", "Buyer", "E-mail", "Phone", "Country",
        "Currency", "Subtotal", "Fees", "Total", "Gateway", "Transaction", "Tickets", "Complimentary",
    ]

    def _rows(self, queryset):
        for order in queryset.select_related("gateway").prefetch_related("items"):
            yield [
                order.reference,
                order.status,
                timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M"),
                timezone.localtime(order.paid_at).strftime("%Y-%m-%d %H:%M") if order.paid_at else "",
                order.buyer_name,
                order.buyer_email,
                order.buyer_phone,
                order.country,
                order.currency,
                order.subtotal,
                order.fees,
                order.total_amount,
                order.gateway.display_name if order.gateway else "",
                order.gateway_transaction_id,
                order.quantity,
                "yes" if order.is_complimentary else "",
            ]

    @action(description=_("Export CSV"))
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="orders-{timezone.now():%Y%m%d-%H%M}.csv"'
        response.write("﻿")  # Excel-friendly UTF-8
        writer = csv.writer(response)
        writer.writerow(self.EXPORT_HEADERS)
        writer.writerows(self._rows(queryset))
        return response

    @action(description=_("Export Excel"))
    def export_xlsx(self, request, queryset):
        from openpyxl import Workbook
        from openpyxl.styles import Font

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Orders"
        sheet.append(self.EXPORT_HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in self._rows(queryset):
            sheet.append(row)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 18
        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="orders-{timezone.now():%Y%m%d-%H%M}.xlsx"'
        workbook.save(response)
        return response


@admin.register(Order)
class OrderAdmin(OrderExportMixin, ModelAdmin):
    list_display = ("reference", "buyer_name", "buyer_email", "country_flag", "total", "status_badge", "gateway", "created_at")
    list_filter = ("status", "country", "gateway", "is_complimentary", ("created_at", admin.DateFieldListFilter))
    search_fields = ("reference", "buyer_email", "buyer_phone", "buyer_name", "gateway_transaction_id", "tickets__code")
    date_hierarchy = "created_at"
    list_select_related = ("gateway",)
    inlines = [OrderItemInline, TicketInline]
    actions = ["resend_tickets", "mark_as_paid", "cancel_orders", "export_csv", "export_xlsx"]
    actions_detail = ["resend_detail", "mark_paid_detail"]
    readonly_fields = (
        "reference", "event", "status_badge", "country", "currency", "subtotal", "fees", "total",
        "gateway", "gateway_transaction_id", "gateway_payload", "reserved_until", "is_complimentary",
        "ip_address", "created_at", "paid_at", "access_link",
    )
    fieldsets = (
        (None, {"fields": ("reference", "event", "status_badge", "access_link")}),
        (_("Buyer"), {"fields": ("buyer_name", "buyer_email", "buyer_phone", "country", "language")}),
        (_("Amount"), {"fields": ("currency", "subtotal", "fees", "total")}),
        (_("Payment"), {"fields": ("gateway", "gateway_transaction_id", "gateway_payload", "reserved_until", "created_at", "paid_at", "ip_address")}),
        (_("Notes"), {"fields": ("is_complimentary", "note")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        return badge(obj.status, obj.get_status_display())

    @admin.display(description=_("Total"), ordering="total_amount")
    def total(self, obj):
        return obj.total_display

    @admin.display(description=_("Country"), ordering="country")
    def country_flag(self, obj):
        return {"BJ": "🇧🇯 BJ", "NG": "🇳🇬 NG"}.get(obj.country, obj.country)

    @admin.display(description=_("Buyer link"))
    def access_link(self, obj):
        if not obj.pk or obj.status != Order.Status.PAID:
            return "—"
        url = order_access_url(obj)
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Open the buyer's ticket page"))

    # -- bulk actions -------------------------------------------------------
    @action(description=_("Resend tickets by e-mail"))
    def resend_tickets(self, request, queryset):
        count = 0
        for order in queryset.filter(status=Order.Status.PAID):
            dispatch(resend_order_email, order.pk)
            count += 1
        messages.success(request, _("%(count)s order(s) queued for e-mail.") % {"count": count})

    @action(description=_("Mark as paid (manual)"))
    def mark_as_paid(self, request, queryset):
        count = 0
        for order in queryset.exclude(status__in=[Order.Status.PAID, Order.Status.REFUNDED]):
            _order, created = fulfil_order(order.pk, transaction_id="manual", note=f"Marked as paid by {request.user}")
            count += int(created)
        messages.success(request, _("%(count)s order(s) marked as paid — tickets issued.") % {"count": count})

    @action(description=_("Cancel"))
    def cancel_orders(self, request, queryset):
        count = sum(1 for order in queryset if cancel_order(order.pk, reason=f"Cancelled by {request.user}"))
        messages.success(request, _("%(count)s order(s) cancelled.") % {"count": count})

    # -- detail buttons -----------------------------------------------------
    @action(description=_("Resend tickets"), url_path="resend")
    def resend_detail(self, request, object_id):
        order = Order.objects.get(pk=object_id)
        if order.status == Order.Status.PAID:
            dispatch(resend_order_email, order.pk)
            messages.success(request, _("Tickets sent again to %(email)s.") % {"email": order.buyer_email})
        else:
            messages.error(request, _("Only paid orders have tickets."))
        return redirect(reverse("admin:tickets_order_change", args=[object_id]))

    @action(description=_("Mark as paid (manual)"), url_path="mark-paid")
    def mark_paid_detail(self, request, object_id):
        order = Order.objects.get(pk=object_id)
        if order.status in (Order.Status.PAID, Order.Status.REFUNDED):
            messages.info(request, _("This order is already %(status)s.") % {"status": order.get_status_display()})
        else:
            fulfil_order(order.pk, transaction_id="manual", note=f"Marked as paid by {request.user}")
            messages.success(request, _("Order marked as paid — tickets issued and e-mailed."))
        return redirect(reverse("admin:tickets_order_change", args=[object_id]))


# --------------------------------------------------------------------------
# Complimentary tickets
# --------------------------------------------------------------------------
class ComplimentaryForm(forms.ModelForm):
    ticket_type = forms.ModelChoiceField(
        label=_("Ticket type"), queryset=TicketType.objects.all(), widget=UnfoldAdminSelectWidget
    )
    quantity = forms.IntegerField(label=_("Quantity"), min_value=1, max_value=50, initial=1, widget=UnfoldAdminIntegerFieldWidget)
    holder_names = forms.CharField(
        label=_("Holder names"), required=False, widget=UnfoldAdminTextInputWidget,
        help_text=_("Optional, comma-separated (one per ticket). Empty = guest name."),
    )
    holder_photo = forms.FileField(label=_("Holder photo"), required=False, help_text=_("Optional, used on the first ticket."))

    class Meta:
        model = ComplimentaryOrder
        fields = ("buyer_name", "buyer_email", "buyer_phone", "language", "note")
        labels = {"buyer_name": _("Guest name"), "buyer_email": _("Send tickets to (e-mail)")}

    def clean_holder_photo(self):
        upload = self.cleaned_data.get("holder_photo")
        return process_holder_photo(upload) if upload else None

    def clean(self):
        cleaned = super().clean()
        ticket_type, quantity = cleaned.get("ticket_type"), cleaned.get("quantity")
        if ticket_type and quantity and ticket_type.available < quantity:
            self.add_error("quantity", _("Only %(count)s ticket(s) left in this category.") % {"count": ticket_type.available})
        return cleaned


@admin.register(ComplimentaryOrder)
class ComplimentaryOrderAdmin(ModelAdmin):
    form = ComplimentaryForm
    list_display = ("reference", "buyer_name", "buyer_email", "tickets_count", "created_at")
    search_fields = ("reference", "buyer_name", "buyer_email")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_complimentary=True)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Tickets"))
    def tickets_count(self, obj):
        return obj.tickets.count()

    def save_model(self, request, obj, form, change):
        data = form.cleaned_data
        event = Event.get_active() or data["ticket_type"].event
        names = [n.strip() for n in (data.get("holder_names") or "").split(",") if n.strip()]
        holders = [{"name": name, "photo": ""} for name in names]
        if data.get("holder_photo") is not None:
            path = private_storage().save(f"holders/{data['holder_photo'].name}", data["holder_photo"])
            if holders:
                holders[0]["photo"] = path
            else:
                holders = [{"name": data["buyer_name"], "photo": path}]
        try:
            order = issue_complimentary(
                event=event,
                ticket_type=data["ticket_type"],
                quantity=data["quantity"],
                buyer_name=data["buyer_name"],
                buyer_email=data["buyer_email"],
                buyer_phone=data.get("buyer_phone") or "",
                language=data.get("language") or "en",
                holders=holders,
                note=data.get("note") or "",
            )
        except StockError as exc:
            messages.error(request, str(exc))
            raise
        obj.pk = order.pk
        obj.reference = order.reference
        obj.created_at = order.created_at

    def add_view(self, request, form_url="", extra_context=None):
        try:
            return super().add_view(request, form_url, extra_context)
        except StockError:
            return redirect(reverse("admin:tickets_complimentaryorder_add"))

    def response_add(self, request, obj, post_url_continue=None):
        messages.success(request, _("Complimentary tickets issued and e-mailed to %(email)s.") % {"email": obj.buyer_email})
        return redirect(reverse("admin:tickets_order_change", args=[obj.pk]))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect(reverse("admin:tickets_order_change", args=[object_id]))


# --------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------
class CheckInInline(TabularInline):
    model = CheckIn
    extra = 0
    can_delete = False
    fields = ("day", "scanned_at", "scanner")
    readonly_fields = fields
    verbose_name_plural = _("Entries (one per day)")

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Scanner"))
    def scanner(self, obj):
        return obj.scanned_by_label or "—"


@admin.register(Ticket)
class TicketAdmin(ModelAdmin):
    list_display = ("code", "serial_display", "holder_name", "ticket_type", "status_badge", "entries", "order_link", "download_count")
    list_filter = ("status", "ticket_type", ("order__print_batch", admin.EmptyFieldListFilter), "order__country", "checkins__day")
    search_fields = ("code", "holder_name", "order__reference", "order__buyer_email", "order__buyer_phone")
    list_select_related = ("order", "ticket_type")
    inlines = [CheckInInline]
    actions = ["print_selected", "regenerate", "cancel_tickets", "reset_checkin"]
    actions_detail = ["regenerate_detail", "reset_checkin_detail"]
    readonly_fields = (
        "code", "serial_display", "uuid", "order_link", "ticket_type", "status_badge", "qr_preview", "photo_preview",
        "checked_in_at", "checked_in_by", "email_sent_at", "download_count", "downloads", "created_at",
    )
    fieldsets = (
        (None, {"fields": (("code", "serial_display"), "status_badge", "order_link", "ticket_type", "holder_name", "holder_photo", "photo_preview")}),
        (_("QR code & files"), {"fields": ("qr_preview", "downloads", "uuid")}),
        (_("Entrance"), {"fields": ("checked_in_at", "checked_in_by", "email_sent_at", "download_count", "created_at")}),
    )

    def get_queryset(self, request):
        from django.db.models import Count

        return super().get_queryset(request).annotate(entry_count=Count("checkins"))

    @admin.display(description=_("No."), ordering="serial")
    def serial_display(self, obj):
        return obj.serial_label or "—"

    @admin.display(description=_("Entries"), ordering="entry_count")
    def entries(self, obj):
        return f"{getattr(obj, 'entry_count', 0)} / {obj.order.event.days}"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "holder_photo":
            kwargs["widget"] = private_photo_widget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        return badge(obj.status, obj.get_status_display())

    @admin.display(description=_("Order"), ordering="order__reference")
    def order_link(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:tickets_order_change", args=[obj.order_id]), obj.order.reference)

    @admin.display(description=_("QR code"))
    def qr_preview(self, obj):
        if not obj.pk:
            return "—"
        data = base64.b64encode(make_qr_png(obj.qr_token, size=240)).decode()
        return format_html('<img src="data:image/png;base64,{}" width="240" height="240" alt="QR">', data)

    @admin.display(description=_("Photo"))
    def photo_preview(self, obj):
        if not obj.holder_photo:
            return "—"
        url = reverse("scanner:photo", args=[obj.uuid])
        return format_html('<img src="{}" alt="" style="width:160px;height:160px;object-fit:cover;border-radius:50%">', url)

    @admin.display(description=_("Download"))
    def downloads(self, obj):
        if not obj.pk or obj.order.status != Order.Status.PAID:
            return "—"
        links = [(ticket_download_url(obj, "png"), "PNG"), (ticket_download_url(obj, "pdf"), "PDF")]
        return format_html_join(" · ", '<a href="{}">{}</a>', links)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and {"holder_name", "holder_photo"} & set(form.changed_data):
            if "holder_photo" in form.changed_data and obj.holder_photo:
                processed = process_holder_photo(obj.holder_photo.file)
                obj.holder_photo.save(processed.name, processed, save=True)
            dispatch(regenerate_ticket_files, obj.pk)
            messages.info(request, _("Ticket files regenerated."))

    @action(description=_("Regenerate ticket files"))
    def regenerate(self, request, queryset):
        for ticket in queryset:
            dispatch(regenerate_ticket_files, ticket.pk)
        messages.success(request, _("Regeneration queued."))

    @action(description=_("Cancel tickets"))
    def cancel_tickets(self, request, queryset):
        count = queryset.exclude(status=Ticket.Status.CANCELLED).update(status=Ticket.Status.CANCELLED)
        messages.success(request, _("%(count)s ticket(s) cancelled.") % {"count": count})

    @action(description=_("Print the selected tickets (A4, 4 per page)"))
    def print_selected(self, request, queryset):
        tickets = list(queryset.exclude(status=Ticket.Status.CANCELLED).select_related("ticket_type__event", "order__event").order_by("serial"))
        if not tickets:
            messages.error(request, _("No valid ticket selected."))
            return None
        if len({t.ticket_type_id for t in tickets}) > 1:
            messages.error(request, _("Select tickets of one ticket type at a time (one design per PDF)."))
            return None
        response = HttpResponse(render_print_pdf(tickets, PrintBatch.Paper.A4, 4), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="tickets-{timezone.now():%Y%m%d-%H%M}.pdf"'
        return response

    @action(description=_("Reset check-in"))
    def reset_checkin(self, request, queryset):
        count = CheckIn.objects.filter(ticket__in=queryset).delete()[0]
        queryset.filter(status=Ticket.Status.USED).update(status=Ticket.Status.VALID, checked_in_at=None, checked_in_by=None)
        messages.success(request, _("%(count)s check-in(s) reset.") % {"count": count})

    @action(description=_("Regenerate files"), url_path="regenerate")
    def regenerate_detail(self, request, object_id):
        dispatch(regenerate_ticket_files, int(object_id))
        messages.success(request, _("Ticket files regenerated."))
        return redirect(reverse("admin:tickets_ticket_change", args=[object_id]))

    @action(description=_("Reset check-in"), url_path="reset-checkin")
    def reset_checkin_detail(self, request, object_id):
        CheckIn.objects.filter(ticket_id=object_id).delete()
        Ticket.objects.filter(pk=object_id).update(status=Ticket.Status.VALID, checked_in_at=None, checked_in_by=None)
        messages.success(request, _("Check-in reset."))
        return redirect(reverse("admin:tickets_ticket_change", args=[object_id]))


# --------------------------------------------------------------------------
# Print batches (bulk printed tickets)
# --------------------------------------------------------------------------
class PrintBatchForm(forms.ModelForm):
    class Meta:
        model = PrintBatch
        fields = ("ticket_type", "quantity", "paper", "per_page", "outlet", "note")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        event = Event.get_active()
        if "ticket_type" in self.fields and event:
            self.fields["ticket_type"].queryset = TicketType.objects.filter(event=event)
        if "quantity" in self.fields:
            self.fields["quantity"].help_text = _("1 to %(max)s. Each ticket gets its own QR code, code and number.") % {
                "max": PrintBatch.MAX_QUANTITY,
            }

    def clean(self):
        cleaned = super().clean()
        ticket_type, quantity = cleaned.get("ticket_type"), cleaned.get("quantity")
        if not self.instance.pk and ticket_type and quantity and ticket_type.available < quantity:
            self.add_error("quantity", _("Only %(count)s ticket(s) left in this category.") % {"count": ticket_type.available})
        return cleaned


@admin.register(PrintBatch)
class PrintBatchAdmin(ModelAdmin):
    form = PrintBatchForm
    list_display = ("created_at", "ticket_type", "quantity", "numbers", "layout", "status_badge", "download", "outlet")
    list_filter = ("ticket_type", "status", "outlet")
    search_fields = ("note", "order__reference")
    list_select_related = ("ticket_type", "outlet")
    actions_detail = ["download_detail", "rebuild_detail"]
    readonly_fields = ("event", "numbers", "status_badge", "download", "order_link", "created_by", "created_at")

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {
                "description": _("The system creates the tickets (QR code, code and number for each one, valid for every "
                                 "day of the event), then the PDF to print: choose A4 and 4 or 5 tickets per page."),
                "fields": ("ticket_type", "quantity", ("paper", "per_page"), "outlet", "note"),
            }),)
        return (
            (None, {"fields": ("ticket_type", "quantity", "numbers", "status_badge", "download")}),
            (_("PDF format"), {
                "description": _("Change the format and save: the PDF is generated again."),
                "fields": (("paper", "per_page"),),
            }),
            (_("Details"), {"fields": ("outlet", "note", "order_link", "event", "created_by", "created_at")}),
        )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return fields + ["ticket_type", "quantity"] if obj else fields

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            if {"paper", "per_page"} & set(form.changed_data):
                dispatch(build_print_batch, obj.pk)
            return
        data = form.cleaned_data
        batch = create_print_batch(
            ticket_type=data["ticket_type"], quantity=data["quantity"], paper=data["paper"], per_page=data["per_page"],
            outlet=data.get("outlet"), note=data.get("note") or "", user=request.user,
        )
        obj.pk = batch.pk
        obj.created_at = batch.created_at

    def response_add(self, request, obj, post_url_continue=None):
        batch = PrintBatch.objects.get(pk=obj.pk)
        messages.success(request, _("%(count)s tickets created (numbers %(range)s). The PDF is being generated.") % {
            "count": batch.quantity, "range": batch.serial_range,
        })
        return redirect(reverse("admin:tickets_printbatch_change", args=[obj.pk]))

    @admin.display(description=_("Numbers"))
    def numbers(self, obj):
        return obj.serial_range

    @admin.display(description=_("Format"))
    def layout(self, obj):
        if obj.paper == PrintBatch.Paper.A4:
            return _("A4 · %(n)s per page") % {"n": obj.per_page}
        return _("Ticket size")

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        return badge(obj.status, obj.get_status_display())

    @admin.display(description=_("PDF"))
    def download(self, obj):
        if not obj.pk or obj.status != PrintBatch.Status.READY or not obj.pdf:
            return "—"
        return format_html('<a href="{}" style="font-weight:bold">{}</a>',
                           reverse("admin:tickets_printbatch_download_detail", args=[obj.pk]), _("Download the PDF"))

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        if not obj.order_id:
            return "—"
        return format_html('<a href="{}">{}</a>', reverse("admin:tickets_order_change", args=[obj.order_id]), obj.order.reference)

    @action(description=_("Download the PDF"), url_path="download", permissions=["view"])
    def download_detail(self, request, object_id):
        batch = get_object_or_404(PrintBatch, pk=object_id)
        if not batch.pdf:
            raise Http404
        name = batch.pdf.name.rsplit("/", 1)[-1]
        return FileResponse(batch.pdf.open("rb"), as_attachment=True, filename=name, content_type="application/pdf")

    @action(description=_("Generate the PDF again"), url_path="rebuild", permissions=["change"])
    def rebuild_detail(self, request, object_id):
        dispatch(build_print_batch, int(object_id))
        messages.success(request, _("The PDF is being generated."))
        return redirect(reverse("admin:tickets_printbatch_change", args=[object_id]))
