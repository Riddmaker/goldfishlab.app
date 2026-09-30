"""Phase 9 F: the board as a card table - the opening, the fan, the mana.

What these tests hold:

1. **The opening is a decision.** Before the hand is kept the board offers
   Mulligan and Keep and nothing that starts the turn; once kept, the other
   way round. The engine has no "kept" flag, so this is read off the actions.
2. **A card is its own button.** Each card in hand is a real form posting the
   action it has - Play land for a land, Cast for the rest - so a click plays
   it with JavaScript off too.
3. **What the board says, it says where it is seen.** An htmx answer carries
   its own messages; a full page shows them once, not twice.
4. **Mana reads like Magic.** The pips come in the order of the colour pie.
"""


import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from decks import seeding
from decks.fixtures import LAND_LIGHT
from playtest import services, views
from simulation.manacost import COLORLESS, COLORS

pytestmark = pytest.mark.django_db

User = get_user_model()
HTMX = {"HX-Request": "true"}


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="table@example.com", password="pw-for-test-only")


@pytest.fixture
def session(owner):
    return services.start(seeding.build(LAND_LIGHT, owner).deck, owner, seed=4242)


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def act(client, session, headers=None, **data):
    return client.post(reverse("playtest:act", args=[session.id]), data, headers=headers)


def board(client, session):
    return client.get(session.get_absolute_url())


def _first_land(client, session):
    return next(tile for tile in board(client, session).context["hand"]
                if tile["card"].is_land)


# --- the opening -----------------------------------------------------------------

def test_a_fresh_hand_is_a_decision(signed_in, session):
    response = board(signed_in, session)
    body = response.content.decode()

    assert response.context["deciding"]
    assert 'value="mulligan"' in body
    assert 'value="keep_hand"' in body
    assert 'value="advance_phase"' not in body
    assert 'value="cast_spell"' not in body, "nothing is played before the hand is kept"


def test_a_kept_hand_starts_the_first_turn(signed_in, session):
    act(signed_in, session, kind="keep_hand")

    response = board(signed_in, session)
    body = response.content.decode()

    assert not response.context["deciding"]
    assert 'value="mulligan"' not in body
    assert "Start turn 1" in body


def test_mulligans_keep_the_decision_open_and_say_what_keeping_costs(signed_in, session):
    act(signed_in, session, kind="mulligan")
    act(signed_in, session, kind="mulligan")

    response = board(signed_in, session)

    assert response.context["deciding"]
    assert response.context["to_bottom"] == 1, "the first mulligan is free in Commander"
    assert "Keeping puts 1 card on the bottom" in response.content.decode()


def test_a_turn_begun_without_keeping_ends_the_decision(signed_in, session):
    """An old session, or a plain post: a begun turn is past the opening."""
    act(signed_in, session, kind="begin_turn")

    assert not board(signed_in, session).context["deciding"]


# --- a card is its own button ----------------------------------------------------

def test_every_card_in_hand_posts_the_action_it_has(signed_in, session):
    act(signed_in, session, kind="keep_hand")
    act(signed_in, session, kind="advance_phase")
    response = board(signed_in, session)
    body = response.content.decode()
    hand = response.context["hand"]
    lands = sum(1 for tile in hand if tile["card"].is_land)

    assert body.count('<button type="submit" class="hand-card') == len(hand)
    assert body.count('value="play_land"') == lands
    assert body.count('value="cast_spell"') == len(hand) - lands


def test_a_land_that_can_be_played_glows_and_lands_on_the_battlefield(signed_in, session):
    act(signed_in, session, kind="keep_hand")
    act(signed_in, session, kind="advance_phase")
    land = _first_land(signed_in, session)
    assert land["playable"]

    act(signed_in, session, kind="play_land", index=land["index"])
    rows = {row["key"]: row for row in board(signed_in, session).context["battlefield"]}

    assert [tile["card"].name for tile in rows["lands"]["tiles"]] == [land["card"].name]


# --- messages ---------------------------------------------------------------------

def _unaffordable(client, session):
    act(client, session, kind="keep_hand")
    act(client, session, kind="begin_turn")
    act(client, session, kind="open_main")
    game = services.state(session)
    return next(index for index, card in enumerate(game.hand)
                if not card.is_land
                and not game.pool.can_pay_cost(card.mana_cost, life=game.life))


def test_an_htmx_answer_carries_its_own_message(signed_in, session):
    index = _unaffordable(signed_in, session)

    response = act(signed_in, session, headers=HTMX, kind="cast_spell", index=index)

    assert response.content.decode().count("the floating mana is") == 1


def test_a_full_page_shows_the_message_once(signed_in, session):
    index = _unaffordable(signed_in, session)

    response = act(signed_in, session, kind="cast_spell", index=index)
    body = signed_in.get(response["Location"]).content.decode()

    assert body.count("the floating mana is") == 1


# --- mana ---------------------------------------------------------------------------

class _Pool:
    """Just enough of a `ManaPool` for `pips`: what it holds, by colour."""

    def __init__(self, held):
        self.held = held

    def by_color(self):
        return self.held


def test_pips_follow_the_colour_pie_and_leave_out_nothing_held():
    pool = _Pool({COLORLESS: 2, "G": 1, "W": 3})

    assert views.pips(pool) == [("W", 3), ("G", 1), (COLORLESS, 2)]
    assert views.pips(None) == []
    assert views.PIP_ORDER == (*COLORS, COLORLESS)


def test_the_main_phase_shows_the_pool_as_pips(signed_in, session):
    act(signed_in, session, kind="keep_hand")
    act(signed_in, session, kind="begin_turn")
    land = _first_land(signed_in, session)
    act(signed_in, session, kind="play_land", index=land["index"])
    act(signed_in, session, kind="open_main")

    response = board(signed_in, session)

    assert response.context["pips"]
    assert 'class="pip pip-' in response.content.decode()
