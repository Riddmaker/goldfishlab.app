"""P16: the engine checked against Frank Karsten's coloured-source table.

Two claims, both on the methodology page:

1. **The counting is Karsten's.** With his mulligan rule, every mana cost of
   his 99-card table reaches its target at his number of sources and misses it
   at one fewer - within sampling error.
2. **The engine's numbers are the ones the page prints** (`karsten.REQUIRED`).
   A change to the mulligan rule or the draws that moves one fails here, and
   the page has to say so.
3. **The engine needs Karsten's numbers**: since v38 it throws back a free
   seven with two lands as he does, and every cost reaches his target at his
   number of sources and misses it at one fewer - within sampling error.

All of them run 50,000 games per question with fixed seeds, so they are exact replays
and never flaky; the tolerance is three standard errors of a share near 90 %.
"""

from functools import cache

import pytest

from simulation import karsten
from simulation.karsten import COSTS, PUBLISHED, REQUIRED

GAMES = 50_000
TOLERANCE = 3 * (0.9 * 0.1 / GAMES) ** 0.5


@cache
def _his(sources):
    return karsten.karsten_tally(sources, GAMES)


@cache
def _engine():
    return karsten.engine_tally(GAMES)


def _ids(costs):
    return [cost.text for cost in costs]


def test_the_costs_are_read_as_written():
    assert (karsten.Cost.parse("2CC").generic, karsten.Cost.parse("2CC").pips) == (2, 2)
    assert karsten.Cost.parse("C").mv == 1
    assert karsten.Cost.parse("1CCCC").target == 94
    assert set(REQUIRED) == set(PUBLISHED)


def test_the_deck_is_karstens():
    assert karsten.DECK.size == 99
    assert karsten.DECK.land_count == 41
    assert karsten.DECK.commander is None


@pytest.mark.parametrize("cost", COSTS, ids=_ids(COSTS))
def test_his_rule_reaches_his_numbers(cost):
    sources = PUBLISHED[cost.text]
    assert _his(sources).chance(cost, sources) >= cost.target / 100 - TOLERANCE
    assert _his(sources - 1).chance(cost, sources - 1) < cost.target / 100 + TOLERANCE


@pytest.mark.parametrize("cost", COSTS, ids=_ids(COSTS))
def test_the_engine_needs_what_the_page_says(cost):
    sources = REQUIRED[cost.text]
    assert _engine().chance(cost, sources) >= cost.target / 100 - TOLERANCE
    assert _engine().chance(cost, sources - 1) < cost.target / 100 + TOLERANCE


def test_the_engine_never_needs_fewer_than_karsten():
    assert all(REQUIRED[text] >= PUBLISHED[text] for text in PUBLISHED)


@pytest.mark.parametrize("cost", COSTS, ids=_ids(COSTS))
def test_the_engine_needs_karstens_numbers(cost):
    """v38: the engine throws back a free seven with two lands, as Karsten
    does. Before, it kept it and needed one or two sources more for seven
    costs."""
    sources = PUBLISHED[cost.text]
    assert _engine().chance(cost, sources) >= cost.target / 100 - TOLERANCE
    assert _engine().chance(cost, sources - 1) < cost.target / 100 + TOLERANCE


def test_his_rule_bottoms_other_colours_first():
    # Two colour lands (0, 1), two others (39, 40), three spells: one land goes.
    assert sorted(karsten._bottom([0, 1, 39, 40, 50, 60, 70], 2, 1, 0)) == [0, 1, 40, 50, 60, 70]
    # No other land in hand: a colour land goes.
    assert sorted(karsten._bottom([0, 1, 50, 60, 70, 80, 90], 2, 1, 1)) == [1, 60, 70, 80, 90]
