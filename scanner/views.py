import json
from functools import wraps

from django import forms
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from core.models import Event
from tickets.models import Ticket

from .models import ScanLog, ScannerAccess, StaffCard
from .services import event_counts, scan_ticket


ACCESS_SESSION_KEY = "nwp_scanner_access"


def is_scanner(user) -> bool:
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_staff or user.groups.filter(name=settings.SCANNER_GROUP_NAME).exists())
    )


def current_access(request) -> ScannerAccess | None:
    """Access (login + code from the admin) of this browser, if still valid.

    Re-checked on every request: unticking it in the admin, changing its code
    or reaching its end date logs the agent out at the next scan.
    """
    data = request.session.get(ACCESS_SESSION_KEY) or {}
    access = ScannerAccess.objects.filter(pk=data.get("id")).first() if data.get("id") else None
    if access is None or access.code != data.get("code") or not access.is_usable:
        if data:
            request.session.pop(ACCESS_SESSION_KEY, None)
        return None
    return access


def scanner_identity(request):
    """(user, access) allowed to scan, or None."""
    if is_scanner(request.user):
        return request.user, None
    access = current_access(request)
    if access:
        return None, access
    return None


def scanner_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        identity = scanner_identity(request)
        if identity is None:
            return redirect(f"{reverse('scanner:login')}?next={request.path}")
        request.scanner_user, request.scanner_access = identity
        return view(request, *args, **kwargs)

    return wrapper


def scanner_api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        identity = scanner_identity(request)
        if identity is None:
            return JsonResponse({"error": "forbidden"}, status=403)
        request.scanner_user, request.scanner_access = identity
        return view(request, *args, **kwargs)

    return wrapper


class ScannerLoginForm(AuthenticationForm):
    username = forms.CharField(
        label=gettext_lazy("E-mail"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "inputmode": "email"}),
    )


class AccessCodeForm(forms.Form):
    login = forms.CharField(label=gettext_lazy("Login"), max_length=30)
    code = forms.CharField(label=gettext_lazy("Access code"), max_length=20)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        self.access = ScannerAccess.find(cleaned.get("login", ""), cleaned.get("code", ""))
        if self.access is None:
            raise forms.ValidationError(_("Wrong login or access code, or this access was disabled."))
        return cleaned


def _next_url(request) -> str:
    next_url = request.GET.get("next", "")
    return next_url if next_url.startswith("/scan") and "//" not in next_url else reverse("scanner:scan")


@never_cache
@ratelimit(key="ip", rate=settings.SCANNER_LOGIN_RATE, method="POST", block=False)
@require_http_methods(["GET", "POST"])
def login_view(request):
    """Entrance agents: login + access code from the admin. Staff: e-mail + password."""
    if scanner_identity(request) is not None:
        return redirect("scanner:scan")
    mode = "account" if request.POST.get("mode") == "account" or request.GET.get("mode") == "account" else "code"
    code_form = AccessCodeForm(
        request.POST if request.method == "POST" and mode == "code" else None,
        initial={"login": request.GET.get("login", ""), "code": request.GET.get("code", "")},
    )
    account_form = ScannerLoginForm(request, data=request.POST if request.method == "POST" and mode == "account" else None)
    if request.method == "POST":
        form = code_form if mode == "code" else account_form
        if getattr(request, "limited", False):
            form.add_error(None, _("Too many attempts. Please wait 15 minutes."))
        elif mode == "code" and code_form.is_valid():
            request.session.cycle_key()
            request.session[ACCESS_SESSION_KEY] = {"id": code_form.access.pk, "code": code_form.access.code}
            return redirect(_next_url(request))
        elif mode == "account" and account_form.is_valid():
            user = account_form.get_user()
            if is_scanner(user):
                login(request, user)
                return redirect(_next_url(request))
            account_form.add_error(None, _("This account is not allowed to scan tickets."))
    return render(request, "scanner/login.html", {"code_form": code_form, "form": account_form, "mode": mode})


@require_POST
def logout_view(request):
    request.session.pop(ACCESS_SESSION_KEY, None)
    logout(request)
    return redirect("scanner:login")


@never_cache
@scanner_required
def scan_page(request):
    event = Event.get_active()
    agent = request.scanner_access.name if request.scanner_access else (request.user.get_full_name() or request.user.get_username())
    return render(request, "scanner/scan.html", {"event": event, "counts": event_counts(event), "agent": agent})


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
        request.scanner_user,
        Event.get_active(),
        device_info=request.META.get("HTTP_USER_AGENT", ""),
        access=request.scanner_access,
    )
    return JsonResponse(payload)


@never_cache
@require_GET
@scanner_api
def api_stats(request):
    event = Event.get_active()
    recent = ScanLog.objects.select_related("ticket", "staff_card", "scanned_by", "scanner_access").order_by("-created_at")[:10]
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
                    "by": log.scanner_access.name if log.scanner_access else (log.scanned_by.get_username() if log.scanned_by else ""),
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
    """Web app manifest: the scanner installs like an app (Android, iPhone, desktop)."""
    data = {
        "id": "/scan/",
        "name": "NextWave Scanner",
        "short_name": "NW Scan",
        "description": "Entrance ticket scanner",
        "start_url": "/scan/?source=app",
        "scope": "/scan/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#111827",
        "theme_color": "#C8102E",
        "icons": [
            {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    response = JsonResponse(data, content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def service_worker(request):
    response = render(request, "scanner/sw.js", content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/scan/"
    response["Cache-Control"] = "no-cache"
    return response
