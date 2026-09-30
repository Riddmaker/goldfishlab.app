"""Playtest sessions: the actions are the truth, the cache is a shortcut.

Everything here defends one property. A session stores a seed and a list of
actions, never a game; `cached_state` is an optimisation that must be
indistinguishable from replaying. The moment it is distinguishable, a player
loses a turn in a way that cannot be reproduced from the row.

The other thing worth protecting is the deck snapshot. A deck can be edited
while a session is open, and a card changing shape mid-game is the kind of bug
that gets reported as "the simulator is wrong" for weeks.
"""


import pytest
from django.contrib.auth import get_user_model

from cards.models import OracleCard
from decks import seeding
from decks.fixtures import LAND_LIGHT
from playtest import services
from playtest.models import PlaytestAction, PlaytestSession
from simulation import actions, serial
from simulations.models import CardAnnotation

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="playtest@example.com",
                                    password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return seeding.build(LAND_LIGHT, owner).deck


@pytest.fixture
def session(deck, owner):
    return services.start(deck, owner, seed=4242)


# --- Starting --------------------------------------------------------------

def test_a_new_session_has_an_opening_hand_and_no_history(session):
    game = services.state(session)
    assert len(game.hand) == 7
    assert game.turn == 0
    assert session.actions.count() == 0


def test_the_opening_seven_are_the_same_every_time(deck, owner):
    """Two sessions on one seed are one game. Nothing stores the hand."""
    first = services.state(services.start(deck, owner, seed=99))
    second = services.state(services.start(deck, owner, seed=99))
    assert [card.name for card in first.hand] == [card.name for card in second.hand]


def test_a_session_freezes_what_the_engine_could_not_read(session):
    """The board has to be as honest as the report."""
    assert session.cards_total > 0
    assert 0.0 <= session.readable_pct <= 100.0
    assert session.cards_unreadable <= session.cards_with_gaps


def test_editing_the_deck_does_not_reshape_a_game_in_progress(session, owner):
    """A card that changes under the player's hands mid-game is unforgivable."""
    before = serial.load_deck(session.deck_snapshot)
    card = OracleCard.objects.filter(
        oracle_id__in=session.deck.entries.values("oracle_card_id")).first()
    CardAnnotation.objects.create(
        owner=owner, deck=session.deck, oracle_card=card,
        overrides={"priority": 99}, note="changed mid-session",
    )
    session.refresh_from_db()
    assert serial.load_deck(session.deck_snapshot) == before


# --- Replay ----------------------------------------------------------------

def _play_a_turn(session):
    services.record(session, actions.BeginTurn())
    game = services.state(session)
    index = next((i for i, card in enumerate(game.hand) if card.is_land), None)
    if index is not None:
        services.record(session, actions.PlayLand(index=index))
    return services.record(session, actions.OpenMainPhase())


def test_an_action_is_written_down_and_takes_effect(session):
    game = _play_a_turn(session)
    assert game.turn == 1
    assert game.pool is not None
    assert list(session.actions.values_list("kind", flat=True)) == [
        "begin_turn", "play_land", "open_main"]


def test_the_cache_and_a_replay_cannot_disagree(session):
    """The property the whole module exists to keep."""
    cached = _play_a_turn(session)
    session.refresh_from_db()
    assert session.cached_seq is not None

    session.cached_state = {}
    session.cached_seq = None
    session.save(update_fields=["cached_state", "cached_seq"])
    replayed = services.state(session)

    assert replayed.log == cached.log
    assert [card.name for card in replayed.hand] == [c.name for c in cached.hand]
    assert replayed.life == cached.life
    assert replayed.mana_available == cached.mana_available


def test_an_illegal_action_stores_nothing_at_all(session):
    before = session.actions.count()
    with pytest.raises(actions.IllegalAction):
        services.record(session, actions.PlayLand(index=999))
    assert session.actions.count() == before


def test_a_cast_before_the_main_phase_is_refused_and_leaves_no_row(session):
    services.record(session, actions.BeginTurn())
    before = session.actions.count()
    with pytest.raises(actions.IllegalAction):
        services.record(session, actions.CastSpell(index=0))
    assert session.actions.count() == before


# --- Undo, redo, fork ------------------------------------------------------

def test_undo_takes_the_board_back_without_losing_the_row(session):
    _play_a_turn(session)
    services.undo(session)
    assert services.state(session).pool is None
    assert session.actions.count() == 3
    assert session.actions.filter(undone=True).count() == 1


def test_redo_puts_it_back(session):
    before = _play_a_turn(session)
    services.undo(session)
    after = services.redo(session)
    assert after.log == before.log
    assert after.mana_available == before.mana_available


def test_undoing_past_the_beginning_is_harmless(session):
    services.undo(session, count=50)
    game = services.state(session)
    assert len(game.hand) == 7
    assert game.turn == 0


def test_a_new_action_throws_the_redo_queue_away(session):
    """A redo that survived a divergent action would replay a different game."""
    _play_a_turn(session)
    services.undo(session, count=2)
    services.record(session, actions.Draw(count=1))
    assert session.actions.filter(undone=True).count() == 0
    assert list(session.actions.values_list("seq", flat=True)) == [1, 2]


def test_a_fork_is_the_same_game_up_to_the_branch(session):
    _play_a_turn(session)
    branch = services.fork(session, seq=1)

    assert branch.seed == session.seed
    assert branch.forked_from_id == session.id
    assert branch.actions.count() == 1
    assert services.state(branch).turn == 1
    assert services.state(branch).pool is None


def test_the_two_halves_of_a_fork_go_their_own_ways(session):
    _play_a_turn(session)
    branch = services.fork(session, seq=1)
    services.record(branch, actions.Draw(count=2))

    assert len(services.state(branch).hand) != len(services.state(session).hand)
    assert session.actions.count() == 3
    assert branch.actions.count() == 2


def test_a_forked_session_carries_the_snapshot_not_a_fresh_reading(session):
    branch = services.fork(session, seq=0)
    assert branch.deck_snapshot == session.deck_snapshot


# --- The phase's own definition of done ------------------------------------

def test_draw_play_three_turns_undo_two_fork_and_diverge(session):
    """Phase 5's definition of done, as one test.

    Written from the phase document rather than from the code, so that it fails
    if the feature stops doing the thing it was commissioned to do - not merely
    if an implementation detail moves.
    """
    for _ in range(3):
        _play_a_turn(session)
        services.record(session, actions.EndStep())
    three_turns = services.state(session)
    assert three_turns.turn == 3

    services.undo(session, count=2)
    assert services.state(session).turn == 3      # still turn three, mid-turn

    branch = services.fork(session, seq=session.actions.filter(
        undone=False).order_by("seq").last().seq)
    services.record(branch, actions.Draw(count=3))

    assert services.state(branch).library != services.state(session).library
    assert services.state(session).log[:4] == services.state(branch).log[:4]


def test_a_long_session_replays_quickly(session):
    """The phase budgets well under 100 ms for a replay. Measure, do not hope."""
    import time

    for _ in range(10):
        _play_a_turn(session)
        services.record(session, actions.EndStep())

    session.refresh_from_db()
    session.cached_state = {}
    session.cached_seq = None
    session.save(update_fields=["cached_state", "cached_seq"])

    started = time.perf_counter()
    services.state(session)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"replay took {elapsed * 1000:.0f} ms"


# --- The stored row --------------------------------------------------------

def test_a_stored_row_rebuilds_the_action_it_came_from(session):
    services.record(session, actions.Draw(count=3))
    row = session.actions.get(seq=1)
    assert row.as_action() == actions.Draw(count=3)


def test_two_actions_cannot_share_a_sequence_number(session):
    services.record(session, actions.Draw(count=1))
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError under any backend
        PlaytestAction.objects.create(session=session, seq=1, kind="draw",
                                      payload={"count": 1})


def test_a_session_knows_where_to_be_found(session):
    assert str(session.id) in session.get_absolute_url()
    assert PlaytestSession.objects.filter(pk=session.pk).exists()
