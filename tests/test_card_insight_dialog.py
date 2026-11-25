"""More about this card (B-22, PR 3): the ⓘ in a playtest and in "Deck in hand".

What these tests are for:

1. **A card shown big leads to its insight**: each card in hand that is not a
   basic land has an ⓘ, named after the card, and the page knows its address.
2. **The ⓘ is never part of the card's own button**, so a click on the card
   still plays it.
3. **Only the deck's owner** gets the addresses, and they are the card page's
   own fragment, scoped by owner there too.
"""

import json
import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from decks import seeding
from decks.fixtures import LAND_LIGHT
from playtest import services
from simulations.views import insight_urls

pytestmark = pytest.mark.django_db
PASSWORD = "pw-for-test-only"
URLS = re.compile(r'<script id="card-insight-urls" type="application/json">(.*?)</script>', re.S)


@pytest.fixture
def owner(catalogue):
    return get_user_model().objects.create_user(email="dialog@example.com", password=PASSWORD)


@pytest.fixture
def deck(owner):
    return seeding.build(LAND_LIGHT, owner).deck


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


def _urls(body):
    return json.loads(URLS.search(body).group(1))


def test_every_card_but_a_basic_land_has_an_address_by_its_front_name(deck):
    urls = insight_urls(deck)
    cards = [entry.oracle_card for entry in deck.entries.select_related("oracle_card")]

    basics = {card.front_name for card in cards if "Basic" in card.type_line}
    others = {card.front_name for card in cards if "Basic" not in card.type_line}
    assert basics and others
    assert set(urls) == others | {card.front_name for card in deck.commanders}
    for card in cards:
        if card.front_name in urls:
            assert urls[card.front_name] == reverse("simulations:insight",
                                                    args=[deck.id, card.pk])


def test_a_card_in_hand_has_an_info_button_beside_its_own(signed_in, deck, owner):
    session = services.start(deck, owner, seed=4242)
    hand = services.state(session).hand

    body = signed_in.get(session.get_absolute_url()).content.decode()

    urls = _urls(body)
    named = set(re.findall(r'data-insight-for="([^"]+)"', body))
    assert named == {card.name for card in hand if card.name in urls}
    assert named, "the seed deals at least one card that is not a basic land"
    assert 'data-insight-dialog' in body and "js/card-insight.js" in body
    for button in re.findall(r'<button type="submit" class="hand-card.*?</button>', body, re.S):
        assert "data-insight-for" not in button


def test_the_board_after_a_move_keeps_the_buttons(signed_in, deck, owner):
    session = services.start(deck, owner, seed=4242)

    fragment = signed_in.post(reverse("playtest:act", args=[session.id]),
                              {"kind": "keep_hand"}, headers={"HX-Request": "true"})

    assert "data-insight-for" in fragment.content.decode()


def test_deck_in_hand_names_the_picked_card_for_the_dialog(signed_in, deck):
    body = signed_in.get(reverse("decks:hand", args=[deck.pk]) + "?seed=7&cut=0",
                         follow=True).content.decode()

    assert "data-in-hand-info" in body and "data-insight-dialog" in body
    assert _urls(body) == insight_urls(deck)


def test_a_stranger_gets_neither_page_nor_addresses(client, deck, owner):
    session = services.start(deck, owner, seed=4242)
    stranger = get_user_model().objects.create_user(email="s@example.com", password=PASSWORD)
    client.force_login(stranger)

    assert client.get(session.get_absolute_url()).status_code == 404
    assert client.get(reverse("decks:hand", args=[deck.pk]) + "?seed=7").status_code == 404
