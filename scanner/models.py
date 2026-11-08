import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.storage import private_storage


class StaffCard(models.Model):
    """Member-card badge (85.6 × 54 mm) with photo and a signed QR for the entrance."""

    class Role(models.TextChoices):
        STAFF = "staff", _("Staff")
        SECURITY = "security", _("Security")
        ORGANIZER = "organizer", _("Organizer")
        ARTIST = "artist", _("Artist")
        PRESS = "press", _("Press")
        VOLUNTEER = "volunteer", _("Volunteer")
        TECHNICAL = "technical", _("Technical crew")

    ROLE_COLORS = {
        "staff": "#FF6B00",
        "security": "#DC2626",
        "organizer": "#7C3AED",
        "artist": "#DB2777",
        "press": "#0891B2",
        "volunteer": "#16A34A",
        "technical": "#4B5563",
    }

    event = models.ForeignKey("core.Event", on_delete=models.CASCADE, related_name="staff_cards", verbose_name=_("event"))
    first_name = models.CharField(_("first name"), max_length=60)
    last_name = models.CharField(_("last name"), max_length=60)
    role = models.CharField(_("role"), max_length=12, choices=Role.choices, default=Role.STAFF)
    title = models.CharField(_("title"), max_length=60, blank=True, help_text=_("Optional, e.g. Head of security, Stage manager."))
    photo = models.ImageField(_("photo"), storage=private_storage, upload_to="staff/")
    language = models.CharField(_("card language"), max_length=5, default="en", choices=settings.LANGUAGES)
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Untick to refuse this card at the entrance."))

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.CharField(_("code"), max_length=20, unique=True, editable=False)
    qr_token = models.CharField(_("QR content"), max_length=80, editable=False)
    card_png = models.FileField(storage=private_storage, upload_to="staff/cards/", blank=True, editable=False)
    card_pdf = models.FileField(storage=private_storage, upload_to="staff/cards/", blank=True, editable=False)

    scan_count = models.PositiveIntegerField(_("entries"), default=0)
    last_scan_at = models.DateTimeField(_("last entry"), null=True, blank=True)
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        verbose_name = _("staff card")
        verbose_name_plural = _("staff cards")
        ordering = ["role", "last_name", "first_name"]

    def __str__(self):
        return f"{self.full_name} — {self.get_role_display()}"

    def save(self, *args, **kwargs):
        from tickets.qr import build_staff_payload
        from tickets.services import _random

        if not self.code:
            while True:
                code = f"STF-{_random(4)}-{_random(4)}"
                if not StaffCard.objects.filter(code=code).exists():
                    self.code = code
                    break
        if not self.qr_token:
            self.qr_token = build_staff_payload(self.uuid)
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def color(self) -> str:
        return self.ROLE_COLORS.get(self.role, "#FF6B00")


class ScanLog(models.Model):
    class Result(models.TextChoices):
        ACCEPTED = "accepted", _("Accepted")
        ALREADY_USED = "already_used", _("Already used")
        INVALID = "invalid", _("Invalid")
        CANCELLED = "cancelled", _("Cancelled")
        WRONG_EVENT = "wrong_event", _("Wrong event")
        STAFF = "staff", _("Staff card")

    ticket = models.ForeignKey(
        "tickets.Ticket", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scan_logs", verbose_name=_("ticket"),
    )
    staff_card = models.ForeignKey(
        StaffCard, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scan_logs", verbose_name=_("staff card"),
    )
    raw_value = models.CharField(_("scanned value"), max_length=255)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="scan_logs", verbose_name=_("scanned by"),
    )
    device_info = models.CharField(_("device"), max_length=255, blank=True)
    result = models.CharField(_("result"), max_length=20, choices=Result.choices, db_index=True)
    created_at = models.DateTimeField(_("date"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("scan log")
        verbose_name_plural = _("scan logs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_result_display()} — {self.raw_value[:30]}"
