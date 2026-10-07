"""A Commander precon and the deck the site simulates for it (P11).

The decklists come from MTGJSON (`datapages.mtgjson`), or as text when a deck
is out before MTGJSON lists it. Each one becomes a deck of the system account
(`datapages.lab`), is simulated, and its report is frozen the way a shared
report is (`sharing.SharedReport`), so the page shows exactly the list that
was played. A list that changes upstream is simulated again; the page always
shows the newest finished run.

**A deck with a card the catalogue does not know is held, not published.**
Its names wait in `unmatched` and the next refresh tries again: a new set
often reaches MTGJSON a few days before Scryfall's bulk files.
"""

from django.db import models
from django.urls import reverse

from decks.models import Deck


class Precon(models.Model):
    """One Commander precon."""

    class Source(models.TextChoices):
        MTGJSON = "mtgjson", "MTGJSON"
        TEXT = "text", "Text list"

    #: The page's address: the deck's name and its set code.
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    set_code = models.CharField(max_length=8)
    set_name = models.CharField(max_length=120, blank=True)
    released = models.DateField()
    source = models.CharField(max_length=8, choices=Source.choices)
    #: MTGJSON's file name for the deck; empty for a text list.
    source_key = models.CharField(max_length=160, blank=True)
    #: `datapages.services.list_print` of the list as last imported, so an
    #: unchanged list is not simulated again.
    list_print = models.CharField(max_length=64, blank=True)
    #: Empty until every card of the list was found in the catalogue.
    deck = models.OneToOneField(Deck, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="precon")
    #: The card names the catalogue did not know at the last import.
    unmatched = models.JSONField(default=list, blank=True)
    #: The admin's brake: off, the page answers 404 and leaves the lists.
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-released", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.set_code})"

    def get_absolute_url(self) -> str:
        return reverse("datapages:precon", args=[self.slug])

