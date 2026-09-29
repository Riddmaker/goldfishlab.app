"""Combos, and which deck has which.

**There is no bulk ingest behind this.** `combos/spellbook.py` explains the
measurement that killed it: `variants.json` is 656.8 MB with no gzip, and 70%
of every record is Scryfall image URLs for cards whose images this database
already holds. So `Combo` is a mirror that **accumulates from real demand** -
every deck somebody looks up leaves its combos behind, deduplicated by
`spellbook_id`, and the table grows towards the combos this application's users
actually play rather than towards all 138,000 of them.

Three things the schema is built to keep honest:

**A combo is named cards plus templates.** `Mikaeus, the Unhallowed` +
`Carrion Feeder` also needs *a creature with persist*, which is a Scryfall
search and not a card anybody can name. `ComboTemplate` exists so that
requirement is visible; dropping it as detail would let the page say "your deck
contains this combo" about a deck that cannot run it.

**A card in a combo may not be in our catalogue.** `ComboCard.oracle_card` is
nullable and the name is kept beside it, because a later ingest cannot invent
what the first lookup discarded.

**An answer is as old as the lookup that fetched it.** `ComboLookup.fetched_at`
is shown wherever combos are, exactly as `prices_updated_at` is shown wherever
prices are. A cached answer that does not say when it was true is a claim about
the present that nobody checked.
"""

import uuid

from django.db import models

from cards.models import OracleCard
from decks.models import Deck


class Combo(models.Model):
    """One Commander Spellbook variant, as much of it as is worth keeping."""

    #: Spellbook's own id, e.g. `628-2438--5`. Natural key: the same combo is
    #: the same row for every deck that contains it.
    spellbook_id = models.CharField(max_length=32, primary_key=True)

    identity = models.CharField(max_length=8, blank=True)
    status = models.CharField(max_length=16, blank=True)
    #: Spellbook's bracket classification, stored raw and uninterpreted. It is
    #: their vocabulary; guessing at what "O" means here and being wrong would
    #: put a made-up word on a deck page.
    bracket_tag = models.CharField(max_length=8, blank=True)

    mana_needed = models.CharField(max_length=64, blank=True)
    mana_value_needed = models.PositiveSmallIntegerField(default=0)
    #: Conditions a reader has to satisfy that are not cards - "there are at
    #: least [X] colors among permanents you control". Kept because a combo
    #: with a prerequisite is a weaker claim than one without.
    notable_prerequisites = models.TextField(blank=True)
    description = models.TextField(blank=True)

    #: How many decks Spellbook has seen running it. The only sane sort order
    #: for a list of 76, and not a quality judgement of ours.
    popularity = models.PositiveIntegerField(default=0)
    legal_commander = models.BooleanField(default=True)

    #: Feature names: "Infinite lifeloss", "Infinite creature ETB". A list of
    #: strings rather than a table, because nothing joins on them - they are
    #: read out to a person and never queried.
    produces = models.JSONField(default=list, blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-popularity", "spellbook_id"]

    def __str__(self) -> str:
        return f"{self.spellbook_id} ({self.card_names})"

    @property
    def card_names(self) -> str:
        return " + ".join(card.name for card in self.cards.all())

    @property
    def needs_templates(self) -> bool:
        return self.templates.exists()


class ComboCard(models.Model):
    """One named card a combo uses."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE, related_name="cards")
    #: Null whenever Spellbook names a card this catalogue does not have - a
    #: new set, a card the oracle ingest dropped, or a lookup run before the
    #: catalogue was ingested at all. Not a failure; the name below still says
    #: what it is.
    oracle_card = models.ForeignKey(
        OracleCard, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="in_combos",
    )
    oracle_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=256)
    quantity = models.PositiveSmallIntegerField(default=1)
    must_be_commander = models.BooleanField(default=False)
    #: Spellbook's zone letters, raw: B battlefield, G graveyard, H hand, and
    #: so on. Stored for phase 7 §2, which has to know whether a card must be
    #: on the battlefield to count as assembled.
    zones = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["oracle_card"])]

    def __str__(self) -> str:
        return self.name


class ComboTemplate(models.Model):
    """A category of card a combo needs, rather than a named one.

    The reason this table exists rather than being folded into a text field:
    a combo with an unmet template is one a deck does **not** have, and that
    distinction has to survive into the page.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE, related_name="templates")
    name = models.CharField(max_length=128)
    quantity = models.PositiveSmallIntegerField(default=1)
    #: The Scryfall search that defines the category. Kept so a later phase can
    #: resolve it against a deck; nothing does yet, and the page says the
    #: requirement in words instead.
    scryfall_query = models.CharField(max_length=512, blank=True)
    zones = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ComboLookup(models.Model):
    """What one deck's last lookup found, and when.

    One per deck. `fingerprint` is what makes staleness a fact rather than a
    guess: it is computed from the deck's own contents, so a deck that has not
    changed since the lookup is a deck whose combos have not changed either -
    unless Spellbook has published new ones, which is what `MAX_AGE` covers.
    """

    class Status(models.TextChoices):
        OK = "ok", "Fetched"
        FAILED = "failed", "Could not be fetched"

    deck = models.OneToOneField(Deck, on_delete=models.CASCADE, related_name="combo_lookup")
    #: When Spellbook last ANSWERED. Only a successful lookup moves it: it is
    #: the date printed beside the combos, and a failed attempt that re-dated
    #: the old answer (as this field did when it was `auto_now`, until the
    #: 2026-09-25 review) made a months-old list read as checked today. Null
    #: means no lookup has ever succeeded.
    fetched_at = models.DateTimeField(null=True, blank=True)
    #: When anybody last asked, whether or not it worked. The cooldown counts
    #: from this, so a failing Spellbook is not asked again every second.
    attempted_at = models.DateTimeField(auto_now=True)
    #: A hash of the deck's cards and commander. Different means the answer
    #: below is about a deck that no longer exists.
    fingerprint = models.CharField(max_length=64, blank=True)
    identity = models.CharField(max_length=8, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    #: Why it failed, in words a person can read. Kept rather than swallowed:
    #: "Spellbook did not answer" and "this deck has no combos" look identical
    #: on a page that only counts rows.
    message = models.CharField(max_length=256, blank=True)

    #: Counts for the two categories deliberately **not** stored as rows. For
    #: the reference deck, `out_of_identity` is 112 combos that would work with
    #: a different commander - a number worth saying and a list worth refusing.
    out_of_identity = models.PositiveIntegerField(default=0)
    needs_other_commanders = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        when = f"{self.fetched_at:%Y-%m-%d}" if self.fetched_at else "never"
        return f"combos for {self.deck_id} at {when}"

    @property
    def ok(self) -> bool:
        return self.status == self.Status.OK


class DeckCombo(models.Model):
    """One combo, and how this deck stands in relation to it."""

    class Kind(models.TextChoices):
        INCLUDED = "included", "In the deck"
        ONE_AWAY = "one_away", "One card away"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lookup = models.ForeignKey(ComboLookup, on_delete=models.CASCADE, related_name="entries")
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE, related_name="in_decks")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    #: Which of the combo's cards the deck has not got. Computed here rather
    #: than asked of Spellbook, because the answer has to agree with *our*
    #: record of the deck - and a shopping list that disagrees with the deck
    #: page is worse than no shopping list.
    missing = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["kind", "-combo__popularity"]
        constraints = [
            models.UniqueConstraint(fields=["lookup", "combo"], name="one_row_per_combo_per_deck")
        ]

    def __str__(self) -> str:
        return f"{self.combo_id} ({self.get_kind_display()})"


class ComboMeasurement(models.Model):
    """How long one combo took to come together, measured by the simulator.

    This is the number no competitor can write. Everyone can say a deck
    contains a combo; only a simulation can say it assembles by turn six in
    four percent of games - and only an honest one can say what it had to
    assume to get there.

    Three fields carry the assumptions, and all three are printed:

    **`added_card`** is the card that was *put into the deck* to make the
    measurement possible. A combo the deck is one card away from cannot be
    measured in the deck's own games: the card that completes it is not in the
    library to be drawn. So the simulated deck is the real one **plus** that
    card - one card larger, never one card swapped, because choosing which card
    somebody's deck can spare is not a judgement this application makes.

    **`games`** is the size of the sample this particular number came from, and
    it is smaller than the run for a hypothetical. A percentage without its
    denominator is a claim nobody can weigh.

    **`turns`** is how far the games were played. "By turn six in 4%" says
    nothing at all without it, and a run of three turns cannot answer a
    question about six.

    A combo with an unmet template never gets a row here at all. "Any creature
    with persist" is a Scryfall search, and until something runs it the honest
    output is the requirement in words with no percentage beside it - the same
    rule `produced_mana` follows when it sets `needs_review` rather than
    guessing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: A string reference rather than an import: `simulations` already knows
    #: about `combos` through the measurement pass, and two apps importing each
    #: other's models at module level is how a working migration graph stops
    #: loading.
    run = models.ForeignKey(
        "simulations.SimulationRun", on_delete=models.CASCADE,
        related_name="combo_measurements",
    )
    #: Denormalised off the run so the deck page can find the latest
    #: measurements without walking every run the deck has ever had.
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE,
                             related_name="combo_measurements")
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE,
                              related_name="measurements")
    kind = models.CharField(max_length=16, choices=DeckCombo.Kind.choices)

    added_card = models.ForeignKey(
        OracleCard, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    #: Kept beside the foreign key for the same reason `ComboCard.name` is: a
    #: number printed with "add Phyrexian Altar" beside it has to keep saying
    #: so even if the catalogue row moves.
    added_name = models.CharField(max_length=256, blank=True)

    games = models.PositiveIntegerField()
    turns = models.PositiveSmallIntegerField()
    #: Cumulative: `by_turn[i]` is how many games had it together by the end of
    #: turn `i + 1`. Cumulative rather than per-turn because that is the
    #: question a player asks, and because it merges across chunks by addition.
    by_turn = models.JSONField(default=list, blank=True)

    measured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "-combo__popularity"]
        constraints = [
            models.UniqueConstraint(fields=["run", "combo"],
                                    name="one_measurement_per_combo_per_run")
        ]
        indexes = [models.Index(fields=["deck", "-measured_at"])]

    def __str__(self) -> str:
        return f"{self.combo_id} in {self.deck_id}: {self.share:.0f}% by turn {self.turns}"

    @property
    def hypothetical(self) -> bool:
        """Whether a card had to be added to the deck to measure this."""
        return bool(self.added_name)

    @property
    def assembled(self) -> int:
        """Games in which it came together at all, within the turns played."""
        return int(self.by_turn[-1]) if self.by_turn else 0

    @property
    def share(self) -> float:
        """Percent of games it was together by the last turn simulated."""
        return 100.0 * self.assembled / self.games if self.games else 0.0

    @property
    def share_label(self) -> str:
        """The share as a page should print it, so two pages cannot differ.

        `0%` is a lie beside a combo that did come together. The reference deck
        assembles Gravecrawler + Phyrexian Altar in 0.6% of games by turn six -
        real, measured, and rounded to a whole percent it reads as never. Below
        a point the honest rendering is the bound rather than the rounding, and
        a first decimal place would be sampling noise at these sample sizes
        anyway.
        """
        if self.never:
            return "never"
        if self.share < 1:
            return "under 1%"
        return f"{self.share:.0f}%"

    @property
    def never(self) -> bool:
        """A real answer, and a common one. Not a missing measurement."""
        return not self.assembled

    @property
    def median_turn(self) -> int:
        """Of the games where it assembled, the turn half of them had it by.

        Zero when it never assembled. Reported beside the share because the two
        say different things: a combo that comes together in 4% of games by
        turn four is a different card to draw than one that gets there on turn
        six.
        """
        half = self.assembled / 2
        for index, count in enumerate(self.by_turn, start=1):
            if count >= half and count:
                return index
        return 0

    @property
    def curve(self) -> list[dict]:
        """The share by the end of each turn, for a small table."""
        return [
            {"turn": index, "percent": 100.0 * count / self.games if self.games else 0.0}
            for index, count in enumerate(self.by_turn, start=1)
        ]
