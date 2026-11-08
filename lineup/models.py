from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import Event, TranslatedMixin
from core.utils import COUNTRY_NAMES, flag_emoji


class ArtistCategory(TranslatedMixin, models.Model):
    name_en = models.CharField(_("name (EN)"), max_length=60)
    name_fr = models.CharField(_("name (FR)"), max_length=60, blank=True)
    slug = models.SlugField(_("slug"), unique=True)
    order = models.PositiveIntegerField(_("position"), default=0)

    class Meta:
        verbose_name = _("artist category")
        verbose_name_plural = _("artist categories")
        ordering = ["order", "pk"]

    def __str__(self):
        return self.name_en


class Artist(TranslatedMixin, models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="artists", verbose_name=_("event"))
    category = models.ForeignKey(ArtistCategory, on_delete=models.PROTECT, related_name="artists", verbose_name=_("category"))
    stage_name = models.CharField(_("stage name"), max_length=100)
    photo = models.ImageField(_("photo"), upload_to="artists/", blank=True, help_text=_("Square, at least 600×600."))
    country_code = models.CharField(_("country code"), max_length=2, default="BJ", help_text=_("ISO code: BJ, NG, GH…"))
    role_en = models.CharField(_("role (EN)"), max_length=100, blank=True)
    role_fr = models.CharField(_("role (FR)"), max_length=100, blank=True)
    bio_en = models.TextField(_("bio (EN)"), blank=True)
    bio_fr = models.TextField(_("bio (FR)"), blank=True)
    is_headliner = models.BooleanField(_("headliner"), default=False)
    order = models.PositiveIntegerField(_("position"), default=0)
    is_active = models.BooleanField(_("active"), default=True)
    instagram = models.URLField(_("Instagram"), blank=True)
    tiktok = models.URLField(_("TikTok"), blank=True)
    x = models.URLField(_("X (Twitter)"), blank=True)
    youtube = models.URLField(_("YouTube"), blank=True)

    class Meta:
        verbose_name = _("artist")
        verbose_name_plural = _("artists")
        ordering = ["order", "-is_headliner", "stage_name"]

    def __str__(self):
        return self.stage_name

    def save(self, *args, **kwargs):
        self.country_code = (self.country_code or "").upper()
        super().save(*args, **kwargs)

    @property
    def flag(self) -> str:
        return flag_emoji(self.country_code)

    @property
    def country_name(self) -> str:
        return COUNTRY_NAMES.get(self.country_code, self.country_code)

    @property
    def role_label(self) -> str:
        return self.tr("role") or self.category.tr("name")

    @property
    def socials(self) -> list[tuple[str, str]]:
        links = [("Instagram", self.instagram), ("TikTok", self.tiktok), ("X", self.x), ("YouTube", self.youtube)]
        return [(label, url) for label, url in links if url]
