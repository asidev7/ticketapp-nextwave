from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("favicon.ico", RedirectView.as_view(url=settings.STATIC_URL + "img/favicon.png", permanent=True)),
    path("i18n/", include("django.conf.urls.i18n")),
    path("payments/", include("payments.urls")),
    path("", include("scanner.urls")),
    path("", include("tickets.urls")),
    path("", include("core.urls")),
]

handler404 = "core.views.page_not_found"
handler500 = "core.views.server_error"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
