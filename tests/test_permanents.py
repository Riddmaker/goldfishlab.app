"""Card model phase 4, PR 12 and 17: permanents, and whose state is whose.

No Django, like the engine.
"""

import json
import random

import pytest

from simulation import agent, serial
from simulation.cards import CREATURE, Card
from simulation.fixtures import chainer
from simulation.game import Game
from simulation.permanent import Zone, ident, same
from tests.engine_builders import FILLER, game_with

BEAR = Card("Bear", 2, 1, 1, CREATURE, types=frozenset({"creature"}), power=2, toughness=2)
GERM = Card("Germ", 0, 0, 0, CREATURE, types=frozenset({"creature"}), token=True)


def played(seed: int = 4, turns: int = 5) -> Game:
    game = Game(random.Random(seed), deck=chainer.DECK)
    game.take_opening_hand()
    for _ in range(turns):
        agent.take_turn(game)
    return game


def test_one_permanent_is_one_object():
    copy = BEAR.twin()
    assert copy == BEAR and hash(copy) == hash(BEAR)
    assert same(BEAR, BEAR) and not same(BEAR, copy)
    assert ident(BEAR) != ident(copy)


def test_each_copy_in_a_library_is_its_own_object():
    library = chainer.DECK.shuffled(random.Random(1))
    assert sorted(card.name for card in library) == \
        sorted(card.name for card in chainer.DECK.library)
    assert len({id(card) for card in library}) == len(library)


def test_every_battlefield_list_is_a_zone_whatever_it_is_given():
    game = game_with(lands=[FILLER], creatures=[BEAR, BEAR])
    assert isinstance(game.lands, Zone) and isinstance(game.creatures, Zone)
    assert game.creatures.in_step()
    assert [permanent.card for permanent in game.creatures.permanents] == [BEAR, BEAR]
    # Copies are one card and two permanents.
    first, second = game.creatures.permanents
    assert first.pid != second.pid


@pytest.mark.parametrize("change", [
    lambda zone: zone.append(GERM),
    lambda zone: zone.extend([GERM, BEAR]),
    lambda zone: zone.insert(0, GERM),
    lambda zone: zone.pop(),
    lambda zone: zone.pop(0),
    lambda zone: zone.remove(BEAR),
    lambda zone: zone.clear(),
    lambda zone: zone.__delitem__(0),
    lambda zone: zone.__setitem__(0, GERM),
    lambda zone: zone.__setitem__(slice(0, 1), [GERM, GERM]),
    lambda zone: zone.sort(key=lambda card: card.name),
    lambda zone: zone.reverse(),
    lambda zone: zone.move(1, 0),
])
def test_every_change_keeps_the_permanents_in_step(change):
    game = game_with(creatures=[BEAR, FILLER, BEAR])
    change(game.creatures)
    assert game.creatures.in_step()


def test_a_card_moved_inside_its_zone_stays_the_same_permanent():
    game = game_with(lands=[FILLER, BEAR])
    moved = game.lands.permanents[1]
    game.lands.move(1, 0)
    assert game.lands.permanents[0] is moved
    game.lands.append(BEAR)
    assert game.lands.permanents[-1].pid > moved.pid


def test_a_slice_of_a_zone_is_a_plain_list():
    game = game_with(lands=[FILLER, BEAR])
    assert type(game.lands[:1]) is list
    assert type(game.lands + game.rocks) is list


def test_the_permanents_stay_in_step_through_whole_games():
    for seed in range(5):
        game = Game(random.Random(seed), deck=chainer.DECK)
        game.take_opening_hand()
        for _ in range(8):
            agent.take_turn(game)
            for zone in (game.lands, game.rocks, game.creatures, game.other_permanents):
                assert zone.in_step()
            pids = [permanent.pid for zone in (game.lands, game.rocks, game.creatures,
                                               game.other_permanents)
                    for permanent in zone.permanents]
            assert len(pids) == len(set(pids))


# --- marks ------------------------------------------------------------------------


def test_marks_are_kept_by_permanent_number():
    game = game_with(creatures=[BEAR, GERM])
    bear, germ = (permanent.pid for permanent in game.creatures.permanents)
    game.mark_arrived(BEAR)
    game.tap_as_fodder(GERM)
    game.set_plus(BEAR, 2)
    game.attach(GERM, BEAR)
    assert game.arrived == [bear] and game.arrived_now(BEAR)
    assert game.creature_tapped(GERM) and game.fodder_tapped(GERM)
    assert not game.busy()[germ]
    assert game.plus_counters == {bear: 2} and game.plus(BEAR) == 2
    assert game.carries(BEAR) and game.is_host_of(BEAR, GERM)
    game.set_plus(BEAR, 0)
    assert game.plus_counters == {}
    game.unattach(GERM)
    assert not game.carries(BEAR)


def test_two_germs_keep_their_own_counters_and_equipment():
    other = GERM.twin()
    game = game_with(creatures=[GERM, other], others=[FILLER])
    game.set_plus(GERM, 3)
    game.attach(FILLER, other)
    assert game.plus(GERM) == 3 and game.plus(other) == 0
    assert game.carries(other) and not game.carries(GERM)
    assert game.host(FILLER) is other


def test_a_card_that_comes_back_is_a_new_permanent():
    game = game_with(creatures=[BEAR])
    game.mark_arrived(BEAR)
    game.tap_creature(BEAR)
    game.creatures.remove(BEAR)
    # Leaving, it keeps its number: what happens as it leaves finds its state.
    assert game.creature_tapped(BEAR)
    game.creatures.append(BEAR)
    assert not game.creature_tapped(BEAR) and not game.arrived_now(BEAR)


def test_a_card_never_on_the_battlefield_has_no_marks_to_write():
    game = game_with(creatures=[])
    assert not game.arrived_now(BEAR) and game.plus(BEAR) == 0
    with pytest.raises(ValueError):
        game.mark_arrived(BEAR)


# --- saved games -----------------------------------------------------


def restored(game: Game) -> Game:
    return serial.load_game(json.loads(json.dumps(serial.dump_game(game))), game.deck)


def test_a_saved_game_keeps_each_card_once_and_each_permanent_number():
    game = played()
    data = serial.dump_game(game)
    assert data["format"] == serial.GAME_FORMAT
    objects = {id(card) for name in (*serial.GAME_ZONES, *serial.GAME_LISTS)
               for card in getattr(game, name)}
    assert len(data["cards"]) == len(objects)
    back = restored(game)
    for name in serial.BATTLEFIELD:
        zone, again = getattr(game, name), getattr(back, name)
        assert [permanent.pid for permanent in again.permanents] == \
            [permanent.pid for permanent in zone.permanents]
        assert again.in_step()
    assert back.pids == game.pids
    assert back.arrived == game.arrived and back.attached == game.attached


def test_two_equal_tokens_come_back_as_two_objects():
    game = game_with(deck=chainer.DECK, creatures=[GERM,
                     Card("Germ", 0, 0, 0, CREATURE, types=frozenset({"creature"}), token=True)])
    back = restored(game)
    assert back.creatures[0] == back.creatures[1]
    assert back.creatures[0] is not back.creatures[1]


@pytest.mark.parametrize("older", [None, 1, 2])
def test_a_state_in_an_older_form_is_not_current_and_is_not_loaded(older):
    data = json.loads(json.dumps(serial.dump_game(played())))
    data.pop("format")
    if older is not None:
        data["format"] = older
    assert not serial.is_current(data)
    with pytest.raises(ValueError):
        serial.load_game(data, chainer.DECK)
