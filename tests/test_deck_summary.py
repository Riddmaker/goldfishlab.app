"""What the deck has none of (phase 10 G, moved by phase 11 D, P3).

The "Mechanisms" chips are gone (K15); what is left of them is the line
"None in the deck: ..." under "By category" - the core categories without a
card. It is arithmetic over the deck's readings, tested here with stand-ins,
and on the real run page in tests/test_simulations_runs.py.
"""

from dataclasses import dataclass, field

from cards.profiles import ROLE_FROM_TAG
from simulations.report import SEEN_ROLES, SEEN_STRATEGY_ROLES
from simulations.summary import missing


@dataclass
class _Card:
    categories: frozenset


@dataclass
class _Reading:
    categories: tuple = ()
    quantity: int = 1
    is_commander: bool = False
    card: _Card = field(init=False)

    def __post_init__(self):
        self.card = _Card(frozenset(self.categories))


def test_the_core_categories_the_deck_lacks_are_named_in_list_order():
    readings = [_Reading((key,)) for key, _ in SEEN_ROLES if key not in {"tutor", "counterspell"}]

    assert missing(readings) == ["Tutor", "Counterspell"]


def test_the_commander_does_not_fill_a_category():
    readings = [_Reading(("ramp",), is_commander=True)]

    assert "Ramp" in missing(readings)


def test_strategies_are_never_missing():
    """A deck without drain is not lacking: only the core eight are named."""
    names = missing([])

    assert names == [label for _, label in SEEN_ROLES]
    assert not set(names) & {label for _, label in SEEN_STRATEGY_ROLES}


def test_every_strategy_is_a_role_the_engine_knows():
    known = set(ROLE_FROM_TAG.values())

    for key, _ in SEEN_STRATEGY_ROLES:
        assert key in known, key
