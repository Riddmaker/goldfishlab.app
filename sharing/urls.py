"""Sharing URLs (P4).

The owner's two buttons hang off the run, under /runs/ like the rest of it.
The public page is /r/<token>/: short enough to paste, with no language in it
(a reader gets their own language from the browser), and outside /runs/, which
stays the owner's.
"""

from django.urls import path

from sharing import views

app_name = "sharing"

urlpatterns = [
    path("runs/<uuid:pk>/share/", views.ShareView.as_view(), name="share"),
    path("runs/<uuid:pk>/share/stop/", views.StopView.as_view(), name="stop"),
    path("r/<str:token>/", views.SharedReportView.as_view(), name="report"),
]
