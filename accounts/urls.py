"""Account URLs.

Mounted under `/account/` rather than `/accounts/`, which allauth already owns.
No primary key anywhere: there is one of each of these per user and it is
always `request.user`'s, exactly like the collection screens.
"""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("data/", views.PrivacyDataView.as_view(), name="data"),
    path("data/export/", views.DataExportView.as_view(), name="export"),
    path("delete/", views.AccountDeleteView.as_view(), name="delete"),
]
