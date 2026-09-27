"""What a person actually owns, and what it is missing for a deck.

One `Collection` per user, holding one `CollectionItem` per **printing** they
own. Printings rather than cards, because that is what a collection export
contains and because throwing the detail away at import time would make it
unrecoverable later - a second import cannot invent the set code the first one
discarded.

**The printing is now resolved as well as recorded**, which it was not when
this app shipped. `cards.Printing` holds all 112,581 of them, so an item
carries a real foreign key to the printing a person owns, and the collection
page can say which Swamp and what Cardmarket last thought it was worth.

The raw strings stay alongside it and are not deleted. `set_code`,
`collector_number`, `scryfall_id` and `finish` remain exactly as the export
wrote them, because **`printing` is nullable and always will be**: a plain text
list names no printing, an export can name one Scryfall has retired, and an
installation may never have ingested `default_cards` at all. Throwing the
strings away would make that unrecoverable, and a later ingest could not invent
what the first import discarded.

So there are two questions and two answers. `oracle_card` - resolved through
the same ladder a deck import uses - is what every *count* is computed on, and
is never null. `printing` is what *prices* and finishes are read from, and a
null there means "nobody knows", which the page says rather than hides.
"""

from django.conf import settings
from django.db import models
from django.urls import reverse

from cards.models import OracleCard, Printing


class Collection(models.Model):
    """Everything one person owns. Exactly one per user.

    A one-to-one rather than a named list of collections: a person has one
    box of cards, and "which of my three collections is this?" is a question
    nobody asked. It can become a ForeignKey the day somebody does.
    """

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="collection"
    )
    #: What the last import was called, so the page can say where this came from.
    source_filename = models.CharField(max_length=255, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    #: Which column of that file was read as what: `[["quantity", "Count"], ...]`.
    #: Same provenance the deck importer records, for the same reason - an
    #: import that did not need to ask anybody still has to be able to say what
    #: it decided. See `decks.services.describe`.
    column_mapping = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.owner}'s collection"

    def get_absolute_url(self) -> str:
        return reverse("collection:detail")

    @property
    def distinct_cards(self) -> int:
        return self.items.values("oracle_card").distinct().count()

    @property
    def total_cards(self) -> int:
        from django.db.models import Sum

        return self.items.aggregate(total=Sum("quantity"))["total"] or 0


class CollectionItem(models.Model):
    """One printing owned, in some quantity.

    A collection export repeats a card once per printing, so four Swamps from
    four sets are four rows. They are kept apart rather than summed, because
    summing at import would be a decision this application cannot undo - and
    `owned_counts()` sums them at read time, where the caller can see it happen.
    """

    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="items"
    )
    oracle_card = models.ForeignKey(
        OracleCard, on_delete=models.CASCADE, related_name="collection_items"
    )
    quantity = models.PositiveIntegerField(default=1)

    #: The printing, when it could be resolved. Null is ordinary - see the
    #: module docstring - and `SET_NULL` rather than `CASCADE` because a
    #: printing disappearing upstream must never delete somebody's cards.
    printing = models.ForeignKey(
        Printing,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collection_items",
    )

    # --- the printing as the export wrote it, kept even when resolved ---
    # These are the raw strings and they are never overwritten from `printing`.
    # They are what a re-resolution runs against after a later ingest, and what
    # the review screen shows when nothing matched.
    scryfall_id = models.CharField(max_length=64, blank=True)
    set_code = models.CharField(max_length=16, blank=True)
    collector_number = models.CharField(max_length=16, blank=True)
    finish = models.CharField(max_length=32, blank=True)
    condition = models.CharField(max_length=32, blank=True)
    language = models.CharField(max_length=16, blank=True)

    class Meta:
        indexes = [models.Index(fields=["collection", "oracle_card"])]
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "oracle_card", "set_code",
                        "collector_number", "finish"],
                name="uniq_collection_printing",
            )
        ]

    def __str__(self) -> str:
        return f"{self.quantity}x {self.oracle_card_id} ({self.set_code})"

    @property
    def unit_price(self):
        """Cardmarket's trend price for one copy, in EUR, or None.

        None whenever the printing was never resolved, or the printing has no
        EUR price for this finish. Both are common and neither is an error, so
        this returns None and the template prints a dash - it never falls back
        to another printing's price, which would put a number next to a card
        that number was not about.
        """
        if self.printing is None:
            return None
        return self.printing.price_for(self.finish)

    @property
    def total_price(self):
        """The row's price times what is on it, or None when there is no price."""
        unit = self.unit_price
        return None if unit is None else unit * self.quantity
