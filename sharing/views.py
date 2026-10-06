"""Sharing a run's report, and the page a shared link opens (P4).

The owner's two buttons live on the run page and post here; they find the run
through the owner's own runs, so somebody else's run id is a 404 like
everywhere else. The public page is found by token alone and shows nothing
that would lead back to the owner: no email, no deck or run id, none of the
owner's buttons, and nothing read off the deck as it is today.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.utils.safestring import mark_safe
from django.utils.translation import get_language, gettext
from django.views.generic import TemplateView, View
from django_ratelimit.decorators import ratelimit

from core import seo
from sharing import services, text
from sharing.models import SharedReport
from simulations import report
from simulations.models import SimulationRun

#: How long a rendered report is kept, per link and language. A report never
#: changes, so this only bounds the memory; the point is that a link posted to
#: a busy thread is drawn once every few minutes, not once a visit.
CACHE_SECONDS = 600


@method_decorator(ratelimit(key="user", rate="10/m", method="POST"), name="post")
class ShareView(LoginRequiredMixin, View):
    """"Share this report": make the link, or say why not."""

    def post(self, request, pk):
        run = get_object_or_404(SimulationRun.objects.select_related("deck", "owner"),
                                pk=pk, owner=request.user)
        back = f"{run.get_absolute_url()}#share"
        refusal = services.refusal(run)
        if refusal:
            messages.error(request, gettext(refusal))
            return redirect(back)
        services.share(run)
        messages.success(request, gettext(
            "Shared. Anybody with the link can read this report, and search engines "
            "may list it."))
        return redirect(back)


@method_decorator(ratelimit(key="user", rate="10/m", method="POST"), name="post")
class StopView(LoginRequiredMixin, View):
    """"Stop sharing": the link answers 404 from now on."""

    def post(self, request, pk):
        run = get_object_or_404(SimulationRun, pk=pk, owner=request.user)
        if services.stop(run):
            messages.success(request, gettext("Stopped. The link no longer works."))
        return redirect(f"{run.get_absolute_url()}#share")


@method_decorator(ratelimit(key="ip", rate="60/m", method="GET", block=True), name="get")
class SharedReportView(TemplateView):
    """/r/<token>/: one shared report, for anybody."""

    template_name = "sharing/report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        shared = get_object_or_404(
            SharedReport.objects.select_related("run", "run__deck"), token=kwargs["token"])
        run = shared.run
        services.count_view(shared, self.request)
        url = seo.absolute(self.request, shared.get_absolute_url())
        key = f"sharing:report:{shared.token}:{get_language()}"
        parts = cache.get(key)
        if parts is None:
            built = report.build(run)
            parts = {
                "body": render_to_string("sharing/_body.html", {
                    "run": run, "report": built, "shared_report": shared, "shared": True,
                }, request=self.request),
                "description": text.description(built, run),
                "text": text.plain(shared, built, run, url),
            }
            cache.set(key, parts, CACHE_SECONDS)
        context.update({
            "shared_report": shared,
            "run": run,
            "page_heading": text.title(shared, run),
            "body": mark_safe(parts["body"]),  # noqa: S308 - our own template's output
            "share_description": parts["description"],
            "share_text": parts["text"],
            "share_url": url,
            "og_type": "article",
            "is_owner": self.request.user.is_authenticated
            and self.request.user.pk == run.owner_id,
        })
        return context
