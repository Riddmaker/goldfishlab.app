"""The deck summary at the foot of a report (phase 10 G, T6.1 and T6.3).

This part is **computed, not written**: the "Mechanisms" are what the deck
plays, counted off the same readings the deck page and the engine use, and
how soon this run drew each of them. The numbers are always right and cost
nothing, which is why no language model is asked for them - batch H's text
gets them as facts instead of inventing its own.

Two sources, said plainly on purpose:

* **the card count** is the deck as it stands now, like the blind spots and
  "Cards that need your attention" beside it - it is a property of the cards;
* **the share** is this run's, read off the "What you drew" chart at turn
  four (or the run's last turn, if it is shorter), so a chip and its line on
  the chart cannot disagree.

A mechanism the run did not measure - sacrifice outlets, drain and the like,
or a category the deck gained after the run - has a count and no share.
"""

from dataclasses import dataclass

from simulations.report import SEEN_ROLES

#: The turn a chip reports the share for (P6), unless the run is shorter.
BY_TURN = 4

#: What else a deck can be built around, beyond the eight categories the run
#: counts, in the engine's own role names (`cards.profiles.ROLE_FROM_TAG`).
#: Each comes from one community tag or the owner's own list.
MORE_MECHANISMS = (
    ("sac_outlet", "Sacrifice outlet"),
    ("reanimate", "Reanimate"),
    ("drain_payoff", "Drain"),
    ("steal", "Theft"),
    ("discard", "Discard"),
    ("cost_reducer", "Cost reducer"),
    ("evasion", "Evasion"),
)


@dataclass(frozen=True)
class Mechanism:
    """One chip: what it is, how many cards, and - if measured - how soon."""

    key: str
    label: str
    cards: int
    #: Share of games that had drawn one by `turn`, in percent; `None` when
    #: the run did not measure it.
    share: float | None = None
    turn: int | None = None
    #: The line on the "What you drew" chart with the same colour, if any.
    line: int | None = None


def mechanisms(readings, report: dict) -> dict:
    """The "Mechanisms" chips, and the core categories the deck has none of.

    Args:
        readings: `simulations.engine.adapter.readings(deck)`.
        report: `simulations.report.build(run)`.

    Returns:
        ``{"chips": [Mechanism, ...], "missing": [label, ...]}``, the chips in
        the order a deck list sorts them.
    """
    counts: dict[str, int] = {}
    for reading in readings:
        if reading.is_commander:
            continue
        for category in reading.card.categories:
            counts[category] = counts.get(category, 0) + reading.quantity

    lines = {}
    if report.get("seen"):
        lines = {line.key: line for line in report["seen"]["roles"].lines}
    turn = min(BY_TURN, report["turns"])

    chips = []
    for key, label in SEEN_ROLES + MORE_MECHANISMS:
        if not counts.get(key):
            continue
        line = lines.get(key)
        chips.append(Mechanism(
            key=key, label=label, cards=counts[key],
            share=line.values[turn - 1] if line else None,
            turn=turn if line else None,
            line=line.index if line else None,
        ))
    missing = [label for key, label in SEEN_ROLES if not counts.get(key)]
    return {"chips": chips, "missing": missing}


__all__ = ["BY_TURN", "MORE_MECHANISMS", "Mechanism", "mechanisms"]
