"""P19 R8 (engine version 12): a draw is read with what it gives back.

Faithless Looting was read as "draw two" and nothing else, so the card played
better than it does. Its discard is read now, Brainstorm's put-back too;
Mystic Confluence draws three, and a spree mode's cost is paid with its draw.
"""

import random

import pytest

from cards.models import OracleCard
from cards.profiles import _draw, derive
from simulation import actions
from simulation.cards import CREATURE, LAND, SORCERY, Card
from simulation.game import Game
from simulation.manacost import parse
from simulations.engine import adapter

# --- the reader -----------------------------------------------------------------


@pytest.mark.parametrize("text, cards, discards, puts_back, extra", [
    ("Draw two cards, then discard two cards.\nFlashback {2}{R}", 2, 2, 0, ""),
    ("Draw two cards, then discard a card.", 2, 1, 0, ""),
    ("Draw three cards, then put two cards from your hand on top of your library in any "
     "order.", 3, 0, 2, ""),
    ("Choose three. You may choose the same mode more than once.\n"
     "• Counter target spell unless its controller pays {3}.\n"
     "• Return target creature to its owner's hand.\n• Draw a card.", 3, 0, 0, ""),
    # Choose two, but each mode once: still one card.
    ("Choose two —\n• Counter target spell.\n• Return target permanent to its owner's hand.\n"
     "• Draw a card.", 1, 0, 0, ""),
    ("Spree (Choose one or more additional costs.)\n"
     "+ {2} — Search your library for a card, then shuffle and put that card on top.\n"
     "+ {B}{B} — Target player draws three cards and loses 3 life.", 3, 0, 0, "{B}{B}"),
    # A discard somewhere else on the card is not the draw's.
    ("Draw two cards.\nAt the beginning of your end step, discard a card.", 2, 0, 0, ""),
])
def test_the_draw_is_read_with_what_comes_after_it(text, cards, discards, puts_back, extra):
    draw = _draw(text)
    assert (draw.cards, draw.discards, draw.puts_back, draw.extra_cost) == (
        cards, discards, puts_back, extra)


def avarice() -> OracleCard:
    return OracleCard(
        name="Insatiable Avarice", front_name="Insatiable Avarice", type_line="Sorcery",
        mana_cost="{B}", cmc=1,
        oracle_text="Spree (Choose one or more additional costs.)\n"
                    "+ {2} — Search your library for a card, then shuffle and put that card "
                    "on top.\n+ {B}{B} — Target player draws three cards and loses 3 life.")


@pytest.mark.django_db
def test_a_spree_draw_costs_its_mode():
    card = avarice()
    profile = derive(card, set())
    assert str(adapter._cost(card, profile, {})) == "{B}{B}{B}"
    assert adapter._draw_on_cast(profile, {}, SORCERY) == 3
    # Somebody who says what the spell draws has said which mode it plays.
    assert str(adapter._cost(card, profile, {"draw_on_cast": 0})) == "{B}"


@pytest.mark.django_db
def test_a_person_who_sets_the_draw_also_sets_what_follows_it():
    card = OracleCard(name="Faithless Looting", front_name="Faithless Looting",
                      type_line="Sorcery", mana_cost="{R}", cmc=1,
                      oracle_text="Draw two cards, then discard two cards.\nFlashback {2}{R}")
    profile = derive(card, set())
    assert adapter._after_draw(profile, {}, SORCERY, "discard_on_cast", "discards_after") == 2
    assert adapter._after_draw(profile, {"draw_on_cast": 1}, SORCERY, "discard_on_cast",
                               "discards_after") == 0
    assert adapter._after_draw(profile, {"discard_on_cast": 1}, SORCERY, "discard_on_cast",
                               "discards_after") == 1


# --- the engine -----------------------------------------------------------------


MOUNTAIN = Card("Mountain", 0, 0, 0, LAND, subtypes=frozenset({"mountain"}))
ISLAND = Card("Island", 0, 0, 0, LAND, subtypes=frozenset({"island"}))
LOOTING = Card("Faithless Looting", 1, 0, 0, SORCERY, cost=parse("{R}"), draw_on_cast=2,
               discard_on_cast=2)
BRAINSTORM = Card("Brainstorm", 1, 0, 0, SORCERY, cost=parse("{U}"), draw_on_cast=3,
                  put_back_on_cast=2)
BEAR = Card("Bear", 2, 0, 1, CREATURE, cost=parse("{1}{G}"))
GIANT = Card("Giant", 6, 0, 5, CREATURE, cost=parse("{5}{G}"))
DRAGON = Card("Dragon", 7, 0, 6, CREATURE, cost=parse("{6}{R}"))


def game_with(lands, hand, library=()):
    game = Game(random.Random(1))
    game.lands, game.hand, game.library = list(lands), list(hand), list(library)
    game.tapped_lands = 0
    return game


def cast(game, card):
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=game.hand.index(card)).run(game, None)


def test_looting_draws_two_and_discards_the_two_worst():
    game = game_with([MOUNTAIN], [LOOTING, BEAR], library=[GIANT, DRAGON, BEAR])
    cast(game, LOOTING)
    assert game.hand == [BEAR]
    assert GIANT in game.graveyard and DRAGON in game.graveyard
    assert game.library == [BEAR]


def test_brainstorm_puts_the_two_worst_back_on_top():
    game = game_with([ISLAND], [BRAINSTORM], library=[BEAR, GIANT, DRAGON, BEAR])
    cast(game, BRAINSTORM)
    assert game.hand == [BEAR]
    assert set(game.library[:2]) == {GIANT, DRAGON}
    assert game.library[2:] == [BEAR]
