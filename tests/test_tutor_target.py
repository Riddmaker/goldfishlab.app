"""Phase 10 N1: what a tutor finds in a deck with no priority list.

Since phase 9 C nobody is asked how early to cast a card, so every user deck
plays without priorities - and the old fallback, "cheaper first", made a land
the best card in the library. Demonic Tutor fetched a Forest. These tests pin
the new rule; `test_engine_parity.py` pins that a deck WITH priorities (the
reference deck) still plays exactly the games it always did.
"""

import random

import pytest

from simulation import agent, analysis
from simulation.cards import (
    CREATURE,
    LAND,
    SORCERY,
    Card,
    DeckDefinition,
    TutorSpec,
)
from simulation.fixtures import chainer
from simulation.game import Game


def _card(name, kind, mv=0, **extra):
    return Card(name, mv, 0, mv, kind, **extra)


FOREST = _card("Forest", LAND, subtypes=frozenset({"Forest"}))
TAPPED = _card("Gloomy Bog", LAND, enters_tapped=True)
SOL_RING = _card("Sol Ring", "rock", 1)
CULTIVATE = _card("Cultivate", SORCERY, 3)
DRAGON = _card("Big Dragon", CREATURE, 7)
ENGINE = _card("Card Engine", "enchantment", 4, tags=frozenset({"draw_engine"}))


def _choose(options, *, mana=3, key_cards=frozenset()):
    deck = DeckDefinition(name="t", commander=None, library=tuple(options),
                          key_cards=frozenset(key_cards))
    game = Game(random.Random(1), deck=deck)
    game.mana_available = mana
    return agent.POLICY.choose_tutor_target(game, list(options))


def test_the_probe_from_the_report_no_longer_finds_a_forest():
    """The exact case the user's question turned up: no priorities anywhere."""
    assert _choose([FOREST, SOL_RING, CULTIVATE, DRAGON]) is CULTIVATE


def test_the_biggest_spell_castable_by_next_turn():
    assert _choose([FOREST, SOL_RING, CULTIVATE, DRAGON], mana=6) is DRAGON


def test_nothing_castable_by_next_turn_takes_the_cheapest():
    assert _choose([FOREST, CULTIVATE, DRAGON], mana=0) is CULTIVATE


@pytest.mark.parametrize("key", [{"Sol Ring"}, set()])
def test_a_key_card_beats_a_bigger_spell(key):
    """A combo piece (named by the deck) or an engine (by its tag)."""
    options = [SOL_RING, CULTIVATE, DRAGON] + ([] if key else [ENGINE])
    expected = SOL_RING if key else ENGINE

    assert _choose(options, mana=6, key_cards=key) is expected


def test_an_explicit_priority_still_decides_as_before():
    """Any option with a priority, and the old rule decides - Forest at 40."""
    ranked = _card("Ranked", SORCERY, 2, priority=10)

    assert _choose([FOREST, ranked, DRAGON]) is FOREST


def test_a_land_only_search_takes_the_land_the_land_rule_would_play():
    assert _choose([TAPPED, FOREST]) is FOREST


def test_ties_go_by_name():
    a, b = _card("Alpha", SORCERY, 2), _card("Beta", SORCERY, 2)

    assert _choose([a, b]) is b
    assert _choose([b, a]) is b


def test_a_whole_game_tutors_for_a_spell():
    """The game log of a deck with Demonic Tutor and no priorities."""
    tutor = _card("Demonic Tutor", SORCERY, 2, tutor=TutorSpec(to_hand=True, count=1))
    swamp = next(card for card in chainer.DECK.library if card.name == "Swamp")
    deck = DeckDefinition(
        name="tutors", commander=None,
        library=(swamp,) * 40 + (tutor,) * 30 + (CULTIVATE,) * 15 + (DRAGON,) * 14,
    )
    rng = random.Random(5)
    found = []
    for _ in range(40):
        game = analysis.simulate_game(rng, turns=5, deck=deck, keep_log=True)
        found += [line for line in game["log"] if "searches up" in line]

    assert found, "no game cast the tutor"
    assert not any("Swamp" in line for line in found)
