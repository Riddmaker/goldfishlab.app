"""The deck summary's "Mechanisms" (phase 10 G, T6.1 and T6.3).

The chips are arithmetic over two things the page already has - the deck's
readings and the report's "What you drew" chart - so they are tested here
with stand-ins for both, and on the real run page in
tests/test_simulations_runs.py.
"""

from dataclasses import dataclass, field

from cards.profiles import ROLE_FROM_TAG
from simulations.report import SEEN_ROLES, SEEN_STRATEGY_ROLES
from simulations.summary import BY_TURN, mechanisms


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


@dataclass
class _Line:
    key: str
    index: int
    values: list


@dataclass
class _Chart:
    lines: list


def _report(lines, turns=6, strategies=None):
    if lines is None:
        return {"turns": turns, "seen": None}
    seen = {"roles": _Chart(lines)}
    if strategies is not None:
        seen["strategies"] = _Chart(strategies)
    return {"turns": turns, "seen": seen}


def _chip(result, key):
    return next(chip for chip in result["chips"] if chip.key == key)


def test_a_category_counts_every_copy_and_not_the_commander():
    readings = [
        _Reading(("ramp",), quantity=1),
        _Reading(("ramp", "draw"), quantity=3),
        _Reading(("ramp",), is_commander=True),
    ]

    result = mechanisms(readings, _report([]))

    assert _chip(result, "ramp").cards == 4
    assert _chip(result, "draw").cards == 3


def test_a_chip_reads_its_share_off_the_chart_at_turn_four():
    lines = [_Line("draw", 0, [10, 20, 30, 40, 50, 60]),
             _Line("removal", 1, [5, 15, 25, 78, 80, 90])]

    chip = _chip(mechanisms([_Reading(("removal",))], _report(lines)), "removal")

    assert BY_TURN == 4
    assert chip.turn == 4
    assert chip.share == 78
    assert chip.line == 1, "the swatch of the same line, not of the chip's position"


def test_a_short_run_reports_its_last_turn():
    lines = [_Line("ramp", 0, [40, 70])]

    chip = _chip(mechanisms([_Reading(("ramp",))], _report(lines, turns=2)), "ramp")

    assert chip.turn == 2
    assert chip.share == 70


def test_an_old_run_without_the_chart_gives_counts_alone():
    chip = _chip(mechanisms([_Reading(("ramp",))], _report(None)), "ramp")

    assert chip.cards == 1
    assert chip.share is None
    assert chip.turn is None
    assert chip.line is None


def test_a_mechanism_the_run_does_not_count_has_no_share():
    lines = [_Line("ramp", 0, [10, 20, 30, 40])]

    chip = _chip(mechanisms([_Reading(("drain_payoff",))], _report(lines, turns=4)),
                 "drain_payoff")

    assert chip.label == "Drain"
    assert chip.share is None
    assert chip.line is None


def test_chips_follow_the_deck_list_order_and_skip_unknown_roles():
    readings = [_Reading(("sac_outlet", "wipe", "ramp", "ritual", "something-new"))]

    keys = [chip.key for chip in mechanisms(readings, _report([]))["chips"]]

    assert keys == ["ramp", "wipe", "sac_outlet"]


def test_the_core_categories_the_deck_lacks_are_named():
    readings = [_Reading((key,)) for key, _ in SEEN_ROLES if key not in {"tutor", "counterspell"}]

    assert mechanisms(readings, _report([]))["missing"] == ["Tutor", "Counterspell"]


def test_every_extra_mechanism_is_a_role_the_engine_knows():
    known = set(ROLE_FROM_TAG.values())

    for key, _ in SEEN_STRATEGY_ROLES:
        assert key in known, key


def test_a_strategy_chip_reads_its_share_off_by_strategy():
    """Phase 11 B: until D removes the chips, a strategy's turn number comes
    from the new chart, with that chart's swatch."""
    strategies = [_Line("evasion", 0, [1, 2, 3]), _Line("drain_payoff", 1, [5, 9, 30, 44])]

    chip = _chip(mechanisms([_Reading(("drain_payoff",))],
                            _report([], turns=4, strategies=strategies)), "drain_payoff")

    assert chip.share == 44
    assert chip.turn == 4
    assert chip.line == 1
