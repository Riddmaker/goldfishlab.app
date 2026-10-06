"""The stats page (P2): staff only, inside the admin.

Routed in goldfishlab/urls.py through `admin.site.admin_view`, which sends
anybody who is not active staff to the admin's sign-in.
"""

from django.conf import settings
from django.contrib import admin
from django.template.response import TemplateResponse
from django.utils import timezone

from metrics import report


def stats(request):
    today = timezone.localdate()
    recent = report.weeks(today)
    context = {
        **admin.site.each_context(request),
        "title": "Stats",
        "today": today,
        "time_zone": settings.TIME_ZONE,
        "weeks": recent,
        "lead": report.lead(today, recent),
        "targets": report.TARGETS,
        "columns": [name.label for name in report.COLUMNS],
        "cohorts": report.cohorts(today),
    }
    return TemplateResponse(request, "admin/stats.html", context)
