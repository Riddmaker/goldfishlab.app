"""Engine v33: mana from a trigger at the main phase or as a creature enters,
and Priest of Forgotten Gods.

What these tests hold:

1. **The reader reads the trigger**: Frontier Siege's Khans ("each of your
   main phases", played in the first only) and Outcaster Trailblazer's mana
   as it enters; a Treasure as it enters stays `treasures`, never a trigger
   besides.
2. **Priest is an altar for two**: read off its text, it taps as a creature
   does - not the turn it arrived - sacrifices two others, adds {B}{B} and
   draws a card.
"""

import random
from types import SimpleNamespace

import pytest

from cards.models import OracleCard
from cards.profiles import _treasure_altar
from simulation import actions, agent, build
from simulation.cards import CREATURE, AdditionalCost, Card, DeckDefinition
from simulation.game import Game
from simulation.manacost import parse
from simulations.engine import adapter
from tests.engine_builders import tap
from tests.test_sacrifice_costs import BEAR, ELF, FIVE, SQUIRE, SWAMP
from tests.test_triggers import read

# --- the reader -----------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("As this enchantment enters, choose Khans or Dragons.\n• Khans — At the beginning of each "
     "of your main phases, add {G}{G}.", {"event": "main", "filter": "", "mana": 2,
                                           "color": "G"}),
    ("When this creature enters, add one mana of any color.",
     {"event": "resolves", "filter": "", "mana": 1, "color": "any"}),
])
def test_a_mana_trigger_is_read(text, expected):
    assert read(text).found == [expected]


def test_a_treasure_as_it_enters_is_no_trigger_besides():
    assert read("When this creature enters, create a Treasure token.").found == []


PRIEST_TEXT = ("{T}, Sacrifice two other creatures: Any number of target players each lose 2 "
               "life and sacrifice a creature of their choice. You add {B}{B} and draw a card. "
               "(Activate only as a sorcery.)")


def test_priest_is_read_as_an_altar_for_two():
    assert _treasure_altar(OracleCard(oracle_text=PRIEST_TEXT)) == {
        "sacrifice": ["creature"], "filter": "", "amount": 2, "produces": {"B": 2},
        "taps": True, "life": 0, "other": True, "count": 2, "draw": 1}


# --- the engine ---------------------------------------------------------------------------

PRIEST = build.card("Priest of Forgotten Gods", 2, 1, 1, CREATURE, cost=parse("{1}{B}"),
                    **adapter._sacrifice_mana(SimpleNamespace(sacrifice_mana=_treasure_altar(
                        OracleCard(oracle_text=PRIEST_TEXT))), frozenset({"B"})))


def game_with(*, creatures=(), hand=(), arrived=()):
    game = Game(random.Random(1), deck=DeckDefinition("v33", None, (SWAMP,)))
    game.library = [Card(f"Card {n}", 0, 0, 0, CREATURE) for n in range(3)]
    game.lands = [SWAMP] * 3
    game.creatures = list(creatures)
    game.hand = list(hand)
    tap(game, lands=0)
    game.turn = 3
    for card in arrived:
        game.mark_arrived(card)
    actions.OpenMainPhase().run(game, None)
    return game


def test_the_view_holds_two_sacrifices_and_a_card():
    altar = PRIEST.mana.altar
    assert (altar.count, altar.amount, altar.color, altar.taps, altar.other, altar.draw) == (
        2, 2, "B", True, True, 1)


def test_priest_sacrifices_two_others_for_two_black_and_a_card():
    game = game_with(creatures=[PRIEST, BEAR, SQUIRE], hand=[FIVE])
    assert agent._try_ritual_line(game, game.pool)
    assert BEAR in game.graveyard and SQUIRE in game.graveyard
    assert game.pool.by_color().get("B") == 5 and len(game.hand) == 2
    assert game.creature_tapped(PRIEST)
    assert game.altar_fodder(PRIEST, game.pool) == []


def test_priest_needs_two_and_never_itself():
    game = game_with(creatures=[PRIEST, BEAR])
    assert game.altar_fodder(PRIEST, game.pool) == []
    game = game_with(creatures=[PRIEST, BEAR, ELF])
    assert game.altar_fodder(PRIEST, game.pool) == []


def test_priest_does_not_tap_the_turn_it_arrived():
    game = game_with(creatures=[PRIEST, BEAR, SQUIRE], arrived=[PRIEST])
    assert game.altar_fodder(PRIEST, game.pool) == []


def test_the_card_page_says_two_are_sacrificed():
    assert adapter._way_text(AdditionalCost(sacrifice=frozenset({"creature"}),
                                            sacrifice_count=2)) == "sacrifice 2 × Creature"


def test_the_editor_says_the_card_priest_draws():
    from simulations import editor

    assert editor._text("sacrifice_mana", SimpleNamespace(card=PRIEST)) == (
        "sacrifice 2 × Creature: 2 mana and 1 card")
