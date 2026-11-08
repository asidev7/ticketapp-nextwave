from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from core.admin import thumb

from .models import Artist, ArtistCategory


@admin.register(ArtistCategory)
class ArtistCategoryAdmin(ModelAdmin):
    list_display = ("name_en", "name_fr", "slug")
    prepopulated_fields = {"slug": ("name_en",)}
    ordering_field = "order"
    hide_ordering_field = True


@admin.register(Artist)
class ArtistAdmin(ModelAdmin):
    list_display = ("photo_preview", "stage_name", "category", "country", "is_headliner", "is_active")
    list_filter = ("category", "is_headliner", "is_active", "event")
    search_fields = ("stage_name", "role_en", "role_fr")
    ordering_field = "order"
    hide_ordering_field = True
    fieldsets = (
        (None, {"fields": ("event", "category", "stage_name", "photo", "country_code", "is_headliner", "is_active")}),
        (_("Role & bio"), {"fields": ("role_en", "role_fr", "bio_en", "bio_fr")}),
        (_("Social links"), {"fields": ("instagram", "tiktok", "x", "youtube")}),
    )

    @admin.display(description=_("Photo"))
    def photo_preview(self, obj):
        return thumb(obj.photo, 48, round_=True)

    @admin.display(description=_("Country"))
    def country(self, obj):
        return f"{obj.flag} {obj.country_name}"
