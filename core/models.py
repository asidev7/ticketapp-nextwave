import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.urls import reverse
from django.utils import dateformat, timezone, translation
from django.utils.translation import gettext_lazy as _

hex_color_validator = RegexValidator(
    regex=r"^#[0-9A-Fa-f]{6}$",
    message=_("Use a hex colour such as #0A3D91."),
)


# A night that goes past midnight still belongs to the evening it started:
# a scan at 02:00 on Dec 26 counts for Dec 25.
DAY_CHANGE_HOUR = 6


def event_day(moment):
    """Entry day of a moment (local time, day changes at DAY_CHANGE_HOUR)."""
    return (timezone.localtime(moment) - timedelta(hours=DAY_CHANGE_HOUR)).date()


def _en(value, fmt: str) -> str:
    with translation.override("en"):
        return dateformat.format(value, fmt)


def youtube_id(url: str) -> str:
    """Video ID of a YouTube watch / short / embed / youtu.be link, else ''."""
    parsed = urlparse((url or "").strip())
    host = parsed.netloc.lower().replace("www.", "").replace("m.", "")
    if host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        if parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            return parsed.path.split("/")[2]
        return parse_qs(parsed.query).get("v", [""])[0]
    if host == "youtu.be":
        return parsed.path.lstrip("/").split("/")[0]
    return ""


class TranslatedMixin:
    """`obj.tr("description")` → description_fr in French when filled, else description_en."""

    def tr(self, field: str) -> str:
        language = (translation.get_language() or "en")[:2]
        if language != "en":
            value = getattr(self, f"{field}_{language}", "")
            if value:
                return value
        return getattr(self, f"{field}_en", "") or ""


# --------------------------------------------------------------------------
# Site settings (singleton)
# --------------------------------------------------------------------------
class SiteSettings(TranslatedMixin, models.Model):
    site_name = models.CharField(_("site name"), max_length=100, default="NextWave Party")
    logo = models.ImageField(_("logo"), upload_to="site/", blank=True)
    favicon = models.ImageField(_("favicon"), upload_to="site/", blank=True, help_text=_("Square PNG, 512×512."))
    meta_description = models.CharField(_("meta description"), max_length=180, blank=True)

    primary_color = models.CharField(
        _("primary colour"), max_length=7, default="#C8102E", validators=[hex_color_validator],
        help_text=_("Buttons and links. Keep it dark enough to read on white (contrast 4.5:1)."),
    )
    accent_color = models.CharField(
        _("accent colour"), max_length=7, default="#F5B921", validators=[hex_color_validator],
        help_text=_("Badges and highlights. Text on it is automatically black or white for contrast."),
    )

    contact_email = models.EmailField(_("contact e-mail"), blank=True)
    contact_phone = models.CharField(_("contact phone"), max_length=30, blank=True)
    whatsapp = models.CharField(_("WhatsApp number"), max_length=30, blank=True, help_text=_("International format, e.g. +22997000000."))
    instagram = models.URLField(_("Instagram"), blank=True)
    facebook = models.URLField(_("Facebook"), blank=True)
    tiktok = models.URLField(_("TikTok"), blank=True)
    x = models.URLField(_("X (Twitter)"), blank=True)
    youtube = models.URLField(_("YouTube"), blank=True)

    sales_open = models.BooleanField(_("sales open"), default=True, help_text=_("Untick to pause all online sales."))
    require_holder_photo = models.BooleanField(_("holder photo required"), default=False)
    max_tickets_per_order = models.PositiveSmallIntegerField(_("max tickets per order"), default=10)
    show_remaining_stock = models.BooleanField(
        _("show remaining tickets"), default=True,
        help_text=_("Shows “Only X left” on a category when less than a fifth of it remains."),
    )
    service_fee_xof = models.PositiveIntegerField(_("service fee per order (XOF)"), default=0)
    service_fee_ngn = models.PositiveIntegerField(_("service fee per order (NGN)"), default=0)

    smtp_from_email = models.CharField(
        _("sender e-mail"), max_length=200, blank=True,
        help_text=_("e.g. NextWave Party <tickets@nextwaveparty.com>. Empty = DEFAULT_FROM_EMAIL."),
    )

    about_text_en = models.TextField(_("about text (EN)"), blank=True)
    about_text_fr = models.TextField(_("about text (FR)"), blank=True)
    about_image = models.ImageField(_("about image"), upload_to="site/", blank=True)

    terms_en = models.TextField(_("terms (EN)"), blank=True)
    terms_fr = models.TextField(_("terms (FR)"), blank=True)
    privacy_en = models.TextField(_("privacy policy (EN)"), blank=True)
    privacy_fr = models.TextField(_("privacy policy (FR)"), blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("site settings")
        verbose_name_plural = _("site settings")

    def __str__(self):
        return str(_("Site settings"))

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return (0, {})

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def whatsapp_link(self) -> str:
        digits = re.sub(r"\D", "", self.whatsapp or "")
        return f"https://wa.me/{digits}" if digits else ""

    def service_fee(self, currency: str) -> int:
        return self.service_fee_ngn if currency == "NGN" else self.service_fee_xof


# --------------------------------------------------------------------------
# Event
# --------------------------------------------------------------------------
class Event(TranslatedMixin, models.Model):
    presented_by = models.CharField(
        _("presented by"), max_length=150, blank=True,
        help_text=_("Small line above the title, e.g. Next Wave Africa & Vibe City Entertainment."),
    )
    name = models.CharField(_("name"), max_length=120, default="NextWave Party")
    edition = models.CharField(_("edition"), max_length=60, default="1st Edition", help_text=_("Shown in the red box under the title, e.g. With Q-Dot."))
    slug = models.SlugField(_("slug"), unique=True)
    description_en = models.TextField(_("description (EN)"), blank=True)
    description_fr = models.TextField(_("description (FR)"), blank=True)
    practical_info_en = models.TextField(
        _("practical info (EN)"), blank=True,
        help_text=_("Shown in the ticket e-mail: forbidden items, dress code, minimum age…"),
    )
    practical_info_fr = models.TextField(_("practical info (FR)"), blank=True)

    date_start = models.DateTimeField(_("show starts"))
    doors_open = models.DateTimeField(_("doors open"), null=True, blank=True)
    date_end = models.DateTimeField(_("ends"), null=True, blank=True)
    days = models.PositiveSmallIntegerField(
        _("number of days"), default=1, validators=[MinValueValidator(1), MaxValueValidator(14)],
        help_text=_("One ticket = one entry per day. 2 = the ticket is valid once on each of two consecutive days "
                    "(e.g. Dec 25 and Dec 26), starting on the show date."),
    )

    venue_name = models.CharField(_("venue name"), max_length=150)
    venue_address = models.CharField(_("venue address"), max_length=255)
    city = models.CharField(_("city"), max_length=100, default="Cotonou")
    country = models.CharField(_("country"), max_length=100, default="Benin")
    map_embed_url = models.URLField(
        _("Google Maps embed URL"), max_length=1000, blank=True,
        help_text=_("Google Maps → Share → Embed a map → copy the src=\"…\" URL."),
    )
    map_link = models.URLField(_("Google Maps link"), max_length=500, blank=True)
    tickets_note_en = models.CharField(
        _("note under the tickets (EN)"), max_length=255, blank=True,
        help_text=_("e.g. One RFID wristband per person, given at the entrance with your QR ticket."),
    )
    tickets_note_fr = models.CharField(_("note under the tickets (FR)"), max_length=255, blank=True)
    enquiry_phones = models.CharField(
        _("reservation & enquiry numbers"), max_length=200, blank=True,
        help_text=_("Comma-separated, e.g. 07070257650, 09166119357, +2290190776888. Shown like on the poster."),
    )

    hall_map_image = models.ImageField(_("hall map"), upload_to="event/", blank=True)
    poster_image = models.ImageField(_("official poster"), upload_to="event/", blank=True)
    video_file = models.FileField(_("teaser video (MP4)"), upload_to="event/video/", blank=True)
    video_url = models.URLField(_("teaser video link (YouTube/Vimeo)"), blank=True)
    video_poster = models.ImageField(_("video cover image"), upload_to="event/", blank=True)

    is_active = models.BooleanField(_("active"), default=True, help_text=_("The active event is the one shown on the website."))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        ordering = ["-date_start"]

    def __str__(self):
        return f"{self.name} — {self.edition}"

    def get_absolute_url(self):
        return reverse("core:home")

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).order_by("date_start").first()

    @property
    def ends_at(self):
        return self.date_end or (self.date_start + timedelta(days=max(self.days, 1) - 1, hours=8))

    # -- entry days -------------------------------------------------------------
    @property
    def entry_days(self) -> list:
        """Dates on which a ticket gives one entry each (first = show date)."""
        first = event_day(self.date_start)
        return [first + timedelta(days=i) for i in range(max(self.days, 1))]

    def day_number(self, moment=None) -> int:
        """1, 2… when `moment` (default: now) falls on an entry day, else 0."""
        day = event_day(moment or timezone.now())
        days = self.entry_days
        return days.index(day) + 1 if day in days else 0

    @property
    def is_multi_day(self) -> bool:
        return self.days > 1

    @property
    def dates_label(self) -> str:
        """Poster-style dates: '27 DEC 2026', '25/26 DEC 2026', '24–26 DEC 2026', '31 DEC 2026 – 1 JAN 2027'."""
        days = self.entry_days
        first, last = days[0], days[-1]
        if first == last:
            return _en(first, "j M Y").upper()
        if (first.year, first.month) == (last.year, last.month):
            joiner = "/" if len(days) == 2 else "–"
            return f"{first.day}{joiner}{_en(last, 'j M Y')}".upper()
        if first.year == last.year:
            return f"{_en(first, 'j M')} – {_en(last, 'j M Y')}".upper()
        return f"{_en(first, 'j M Y')} – {_en(last, 'j M Y')}".upper()

    @property
    def time_label(self) -> str:
        """'5PM', '8:30PM' (show time, local)."""
        start = timezone.localtime(self.date_start)
        return _en(start, "gA" if start.minute == 0 else "g:iA")

    @property
    def phone_list(self) -> list[tuple[str, str]]:
        """[(shown, tel: link)] from the comma-separated enquiry numbers."""
        phones = []
        for raw in (self.enquiry_phones or "").split(","):
            shown = raw.strip()
            if shown:
                phones.append((shown, re.sub(r"[^\d+]", "", shown)))
        return phones

    def day_label(self, day) -> str:
        """'25 DEC' for an entry day."""
        return _en(day, "j M").upper()

    @property
    def is_over(self) -> bool:
        return timezone.now() > self.ends_at

    @property
    def video_embed_url(self) -> str:
        """YouTube/Vimeo watch link → privacy-friendly embed URL (autoplay muted)."""
        url = (self.video_url or "").strip()
        if not url:
            return ""
        parsed = urlparse(url)
        host = parsed.netloc.lower().replace("www.", "").replace("m.", "")
        video_id = youtube_id(url)
        if video_id:
            return f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&mute=1&playsinline=1&rel=0"
        if host in ("vimeo.com", "player.vimeo.com"):
            match = re.search(r"(\d+)", parsed.path)
            if match:
                return f"https://player.vimeo.com/video/{match.group(1)}?autoplay=1&muted=1&playsinline=1"
        return ""

    def min_prices(self) -> dict:
        """Cheapest active ticket price per currency, for the 'From XOF X' block."""
        types = self.ticket_types.filter(is_active=True)
        xof = [t.price_xof for t in types if t.price_xof]
        ngn = [t.price_ngn for t in types if t.price_ngn]
        return {"XOF": min(xof) if xof else None, "NGN": min(ngn) if ngn else None}


class HeroSlide(TranslatedMixin, models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="slides", verbose_name=_("event"))
    image = models.ImageField(_("image"), upload_to="hero/", help_text=_("Landscape, at least 1600×900, under 400 KB."))
    title_en = models.CharField(_("title (EN)"), max_length=120, blank=True)
    title_fr = models.CharField(_("title (FR)"), max_length=120, blank=True)
    subtitle_en = models.CharField(_("subtitle (EN)"), max_length=200, blank=True)
    subtitle_fr = models.CharField(_("subtitle (FR)"), max_length=200, blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("hero slide")
        verbose_name_plural = _("hero slides")
        ordering = ["order", "pk"]

    def __str__(self):
        return self.title_en or f"Slide {self.pk}"


class EventVideo(TranslatedMixin, models.Model):
    """YouTube videos shown on the website (teasers, artists, last edition…)."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="videos", verbose_name=_("event"))
    url = models.URLField(_("YouTube link"), help_text=_("e.g. https://www.youtube.com/watch?v=… or https://youtu.be/…"))
    title_en = models.CharField(_("title (EN)"), max_length=120, blank=True)
    title_fr = models.CharField(_("title (FR)"), max_length=120, blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("YouTube video")
        verbose_name_plural = _("YouTube videos")
        ordering = ["order", "pk"]

    def __str__(self):
        return self.title_en or self.url

    def clean(self):
        from django.core.exceptions import ValidationError

        if not youtube_id(self.url):
            raise ValidationError({"url": _("This is not a YouTube video link.")})

    @property
    def video_id(self) -> str:
        return youtube_id(self.url)

    @property
    def thumbnail_url(self) -> str:
        return f"https://i.ytimg.com/vi/{self.video_id}/hqdefault.jpg" if self.video_id else ""

    @property
    def embed_url(self) -> str:
        return f"https://www.youtube-nocookie.com/embed/{self.video_id}?autoplay=1&rel=0&playsinline=1" if self.video_id else ""


class PhysicalOutlet(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="outlets", verbose_name=_("event"))
    name = models.CharField(_("name"), max_length=120, blank=True, help_text=_("Shop or contact person. Empty = the city only."))
    address = models.CharField(_("address"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=100)
    country = models.CharField(_("country"), max_length=100, default="Benin")
    phone = models.CharField(_("phone"), max_length=30, blank=True)
    opening_hours = models.CharField(_("opening hours"), max_length=150, blank=True)
    map_link = models.URLField(_("map link"), max_length=500, blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("physical outlet")
        verbose_name_plural = _("physical outlets")
        ordering = ["order", "country", "city", "name"]

    def __str__(self):
        return f"{self.name} ({self.city})" if self.name else self.city


class FAQ(TranslatedMixin, models.Model):
    question_en = models.CharField(_("question (EN)"), max_length=255)
    question_fr = models.CharField(_("question (FR)"), max_length=255, blank=True)
    answer_en = models.TextField(_("answer (EN)"))
    answer_fr = models.TextField(_("answer (FR)"), blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("FAQ")
        verbose_name_plural = _("FAQ")
        ordering = ["order", "pk"]

    def __str__(self):
        return self.question_en
