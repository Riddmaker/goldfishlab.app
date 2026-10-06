"""A shared report: one finished run, readable by anybody with its link (P4).

Until P4 every report was its owner's alone, and nothing could be shown to a
friend, a playgroup or a subreddit. Sharing is opt-in, one run at a time, and
undone by deleting the row - the link then answers 404, and sharing again
makes a new token, so a link once stopped never comes back.

**The deck is frozen when the report is shared.** A run stores its result but
not the card list it shuffled, and the deck goes on changing afterwards. So a
run may only be shared while the deck is still the one it played (its
`deck_print` matches), and the list, the names and - if it describes exactly
that deck - the written summary are copied here. A later edit to the deck
changes nothing a reader of the link sees.

`views` is one number: how often the page was opened by somebody other than
the owner, link-preview robots left out. Nothing about who opened it is kept.
"""

import secrets

from django.db import models
from django.urls import reverse

from simulations.models import SimulationRun

#: Bytes of randomness in a token: 128 bits, as many as a UUID4 has, written
#: in 22 URL-safe characters.
TOKEN_BYTES = 16


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class SharedReport(models.Model):
    """One run, shared."""

    run = models.OneToOneField(SimulationRun, on_delete=models.CASCADE, related_name="share")
    token = models.CharField(max_length=32, unique=True, default=new_token, editable=False)

    # --- the deck as the run played it, copied when it was shared ----------
    deck_name = models.CharField(max_length=120)
    commander = models.CharField(max_length=256, blank=True)
    #: [{"name": str, "quantity": int, "type": one of simulation.cards.CARD_TYPES
    #: or ""}], in the order the page lists them.
    cards = models.JSONField(default=list, blank=True)
    #: `DeckSummary.content` when it was written about exactly this deck, else
    #: empty; with the language it was written in.
    summary = models.JSONField(default=dict, blank=True)
    summary_language = models.CharField(max_length=8, blank=True)

    views = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.deck_name} ({self.token[:6]}…)"

    def get_absolute_url(self) -> str:
        return reverse("sharing:report", args=[self.token])

    @property
    def card_count(self) -> int:
        return sum(card["quantity"] for card in self.cards)
