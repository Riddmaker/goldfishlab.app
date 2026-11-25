"""Look URLs (B-10)."""

from django.urls import path

from looks import views

app_name = "looks"

urlpatterns = [
    # The image's path on the share never shows: the look and the kind name it.
    path("<uuid:pk>/<slug:kind>.webp", views.image, name="image"),
]
