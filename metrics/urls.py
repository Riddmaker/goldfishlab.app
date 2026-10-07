"""Metrics URLs: the fake door's button (P9). The stats page is in the admin."""

from django.urls import path

from metrics import views

app_name = "metrics"

urlpatterns = [
    path("runs/<uuid:pk>/compare/", views.CompareView.as_view(), name="compare"),
]
