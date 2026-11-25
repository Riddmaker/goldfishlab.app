"""What could go: the deck's cards a rule calls the easiest to cut
(deck page v2, PR D, decision D2).

A suggestion to add a card needs a slot. The rule picks the candidates, so
the same list gives the same cards every time, and the summary's AI only
writes a reason for each (`simulations.summary`). It never picks a card.

A card is a candidate when it plays no part in the deck's plan:

* **Nothing to do**: none of our roles, strategies or mechanics, and no piece
  of a theme the deck is built around.
* **One too many**: every role it has is over the common template the bars
  show (`deck_cards.TEMPLATE`), so cutting it keeps the role in its band.
  Each role over its band gives up only as many cards as it is over.

Never a candidate: the commander (it is not in the library), a land (the
land tile judges those), a card of a theme or an engine, and a card of a combo
Commander Spellbook found complete in the deck - for this deck, or for
another one with the same cards (`summary.cut_candidates`). The most expensive come
first: a slot at six mana is worth more than one at two.
"""

from dataclasses import dataclass

from django.utils.translation import gettext

from simulations.deck_cards import TEMPLATE
from simulations.report import SEEN_MECHANIC_ROLES, SEEN_ROLES, SEEN_STRATEGY_ROLES

#: The page shows this many candidates.
CUTS_MAX = 5

LABELS = dict(SEEN_ROLES + SEEN_STRATEGY_ROLES + SEEN_MECHANIC_ROLES)
#: The highest count of each role the template names ("role:ramp": 10).
BAND_HIGH = {key.split(":", 1)[1]: high for key, (_low, high) in TEMPLATE.items()
             if key.startswith("role:")}

NONE = "none"
OVER = "over"


@dataclass(frozen=True)
class Cut:
    """A card that could go, and the rule's word on why."""

    name: str
    mana_value: int
    kind: str
    #: For `OVER`: each role over its band, with its count in the deck.
    counted: tuple[tuple[str, int], ...] = ()
    reason: str = ""
    url: str = ""
    art: str = ""
    image: str = ""

    @property
    def rule(self) -> str:
        """Why the rule names the card, in the page's language."""
        if self.kind == NONE:
            return gettext("No role and no part in a theme.")
        return "; ".join(
            gettext("%(role)s %(count)s / ~%(high)s")
            % {"role": gettext(LABELS[role]), "count": count, "high": BAND_HIGH[role]}
            for role, count in self.counted
        )

    def for_prompt(self) -> dict:
        """The candidate as the summary's facts give it, in English."""
        if self.kind == NONE:
            why = "no role and no part in any theme"
        else:
            why = "; ".join(f"{LABELS[role]}: {count} in the deck, template {BAND_HIGH[role]}"
                            for role, count in self.counted)
        return {"name": self.name, "mana_value": self.mana_value, "why_it_could_go": why}


def protected(themes=(), engines=(), combos=None) -> set[str]:
    """The names no rule may cut: theme and engine cards, combo pieces.

    Names are kept in full and by front face, so a double-faced card is
    found either way.
    """
    names = {name for found in themes for name in found.cards}
    names |= {name for engine in engines for name in getattr(engine, "cards", ())}
    names |= {name for combo in combos or [] for name in combo.get("cards", [])}
    return names | {name.split(" // ", 1)[0] for name in names}


def candidates(readings, keep: set[str] = frozenset(), limit: int = CUTS_MAX) -> list[Cut]:
    """The rule's candidates, the most expensive first (ties by name).

    Args:
        readings: `simulations.engine.adapter.readings(deck)`.
        keep: names never cut (`protected`).
    """
    library = [reading for reading in readings
               if not reading.is_commander and not reading.card.is_land]
    counts = dict.fromkeys(BAND_HIGH, 0)
    for reading in library:
        for role in BAND_HIGH:
            if role in reading.card.categories:
                counts[role] += reading.quantity
    room = {role: counts[role] - high for role, high in BAND_HIGH.items()
            if counts[role] > high}
    ordered = sorted(library, key=lambda reading: (-int(reading.oracle_card.cmc),
                                                   reading.oracle_card.front_name))
    cuts = []
    for reading in ordered:
        if len(cuts) == limit:
            break
        oracle = reading.oracle_card
        if oracle.name in keep or oracle.front_name in keep:
            continue
        roles = [key for key in LABELS if key in reading.card.categories]
        if not roles:
            cuts.append(Cut(name=oracle.front_name, mana_value=int(oracle.cmc), kind=NONE))
        elif all(room.get(role, 0) > 0 for role in roles):
            for role in roles:
                room[role] -= 1
            cuts.append(Cut(name=oracle.front_name, mana_value=int(oracle.cmc), kind=OVER,
                            counted=tuple((role, counts[role]) for role in roles)))
    return cuts


__all__ = ["CUTS_MAX", "Cut", "NONE", "OVER", "candidates", "protected"]
