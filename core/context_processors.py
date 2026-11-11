from django.conf import settings

from .models import Event, SiteSettings
from .utils import readable_on_white, text_color_on


def site(request):
    """Site settings, active event and theme colours for every template."""
    if request.path.startswith("/" + settings.ADMIN_URL):
        return {}
    site_settings = SiteSettings.load()
    primary = site_settings.primary_color or "#C8102E"
    accent = site_settings.accent_color or "#F5B921"
    return {
        "site": site_settings,
        "active_event": Event.get_active(),
        "theme": {
            "primary": primary,
            "on_primary": text_color_on(primary),
            "link": readable_on_white(primary),
            "accent": accent,
            "on_accent": text_color_on(accent),
        },
        "SITE_URL": settings.SITE_URL,
    }
