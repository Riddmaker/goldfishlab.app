"""Decks, their cards, and the record of how they got here.

The import is modelled as a first-class object rather than a transient action,
because the interesting question after an import is not "did it work" but
"which rows did you have to guess at, and how". `DeckImport` and
`UnresolvedRow` are what the review screen and the Phase 4 honesty panel read.

Rule that shapes the schema: **a row is never silently dropped.** Every line of
the uploaded file ends up either as a `DeckCard` or as an `UnresolvedRow` with
a reason. The counts must add up, and a test asserts that they do.
"""

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from cards.models import OracleCard


class Deck(models.Model):
    """One deck belonging to one user."""

    class Format(models.TextChoices):
        COMMANDER = "commander", "Commander"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="decks"
    )
    name = models.CharField(max_length=120)
    format = models.CharField(max_length=16, choices=Format.choices, default=Format.COMMANDER)
    commander = models.ForeignKey(
        OracleCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commands_decks",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["owner", "-updated_at"])]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("decks:detail", args=[self.id])

    # --- composition ------------------------------------------------------

    @property
    def card_count(self) -> int:
        """Cards in the 99. The commander sits in the command zone, not here."""
        return sum(entry.quantity for entry in self.entries.all())

    @property
    def total_with_commander(self) -> int:
        return self.card_count + (1 if self.commander_id else 0)


class DeckCard(models.Model):
    """A card in a deck, with a quantity.

    Quantity exists even in singleton formats: basic lands are unlimited, and
    31 Swamps as 31 rows would make every deck page a thousand-row table.
    """

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="entries")
    oracle_card = models.ForeignKey(OracleCard, on_delete=models.PROTECT, related_name="in_decks")
    quantity = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["deck", "oracle_card"], name="uniq_deck_card"),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="deck_card_qty"),
        ]
        ordering = ["oracle_card__cmc", "oracle_card__name"]

    def __str__(self) -> str:
        return f"{self.quantity}x {self.oracle_card_id}"


class DeckImport(models.Model):
    """One upload, with its tally.

    Kept after the fact on purpose: "this deck was imported from an Archidekt
    export in which four rows had to be matched by name" is exactly the kind of
    provenance the honesty layer is built to surface.
    """

    class Status(models.TextChoices):
        REVIEW = "review", "Awaiting review"
        APPLIED = "applied", "Applied"
        ABANDONED = "abandoned", "Abandoned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deck = models.ForeignKey(
        Deck, on_delete=models.CASCADE, related_name="imports", null=True, blank=True
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deck_imports"
    )
    filename = models.CharField(max_length=255, blank=True)
    parser = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REVIEW)

    rows_total = models.PositiveIntegerField(default=0)
    rows_resolved = models.PositiveIntegerField(default=0)
    rows_unresolved = models.PositiveIntegerField(default=0)
    # How many rows landed on each rung of the ladder: {"scryfall_id": 62, ...}.
    # The shape of this dict is the honest answer to "how exact was this import".
    rung_counts = models.JSONField(default=dict, blank=True)
    # `[["quantity", "Count"], ["card name", "Name"], ...]` - which column of the
    # uploaded file was read as what. The counterpart to the mapping screen: a
    # file whose headers answer for themselves is imported without interrupting
    # anybody, so this is where that reading becomes visible instead of never.
    column_mapping = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.filename or 'import'} ({self.rows_resolved}/{self.rows_total})"

    @property
    def is_clean(self) -> bool:
        return self.rows_unresolved == 0


class UnresolvedRow(models.Model):
    """A line the importer could not turn into a card.

    It exists so that nothing is dropped quietly. The user sees each one and
    decides; the alternative - importing 95 of 100 cards and rendering a
    confident mana curve - produces a deck page that is wrong without saying so.
    """

    deck_import = models.ForeignKey(
        DeckImport, on_delete=models.CASCADE, related_name="unresolved"
    )
    line_number = models.PositiveIntegerField()
    raw_name = models.CharField(max_length=256)
    quantity = models.PositiveSmallIntegerField(default=1)
    reason = models.CharField(max_length=200)
    # Best-effort near matches, for the review screen's "did you mean" list.
    suggestions = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["line_number"]

    def __str__(self) -> str:
        return f"line {self.line_number}: {self.raw_name}"


class PendingImport(models.Model):
    """An upload waiting for somebody to confirm what its columns mean.

    A file that needs the mapping screen has to survive one round trip, and
    every cheaper way of doing that was worse. A browser cannot re-populate a
    file input, so asking for the file again means asking the person to find it
    again. Posting the text back in a hidden field doubles a megabyte of
    payload and puts the thing being validated inside the thing doing the
    validating. The session would work and would put a megabyte of somebody's
    CSV in a cookie-keyed store with no way to look at it.

    So it is a row: inspectable, deletable, and bounded at **one per person per
    kind** by the unique constraint. A second upload replaces the first rather
    than accumulating, and a completed import deletes its own.

    Nothing here is a decision about the person's data. It is their own file,
    on its way to becoming deck rows a few seconds later.
    """

    class Kind(models.TextChoices):
        DECK = "deck", "Deck"
        COLLECTION = "collection", "Collection"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pending_imports"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    filename = models.CharField(max_length=255, blank=True)
    #: The decoded file. Decoding happens before this row exists, so whatever
    #: is here has already passed the size limit, the NUL-byte check and the
    #: encoding ladder - the three things `decks.services.decode` is for.
    text = models.TextField()
    parser = models.CharField(max_length=32, blank=True)
    #: Only for a deck import: the name the person typed, and the deck they
    #: were refilling. Both meaningless for a collection, which has exactly one
    #: per user and no name.
    deck_name = models.CharField(max_length=120, blank=True)
    deck = models.ForeignKey(
        Deck, on_delete=models.CASCADE, related_name="pending_imports", null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "kind"], name="one_pending_import_per_kind")
        ]

    def __str__(self) -> str:
        return f"{self.filename or 'upload'} awaiting column mapping"

    def get_absolute_url(self) -> str:
        return reverse("decks:map", args=[self.id])
