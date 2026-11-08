from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ScanLog(models.Model):
    class Result(models.TextChoices):
        ACCEPTED = "accepted", _("Accepted")
        ALREADY_USED = "already_used", _("Already used")
        INVALID = "invalid", _("Invalid")
        CANCELLED = "cancelled", _("Cancelled")
        WRONG_EVENT = "wrong_event", _("Wrong event")

    ticket = models.ForeignKey(
        "tickets.Ticket", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scan_logs", verbose_name=_("ticket"),
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
