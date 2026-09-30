"""A playtest: a seeded shuffle, and every action taken since.

The state of a game is **not** stored. What is stored is the deck the session
started with, the seed, and an ordered list of actions; the current state is
that shuffle replayed through them. Replaying ~60 pure-Python mutations takes
well under a millisecond, and `cached_state` makes the common case a single
JSON load rather than a replay at all.

Storing actions rather than states is what makes undo, redo and fork fall out
for free instead of each being a feature:

* **undo** marks the tail of the list undone and drops the cache
* **redo** un-marks it
* **fork** copies actions 1..n into a new session, and "replay turn 3
  differently" costs one INSERT per action rather than a new engine

Two decisions are load-bearing and easy to undo by accident:

1. **The deck is snapshotted, not referenced.** A deck's cards and annotations
   can be edited while a session is open, and re-deriving would change a card
   under the player's hands mid-game. Same reasoning as a stored
   `SimulationRun` keeping the gaps it was computed with.
2. **The cache is never the truth.** Anything that changes the action list
   clears it. A cache that can disagree with a replay is worse than no cache,
   because the disagreement is what the player would report as "it forgot my
   turn".
"""

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from decks.models import Deck
from simulation import actions
from simulations import gaps as gaps_module


class PlaytestSession(models.Model):
    """One game being played by hand."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE,
                             related_name="playtests")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="playtests",
    )

    #: The deck as the engine read it when the session opened, via
    #: `simulation.serial.dump_deck`. Frozen on purpose - see the module
    #: docstring.
    deck_snapshot = models.JSONField(default=dict)

    #: What the shuffle was seeded with. Two sessions with the same seed and
    #: the same snapshot are the same game.
    seed = models.BigIntegerField()
    on_the_play = models.BooleanField(default=True)

    #: The coverage the snapshot was taken with, so the board can stay as
    #: honest as the report is. A playtest of a deck the engine only half
    #: understands must not look like a playtest of one it fully does.
    gaps = models.JSONField(default=list, blank=True)
    cards_total = models.PositiveIntegerField(default=0)
    cards_with_gaps = models.PositiveIntegerField(default=0)

    #: A replay of every action not undone, cached. `cached_seq` is the
    #: sequence number it was taken at; `None` means there is no cache.
    cached_state = models.JSONField(default=dict, blank=True)
    cached_seq = models.IntegerField(null=True, blank=True)

    forked_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forks",
        help_text="The session this one branched off, if any.",
    )
    forked_at_seq = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            models.Index(fields=["owner", "-updated_at"]),
        ]

    def __str__(self) -> str:
        return f"Playtest of {self.deck.name} ({self.id})"

    def get_absolute_url(self) -> str:
        return reverse("playtest:detail", args=[self.id])

    # --- the score: how much of the deck the engine read ---------------------
    #
    # "The engine could not read 11 of 12 cards" is something the player can
    # fix. The stored gaps also hold the cast-priority questions, which no page
    # counts since phase 9 C - see `simulations.gaps`. The commander counts
    # toward its own denominator: it is not a `DeckCard`, and forgetting that
    # once produced -50%.

    @property
    def cards_unreadable(self) -> int:
        return len(gaps_module.cards_with(self.gaps, gaps_module.READING))

    @property
    def readable_pct(self) -> float:
        return round(100 * gaps_module.share(self.cards_total,
                                             self.cards_unreadable), 1)

    def invalidate(self) -> None:
        """Forget the cached state. Called by anything that edits the actions."""
        self.cached_state = {}
        self.cached_seq = None


class PlaytestAction(models.Model):
    """One thing the player did, in order."""

    session = models.ForeignKey(PlaytestSession, on_delete=models.CASCADE,
                                related_name="actions")

    #: Position in the session, from 1. Gaps never appear: undo marks rows
    #: undone rather than deleting them, so that redo has something to find.
    seq = models.PositiveIntegerField()

    #: `Action.kind` from `simulation.actions`. Stored, so never renamed.
    kind = models.CharField(max_length=32)
    payload = models.JSONField(default=dict, blank=True)

    undone = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("seq",)
        constraints = [
            models.UniqueConstraint(fields=["session", "seq"],
                                    name="playtest_action_seq_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.seq}. {self.kind}"

    def as_action(self) -> actions.Action:
        """The engine action this row stands for."""
        return actions.from_row(self.kind, self.payload)
