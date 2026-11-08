from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("buy/", views.buy, name="buy"),
    path("terms/", views.legal, {"page": "terms"}, name="terms"),
    path("privacy/", views.legal, {"page": "privacy"}, name="privacy"),
    path("event.ics", views.event_ics, name="event_ics"),
]
