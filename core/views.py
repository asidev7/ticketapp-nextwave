from datetime import timezone as dt_timezone

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseServerError
from django.shortcuts import redirect, render
from django.template import loader
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from lineup.models import ArtistCategory

from .models import FAQ, Event, SiteSettings


@require_GET
def home(request):
    event = Event.get_active()
    if not event:
        return render(request, "core/no_event.html")

    artists = list(event.artists.filter(is_active=True).select_related("category"))
    lineup = []  # poster style: "WHEELS: DJ Lion, DJ Khalifa…" per category
    for category in ArtistCategory.objects.filter(pk__in={a.category_id for a in artists}):
        names = [a for a in artists if a.category_id == category.pk and not a.is_headliner]
        if names:
            lineup.append((category, names))
    ticket_types = list(event.ticket_types.filter(is_active=True).prefetch_related("benefits"))
    on_sale = SiteSettings.load().sales_open
    first_slide = event.slides.filter(is_active=True).first()

    return render(
        request,
        "core/home.html",
        {
            "event": event,
            "visual": event.poster_image or (first_slide.image if first_slide else None),
            "headliners": [a for a in artists if a.is_headliner],
            "lineup": lineup,
            "ticket_types": ticket_types,
            "currency": "NGN" if event.country.strip().lower().startswith("nigeria") else "XOF",
            "sales_open": on_sale,
            "outlets": event.outlets.filter(is_active=True),
            "videos": [v for v in event.videos.filter(is_active=True) if v.video_id],
            "faqs": FAQ.objects.filter(is_active=True),
            "min_prices": event.min_prices(),
        },
    )


@require_GET
def buy(request):
    """Target of the poster QR code: always lands on the ticket section."""
    return redirect(reverse("core:home") + "#tickets")


@require_GET
def legal(request, page):
    site = SiteSettings.load()
    if page not in ("terms", "privacy"):
        raise Http404
    return render(request, "core/legal.html", {"page": page, "content": site.tr(page)})


def _ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@require_GET
def event_ics(request):
    """'Add to calendar' file for the active event."""
    event = Event.get_active()
    if not event:
        raise Http404
    fmt = "%Y%m%dT%H%M%SZ"
    start = event.date_start.astimezone(dt_timezone.utc)
    end = event.ends_at.astimezone(dt_timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//NextWave Party//Tickets//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:event-{event.pk}@{request.get_host()}",
        f"DTSTAMP:{timezone.now().astimezone(dt_timezone.utc).strftime(fmt)}",
        f"DTSTART:{start.strftime(fmt)}",
        f"DTEND:{end.strftime(fmt)}",
        f"SUMMARY:{_ics_escape(f'{event.name} — {event.edition}')}",
        f"LOCATION:{_ics_escape(f'{event.venue_name}, {event.venue_address}, {event.city}')}",
        f"DESCRIPTION:{_ics_escape(settings.SITE_URL)}",
        f"URL:{settings.SITE_URL}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    response = HttpResponse("\r\n".join(lines) + "\r\n", content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}.ics"'
    return response


def page_not_found(request, exception=None):
    return render(request, "404.html", status=404)


def server_error(request):
    # No request context: the error may come from the database itself.
    return HttpResponseServerError(loader.get_template("500.html").render())
