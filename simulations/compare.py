"""Compared with your last run (Paket 3, PR 13; Themen-Doc row 31).

Replaces the "Compare two versions" fake door (P9): a finished report sets its
numbers next to the deck's finished run before it. Runs keep no card list,
only the list's fingerprint (`SimulationRun.list_print`), so the panel can say
whether the deck changed in between, not which cards did.
"""

from dataclasses import dataclass

from simulations import numbers
from simulations.models import SimulationRun


@dataclass(frozen=True)
class Comparison:
    before: SimulationRun
    changes: list[numbers.Change]
    #: True or False when both runs know their list, None when one does not.
    deck_changed: bool | None
    #: The two runs did not draw the same way (on the play or not).
    other_draw: bool


def last_before(run: SimulationRun) -> SimulationRun | None:
    """The deck's newest finished run that finished before `run`."""
    if run.finished_at is None:
        return None
    return (SimulationRun.objects
            .filter(deck_id=run.deck_id, status=SimulationRun.Status.DONE,
                    finished_at__lt=run.finished_at)
            .exclude(pk=run.pk).exclude(result={})
            .order_by("-finished_at").first())


def with_last(run: SimulationRun, now: numbers.Numbers) -> Comparison | None:
    """`run` against the deck's run before, or None when it is the first."""
    before = last_before(run)
    if before is None:
        return None
    changed = None
    if before.list_print and run.list_print:
        changed = before.list_print != run.list_print
    return Comparison(
        before=before,
        changes=numbers.changes(numbers.of(before), now),
        deck_changed=changed,
        other_draw=before.on_the_play != run.on_the_play,
    )
