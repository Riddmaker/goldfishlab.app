"""Card model phase 4: a game as one record per turn (`simulation.trace`).

No Django, like the engine.
"""

import random

from simulation import analysis, trace
from simulation.fixtures import chainer
from simulation.mana import Stored
from tests.engine_builders import FILLER, game_with, put


def test_a_trace_changes_nothing_the_game_measures():
    records = []
    traced = analysis.simulate_game(random.Random(5), turns=4, trace=records)
    plain = analysis.simulate_game(random.Random(5), turns=4)
    assert traced == plain
    # The opening hand, then one record per turn.
    assert [state["turn"] for state in records] == [0, 1, 2, 3, 4]


def test_each_record_holds_its_own_turns_log_lines():
    records = trace.play(chainer.DECK, 0, 3)
    assert records[1]["log"][0] == "--- Turn 1 ---"
    assert all(line not in records[1]["log"] for line in records[2]["log"]
               if line.startswith("---"))


def test_the_same_game_gives_the_same_digests():
    once, again = trace.play(chainer.DECK, 3, 4), trace.play(chainer.DECK, 3, 4)
    assert trace.digests(once) == trace.digests(again)
    assert len(trace.digests(once)["game"]) == trace.GAME_DIGEST
    assert trace.digests(trace.play(chainer.DECK, 4, 4)) != trace.digests(once)


def test_games_alternate_between_the_play_and_the_draw():
    # On the play there is no draw on the first turn; on the draw there is.
    play, draw = trace.play(chainer.DECK, 0, 1), trace.play(chainer.DECK, 1, 1)
    assert len(play[1]["drawn"]) == len(play[0]["drawn"])
    assert len(draw[1]["drawn"]) == len(draw[0]["drawn"]) + 1
    assert trace.game_seed("Chainer", 0) != trace.game_seed("Chainer", 1)


def test_a_difference_is_named_by_its_field():
    game = game_with(lands=[FILLER])
    before = trace.record(game)
    put(game, "graveyard", FILLER)
    game.treasures = [Stored("B")]
    assert trace.differences(before, trace.record(game)) == ["graveyard", "treasures"]
    assert trace.record(game)["treasures"] == ["stored:B"]


def test_the_builder_takes_only_the_zones_it_knows():
    game = game_with(lands=[FILLER], others=[FILLER], tapped_lands=1)
    assert (game.lands, game.other_permanents, game.tapped_lands) == ([FILLER], [FILLER], 1)
    assert game.library == []
    try:
        game_with(sideboard=[FILLER])
    except TypeError as error:
        assert "sideboard" in str(error)
    else:
        raise AssertionError("an unknown zone was taken")
