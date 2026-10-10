"""P19 R7 (engine version 11): Treasure tokens and "discard a card" as a cost.

Big Score, Seize the Spoils and Rapacious Dragon make Treasure as they resolve;
a payment sacrifices one only when it would fail without, and the rest wait.
"As an additional cost to cast this spell, discard a card" is paid with the
weakest card in hand.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _treasures, derive
from simulation import actions, serial
from simulation.cards import CREATURE, INSTANT, LAND, Card, DeckDefinition
from simulation.game import Game
from simulation.mana import ManaPool
from simulation.manacost import parse

Kind = DerivedProfile.Kind
REMINDER = (' (They\'re artifacts with "{T}, Sacrifice this token: Add one mana of any '
            'color.")')


# --- the reader -----------------------------------------------------------------


@pytest.mark.parametrize("text, kind, count", [
    ("Draw two cards and create two Treasure tokens." + REMINDER, Kind.INSTANT, 2),
    ("Create a Treasure token.", Kind.SORCERY, 1),
    ("Flying\nWhen this creature enters, create two Treasure tokens." + REMINDER,
     Kind.CREATURE, 2),
    # Somebody else's draw, or once per land: not a fixed number.
    ("Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, "
     "you create a Treasure token.", Kind.ENCHANTMENT, 0),
    ("For each land you control, create a Treasure token.", Kind.SORCERY, 0),
])
def test_treasure_made_as_it_resolves_is_read(text, kind, count):
    assert _treasures(OracleCard(oracle_text=text), kind) == count


def big_score() -> OracleCard:
    return OracleCard(name="Big Score", front_name="Big Score", type_line="Instant",
                      mana_cost="{3}{R}", cmc=4, produced_mana=["W", "U", "B", "R", "G"],
                      oracle_text="As an additional cost to cast this spell, discard a card.\n"
                                  "Draw two cards and create two Treasure tokens." + REMINDER)


@pytest.mark.django_db
def test_big_score_is_read_whole():
    profile = derive(big_score(), set())
    assert (profile.treasures, profile.discard_cost) == (2, 1)
    assert not profile.needs_review, profile.review_reasons


@pytest.mark.django_db
def test_a_sacrifice_as_additional_cost_is_read_beside_the_treasure():
    dispute = OracleCard(name="Deadly Dispute", front_name="Deadly Dispute",
                         type_line="Instant", mana_cost="{1}{B}", cmc=2,
                         oracle_text="As an additional cost to cast this spell, sacrifice an "
                                     "artifact or creature.\nDraw two cards and create a "
                                     "Treasure token.")
    profile = derive(dispute, set())
    assert profile.discard_cost == 0 and profile.treasures == 1
    # P19 R15: the sacrifice is paid, so it is no longer a gap.
    assert profile.additional_cost[0]["sacrifice"] == ["artifact", "creature"]
    assert not profile.review_reasons


# --- the engine -----------------------------------------------------------------


def land(name, subtype):
    return Card(name, 0, 0, 0, LAND, subtypes=frozenset({subtype}))


MOUNTAIN, FOREST = land("Mountain", "mountain"), land("Forest", "forest")
SCORE = Card("Big Score", 4, 0, 3, INSTANT, cost=parse("{3}{R}"), treasures=2,
             treasure_mana="RG", discard_cost=1, draw_on_cast=2)
BEAR = Card("Bear", 2, 0, 1, CREATURE, cost=parse("{1}{G}"))
GIANT = Card("Giant", 6, 0, 5, CREATURE, cost=parse("{5}{G}"))


def game_with(lands, hand, library=()):
    game = Game(random.Random(1))
    game.lands, game.hand, game.library = list(lands), list(hand), list(library)
    game.tapped_lands = 0
    return game


def open_and_cast(game, card):
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=game.hand.index(card)).run(game, None)


def test_big_score_discards_the_worst_card_draws_and_makes_two_treasures():
    game = game_with([MOUNTAIN] * 4, [SCORE, BEAR, GIANT], library=[BEAR, BEAR])
    open_and_cast(game, SCORE)
    assert GIANT in game.graveyard and SCORE in game.graveyard
    assert game.treasures == ["RG", "RG"]
    assert game.pool.treasures == ["RG", "RG"]
    assert len(game.hand) == 3  # the Bear kept, two drawn


def test_a_discard_cost_needs_another_card_in_hand():
    game = game_with([MOUNTAIN] * 4, [SCORE])
    actions.OpenMainPhase().run(game, None)
    assert not game.can_cast(SCORE, game.pool)


def test_a_treasure_is_sacrificed_only_when_the_payment_needs_it():
    pool = ManaPool(R=2)
    pool.treasures = ["RG", "RG"]
    assert pool.pay_cost(parse("{1}{R}")) is not None
    assert pool.treasures == ["RG", "RG"], "two Mountains paid; no Treasure touched"

    pool = ManaPool(R=1)
    pool.treasures = ["RG", "RG"]
    assert pool.pay_cost(parse("{1}{G}")) is not None
    assert pool.treasures == ["RG"] and pool.total == 0


def test_treasures_last_from_turn_to_turn_and_pay_for_a_spell():
    game = game_with([FOREST], [BEAR])
    game.treasures = ["RG"]
    open_and_cast(game, BEAR)
    assert BEAR in game.creatures and game.treasures == []


def test_a_cached_game_keeps_its_treasures():
    deck = DeckDefinition("Treasure", None, (MOUNTAIN,) * 10)
    game = Game(random.Random(1), deck=deck)
    game.treasures = ["RG"]
    game.pool = ManaPool(C=1)
    game.pool.treasures, game.pool.converters = ["RG"], ["WUB"]
    again = serial.load_game(serial.dump_game(game), deck)
    assert again.treasures == ["RG"]
    assert (again.pool.treasures, again.pool.converters) == (["RG"], ["WUB"])


@pytest.mark.parametrize("text", [
    # An ability's cost before it.
    "Draw two cards.\n{U/R}{U/R}, Discard this card: Create a Treasure token.",
    # Only for each player who chose it.
    "For each player who chose fortune, you draw a card and create a Treasure token.",
    # A trigger the spell hands to a creature.
    'Target creature gains "When this creature dies, you create a Treasure token."',
])
def test_a_treasure_made_only_sometimes_is_not_read(text):
    assert _treasures(OracleCard(oracle_text=text), Kind.INSTANT) == 0
