from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("feexpay/pay/<str:order_ref>/", views.feexpay_pay, name="feexpay_pay"),
    path("<slug:gateway>/init/<str:order_ref>/", views.init_payment, name="init"),
    path("<slug:gateway>/callback/", views.callback, name="callback"),
    path("<slug:gateway>/webhook/", views.webhook, name="webhook"),
]
