"""Account screens: your data, and the end of your account.

Both are POST-only where they act. A GET that deletes an account is a link a
browser's prefetcher can follow, and a GET that returns an export is a URL that
lands in somebody's history and in any proxy log between here and them.
"""

import json

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.views.generic import TemplateView, View
from django_ratelimit.decorators import ratelimit

from accounts import consent, privacy


class PrivacyDataView(LoginRequiredMixin, TemplateView):
    """The page that offers both rights, and warns before the irreversible one."""

    template_name = "accounts/data.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["blockers"] = privacy.deletion_blockers(self.request.user)
        return context


# Building an export walks a dozen tables and serialises every simulation
# result the account owns, which for a heavy user is megabytes. Three a minute
# is far more than anybody needs and far less than a way to make the database
# work for free.
@method_decorator(ratelimit(key="user", rate="3/m", method="POST"), name="post")
class DataExportView(LoginRequiredMixin, View):
    """Download everything this application holds about you, as JSON."""

    def post(self, request):
        payload = privacy.export(request.user)
        stamp = timezone.now().strftime("%Y-%m-%d")

        # `default=str` because the payload is full of UUIDs, dates and
        # Decimals. Indented because a person is going to open this in a text
        # editor, and a single-line megabyte is not an answer to "what do you
        # hold about me".
        body = json.dumps(payload, default=str, indent=2, ensure_ascii=False)

        response = HttpResponse(body, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="goldfish-lab-export-{stamp}.json"'
        )
        # Nothing between here and the browser should keep a copy.
        response["Cache-Control"] = "no-store"
        return response


class AccountDeleteView(LoginRequiredMixin, View):
    """Erase the account. There is no undo and the page says so twice."""

    def post(self, request):
        blockers = privacy.deletion_blockers(request.user)
        if blockers:
            # Checked again here and not only in the template. A page that
            # hides a button is not a rule; the rule is the one on the server.
            for blocker in blockers:
                messages.error(request, blocker)
            return redirect("accounts:data")

        # Typing the address is the confirmation. A checkbox is muscle memory
        # by the third time somebody sees one, and this is the action in the
        # whole application with no way back.
        typed = (request.POST.get("confirm_email") or "").strip().lower()
        if typed != request.user.email.lower():
            messages.error(
                request,
                gettext("That is not the address this account uses, so nothing was deleted."),
            )
            return redirect("accounts:data")

        user = request.user
        # Log out FIRST: the session row points at a user about to stop
        # existing, and `logout()` also flushes the session, so doing it after
        # the delete would be writing a session for a deleted account.
        logout(request)
        privacy.delete(user)

        messages.success(
            request,
            gettext("Your account and everything in it have been deleted. "
                    "Nothing was kept."),
        )
        return redirect("home")


class PrivacyUpdateView(LoginRequiredMixin, TemplateView):
    """What changed in the privacy policy, and the OK that lets a person go on
    (D2, `accounts.consent`)."""

    template_name = "accounts/privacy_update.html"

    def _next(self, value: str) -> str:
        if value and url_has_allowed_host_and_scheme(
                value, allowed_hosts={self.request.get_host()},
                require_https=self.request.is_secure()):
            return value
        return reverse("home")

    def get(self, request, *args, **kwargs):
        if not consent.needs_consent(request.user):
            return redirect(self._next(request.GET.get("next", "")))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next"] = self._next(self.request.GET.get("next", ""))
        context["version"] = consent.CONSENT_VERSION
        return context

    def post(self, request):
        consent.accept(request.user)
        return redirect(self._next(request.POST.get("next", "")))
