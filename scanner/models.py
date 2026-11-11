import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.storage import private_storage

# No 0/O/1/I: easy to read out on the phone.
ACCESS_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def new_access_code() -> str:
    raw = "".join(secrets.choice(ACCESS_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


class ScannerAccess(models.Model):
    """Login + access code given by the admin to an entrance agent: the scan
    page opens with them, no user account needed. Untick or delete to revoke.
    At most MAX_ACTIVE accesses are active at the same time."""

    MAX_ACTIVE = 6

    name = models.CharField(_("name"), max_length=80, help_text=_("Who uses it, e.g. Gate 1 — John."))
    login = models.SlugField(_("login"), max_length=30, unique=True, help_text=_("Identifier the agent types, e.g. gate1."))
    code = models.CharField(_("access code"), max_length=12, default=new_access_code, editable=False)
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Untick to log the agent out and refuse the code."))
    expires_at = models.DateTimeField(_("valid until"), null=True, blank=True, help_text=_("Empty = no limit."))
    scan_count = models.PositiveIntegerField(_("scans"), default=0, editable=False)
    last_used_at = models.DateTimeField(_("last used"), null=True, blank=True, editable=False)
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        verbose_name = _("scanner access")
        verbose_name_plural = _("scanner accesses")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_usable(self) -> bool:
        return self.is_active and (self.expires_at is None or self.expires_at > timezone.now())

    def save(self, *args, **kwargs):
        self.login = (self.login or "").lower()
        super().save(*args, **kwargs)

    @staticmethod
    def normalize_code(code: str) -> str:
        """'abcd efgh' / 'ABCDEFGH' → 'ABCD-EFGH'."""
        compact = "".join(ch for ch in (code or "").upper() if ch.isalnum())
        return f"{compact[:4]}-{compact[4:]}" if len(compact) == 8 else ""

    @classmethod
    def find(cls, login: str, code: str):
        """Usable access for a login + code typed on the scan page, else None."""
        code = cls.normalize_code(code)
        if not code:
            return None
        access = cls.objects.filter(login=(login or "").strip().lower()).first()
        if access is None or not secrets.compare_digest(access.code, code):
            return None
        return access if access.is_usable else None


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
        "staff": "#F5B921",
        "security": "#111827",
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
        return self.ROLE_COLORS.get(self.role, "#F5B921")


class CheckIn(models.Model):
    """One entry of a ticket on one event day (a 2-day ticket gets at most two)."""

    ticket = models.ForeignKey("tickets.Ticket", on_delete=models.CASCADE, related_name="checkins", verbose_name=_("ticket"))
    day = models.DateField(_("day"))
    scanned_at = models.DateTimeField(_("time"), default=timezone.now)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("scanned by"),
    )
    access = models.ForeignKey(
        ScannerAccess, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="checkins", verbose_name=_("scanner access"),
    )

    class Meta:
        verbose_name = _("entry")
        verbose_name_plural = _("entries")
        ordering = ["day"]
        constraints = [models.UniqueConstraint(fields=["ticket", "day"], name="one_entry_per_ticket_per_day")]

    def __str__(self):
        return f"{self.ticket} — {self.day}"

    @property
    def scanned_by_label(self) -> str:
        if self.access_id:
            return self.access.name
        if self.scanned_by_id:
            user = self.scanned_by
            return user.get_full_name() or user.email or user.get_username()
        return ""


class ScanLog(models.Model):
    class Result(models.TextChoices):
        ACCEPTED = "accepted", _("Accepted")
        ALREADY_USED = "already_used", _("Already used")
        INVALID = "invalid", _("Invalid")
        CANCELLED = "cancelled", _("Cancelled")
        WRONG_EVENT = "wrong_event", _("Wrong event")
        WRONG_DAY = "wrong_day", _("Not valid today")
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
    scanner_access = models.ForeignKey(
        ScannerAccess, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scan_logs", verbose_name=_("scanner access"),
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
