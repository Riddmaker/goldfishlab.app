"""Billing URLs.

`webhook/` is the only unauthenticated one, and it is deliberately not under
`plans/` - a person browsing should never be one mistyped path away from the
endpoint that writes subscription status.
"""

from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("", views.PlansView.as_view(), name="plans"),
    path("checkout/<slug:slug>/", views.StartCheckoutView.as_view(), name="checkout"),
    path("portal/", views.PortalView.as_view(), name="portal"),
    path("done/", views.CheckoutDoneView.as_view(), name="done"),
    path("webhook/", views.WebhookView.as_view(), name="webhook"),
]
