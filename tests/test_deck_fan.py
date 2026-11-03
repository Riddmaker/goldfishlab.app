"""Themen-Doc, Deckübersicht: the commander with three cards fanned out behind it.

What matters:

1. **The same three every visit**: the most expensive spells, lands never,
   the commander never (it has its own slot), the name breaking ties.
2. **No commander, still a fan**: the slot stays, empty.
3. **Small pictures**: Scryfall's small size, loaded lazily.
4. **One query for the whole list**: more decks, not more queries.
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from decks import services as deck_services
from decks.models import ART_HOST, DeckCard, small_image
from decks.views import FAN_SIZE, fan_cards

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="fan@example.com", password="pw-for-test-only")


def _deck(owner, name="Chainer"):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name=name, filename="sample.csv"
    ).deck


def _expected(deck):
    spells = [entry.oracle_card for entry in
              DeckCard.objects.filter(deck=deck).select_related("oracle_card")
              if not entry.oracle_card.is_land]
    spells.sort(key=lambda card: (-card.cmc, card.name))
    return [card.name for card in spells[:FAN_SIZE]]


def test_the_fan_is_the_three_most_expensive_spells(owner):
    deck = _deck(owner)

    names = [name for name, _image in fan_cards([deck])[deck.pk]]

    assert names == _expected(deck)
    assert len(names) == FAN_SIZE
    assert "Swamp" not in names
    assert deck.commander.name not in names


def test_small_image_swaps_only_scryfalls_normal_size():
    normal = ART_HOST + "normal/front/a/b/ab.jpg?1"

    assert small_image(normal) == ART_HOST + "small/front/a/b/ab.jpg?1"
    assert small_image("https://example.com/normal/x.jpg") == "https://example.com/normal/x.jpg"
    assert small_image("") == ""


def test_the_list_shows_the_commander_and_the_fan(client, owner):
    deck = _deck(owner)
    client.force_login(owner)

    body = client.get(reverse("decks:list")).content.decode()
    fan = body[body.index("data-deck-fan"):body.index("</a>", body.index("data-deck-fan"))]

    assert fan.count("data-fan-card") == FAN_SIZE
    assert "data-fan-commander" in fan
    assert "deck-fan-empty" not in fan
    start = body.index("data-deck-fan")
    assert 'aria-hidden="true"' in body[start - 120:start]
    assert "/normal/" not in fan, "the list loads the small pictures"
    assert deck.get_absolute_url() in body


def test_without_a_commander_the_slot_stays_empty(client, owner):
    deck = _deck(owner)
    deck.commander = None
    deck.save(update_fields=["commander"])
    client.force_login(owner)

    body = client.get(reverse("decks:list")).content.decode()
    fan = body[body.index("data-deck-fan"):body.index("</a>", body.index("data-deck-fan"))]

    assert "deck-fan-empty" in fan
    assert fan.count("data-fan-card") == FAN_SIZE


def test_more_decks_do_not_mean_more_queries(client, owner):
    client.force_login(owner)
    _deck(owner, "One")
    client.get(reverse("decks:list"))  # stores the open-question counts once
    with CaptureQueriesContext(connection) as one:
        client.get(reverse("decks:list"))

    _deck(owner, "Two")
    _deck(owner, "Three")
    client.get(reverse("decks:list"))
    with CaptureQueriesContext(connection) as three:
        client.get(reverse("decks:list"))

    assert len(three) == len(one), "\n".join(q["sql"][:160] for q in three.captured_queries)
