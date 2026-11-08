from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LineupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "lineup"
    verbose_name = _("Lineup")
