"""Data page URLs (P11), mounted with the other public pages, which carry
their language in the address (P10, goldfishlab.urls)."""

from django.urls import path

from datapages import views

app_name = "datapages"

urlpatterns = [
    path("commander-precons/", views.PreconListView.as_view(), name="precons"),
    path("commander-precons/<slug:slug>/", views.PreconView.as_view(), name="precon"),
    path("how-many-lands-in-commander/", views.LandsArticleView.as_view(), name="lands"),
]
