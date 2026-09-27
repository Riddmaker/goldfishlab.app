"""The one screen element that can cause an outbound request.

Deliberately a POST, and deliberately the only entry point. A GET that fetches
is a GET a link preview, a crawler or a browser prefetch can fire - and every
one of those would be this application making a request to somebody else's free
API on behalf of nobody.

The panel itself renders from cache on the deck page and never fetches, so a
Spellbook outage costs the page its freshness and not its contents.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views.generic import View
from django_ratelimit.decorators import ratelimit

from combos import services
from decks.models import Deck


# The per-deck cooldown bounds how often ONE deck is looked up; this bounds a
# person with many decks. Six a minute is more than anybody presses a button
# and less than a script walking an account's decks against somebody else's
# free API.
@method_decorator(ratelimit(key="user", rate="6/m", method="POST"), name="post")
class RefreshCombosView(LoginRequiredMixin, View):
    """Ask Commander Spellbook about this deck, then re-render the panel."""

    template_name = "combos/_panel.html"

    def post(self, request, pk):
        # Filtered on owner at the source, like every other deck queryset here:
        # a missing filter is then a missing row rather than someone else's deck.
        deck = get_object_or_404(Deck, pk=pk, owner=request.user)

        outcome = services.refresh(deck)
        refusal = outcome.reason if isinstance(outcome, services.Refusal) else ""

        panel = services.panel_for(deck)
        if request.headers.get("HX-Request"):
            return render(request, self.template_name,
                          {"deck": deck, "combos": panel, "refusal": refusal})
        return redirect(deck.get_absolute_url())
