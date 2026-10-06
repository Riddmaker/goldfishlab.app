"""The stats page (P2) and the fake door's button (P9).

The stats page is staff only, inside the admin: routed in goldfishlab/urls.py
through `admin.site.admin_view`, which sends anybody who is not active staff
to the admin's sign-in.
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import View
from django_ratelimit.decorators import ratelimit

from metrics import doors, report
from simulations.models import SimulationRun


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
        "gate": report.gate(),
    }
    return TemplateResponse(request, "admin/stats.html", context)


@method_decorator(ratelimit(key="user", rate="10/m", method="POST"), name="post")
class CompareView(LoginRequiredMixin, View):
    """"Compare two versions" (P9): count the click, back to the report."""

    def post(self, request, pk):
        run = get_object_or_404(SimulationRun, pk=pk, owner=request.user)
        doors.ask(request)
        return redirect(f"{run.get_absolute_url()}#compare")
