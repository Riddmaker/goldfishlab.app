"""Engine v38: the free seven needs one land more (Karsten's rule).

tests/test_game.py is vendored and stays byte-identical, so it pins
`keepable`, the rule for every hand after the free one. This file pins
`keeps_free_seven` and that `take_opening_hand` uses it for the first seven
only.
"""

import random

import pytest

from simulation.cards import SPELLS, SWAMP
from simulation.game import Game

SOL_RING = next(c for c in SPELLS if c.name == "Sol Ring")
DARK_RITUAL = next(c for c in SPELLS if c.name == "Dark Ritual")
SPELL = next(c for c in SPELLS if c.name == "Necropotence")


def _hand(lands, accelerants=0):
    fast = [SOL_RING, DARK_RITUAL][:accelerants]
    return [SWAMP] * lands + fast + [SPELL] * (7 - lands - len(fast))


@pytest.mark.parametrize(("lands", "accelerants", "kept"), [
    (0, 0, False),
    (1, 2, False),  # keepable later, not as the free seven
    (2, 0, False),  # the v38 change
    (2, 1, False),
    (2, 2, True),   # two accelerants stand in for the third land
    (3, 0, True),
    (5, 0, True),
    (6, 0, False),
])
def test_the_free_seven_needs_one_land_more(lands, accelerants, kept):
    assert Game(random.Random(1)).keeps_free_seven(_hand(lands, accelerants)) is kept


def test_a_two_lander_is_kept_after_the_free_mulligan():
    assert Game(random.Random(1)).keepable(_hand(2))


def test_take_opening_hand_judges_the_first_seven_by_it():
    """A first seven with two lands is kept only with two accelerants;
    otherwise the free mulligan is taken."""
    mulliganed = 0
    for seed in range(400):
        game = Game(random.Random(seed))
        game.take_opening_hand()
        if game.first_hand_lands != 2:
            continue
        if game.mulligans == 0:
            assert sum(1 for card in game.hand if card.is_accelerant) >= 2
        else:
            mulliganed += 1
    assert mulliganed  # the seeds do reach a two-land first seven
