"""The action set: the seam a human and the agent both drive.

These tests import no Django and touch no database, like the engine tests
beside them - but this file is **ours**, not vendored, so it is written in the
repository's own style rather than the original project's German.

The property worth protecting here is not any single action. It is that
`legal_actions` and `apply` agree: everything the first offers, the second can
carry out. The moment those drift, the playtest UI starts showing buttons that
raise on click, which is exactly the two-sources-of-truth problem the split was
meant to prevent.
"""

import random

import pytest

from simulation import actions, agent
from simulation.game import Game


def fresh(seed: int = 7) -> Game:
    """A game with an opening hand and nothing played."""
    game = Game(random.Random(seed))
    game.take_opening_hand()
    return game


def opened(seed: int = 7) -> Game:
    """A game on turn one with the main phase open and mana floating."""
    game = fresh(seed)
    actions.apply(game, actions.BeginTurn(), agent.POLICY)
    land = agent.choose_land(game)
    if land is not None:
        actions.apply(game, actions.PlayLand(index=game.hand.index(land)),
                      agent.POLICY)
    actions.apply(game, actions.OpenMainPhase(), agent.POLICY)
    return game


# --- The register ----------------------------------------------------------

def test_every_action_has_its_own_stored_kind():
    """Two actions sharing a `kind` would silently replay as each other."""
    kinds = [cls.kind for cls in actions.BY_KIND.values()]
    assert len(kinds) == len(set(kinds))


def test_the_register_covers_every_action_that_exists():
    """A new action nobody registered cannot be replayed out of the database."""
    defined = {
        value for value in vars(actions).values()
        if isinstance(value, type)
        and issubclass(value, actions.Action)
        and value is not actions.Action
    }
    assert defined == set(actions.BY_KIND.values())


@pytest.mark.parametrize("action", [
    actions.BeginTurn(),
    actions.PlayLand(index=3),
    actions.CastSpell(index=2),
    actions.MoveCard(from_zone=actions.LIBRARY, index=0, to_zone=actions.EXILED),
    actions.Draw(count=2),
    actions.SetLife(total=17),
    actions.TapPermanent(zone=actions.ROCKS),
])
def test_an_action_survives_the_round_trip_through_storage(action):
    """What goes into a row has to come back out as the same action."""
    assert actions.from_row(action.kind, action.payload()) == action


def test_an_unknown_kind_is_refused_rather_than_guessed():
    with pytest.raises(actions.IllegalAction):
        actions.from_row("teleport", {})


# --- What the rules allow --------------------------------------------------

def test_before_the_game_starts_the_only_choice_is_the_hand():
    game = fresh()
    assert actions.legal_actions(game) == [actions.Mulligan(), actions.KeepHand()]


def test_everything_offered_can_actually_be_carried_out():
    """The property the playtest UI rests on: no button that raises on click."""
    for seed in range(25):
        game = opened(seed)
        for action in actions.legal_actions(game):
            probe = opened(seed)
            actions.apply(probe, action, agent.POLICY)


def test_a_spell_the_pool_cannot_pay_for_is_never_offered():
    game = opened()
    offered = {a.index for a in actions.legal_actions(game)
               if isinstance(a, actions.CastSpell)}
    for index, card in enumerate(game.hand):
        assert (index in offered) == game.can_cast(card, game.pool)


def test_the_land_drop_disappears_once_it_is_used():
    game = opened()
    assert game.land_drop_used
    assert not [a for a in actions.legal_actions(game)
                if isinstance(a, actions.PlayLand)]


def test_nothing_can_be_cast_before_the_main_phase_opens():
    """A floating pool is not optional - a cast without one would be free."""
    game = fresh()
    actions.apply(game, actions.BeginTurn(), agent.POLICY)
    with pytest.raises(actions.IllegalAction):
        actions.apply(game, actions.CastSpell(index=0), agent.POLICY)


# --- Carrying an action out ------------------------------------------------

def test_playing_a_land_plays_the_one_the_index_names():
    game = fresh()
    actions.apply(game, actions.BeginTurn(), agent.POLICY)
    index = next(i for i, card in enumerate(game.hand) if card.is_land)
    wanted = game.hand[index]
    actions.apply(game, actions.PlayLand(index=index), agent.POLICY)
    assert wanted.name in {land.name for land in game.lands}


def test_an_index_outside_the_zone_says_which_zone_it_meant():
    game = fresh()
    with pytest.raises(actions.IllegalAction, match="hand"):
        actions.apply(game, actions.PlayLand(index=99), agent.POLICY)


def test_an_unknown_zone_is_refused():
    game = fresh()
    with pytest.raises(actions.IllegalAction, match="no such zone"):
        actions.apply(
            game,
            actions.MoveCard(from_zone="sideboard", index=0, to_zone=actions.HAND),
            agent.POLICY,
        )


def test_a_card_can_be_moved_anywhere_at_all():
    """Legality is advisory. In paper you pick the card up and put it down."""
    game = fresh()
    before = len(game.hand)
    actions.apply(
        game,
        actions.MoveCard(from_zone=actions.HAND, index=0,
                         to_zone=actions.CREATURES),
        agent.POLICY,
    )
    assert len(game.hand) == before - 1
    assert len(game.creatures) == 1


def test_tapping_never_taps_more_than_there_is():
    game = opened()
    for _ in range(len(game.lands) + 5):
        actions.apply(game, actions.TapPermanent(zone=actions.LANDS), agent.POLICY)
    assert game.tapped_lands == len(game.lands)


def test_a_zone_that_makes_no_mana_cannot_be_tapped():
    game = opened()
    with pytest.raises(actions.IllegalAction):
        actions.apply(game, actions.TapPermanent(zone=actions.GRAVEYARD),
                      agent.POLICY)


def test_setting_life_says_what_it_was_told():
    game = fresh()
    actions.apply(game, actions.SetLife(total=13), agent.POLICY)
    assert game.life == 13


# --- Phases ----------------------------------------------------------------

def test_advancing_from_nowhere_starts_the_turn():
    game = fresh()
    actions.apply(game, actions.AdvancePhase(), agent.POLICY)
    assert game.turn == 1
    assert game.phase == actions.DRAW


def test_advancing_far_enough_opens_the_main_phase_and_floats_mana():
    game = fresh()
    actions.apply(game, actions.AdvancePhase(), agent.POLICY)   # -> draw
    actions.apply(game, actions.AdvancePhase(), agent.POLICY)   # -> main1
    assert game.phase == actions.MAIN1
    assert game.pool is not None
    assert game.mana_available == game.pool.total


def test_the_turn_walks_to_its_end_and_starts_the_next_one():
    game = fresh()
    for _ in range(len(actions.PHASES)):
        actions.apply(game, actions.AdvancePhase(), agent.POLICY)
    assert game.phase == actions.END
    actions.apply(game, actions.AdvancePhase(), agent.POLICY)
    assert game.turn == 2
    assert game.phase == actions.DRAW


def test_every_phase_is_one_a_game_can_actually_be_in():
    """No decorative constants: a phase nothing reaches is a phase that lies."""
    game = fresh()
    seen = set()
    for _ in range(len(actions.PHASES) * 2):
        actions.apply(game, actions.AdvancePhase(), agent.POLICY)
        seen.add(game.phase)
    assert seen == set(actions.PHASES)


def test_mana_does_not_float_into_the_next_turn():
    """Tap out on turn one, spend nothing, and it must be gone by turn two.

    The agent could never show this: it opens a fresh pool at every main phase,
    so a pool that survived the turn was overwritten before anything could
    spend it. A human stepping through phases is the only way to reach it.
    """
    game = opened()
    assert game.pool is not None

    actions.apply(game, actions.BeginTurn(), agent.POLICY)
    assert game.pool is None

    with pytest.raises(actions.IllegalAction):
        actions.apply(game, actions.CastSpell(index=0), agent.POLICY)


def test_mana_still_floats_across_the_phases_of_one_turn():
    """The other half: it is one pool per turn, not one per phase."""
    game = opened()
    before = game.pool.by_color()
    for _ in range(2):                      # main1 -> combat -> main2
        actions.apply(game, actions.AdvancePhase(), agent.POLICY)
    assert game.phase == actions.MAIN2
    assert game.pool.by_color() == before


# --- The agent drives the same code ---------------------------------------

def test_the_agent_reaches_the_same_state_the_actions_do():
    """The correctness argument for the whole split, asserted directly."""
    by_agent = Game(random.Random(3))
    by_agent.take_opening_hand()
    agent.take_turn(by_agent)

    by_hand = Game(random.Random(3))
    by_hand.take_opening_hand()
    actions.apply(by_hand, actions.BeginTurn(), agent.POLICY)
    land = agent.choose_land(by_hand)
    if land is not None:
        actions.apply(by_hand, actions.PlayLand(index=by_hand.hand.index(land)),
                      agent.POLICY)
    actions.apply(by_hand, actions.OpenMainPhase(), agent.POLICY)
    while agent._cast_best(by_hand, by_hand.pool) or \
            agent._try_ritual_line(by_hand, by_hand.pool):
        pass
    actions.apply(by_hand, actions.EndStep(), agent.POLICY)

    assert by_hand.log == by_agent.log
    assert by_hand.life == by_agent.life
    assert by_hand.permanent_names == by_agent.permanent_names


def test_a_tutor_without_a_policy_still_finds_something():
    """The playtest asks a person; nothing may crash while nobody has answered."""
    game = opened()
    game.hand.append(next(c for c in game.library if c.tutor is not None))
    before = len(game.library)
    actions._apply_cast_effect(game, game.hand[-1], None)
    assert len(game.library) < before
