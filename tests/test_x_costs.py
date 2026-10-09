"""P19 R9 (engine version 13): X is paid.

Until now the engine read {X} and paid nothing for it: Stroke of Genius was cast
for X = 0 the moment three mana were open, and drew nothing. An X spell now
waits until nothing else can be cast and takes everything left as X - the way
Forge's AI chooses its X - and "draw X cards" draws them.
"""

import random

import pytest

from cards.models import DerivedProfile, OracleCard
from cards.profiles import _draw, derive
from playtest.forms import ActionForm
from simulation import actions, agent
from simulation.cards import CREATURE, INSTANT, LAND, Card, CostReduction
from simulation.game import Game
from simulation.mana import effective_mana_cost
from simulation.manacost import parse

Kind = DerivedProfile.Kind

# --- the reader -----------------------------------------------------------------


@pytest.mark.parametrize("text, x, discards, unread", [
    ("Target player draws X cards.", True, 0, False),
    ("Draw X cards, then discard a card.", True, 1, False),
    # Discarding X too is not something the engine can play yet.
    ("Draw X cards, then discard X cards. Create a 1/1 white Spirit creature token "
     "with flying for each card type among cards discarded this way.", False, 0, True),
    # Half X is a creature's cast trigger; the engine reads no permanent's draw.
    ("When you cast this spell, you gain half X life and draw half X cards.", False, 0, False),
])
def test_a_draw_of_x_is_read(text, x, discards, unread):
    draw = _draw(text)
    assert (draw.x, draw.discards, draw.x_unread) == (x, discards, unread)


def card(name, cost, text, type_line="Sorcery"):
    return OracleCard(name=name, front_name=name, type_line=type_line, mana_cost=cost,
                      cmc=0, oracle_text=text)


@pytest.mark.django_db
def test_an_x_no_longer_needs_anybody_by_itself():
    ballista = derive(card("Walking Ballista", "{X}{X}",
                           "This creature enters with X +1/+1 counters on it.",
                           "Artifact Creature — Construct"), set())
    assert ballista.has_x and not ballista.needs_review, ballista.review_reasons


@pytest.mark.django_db
def test_an_x_that_makes_lands_stays_a_gap():
    awakening = derive(card("Animist's Awakening", "{X}{G}",
                            "Reveal the top X cards of your library. Put all land cards from "
                            "among them onto the battlefield tapped and the rest on the bottom "
                            "of your library in a random order."), set())
    assert any("by X" in reason for reason in awakening.review_reasons)


# --- the engine -----------------------------------------------------------------


ISLAND = Card("Island", 0, 0, 0, LAND, subtypes=frozenset({"island"}))
STROKE = Card("Stroke of Genius", 3, 0, 2, INSTANT, cost=parse("{X}{2}{U}"), x_count=1,
              draws_x=True)
CORNUCOPIA = Card("Astral Cornucopia", 0, 0, 0, "artifact", cost=parse("{X}{X}{X}"),
                  x_count=3)
BEAR = Card("Bear", 2, 0, 1, CREATURE, cost=parse("{1}{U}"))
FILLER = Card("Filler", 9, 0, 9, CREATURE, cost=parse("{9}"))


def game_with(lands, hand, library=()):
    game = Game(random.Random(1))
    game.lands, game.hand, game.library = list(lands), list(hand), list(library)
    game.tapped_lands = 0
    return game


def test_x_is_generic_mana_for_each_x_and_reduced_like_it():
    assert str(effective_mana_cost(STROKE, x=4)) == "{6}{U}"
    assert str(effective_mana_cost(CORNUCOPIA, x=2)) == "{6}"
    medallion = Card("Medallion", 2, 0, 2, "artifact", cost=parse("{2}"),
                     cost_reduction=CostReduction(amount=1, color="U"))
    game = game_with([ISLAND] * 4, [STROKE])
    game.other_permanents.append(medallion)
    actions.OpenMainPhase().run(game, None)
    assert game.max_x(STROKE, game.pool) == 2  # {2}{U} - {1} = {1}{U}, two left


def test_the_largest_x_the_pool_pays():
    game = game_with([ISLAND] * 6, [STROKE, CORNUCOPIA])
    actions.OpenMainPhase().run(game, None)
    assert game.max_x(STROKE, game.pool) == 3
    assert game.max_x(CORNUCOPIA, game.pool) == 2


def test_an_x_spell_waits_for_its_minimum():
    game = game_with([ISLAND] * 3, [STROKE])
    actions.OpenMainPhase().run(game, None)
    assert not game.can_cast(STROKE, game.pool), "X = 0 is not worth a card"
    patient = Card("Stroke of Genius", 3, 0, 2, INSTANT, cost=parse("{X}{2}{U}"),
                   x_count=1, draws_x=True, x_min=4)
    game = game_with([ISLAND] * 6, [patient])
    actions.OpenMainPhase().run(game, None)
    assert not game.can_cast(patient, game.pool)


def test_the_agent_casts_everything_else_first_then_x_with_the_rest():
    game = game_with([ISLAND] * 6, [STROKE, BEAR], library=[FILLER] * 5)
    actions.OpenMainPhase().run(game, None)
    pool = game.pool
    while agent._cast_best(game, pool):
        pass
    assert BEAR in game.creatures, "the Bear first"
    assert STROKE in game.graveyard
    assert len(game.hand) == 1, "X = 1 after the Bear's two: one card drawn"


def test_a_playtest_recorded_before_x_replays_x_as_zero():
    game = game_with([ISLAND] * 3, [STROKE], library=[FILLER] * 3)
    actions.OpenMainPhase().run(game, None)
    actions.CastSpell(index=0).run(game, None)
    assert game.hand == [] and game.pool.total == 0


def test_the_board_posts_x_and_the_engine_checks_it():
    form = ActionForm({"kind": "cast_spell", "index": 0, "x": 2})
    assert form.is_valid(), form.errors
    action = form.action()
    assert action == actions.CastSpell(index=0, x=2)

    game = game_with([ISLAND] * 5, [STROKE], library=[FILLER] * 5)
    actions.OpenMainPhase().run(game, None)
    action.run(game, None)
    assert len(game.hand) == 2
    game = game_with([ISLAND] * 4, [STROKE], library=[FILLER] * 5)
    actions.OpenMainPhase().run(game, None)
    with pytest.raises(actions.IllegalAction):
        action.run(game, None)
