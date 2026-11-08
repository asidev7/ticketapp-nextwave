from django.urls import path

from . import views

app_name = "tickets"

urlpatterns = [
    path("checkout/<slug:slug>/", views.checkout, name="checkout"),
    path("order/<str:reference>/", views.order_detail, name="order"),
    path("api/order/<str:reference>/status/", views.order_status, name="order_status"),
    path("find-ticket/", views.find_ticket, name="find"),
    path("dl/ticket/<str:token>/<str:kind>/", views.download_ticket, name="download_ticket"),
    path("dl/order/<str:token>/", views.download_order, name="download_order"),
]
