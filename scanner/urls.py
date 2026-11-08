from django.urls import path

from . import views

app_name = "scanner"

urlpatterns = [
    path("scan/", views.scan_page, name="scan"),
    path("scan/login/", views.login_view, name="login"),
    path("scan/logout/", views.logout_view, name="logout"),
    path("scan/manifest.webmanifest", views.manifest, name="manifest"),
    path("scan/sw.js", views.service_worker, name="sw"),
    path("scan/photo/<uuid:ticket_uuid>/", views.holder_photo, name="photo"),
    path("scan/staff-photo/<uuid:card_uuid>/", views.staff_photo, name="staff_photo"),
    path("api/scan/", views.api_scan, name="api_scan"),
    path("api/scan/stats/", views.api_stats, name="api_stats"),
]
