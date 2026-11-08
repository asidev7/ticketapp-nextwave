import json
from functools import wraps

from django import forms
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from core.models import Event
from tickets.models import Ticket

from .models import ScanLog, StaffCard
from .services import event_counts, scan_ticket


def is_scanner(user) -> bool:
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_staff or user.groups.filter(name=settings.SCANNER_GROUP_NAME).exists())
    )


def scanner_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_scanner(request.user):
            return redirect(f"{reverse('scanner:login')}?next={request.path}")
        return view(request, *args, **kwargs)

    return wrapper


def scanner_api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_scanner(request.user):
            return JsonResponse({"error": "forbidden"}, status=403)
        return view(request, *args, **kwargs)

    return wrapper


class ScannerLoginForm(AuthenticationForm):
    username = forms.CharField(
        label=gettext_lazy("E-mail"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "inputmode": "email"}),
    )


@never_cache
@ratelimit(key="ip", rate=settings.SCANNER_LOGIN_RATE, method="POST", block=False)
@require_http_methods(["GET", "POST"])
def login_view(request):
    if is_scanner(request.user):
        return redirect("scanner:scan")
    form = ScannerLoginForm(request, data=request.POST or None)
    if request.method == "POST":
        if getattr(request, "limited", False):
            form.add_error(None, _("Too many attempts. Please wait 15 minutes."))
        elif form.is_valid():
            user = form.get_user()
            if is_scanner(user):
                login(request, user)
                next_url = request.GET.get("next", "")
                return redirect(next_url if next_url.startswith("/scan") else reverse("scanner:scan"))
            form.add_error(None, _("This account is not allowed to scan tickets."))
    return render(request, "scanner/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("scanner:login")


@never_cache
@scanner_required
def scan_page(request):
    event = Event.get_active()
    return render(request, "scanner/scan.html", {"event": event, "counts": event_counts(event)})


@never_cache
@require_POST
@scanner_api
def api_scan(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "bad request"}, status=400)
    value = str(data.get("value", ""))
    if not value.strip():
        return JsonResponse({"error": "empty"}, status=400)
    payload = scan_ticket(
        value,
        request.user,
        Event.get_active(),
        device_info=request.META.get("HTTP_USER_AGENT", ""),
    )
    return JsonResponse(payload)


@never_cache
@require_GET
@scanner_api
def api_stats(request):
    event = Event.get_active()
    recent = ScanLog.objects.select_related("ticket", "staff_card", "scanned_by").order_by("-created_at")[:10]
    return JsonResponse(
        {
            **event_counts(event),
            "recent": [
                {
                    "result": log.result,
                    "label": log.get_result_display(),
                    "code": log.ticket.code if log.ticket else (log.staff_card.code if log.staff_card else log.raw_value[:20]),
                    "holder": log.ticket.holder_name if log.ticket else (log.staff_card.full_name if log.staff_card else ""),
                    "at": log.created_at.isoformat(),
                    "by": log.scanned_by.get_username() if log.scanned_by else "",
                }
                for log in recent
            ],
        }
    )


@scanner_required
def holder_photo(request, ticket_uuid):
    ticket = get_object_or_404(Ticket, uuid=ticket_uuid)
    if not ticket.holder_photo:
        raise Http404
    response = FileResponse(ticket.holder_photo.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=600"
    return response


@scanner_required
def staff_photo(request, card_uuid):
    card = get_object_or_404(StaffCard, uuid=card_uuid)
    if not card.photo:
        raise Http404
    response = FileResponse(card.photo.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=600"
    return response


@require_GET
def manifest(request):
    data = {
        "name": "NextWave Scanner",
        "short_name": "NWP Scan",
        "start_url": "/scan/",
        "scope": "/scan/",
        "display": "fullscreen",
        "orientation": "portrait",
        "background_color": "#111827",
        "theme_color": "#0A3D91",
        "icons": [
            {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json")


@require_GET
def service_worker(request):
    response = render(request, "scanner/sw.js", content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/scan/"
    response["Cache-Control"] = "no-cache"
    return response
