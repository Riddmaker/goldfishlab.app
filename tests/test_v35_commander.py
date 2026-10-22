"""The commander is cast as soon as it can be paid (engine v35, P20b).

Until v34 the commander went through the default "cheaper first" priority, so
it waited behind every cheaper spell in hand: a three-colour four-drop was out
by turn 4 in a few percent of games. Now a commander with no priority of its
own outranks every default; a deck that ranks its commander keeps that order.
"""

import random

from simulation import actions, agent, build
from simulation.cards import CREATURE, LAND, SORCERY, Card, DeckDefinition
from simulation.game import Game
from simulation.manacost import parse
from simulations.engine.adapter import Reading
from tests.engine_builders import tap

FOREST = build.card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}), basic=True)
LEADER = Card("Leader", 3, 0, 2, CREATURE, cost=parse("{2}{G}"), types=frozenset({"creature"}),
              colors=frozenset({"G"}))
BEAR = Card("Bear", 2, 0, 2, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
            colors=frozenset({"G"}))
ELF = Card("Elf", 1, 0, 1, CREATURE, cost=parse("{G}"), types=frozenset({"creature"}),
           colors=frozenset({"G"}))
BLAZE = Card("Blaze", 1, 0, 0, SORCERY, cost=parse("{X}{G}"), x_count=1)


def game_with(*, lands, hand, commander=LEADER):
    game = Game(random.Random(1), deck=DeckDefinition("v35", commander, (FOREST,)))
    game.lands = list(lands)
    game.hand = list(hand)
    tap(game, lands=0)
    game.turn = 3
    actions.OpenMainPhase().run(game, None)
    return game


def test_a_commander_without_a_priority_outranks_every_default():
    assert agent.commander_priority(LEADER) == agent.COMMANDER_PRIORITY
    assert agent.COMMANDER_PRIORITY > agent.priority(ELF)
    assert agent.COMMANDER_PRIORITY > 85  # Dark Confidant, the highest built-in below fast mana
    ranked = build.card("Ranked", 3, 0, 2, CREATURE, cost=parse("{2}{G}"), priority=81)
    assert agent.commander_priority(ranked) == 81


def test_the_commander_comes_before_cheaper_spells():
    game = game_with(lands=[FOREST] * 3, hand=[ELF, BEAR])

    assert agent._cast_best(game, game.pool)
    assert game.has("Leader") and game.commander_casts == 1
    assert game.hand == [ELF, BEAR]


def test_what_is_left_after_the_commander_is_still_spent():
    game = game_with(lands=[FOREST] * 4, hand=[ELF, BEAR])

    while agent._cast_best(game, game.pool):
        pass

    assert game.has("Leader") and game.has("Elf")
    assert game.hand == [BEAR]


def test_a_spell_the_deck_ranks_higher_still_goes_first():
    necro = build.card("Necro", 3, 0, 0, SORCERY, cost=parse("{2}{G}"), priority=90)
    game = game_with(lands=[FOREST] * 3, hand=[necro])

    assert agent._cast_best(game, game.pool)
    assert not game.has("Leader") and necro not in game.hand


def test_a_commander_the_deck_ranks_keeps_that_rank():
    ranked = Card("Ranked", 3, 0, 2, CREATURE, cost=parse("{2}{G}"),
                  types=frozenset({"creature"}), colors=frozenset({"G"}), priority=30)
    game = game_with(lands=[FOREST] * 3, hand=[ELF], commander=ranked)

    assert agent._cast_best(game, game.pool)
    assert game.has("Elf") and not game.has("Ranked")


def test_an_x_spell_still_waits_for_the_commander():
    game = game_with(lands=[FOREST] * 4, hand=[BLAZE])

    assert agent._cast_best(game, game.pool)
    assert game.has("Leader") and BLAZE in game.hand


def test_over_many_games_the_commander_comes_down_on_curve():
    deck = DeckDefinition("Curve", LEADER, tuple([FOREST] * 38 + [ELF] * 31 + [BEAR] * 30))
    out = 0
    for seed in range(200):
        game = Game(random.Random(seed), deck=deck)
        game.take_opening_hand()
        for _ in range(3):
            agent.take_turn(game)
        out += game.has("Leader")

    # v34 cast the Elves and Bears first: 2 % here, against 90 % now.
    assert out / 200 > 0.8


def test_the_card_page_shows_the_commander_s_priority():
    assert Reading(oracle_card=None, card=LEADER, is_commander=True).effective_priority == \
        agent.COMMANDER_PRIORITY
    assert Reading(oracle_card=None, card=LEADER).effective_priority == agent.priority(LEADER)
