"""Template helpers. Numbers and dates are always English-formatted."""
from django import template
from django.utils import dateformat, timezone, translation
from django.utils.html import escape
from django.utils.safestring import mark_safe

from core.icons import BRAND, LINE
from core.utils import flag_emoji, format_money, format_number

register = template.Library()

# Inline SVG flags (emoji flags render as two letters on Windows).
FLAGS = {
    "BJ": ("0 0 900 600", '<rect width="900" height="600" fill="#E8112D"/><rect width="900" height="300" fill="#FCD116"/><rect width="360" height="600" fill="#008751"/>'),
    "NG": ("0 0 3 2", '<rect width="3" height="2" fill="#008751"/><rect x="1" width="1" height="2" fill="#FFFFFF"/>'),
    "GH": ("0 0 450 300", '<rect width="450" height="300" fill="#006B3F"/><rect width="450" height="200" fill="#FCD116"/><rect width="450" height="100" fill="#CE1126"/>'
           '<polygon fill="#000000" points="225,100 236.2,134.5 272.6,134.5 243.2,155.9 254.4,190.5 225,169.1 195.6,190.5 206.8,155.9 177.4,134.5 213.8,134.5"/>'),
    "FR": ("0 0 3 2", '<rect width="3" height="2" fill="#CE1126"/><rect width="2" height="2" fill="#FFFFFF"/><rect width="1" height="2" fill="#002654"/>'),
    "GB": ("0 0 60 30", '<rect width="60" height="30" fill="#012169"/><path d="M0,0 L60,30 M60,0 L0,30" stroke="#FFFFFF" stroke-width="6"/>'
           '<path d="M0,0 L60,30 M60,0 L0,30" stroke="#C8102E" stroke-width="2"/><path d="M30,0 v30 M0,15 h60" stroke="#FFFFFF" stroke-width="10"/>'
           '<path d="M30,0 v30 M0,15 h60" stroke="#C8102E" stroke-width="6"/>'),
}
LANGUAGE_FLAGS = {"en": "GB", "fr": "FR"}


@register.simple_tag
def icon(name: str, size: int = 20, css: str = ""):
    """{% icon "calendar" %} · {% icon "instagram" 22 "social__icon" %} — decorative inline SVG."""
    classes = escape(f"icon icon-{name} {css}".strip())
    if name in BRAND:
        return mark_safe(
            f'<svg class="{classes}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="currentColor" '
            f'aria-hidden="true" focusable="false">{BRAND[name]}</svg>'
        )
    return mark_safe(
        f'<svg class="{classes}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">{LINE.get(name, "")}</svg>'
    )


@register.simple_tag
def flag_svg(code: str, css: str = ""):
    """{% flag_svg "BJ" %} — SVG flag for known countries / languages, emoji otherwise."""
    code = (code or "").upper()
    code = LANGUAGE_FLAGS.get(code.lower(), code)
    if code not in FLAGS:
        return flag_emoji(code)
    view_box, body = FLAGS[code]
    return mark_safe(
        f'<svg class="flag {escape(css)}" viewBox="{view_box}" preserveAspectRatio="xMidYMid slice" '
        f'aria-hidden="true" focusable="false">{body}</svg>'
    )


@register.filter
def tr(obj, field: str) -> str:
    """{{ event|tr:"description" }} → description_fr in French (if filled) else description_en."""
    if obj is None:
        return ""
    return obj.tr(field)


@register.filter
def num(value) -> str:
    """{{ 1500|num }} → 1,500"""
    return format_number(value)


@register.filter
def money(amount, currency: str = "XOF") -> str:
    """{{ 10000|money:"XOF" }} → XOF 10,000"""
    if amount in (None, ""):
        return ""
    return format_money(amount, currency)


@register.filter
def endate(value, fmt: str = "M j, Y · g:i A") -> str:
    """{{ event.date_start|endate:"M j, Y" }} — English month names in every language."""
    if not value:
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    with translation.override("en"):
        return dateformat.format(value, fmt)


@register.filter
def isodate(value) -> str:
    return value.isoformat() if value else ""


@register.filter
def flag(country_code: str) -> str:
    return flag_emoji(country_code)


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.simple_tag(takes_context=True)
def lang_is(context, code: str) -> bool:
    return (translation.get_language() or "en").startswith(code)
