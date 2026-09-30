"""Guest URLs: try without an account, then save."""

from django.urls import path

from guests import views

app_name = "guests"

urlpatterns = [
    path("", views.TryView.as_view(), name="try"),
    path("save/", views.SaveDeckView.as_view(), name="save"),
]
