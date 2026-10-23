"""Decks, their cards, and the record of how they got here.

The import is modelled as a first-class object rather than a transient action,
because the interesting question after an import is not "did it work" but
"which rows did you have to guess at, and how". `DeckImport` and
`UnresolvedRow` are what the review screen and the Phase 4 honesty panel read.

Rule that shapes the schema: **a row is never silently dropped.** Every line of
the uploaded file ends up either as a `DeckCard` or as an `UnresolvedRow` with
a reason. The counts must add up, and a test asserts that they do. A sideboard
or maybeboard row is the one kind that is left out on purpose, and it is
counted on `DeckImport.left_out` and said on the page (P20 F1).
"""

import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext, gettext_noop, ngettext

from cards.models import OracleCard

#: Commander is a 100-card format, commanders included.
COMMANDER_DECK_SIZE = 100


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
    #: The second commander, when two share the command zone - two partners,
    #: a creature and its Background, a companion and the Doctor (P20 F2,
    #: `cards.commanders.pair_ok`). Never set without `commander`.
    partner = models.ForeignKey(
        OracleCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="partners_decks",
    )
    notes = models.TextField(blank=True)
    #: The printings the file named for the card(s) in the command zone, which
    #: are not `DeckCard`s: `{oracle id: [printing, ...]}` in the shape of
    #: `DeckCard.printings`. Display only (P20 F1), the art included.
    commander_printings = models.JSONField(default=dict, blank=True)
    #: How many cards the engine could not read and the owner has not answered
    #: yet - the red marker (Phase 9 C2). `None` means "not counted since the
    #: last thing that could change it", and the page that needs it counts
    #: then. Written with `QuerySet.update`, never `save()`: a count is not an
    #: edit, and `updated_at` orders the deck list.
    open_questions = models.PositiveSmallIntegerField(null=True, blank=True)
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
    def commanders(self) -> list[OracleCard]:
        """The command zone, first commander first: none, one or two cards."""
        return [card for card in (self.commander, self.partner) if card is not None]

    @property
    def commander_count(self) -> int:
        return (1 if self.commander_id else 0) + (1 if self.partner_id else 0)

    @property
    def total_with_commander(self) -> int:
        return self.card_count + self.commander_count

    @property
    def is_commander_deck(self) -> bool:
        """A commander, or the 100 cards a Commander deck has (P20 F2, E1.81).

        Goldfish Lab only plays by the Commander rules. A 75-card list without
        a commander still runs, and the deck page and the report say whose
        rules the numbers are under.
        """
        return bool(self.commander_id) or self.total_with_commander == COMMANDER_DECK_SIZE

    def printing_images(self) -> dict[str, str]:
        """`{oracle id: the art of the first printing the file named}`, for
        the cards whose printing art was found (`decks.printing_art`). A card
        missing here shows the catalogue's own picture."""
        images = {}
        for oracle_id, printings in (self.commander_printings or {}).items():
            if image := first_image(printings):
                images[oracle_id] = image
        for entry in self.entries.all():
            if image := first_image(entry.printings):
                images.setdefault(str(entry.oracle_card_id), image)
        return images


class DeckCard(models.Model):
    """A card in a deck, with a quantity.

    Quantity exists even in singleton formats: basic lands are unlimited, and
    31 Swamps as 31 rows would make every deck page a thousand-row table.
    """

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="entries")
    oracle_card = models.ForeignKey(OracleCard, on_delete=models.PROTECT, related_name="in_decks")
    quantity = models.PositiveSmallIntegerField(default=1)
    #: The printings the imported file named for this card, as
    #: `[{"set": "cmr", "number": "189", "finish": "foil", "quantity": 1}]`,
    #: plus `"id"` when the file gave a Scryfall printing id, `"image"`
    #: once that printing's art has been looked up (`decks.printing_art`) and
    #: `"art": "missing"` while that lookup failed because Scryfall did not
    #: answer.
    #: The catalogue holds one printing per card, so this is what the file
    #: said, shown beside the card and never read by the engine: a printing
    #: does not change the rules (P20 F1, decision E1). Empty for a card
    #: added by hand and for every import before F1.
    printings = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["deck", "oracle_card"], name="uniq_deck_card"),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="deck_card_qty"),
        ]
        ordering = ["oracle_card__cmc", "oracle_card__name"]

    def __str__(self) -> str:
        return f"{self.quantity}x {self.oracle_card_id}"

    @property
    def printing_label(self) -> str:
        return printing_label(self.printings)

    @property
    def printings_shown(self) -> "Printings":
        return printings_shown(self.printings)


#: How a finish reads beside a card, in English (translated where shown).
FINISH_LABELS = {"foil": gettext_noop("Foil"), "etched": gettext_noop("Etched foil")}

#: More printings than this for one card are summarised, not listed.
PRINTINGS_SHOWN = 3

#: The only place a printing's art may come from: Scryfall's image host, the
#: one the Content-Security-Policy lets the page load pictures from.
ART_HOST = "https://cards.scryfall.io/"


#: A printing whose art lookup failed - Scryfall did not answer - and can be
#: tried again. A printing Scryfall does not know is not marked: what the file
#: meant cannot be guessed, so it keeps the catalogue's picture quietly.
ART_MISSING = "missing"


def art_missing(printings) -> bool:
    """Whether the art of these printings could not be fetched and may be
    asked for again."""
    printings = printings or []
    return not first_image(printings) and any(
        printing.get("art") == ART_MISSING for printing in printings)


def first_image(printings) -> str:
    """The art of the first printing that has some, or `""`."""
    for printing in printings or []:
        image = printing.get("image") or ""
        if isinstance(image, str) and image.startswith(ART_HOST):
            return image
    return ""


@dataclass(frozen=True)
class Printings:
    """What a file said about one card's printings, ready to show.

    `codes` pairs a set and number (`CMR 189`, language-free, so a page marks
    it as such) with its finish in the page's language; `more` counts the
    printings past `PRINTINGS_SHOWN`; `image` is the first printing's art,
    once it has been looked up; `missing` says that lookup failed and may be
    tried again.
    """

    codes: tuple[tuple[str, str], ...] = ()
    more: int = 0
    image: str = ""
    missing: bool = False

    def __bool__(self) -> bool:
        return bool(self.codes)

    def __str__(self) -> str:
        parts = [" · ".join(part for part in pair if part) for pair in self.codes]
        if self.more:
            parts.append(ngettext("%(count)s more", "%(count)s more", self.more)
                         % {"count": self.more})
        return ", ".join(parts)


def printings_shown(printings) -> Printings:
    """One entry per distinct printing the file named, for display.

    Printings without a set and without a finish say nothing worth showing.
    """
    codes = []
    for printing in printings or []:
        code = " ".join(filter(None, (printing.get("set", "").upper(),
                                      printing.get("number", ""))))
        finish = FINISH_LABELS.get(printing.get("finish", ""))
        pair = (code, gettext(finish) if finish else "")
        if any(pair) and pair not in codes:
            codes.append(pair)
    return Printings(tuple(codes[:PRINTINGS_SHOWN]), max(0, len(codes) - PRINTINGS_SHOWN),
                     first_image(printings), art_missing(printings))


def printing_label(printings) -> str:
    """`CMR 189 · Foil, SLD 1011` as plain text."""
    return str(printings_shown(printings))


class CommanderIssue(models.TextChoices):
    """Why an import left the command zone empty (`DeckImport.commander_issue`)."""

    UNMARKED = "unmarked", "The file names no commander"
    NOT_FOUND = "not_found", "The named commander was not found"
    NOT_ALLOWED = "not_allowed", "The named card cannot be a commander"
    NOT_PAIR = "not_pair", "The named cards cannot share the command zone"


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
    # `{"side": 15, "maybe": 4}` - cards the file listed outside the deck, by
    # board (`decks.importers.boards`), which the import leaves out on purpose
    # and says so. Not rows: a left-out card is not an unresolved one.
    left_out = models.JSONField(default=dict, blank=True)
    #: Why this import set no commander, when it set none (P20 F2): the file
    #: named none, named a card that was not found, named one that cannot lead
    #: a deck, or named two that cannot share the command zone.
    commander_issue = models.CharField(max_length=16, choices=CommanderIssue.choices,
                                       blank=True)
    #: The names the file marked as commander, as written, for that sentence.
    commander_named = models.JSONField(default=list, blank=True)
    #: Oracle ids the file's own order suggests as the commander(s) - the
    #: first lines, or a last block of one or two (decision E4). A suggestion
    #: the deck page fills in, never one it saves without a click.
    suggested_commanders = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.filename or 'import'} ({self.rows_resolved}/{self.rows_total})"

    @property
    def is_clean(self) -> bool:
        return self.rows_unresolved == 0

    @property
    def parser_label(self) -> str:
        """The format the file was read as, in the page's language."""
        from decks import importers

        parser = importers.by_name(self.parser) if self.parser else None
        return gettext(parser.label) if parser is not None else self.parser

    @property
    def columns_shown(self) -> list[tuple[str, str]]:
        """`column_mapping` with each label in the page's language."""
        return [(gettext(label), header) for label, header in self.column_mapping]

    @property
    def commander_issue_shown(self) -> str:
        """Why the import set no commander, as a sentence in the page's language.

        Three different things used to read "the file did not mark one" - also
        when it had named two partners (Lauf 1, E1.54).
        """
        names = " + ".join(self.commander_named or [])
        sentences = {
            CommanderIssue.UNMARKED: gettext("The file does not name a commander."),
            CommanderIssue.NOT_FOUND: gettext(
                "The file names %(names)s as commander, but that card was not found."),
            CommanderIssue.NOT_ALLOWED: gettext(
                "The file names %(names)s as commander, but that card cannot lead a "
                "Commander deck."),
            CommanderIssue.NOT_PAIR: gettext(
                "The file names %(names)s as commanders, but they cannot share the command "
                "zone."),
        }
        sentence = sentences.get(self.commander_issue, "")
        return sentence % {"names": names} if "%(names)s" in sentence else sentence

    @property
    def left_out_shown(self) -> list[str]:
        """One sentence per board the import left out, in the page's language."""
        sentences = {
            "side": lambda n: ngettext(
                "%(count)s sideboard card was not imported.",
                "%(count)s sideboard cards were not imported.", n),
            "maybe": lambda n: ngettext(
                "%(count)s maybeboard card was not imported.",
                "%(count)s maybeboard cards were not imported.", n),
            "companion": lambda n: ngettext(
                "%(count)s companion was not imported.",
                "%(count)s companions were not imported.", n),
            "tokens": lambda n: ngettext(
                "%(count)s token was not imported.",
                "%(count)s tokens were not imported.", n),
        }
        return [sentences[board](count) % {"count": count}
                for board, count in (self.left_out or {}).items()
                if board in sentences and count]


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

    #: One kind since the collection went (phase 9 B). The column and the
    #: constraint stay: dropping them buys nothing, and a second kind of
    #: upload would need them back.
    class Kind(models.TextChoices):
        DECK = "deck", "Deck"

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
    #: The name the person typed, and the deck they were refilling.
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
