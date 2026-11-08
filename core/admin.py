import base64

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, StackedInline
from unfold.decorators import action
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldAdminImageSmallFieldWidget

from .models import FAQ, Event, HeroSlide, PhysicalOutlet, SiteSettings
from .qrcodes import buy_page_url, buy_qr_png, buy_qr_svg


class PrivateImageWidget(UnfoldAdminImageSmallFieldWidget):
    """Unfold upload field for photos kept in private storage.

    Those files have no public URL, so the existing file is never shown or
    linked (no broken preview, storage path not exposed). The layout Unfold's
    JavaScript expects is kept.
    """

    def is_initial(self, value):
        return False

    def format_value(self, value):
        return None


def private_photo_widget():
    return PrivateImageWidget(attrs={"accept": "image/jpeg,image/png,image/webp"})


def thumb(image, size: int = 56, round_: bool = False):
    if not image:
        return "—"
    radius = "50%" if round_ else "8px"
    return format_html(
        '<img src="{}" alt="" style="width:{}px;height:{}px;object-fit:cover;border-radius:{}">',
        image.url, size, size, radius,
    )


# --------------------------------------------------------------------------
# Users & groups (Unfold styling) — create scanner accounts here.
# --------------------------------------------------------------------------
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "is_active")
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "email", "usable_password", "password1", "password2")}),
    )


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# --------------------------------------------------------------------------
# Site settings (singleton)
# --------------------------------------------------------------------------
@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    fieldsets = (
        (_("Branding"), {"fields": ("site_name", "logo", "favicon", "meta_description", "primary_color", "accent_color")}),
        (_("Contacts & social"), {"fields": ("contact_email", "contact_phone", "whatsapp", "instagram", "facebook", "tiktok", "x", "youtube")}),
        (_("Ticket sales"), {"fields": ("sales_open", "max_tickets_per_order", "show_remaining_stock", "service_fee_xof", "service_fee_ngn", "smtp_from_email")}),
        (_("About"), {"fields": ("about_text_en", "about_text_fr", "about_image")}),
        (_("Legal pages"), {"classes": ("collapse",), "fields": ("terms_en", "terms_fr", "privacy_en", "privacy_fr")}),
        (_("QR code — Buy Ticket page"), {"fields": ("buy_qr",)}),
    )
    readonly_fields = ("buy_qr",)
    actions_detail = ["download_buy_qr_png", "download_buy_qr_svg"]

    @admin.display(description=_("QR code"))
    def buy_qr(self, obj):
        if not obj or not obj.pk:
            return "—"
        preview = base64.b64encode(buy_qr_png(size=240)).decode("ascii")
        url = buy_page_url()
        warning = ""
        if any(host in url for host in ("localhost", "127.0.0.1", "0.0.0.0")):
            warning = format_html(
                '<p style="color:#DC2626;font-weight:bold;margin:0 0 8px">{}</p>',
                _("SITE_URL points to a local address: set the real domain in .env before printing this QR code."),
            )
        return format_html(
            '{}<img src="data:image/png;base64,{}" width="200" height="200" alt="QR" style="border:1px solid #E5E7EB;border-radius:8px">'
            '<p style="margin:8px 0 4px">{} <a href="{}" target="_blank" rel="noopener">{}</a></p>'
            '<p style="margin:0 0 8px">{}</p>'
            '<p><a href="{}" style="font-weight:bold">{}</a> · <a href="{}" style="font-weight:bold">{}</a></p>',
            warning,
            preview,
            _("Opens:"), url, url,
            _("Print it on posters and flyers: scanning it opens the ticket section of the website."),
            reverse("admin:core_sitesettings_download_buy_qr_png", args=[obj.pk]), _("Download PNG (print, 1200 px)"),
            reverse("admin:core_sitesettings_download_buy_qr_svg", args=[obj.pk]), _("Download SVG (vector)"),
        )

    @action(description=_("Buy Ticket QR (PNG)"), url_path="buy-qr-png", permissions=["change"])
    def download_buy_qr_png(self, request, object_id):
        response = HttpResponse(buy_qr_png(), content_type="image/png")
        response["Content-Disposition"] = 'attachment; filename="nextwave-buy-ticket-qr.png"'
        return response

    @action(description=_("Buy Ticket QR (SVG)"), url_path="buy-qr-svg", permissions=["change"])
    def download_buy_qr_svg(self, request, object_id):
        response = HttpResponse(buy_qr_svg(), content_type="image/svg+xml")
        response["Content-Disposition"] = 'attachment; filename="nextwave-buy-ticket-qr.svg"'
        return response

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse("admin:core_sitesettings_change", args=[obj.pk]))


# --------------------------------------------------------------------------
# Event & content
# --------------------------------------------------------------------------
class HeroSlideInline(StackedInline):
    model = HeroSlide
    extra = 0
    fields = ("image", ("title_en", "title_fr"), ("subtitle_en", "subtitle_fr"), ("order", "is_active"))
    tab = True


@admin.register(Event)
class EventAdmin(ModelAdmin):
    list_display = ("__str__", "date_start", "venue_name", "city", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name", "edition")}
    inlines = [HeroSlideInline]
    fieldsets = (
        (None, {"fields": ("name", "edition", "slug", "is_active")}),
        (_("Date & time"), {"fields": ("date_start", "doors_open", "date_end")}),
        (_("Description"), {"fields": ("description_en", "description_fr", "practical_info_en", "practical_info_fr")}),
        (_("Venue"), {"fields": ("venue_name", "venue_address", "city", "country", "map_embed_url", "map_link", "hall_map_image")}),
        (_("Poster & video"), {"fields": ("poster_image", "video_file", "video_url", "video_poster")}),
    )


@admin.register(HeroSlide)
class HeroSlideAdmin(ModelAdmin):
    list_display = ("preview", "title_en", "event", "is_active")
    list_filter = ("event", "is_active")
    ordering_field = "order"
    hide_ordering_field = True

    @admin.display(description=_("Image"))
    def preview(self, obj):
        return thumb(obj.image, 80)


@admin.register(PhysicalOutlet)
class PhysicalOutletAdmin(ModelAdmin):
    list_display = ("name", "city", "country", "phone", "opening_hours", "is_active")
    list_filter = ("country", "city", "is_active")
    search_fields = ("name", "address", "city")
    ordering_field = "order"
    hide_ordering_field = True


@admin.register(FAQ)
class FAQAdmin(ModelAdmin):
    list_display = ("question_en", "question_fr", "is_active")
    list_filter = ("is_active",)
    search_fields = ("question_en", "question_fr", "answer_en", "answer_fr")
    ordering_field = "order"
    hide_ordering_field = True
