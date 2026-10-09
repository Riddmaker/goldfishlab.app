"""P19 R12 (engine version 16): tutors that put the card on top of the library.

Vampiric Tutor, Mystical Tutor, Enlightened Tutor, Worldly Tutor and Imperial
Seal were gaps: the zone is in the text ("then shuffle and put that card on
top"), and no tag says it. They now put the card on top, and the next draw
takes it. Sterling Grove's search is an ability and Insatiable Avarice's a
spree mode whose {2} nobody pays: both stay gaps.
"""

import random

import pytest

from cards.models import OracleCard
from cards.profiles import _tutor
from simulation import actions
from simulation.cards import CREATURE, INSTANT, LAND, SORCERY, Card, TutorSpec
from simulation.game import Game
from simulation.manacost import parse

TAGS = {"tutor"}


def oracle(text):
    return OracleCard(name="Card", front_name="Card", type_line="Instant", mana_cost="{B}",
                      oracle_text=text)


@pytest.mark.parametrize("text, types", [
    ("Search your library for a card, then shuffle and put that card on top. You lose 2 life.",
     []),
    ("Search your library for an instant or sorcery card, reveal it, then shuffle and put that "
     "card on top.", ["instant", "sorcery"]),
    ("Search your library for a creature card, reveal it, then shuffle and put the card on "
     "top.", ["creature"]),
    ("When this creature enters, you may search your library for an enchantment card, reveal "
     "it, then shuffle and put that card on top.", ["enchantment"]),
])
def test_a_search_to_the_top_is_read(text, types):
    tutor = _tutor(oracle(text), TAGS)
    assert (tutor.zone, tutor.count, tutor.filter, tutor.reason) == (
        "top", 1, {"types": types}, "")


@pytest.mark.parametrize("text", [
    "{1}, Sacrifice this enchantment: Search your library for an enchantment card, reveal it, "
    "then shuffle and put that card on top.",
    "Spree (Choose one or more additional costs.)\n+ {2} — Search your library for a card, "
    "then shuffle and put that card on top.\n+ {B}{B} — Target player draws three cards and "
    "loses 3 life.",
])
def test_an_ability_or_a_spree_mode_stays_a_gap(text):
    assert _tutor(oracle(text), TAGS).zone != "top"


SWAMP = Card("Swamp", 0, 0, 0, LAND, subtypes=frozenset({"swamp"}), basic=True)
VAMPIRIC = Card("Vampiric Tutor", 1, 1, 0, INSTANT, cost=parse("{B}"),
                tutor=TutorSpec(to_hand=False, to_top=True, life=2))
#: Paid with {B} here: the test board has Swamps.
MYSTICAL = Card("Mystical Tutor", 1, 1, 0, INSTANT, cost=parse("{B}"),
                tutor=TutorSpec(to_hand=False, to_top=True,
                                types=frozenset({"instant", "sorcery"})))
BIG = Card("Big Creature", 7, 0, 7, CREATURE, cost=parse("{7}"), types=frozenset({"creature"}))
RITUAL = Card("Ritual", 2, 1, 1, SORCERY, cost=parse("{1}{B}"), types=frozenset({"sorcery"}))


def game_with(hand, library):
    game = Game(random.Random(1))
    game.lands, game.hand, game.library = [SWAMP, SWAMP], list(hand), list(library)
    game.tapped_lands = 0
    actions.OpenMainPhase().run(game, None)
    return game


def test_vampiric_tutor_puts_its_card_on_top_and_costs_two_life():
    game = game_with([VAMPIRIC], [SWAMP, BIG])
    actions.CastSpell(index=0).run(game, None)
    assert game.library[0] is BIG and game.life == 38
    assert BIG not in game.hand
    game.draw()
    assert BIG in game.hand


def test_mystical_tutor_finds_only_an_instant_or_sorcery():
    game = game_with([MYSTICAL], [BIG, SWAMP, RITUAL])
    actions.CastSpell(index=0).run(game, None)
    assert game.library[0] is RITUAL
