"""Human judgements layered on top of the derived card data.

`cards.DerivedProfile` records what can be read off a card: its cost, its type,
its role tags, and - where a small set of regexes can manage it - how much mana
it adds. A great deal of how a card actually *plays* is not in that list:

* how early you want to cast it,
* whether it makes a one-land hand keepable,
* whether its draw happens when you cast it or every upkeep.

Those are judgements. Phase 1's deriver deliberately refuses to guess them, and
Phase 4 gives the user an editor for them. This model is where they live in the
meantime, and it is what lets the adapter rebuild the hand-annotated reference
deck exactly from the database.

**Three scopes, narrowest wins:**

===================  ==========================================================
`owner` and `deck`   both null - a built-in default, seeded from the fixture
`owner` set         the user's own preference, across all their decks
`owner` and `deck`  set - this deck only
===================  ==========================================================

Overrides are a JSON dict rather than thirty nullable columns. The engine's
`Card` gains fields as the simulation grows, and a schema migration per field
would make that expensive enough to discourage it. The keys are validated
against `ALLOWED_KEYS`, so a typo is an error rather than a silently ignored
setting.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from cards.models import OracleCard
from decks.models import Deck
from simulations import gaps

#: The mana a colour annotation may name. Spelled out here rather than imported
#: from `simulation.manacost`, because the engine stays a stranger to Django and
#: `simulations/engine/adapter.py` is the only module allowed to know both. Six
#: letters that have not changed since 1993 are a cheap thing to repeat.
MANA_SOURCES = frozenset({"W", "U", "B", "R", "G", "C"})

#: Every key an annotation may set, and what it means on the engine's `Card`.
#: Adding one here is the whole cost of exposing a new engine field to users.
ALLOWED_KEYS = {
    "kind": "Engine card kind, overriding the one derived from the type line.",
    "land_type": "Engine land subtype label (basic_swamp, coffers, urborg, ...).",
    "pips": "Coloured pips, either {'B': 2} or a bare number meaning black ones.",
    "generic": "Generic portion of the mana cost.",
    "priority": "How early to cast it. Higher goes first.",
    "accelerant": "Counts toward the mulligan rule's acceleration test.",
    "goldfish_castable": "False for cards with no legal target against no opponent.",
    "needs_creature_in_yard": "Reanimation: needs a creature in the graveyard.",
    "needs_creature_on_bf": "Needs one of your own creatures, usually to sacrifice.",
    "ritual_gain": "Mana added when cast, for rituals.",
    "ritual_color": "Which colour that ritual mana is. Defaults to the deck's.",
    "mana_produces": "What it taps for, by colour letter: {'B': 1}. {} means nothing.",
    "mana_black": "Black mana added when tapped. Superseded by mana_produces.",
    "mana_colorless": "Colourless mana added when tapped. Superseded by mana_produces.",
    "mana_activation": "Generic mana the mana ability costs beside tapping: 1 for a Signet.",
    "untaps": "False for a permanent that stays tapped once used, like Mana Vault.",
    "scaling_rule": "per_controlled | double_subtype | type_adding.",
    "scaling_subtype": "The land subtype the scaling rule counts.",
    "scaling_color": "Colour the scaling rule makes. Defaults to the subtype's.",
    "scaling_activation": "Generic mana the scaling ability costs to activate.",
    "cost_reduction": "Generic reduction this permanent gives your spells.",
    "draw_on_cast": "Cards drawn when the spell resolves.",
    "discard_on_cast": "Cards discarded right after that draw, like Faithless Looting.",
    "put_back_on_cast": "Cards put back on top of the library after that draw, like Brainstorm.",
    "life_on_cast": "Life paid when the spell resolves.",
    "upkeep_draw": "Cards drawn each upkeep.",
    "upkeep_life": "Life paid each upkeep.",
    "upkeep_life_per_mv": "Pay the drawn card's mana value instead of a fixed amount.",
    "end_step_max_hand": "Refill the hand to this size in the end step.",
    "end_step_life_floor": "Never pay life below this in the end step.",
    "skips_draw_step": "Necropotence: no normal draw.",
    "tutor_to_hand": "Search to hand rather than to the graveyard.",
    "tutor_count": "How many cards to search for.",
    "tutor_life": "Life paid to search.",
    "tutor_kind": "Restrict the search to this card kind.",
    "subtypes": "Land subtypes the card carries, e.g. ['swamp'].",
    "tags": "Role tags, overriding the ones rolled up from the community tags.",
    "enters_tapped": "Override the derived enters-tapped reading.",
}


class CardAnnotation(models.Model):
    """One user's opinion about how one card plays."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="card_annotations",
        help_text="Null means a built-in default that applies to everyone.",
    )
    deck = models.ForeignKey(
        Deck,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="card_annotations",
        help_text="Null means it applies to all of the owner's decks.",
    )
    oracle_card = models.ForeignKey(
        OracleCard, on_delete=models.CASCADE, related_name="annotations"
    )

    overrides = models.JSONField(default=dict, blank=True)
    note = models.TextField(blank=True, help_text="Why, for the provenance panel.")
    #: "Looks right": the user read what the engine made of this card and
    #: agrees (Phase 9 C2). Needed because many reading gaps - hybrid pips, a
    #: cost of X - have no field that could fix them, so the engine's warning
    #: stays after any save. A row carrying only this flag is kept. The engine
    #: never reads it.
    confirmed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "deck", "oracle_card"],
                name="uniq_card_annotation",
                # Postgres treats NULLs as distinct in a unique index, which
                # would allow unlimited duplicate built-in defaults.
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(deck__isnull=True) | models.Q(owner__isnull=False),
                name="deck_annotation_needs_owner",
            ),
        ]
        indexes = [models.Index(fields=["deck", "oracle_card"])]

    def __str__(self) -> str:
        return f"{self.oracle_card_id} ({self.scope})"

    def save(self, *args, **kwargs):
        # Validated on every save, not only in forms: this data drives a
        # simulation, and a typo'd key that is silently dropped produces a
        # number that is wrong with no error anywhere.
        self.clean()
        return super().save(*args, **kwargs)

    def clean(self):
        unknown = set(self.overrides) - set(ALLOWED_KEYS)
        if unknown:
            raise ValidationError(
                {"overrides": f"unknown keys: {sorted(unknown)}. "
                              f"Known keys: {sorted(ALLOWED_KEYS)}"}
            )
        self._clean_colors()

    def _clean_colors(self):
        """Reject a colour the engine would not recognise.

        Without this, `{"mana_produces": {"Black": 1}}` saves happily and then
        raises somewhere inside a Celery worker three screens away from the
        form that accepted it.
        """
        produces = self.overrides.get("mana_produces")
        if produces is not None:
            if not isinstance(produces, dict):
                raise ValidationError(
                    {"overrides": "mana_produces must be a mapping, e.g. {'B': 1}"}
                )
            for color, amount in produces.items():
                self._check_color("mana_produces", color)
                if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                    raise ValidationError(
                        {"overrides": f"mana_produces[{color!r}] must be a whole "
                                      f"number of mana, not {amount!r}"}
                    )

        for key in ("ritual_color", "scaling_color"):
            if self.overrides.get(key):
                self._check_color(key, self.overrides[key])

    @staticmethod
    def _check_color(key: str, color) -> None:
        if not isinstance(color, str) or color.upper() not in MANA_SOURCES:
            raise ValidationError(
                {"overrides": f"{key}: {color!r} is not a mana colour. "
                              f"Use one of {sorted(MANA_SOURCES)}."}
            )

    @property
    def scope(self) -> str:
        if self.deck_id:
            return "deck"
        return "user" if self.owner_id else "builtin"



class SimulationRun(models.Model):
    """One simulation of one deck, and everything known about how it went.

    **Progress lives here, not in the Celery result backend.** A row that says
    `games_done=34000` survives a worker restart, shows up in the admin, and
    can be read by the page the user is looking at without asking the broker
    anything. The result backend is used only for terminal state and for
    exceptions, which is what it is good at.

    The result is stored as the JSON form of `simulation.analysis` - histograms
    and counters, a few kB whatever the iteration count. Storing per-game
    values would make a 100,000 game run a multi-megabyte row for numbers
    nobody reads individually.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    #: Statuses from which nothing further will happen. The progress fragment
    #: stops polling on these, and the quota refund happens on the way in.
    TERMINAL = frozenset({Status.DONE, Status.FAILED, Status.CANCELLED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="runs")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="simulation_runs"
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)

    # --- what was asked for ------------------------------------------------
    games_total = models.PositiveIntegerField()
    #: Turns simulated. Spelled out rather than imported from
    #: `simulation.analysis.DEFAULT_TURNS` for the same reason as
    #: `MANA_SOURCES` above: the engine stays a stranger to Django, and the
    #: adapter is the only module allowed to know both.
    turns = models.PositiveSmallIntegerField(default=3)
    on_the_play = models.BooleanField(default=True)
    #: The seed the whole run derives from. Stored, so the run can be replayed
    #: exactly - see `simulation.analysis.chunk_seed` for what that guarantees.
    seed = models.BigIntegerField()

    # --- how far it has got ------------------------------------------------
    games_done = models.PositiveIntegerField(default=0)
    chunks_total = models.PositiveIntegerField(default=0)
    chunks_done = models.PositiveIntegerField(default=0)
    #: Cancellation is cooperative: this is a request, and each chunk checks it
    #: before starting. Never `revoke(terminate=True)` - a SIGKILL mid-chunk
    #: leaves dirty state and, after a worker restart, can kill another task.
    cancel_requested = models.BooleanField(default=False)

    # --- what came out -----------------------------------------------------
    result = models.JSONField(default=dict, blank=True)
    #: What the adapter could not model, stored with the run rather than
    #: recomputed: the honest reading of a result is the reading that was true
    #: when it was computed, not the one the deck would give today.
    gaps = models.JSONField(default=list, blank=True)
    cards_total = models.PositiveIntegerField(default=0)
    cards_with_gaps = models.PositiveIntegerField(default=0)
    #: The same deck counted by copies (phase 10): every card, and the copies
    #: of the ones the engine could not read. Zero on a run from before.
    copies_total = models.PositiveIntegerField(default=0)
    copies_unreadable = models.PositiveIntegerField(default=0)
    #: The deck as it was when this run happened. Stored rather than counted
    #: at render time, because the report compares the opening hands against
    #: the exact hypergeometric distribution - and that comparison is only
    #: honest against the library the simulation actually shuffled, not
    #: against whatever the deck contains by the time somebody reads it.
    library_size = models.PositiveIntegerField(default=0)
    lands_total = models.PositiveIntegerField(default=0)
    #: Which engine computed this. Written from the adapter's `Conversion`, so
    #: a stored result can later say "computed with v2, current is v5 - re-run
    #: to compare". Zero means it was never computed.
    engine_version = models.PositiveSmallIntegerField(default=0)
    #: The deck's `summary.fingerprint` when the run started (phase 11 F11):
    #: the deck page compares it with today's to say "The deck has changed
    #: since this run." Empty on a run from before, which then says nothing.
    deck_print = models.CharField(max_length=64, blank=True)

    #: How long one game actually took, measured by the first chunk. Used to
    #: size the chunks of the *next* run of this deck: a measurement on this
    #: hardware beats any estimate, and wall-clock arithmetic across parallel
    #: workers would only measure the concurrency.
    usec_per_game = models.FloatField(null=True, blank=True)

    task_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["deck", "-created_at"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.deck_id} {self.games_total} games ({self.status})"

    def get_absolute_url(self) -> str:
        return reverse("simulations:detail", args=[self.id])

    # --- progress ----------------------------------------------------------

    @property
    def is_finished(self) -> bool:
        return self.status in self.TERMINAL

    @property
    def progress_pct(self) -> int:
        """Whole percent, floored.

        Only a **completed** run reads 100. A cancelled or failed one reports
        how far it actually got, because that is the honest answer and because
        a full bar over the words "Cancelled - 5,000 of 10,000 games" is a
        screen that contradicts itself. Caught by looking at the screenshot;
        no test was going to notice.

        A run still in flight is capped at 99: a bar that sits full while the
        page keeps polling reads as a bug even when the arithmetic is right.
        """
        if self.status == self.Status.DONE:
            return 100
        if not self.games_total:
            return 0
        share = int(100 * self.games_done / self.games_total)
        return share if self.is_finished else min(99, share)

    # --- the score: how much of the deck the engine read ---------------------
    #
    # Classified out of the stored `gaps` by field name, which every row -
    # including every run finished before the split existed - already carries.
    # The engine still records the `judgement` gaps (a cast priority nobody
    # set), but since phase 9 C no page counts them: the default rule is an
    # answer nobody has to give. Nothing is back-filled and nothing rewritten.

    @property
    def reading_gaps(self) -> list:
        """The stored gaps the engine could not read. Shown as their own list."""
        return gaps.of_kind(self.gaps, gaps.READING)

    @property
    def cards_unreadable(self) -> int:
        """Distinct cards the engine could not read. Our own limit."""
        return len(gaps.cards_with(self.gaps, gaps.READING))

    @property
    def cards_read(self) -> int:
        """Distinct cards the engine read in full."""
        return max(0, self.cards_total - self.cards_unreadable)

    @property
    def readable_pct(self) -> float:
        """How much of the deck the engine read, as a percentage - of every copy
        on a run from phase 10 on, of distinct cards on an older one."""
        return 100.0 * gaps.share(self.coverage_total, self.coverage_unreadable)

    # --- coverage over every copy (phase 10 T5.9) ----------------------------
    #
    # Since phase 10 the coverage counts copies: thirty Swamps are thirty of a
    # hundred. A run or session from before has no copy counts (both zero) and
    # keeps its distinct-card pair - the two ways of counting are never mixed in
    # one sentence.

    @property
    def coverage_total(self) -> int:
        return self.copies_total or self.cards_total

    @property
    def coverage_unreadable(self) -> int:
        return self.copies_unreadable if self.copies_total else self.cards_unreadable

    @property
    def coverage_read(self) -> int:
        return max(0, self.coverage_total - self.coverage_unreadable)


class DeckSummary(models.Model):
    """The written part of a deck's summary (phase 10 H, T6.2), one per deck.

    Written by Mistral while a run plays, kept, and shown again until the deck
    changes: `fingerprint` is the cards, their counts, the commander and the
    annotations in the scope the engine merges (`simulations.summary.
    fingerprint`). A new prompt alone does not rewrite anything - that would
    charge people for our change - which is why `prompt_version` is recorded
    and not compared.

    `charged` is whether a run was taken from the owner's monthly allowance
    for it, so a failure gives back exactly what was taken, once.

    `language` is the language of whoever started it (phase 12 G, Q3), a key
    of `settings.LANGUAGE_NAMES`. It is not in the fingerprint: a language
    change alone would otherwise rewrite the summary and charge for it.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Being written")
        DONE = "done", _("Written")
        FAILED = "failed", _("Could not be written")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deck = models.OneToOneField(Deck, on_delete=models.CASCADE, related_name="summary")
    fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    #: The checked answer: {"feel": str, "strengths": [str], "weaknesses": [str],
    #: "tactics": str}. Empty until it is written.
    content = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=64, blank=True)
    prompt_version = models.PositiveSmallIntegerField(default=0)
    language = models.CharField(max_length=8, default="en")
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    charged = models.BooleanField(default=False)
    #: The day whose budget it was counted against (P1, `simulations.budget`),
    #: so a failure gives back that day's slot, not today's. None: written
    #: before the budget existed, or not counted.
    budget_day = models.DateField(null=True, blank=True)
    budget_guest = models.BooleanField(default=False)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Summary of {self.deck_id} ({self.status})"


class SummaryDay(models.Model):
    """How many deck summaries were started on one day (P1, load protection).

    One row a day, Europe/Zurich, counted up by a conditional update so that
    two runs started together cannot both take the last slot
    (`simulations.budget`). Guests and members are counted apart: guests may
    take only their share. Nothing about a person is in it.
    """

    day = models.DateField(unique=True)
    guests = models.PositiveIntegerField(default=0)
    members = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.day}: {self.guests} guest + {self.members} member summaries"


class UnreadCard(models.Model):
    """A card the engine could not read in a deck somebody uploaded (P19a).

    The operator's queue of what to teach the engine next
    (`simulations.unread`): which card, why, how often. **Nothing here points
    at a person** - no deck, no account, no address - like the P2 counters;
    the player still answers the card on their own deck as before, and never
    sees this.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        #: The engine reads it now - `read_since` says from which version.
        READ = "read", "Read now"
        #: Out of a goldfish's reach (it needs an opponent); stays a blind spot.
        WONT_FIX = "wont_fix", "Won't fix"

    oracle_card = models.OneToOneField(
        OracleCard, on_delete=models.CASCADE, related_name="unread", primary_key=True
    )
    #: What the reader said at the last sighting, in English as gaps are kept.
    reasons = models.JSONField(default=list, blank=True)
    #: Uploads it was in, counted - a refilled deck counts again.
    seen = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField()
    #: The engine that could not read it, at the last sighting.
    engine_version = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    read_since = models.PositiveSmallIntegerField(null=True, blank=True)
    #: When the weekly mail named it; None: not yet, or open again since.
    mailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-seen",)
        indexes = [models.Index(fields=["status", "mailed_at"])]

    def __str__(self) -> str:
        return f"{self.oracle_card.front_name} ({self.status}, seen {self.seen}x)"
