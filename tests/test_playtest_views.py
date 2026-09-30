"""The playtest board over HTTP.

The rule this file exists to hold: **every action works with JavaScript off.**
Each one is a real form post, and the only difference htmx makes is that the
answer is a fragment instead of a redirect. So every action is exercised twice
here - once as a plain POST and once with `HX-Request` - and if the two ever
stop agreeing about what happened, one of them is a code path nobody tests.

The other rule is the one every view in this application follows: a session
belonging to somebody else is a 404, never a 403 and never a page.
"""

import random

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from decks import seeding
from decks.fixtures import LAND_LIGHT, UNMODELLABLE
from playtest import services
from playtest.models import PlaytestSession
from simulation import actions
from simulation.game import Game

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "pw-for-test-only"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="board@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    return seeding.build(LAND_LIGHT, owner).deck


@pytest.fixture
def session(deck, owner):
    return services.start(deck, owner, seed=4242)


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def act(client, session, **data):
    return client.post(reverse("playtest:act", args=[session.id]), data)


# --- Reaching the board ----------------------------------------------------

def test_the_board_renders_for_its_owner(signed_in, session):
    response = signed_in.get(session.get_absolute_url())
    assert response.status_code == 200
    assert b"Playtest" in response.content


def test_somebody_elses_game_is_simply_not_there(client, session):
    intruder = User.objects.create_user(email="nosy@example.com", password=PASSWORD)
    client.force_login(intruder)
    assert client.get(session.get_absolute_url()).status_code == 404


def test_a_signed_out_visitor_is_sent_to_sign_in(client, session):
    response = client.get(session.get_absolute_url())
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_a_deck_page_offers_a_game(signed_in, deck):
    response = signed_in.get(deck.get_absolute_url())
    assert reverse("playtest:start", args=[deck.id]).encode() in response.content


def test_dealing_a_hand_opens_a_session(signed_in, deck):
    response = signed_in.post(reverse("playtest:start", args=[deck.id]),
                              {"on_the_play": "on"})
    session = PlaytestSession.objects.get(deck=deck)
    assert response.status_code == 302
    assert response["Location"] == session.get_absolute_url()
    assert len(services.state(session).hand) == 7


# --- With JavaScript off ---------------------------------------------------

def test_an_action_posted_plainly_redirects_back_to_the_board(signed_in, session):
    response = act(signed_in, session, kind="begin_turn")
    assert response.status_code == 302
    assert response["Location"] == session.get_absolute_url()
    assert services.state(session).turn == 1


def test_the_same_action_with_htmx_answers_with_the_board_itself(signed_in, session):
    response = signed_in.post(
        reverse("playtest:act", args=[session.id]),
        {"kind": "begin_turn"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b'id="board"' in response.content
    assert b"<html" not in response.content


def test_both_paths_reach_the_same_game(signed_in, deck, owner):
    """Two sessions, one seed, the same actions - one with htmx and one without."""
    plain = services.start(deck, owner, seed=77)
    enhanced = services.start(deck, owner, seed=77)

    for kind in ("begin_turn", "advance_phase"):
        act(signed_in, plain, kind=kind)
        signed_in.post(reverse("playtest:act", args=[enhanced.id]),
                       {"kind": kind}, headers={"HX-Request": "true"})

    assert services.state(plain).log == services.state(enhanced).log


# --- Undo, redo, fork over HTTP -------------------------------------------

def test_undo_and_redo_are_posts(signed_in, session):
    act(signed_in, session, kind="begin_turn")
    signed_in.post(reverse("playtest:undo", args=[session.id]))
    assert services.state(session).turn == 0
    signed_in.post(reverse("playtest:redo", args=[session.id]))
    assert services.state(session).turn == 1


def test_forking_makes_a_second_game_and_goes_to_it(signed_in, session):
    act(signed_in, session, kind="begin_turn")
    act(signed_in, session, kind="advance_phase")
    response = signed_in.post(reverse("playtest:fork", args=[session.id]),
                              {"seq": 1})
    branch = PlaytestSession.objects.get(forked_from=session)
    assert response["Location"] == branch.get_absolute_url()
    assert branch.actions.count() == 1


def test_a_fork_point_that_is_not_a_number_is_refused(signed_in, session):
    response = signed_in.post(reverse("playtest:fork", args=[session.id]),
                              {"seq": "the good bit"})
    assert response.status_code == 400


# --- What a form post may say ---------------------------------------------

def test_an_action_nobody_defined_is_refused(signed_in, session):
    assert act(signed_in, session, kind="drop_a_meteor").status_code == 400
    assert session.actions.count() == 0


def test_a_stray_field_cannot_become_an_argument(signed_in, session):
    """`Action(**payload)` straight off a form is how this would have broken."""
    act(signed_in, session, kind="draw", count=2, index=5, to_zone="exiled")
    assert session.actions.get(seq=1).payload == {"count": 2}


def test_a_zone_that_does_not_exist_is_refused_by_the_form(signed_in, session):
    response = act(signed_in, session, kind="move_card", from_zone="sideboard",
                   index=0, to_zone="hand")
    assert response.status_code == 400
    assert session.actions.count() == 0


def test_an_impossible_index_is_reported_not_stored(signed_in, session):
    response = act(signed_in, session, kind="play_land", index=400)
    assert response.status_code == 302
    assert session.actions.count() == 0


def test_a_draw_cannot_ask_for_the_whole_library(signed_in, session):
    assert act(signed_in, session, kind="draw", count=5000).status_code == 400


# --- Honesty ---------------------------------------------------------------

def test_a_deck_the_engine_cannot_read_says_so_on_the_board(signed_in, owner):
    """The same claim the deck page makes, made where the game is played."""
    deck = seeding.build(UNMODELLABLE, owner).deck
    session = services.start(deck, owner, seed=1)
    body = signed_in.get(session.get_absolute_url()).content.decode()
    assert "could not read" in body
    assert f"{session.cards_with_gaps}" in body
    assert reverse("simulations:review", args=[deck.id]) in body


def test_a_deck_the_engine_understands_does_not_shout(signed_in, session):
    body = signed_in.get(session.get_absolute_url()).content.decode()
    if not session.cards_with_gaps:
        assert "could not read" not in body


# --- Every action kind is reachable from the board ------------------------

def test_every_action_the_board_can_post_is_one_the_engine_knows(signed_in, session):
    """Guards against a template posting a `kind` that no longer exists."""
    board = signed_in.get(session.get_absolute_url()).content.decode()
    posted = {line.split('value="')[1].split('"')[0]
              for line in board.splitlines()
              if 'name="kind"' in line and 'value="' in line}
    assert posted
    assert posted <= set(actions.BY_KIND)


# --- A cast nobody can pay for ---------------------------------------------

def test_casting_a_card_the_pool_cannot_pay_for_is_a_message_not_a_500(signed_in, session):
    """The board puts a Cast button on every card, affordable or not.

    Before the 2026-09-25 review that button raised `ValueError` from
    `Game.cast` for a card the floating mana could not pay, which is an HTTP
    500 - and htmx swaps nothing on a 500, so the click simply did nothing.
    """
    act(signed_in, session, kind="begin_turn")
    act(signed_in, session, kind="open_main")
    game = services.state(session)
    expensive = next(
        (index for index, card in enumerate(game.hand)
         if not card.is_land and not game.pool.can_pay_cost(card.mana_cost, life=game.life)),
        None,
    )
    assert expensive is not None, "the seeded hand should hold something unaffordable on turn 1"
    before = session.actions.count()

    response = signed_in.post(
        reverse("playtest:act", args=[session.id]),
        {"kind": "cast_spell", "index": expensive},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    # "costs": the message itself. "floating mana" alone was also in the old
    # hand's caption, so this test passed while the message went unseen.
    assert b"costs" in response.content
    assert session.actions.count() == before


def test_a_commander_the_pool_cannot_pay_for_is_refused_politely():
    game = Game(random.Random(1))
    actions.apply(game, actions.BeginTurn())
    actions.apply(game, actions.OpenMainPhase())
    with pytest.raises(actions.IllegalAction, match="with tax"):
        actions.apply(game, actions.CastCommander())
