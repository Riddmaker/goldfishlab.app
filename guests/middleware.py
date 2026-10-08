"""Keep a guest out of what only an account has.

A guest is a signed-in user, so every `LoginRequiredMixin` lets it through.
Most of what that opens is its own deck and exactly what it should see; what
is left is here, by URL, in one place: billing, the account screens, and
allauth's address and password screens - none of which mean anything for a
row with a placeholder address and no password.
"""

from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from changelog import services as changelog_services
from guests import services

#: Path prefixes a guest is sent away from, and where to.
FENCED = (
    ("/billing/", "guests:save"),
    ("/account/", "guests:save"),
    ("/accounts/email/", "guests:save"),
    ("/accounts/password/", "guests:save"),
    ("/accounts/signup/", "guests:save"),
    # A guest holds one deck; its importer is the trial's.
    ("/decks/import/", "guests:try"),
)

#: Signing in as somebody else ends the guest here, so allauth's login page
#: (which turns away anybody signed in) opens for them. The unsaved deck goes
#: with the guest when it expires.
SIGN_IN = "/accounts/login/"


class GuestFenceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if services.is_guest(request.user):
            path = request.path
            if path.startswith(SIGN_IN):
                # A wish to get the changelog by mail (C7) outlives the guest.
                changelog_services.keep_across(request, logout)
            else:
                for prefix, target in FENCED:
                    if path.startswith(prefix):
                        return redirect(reverse(target))
        return self.get_response(request)
