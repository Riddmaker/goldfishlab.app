"""The stress test (engine v36): the commander removed, a board wipe.

Each scenario is played on a copy of a game taken at its moment, so the copy
and the game are one game until then, and a run with the stress test has
every other number exactly as a run without it.
"""

import copy
import random

from simulation import analysis, build
from simulation.cards import CREATURE, DIES, LAND, ROCK, Card, DeckDefinition, Trigger
from simulation.game import Game
from simulation.manacost import parse
from tests.engine_builders import game_with

FOREST = build.card("Forest", 0, 0, 0, LAND, subtypes=frozenset({"forest"}), basic=True)
LEADER = Card("Leader", 3, 0, 2, CREATURE, cost=parse("{2}{G}"), types=frozenset({"creature"}),
              colors=frozenset({"G"}))
BEAR = Card("Bear", 2, 0, 2, CREATURE, cost=parse("{1}{G}"), types=frozenset({"creature"}),
            colors=frozenset({"G"}))
SPROUT = Card("Sprout", 0, 0, 0, CREATURE, types=frozenset({"creature"}), token=True)
ROCKY = build.card("Rocky", 2, 0, 0, ROCK, cost=parse("{2}"))
ARTIST = build.card("Artist", 2, 0, 1, CREATURE, cost=parse("{1}{G}"),
                    types=frozenset({"creature"}), triggers=(Trigger(DIES, draw=1),))
DECK = DeckDefinition("Stress", LEADER, tuple([FOREST] * 38 + [BEAR] * 61))


def test_the_commander_goes_back_to_the_command_zone_and_costs_two_more():
    game = game_with(commander=LEADER, creatures=[LEADER, BEAR], lands=[FOREST] * 3)
    game.commander_casts = 1

    assert game.remove_commander()
    assert not game.has("Leader") and game.has("Bear")
    assert LEADER not in game.graveyard
    assert str(game._commander_cost(LEADER)) == "{4}{G}"
    assert not game.remove_commander()


def test_a_wipe_takes_every_creature_and_nothing_else():
    game = game_with(commander=LEADER, creatures=[LEADER, BEAR, SPROUT], lands=[FOREST] * 4,
                     rocks=[ROCKY])

    assert game.board_wipe() == 3
    assert list(game.creatures) == []
    assert game.graveyard == [BEAR]
    assert len(game.lands) == 4 and list(game.rocks) == [ROCKY]


def test_a_dies_trigger_sees_every_creature_of_the_wipe():
    game = game_with(commander=LEADER, creatures=[ARTIST, BEAR, SPROUT],
                     library=[FOREST] * 5)

    game.board_wipe()

    assert len(game.hand) == 3


def test_a_copied_game_plays_on_alone():
    game = Game(random.Random(3), deck=DECK)
    game.take_opening_hand()
    state = game.rng.getstate()
    copied = copy.deepcopy(game, {id(game.deck): game.deck})

    copied.rng.random()
    copied.creatures.append(BEAR)

    assert game.rng.getstate() == state
    assert BEAR not in game.creatures
    assert [p.pid for p in copied.lands.permanents] == [p.pid for p in game.lands.permanents]


def test_the_stress_test_changes_no_other_number():
    plain = analysis.as_json(analysis.run(60, turns=7, seed=5, deck=DECK))
    stressed = analysis.as_json(analysis.run(60, turns=7, seed=5, deck=DECK, stress=True))

    stress = stressed.pop("stress")
    assert stressed == plain
    assert set(stress) == {"removal", "wipe"}
    assert stress["wipe"]["games"] == 60
    assert 0 < stress["removal"]["games"] <= 60
    removal = stress["removal"]
    assert removal["struck"] == removal["games"] == sum(removal["back"].values())
    assert removal["hit"]["commander"] <= removal["base"]["commander"]


def test_a_run_too_short_for_a_wipe_has_none():
    stress = analysis.run(20, turns=5, seed=5, deck=DECK, stress=True)["stress"]

    assert stress["wipe"]["games"] == 0


def test_only_the_first_blocks_play_it_and_chunks_add_up():
    first = analysis.run_chunk(250, run_seed=9, first_block=0, turns=6, deck=DECK, stress=True)
    late = analysis.run_chunk(250, run_seed=9, first_block=analysis.STRESS_BLOCKS, turns=6,
                              deck=DECK, stress=True)
    assert "stress" not in late

    merged = analysis.merge([late, first])
    assert merged["stress"] == first["stress"]
    assert analysis.as_json(analysis.from_json(merged)) == merged
    twice = analysis.merge([first, first])
    assert twice["stress"]["wipe"]["games"] == 2 * first["stress"]["wipe"]["games"]
