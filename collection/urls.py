"""Collection URLs.

No primary key anywhere: there is one collection per user and it is always
`request.user`'s, so an id in the path would be an id somebody could change.
"""

from django.urls import path

from collection import views

app_name = "collection"

urlpatterns = [
    path("", views.CollectionView.as_view(), name="detail"),
    path("import/", views.CollectionImportView.as_view(), name="import"),
]
