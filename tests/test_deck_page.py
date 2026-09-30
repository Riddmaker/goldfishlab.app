"""Phase 9 D: the deck page - actions first, tiles, bars, and the card grid.

What these tests are for, in order of how much they matter:

1. **The grid sorts a card the way the report does.** Types are printed,
   categories are `Card.categories` - so a role the owner replaced is where
   the owner put it, on the page and in the simulation alike.
2. **The filters cannot be abused.** A query is matched in Python and
   escaped on the way out; an unknown type or category is ignored; a long
   query is cut.
3. **htmx is an enhancement.** Its request gets the grid alone, every other
   request - a history restore included - the whole page, and the response
   says it varies on the header.
4. **Nothing was lost by folding it.** Legality, the engine's limits, the
   import match and the earlier runs are still on the page.
5. **Ownership.** Somebody else's deck is a 404, fragment or not.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cards import ingest, profiles
from decks import services as deck_services
from simulations import deck_cards, review, services
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE
from simulations.forms import RunForm

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIDEKT_CSV = FIXTURES / "archidekt_sample.csv"
VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def owner():
    ingest.ingest_cards(source=FIXTURES / "oracle_cards_sample.jsonl.gz", updated_at=VERSION)
    ingest.ingest_tags(source=FIXTURES / "oracle_tags_sample.jsonl.gz", updated_at=VERSION)
    profiles.rebuild()
    return User.objects.create_user(email="owner@example.com", password="pw-for-test-only")


@pytest.fixture
def deck(owner):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer", filename="sample.csv"
    ).deck


@pytest.fixture
def client_in(client, owner):
    client.force_login(owner)
    return client


def _grid(deck):
    readings = adapter.readings(deck)
    return deck_cards.cards(readings, review.queue(deck, readings))


def _names(response):
    return [card.name for card in response.context["grid"]]


# --- the grid sorts like the report ---------------------------------------------

def test_the_grid_holds_the_deck_without_its_commander(deck):
    grid = _grid(deck)

    assert {card.oracle_card.pk for card in grid} == set(
        deck.entries.exclude(oracle_card_id=deck.commander_id)
        .values_list("oracle_card_id", flat=True))


def test_a_type_filter_shows_only_that_type(client_in, deck):
    response = client_in.get(deck.get_absolute_url(), {"type": "creature"})

    shown = response.context["grid"]
    assert shown
    assert all("creature" in card.types for card in shown)
    assert len(shown) < response.context["grid_total"]


def test_a_category_the_owner_gave_a_card_is_where_the_grid_finds_it(client_in, deck):
    card = next(card for card in _grid(deck) if "counterspell" not in card.categories)
    services.save_annotation(deck=deck, oracle_card=card.oracle_card, scope=DECK_SCOPE,
                             judgements={"tags": ["counterspell"]})

    response = client_in.get(deck.get_absolute_url(), {"cat": "counterspell"})

    assert card.name in _names(response)


def test_the_bars_count_copies_and_always_show_every_category(deck):
    grid = _grid(deck)
    type_bars, category_bars = deck_cards.bars(grid)
    lands = next(bar for bar in type_bars if bar.value == "land")

    assert lands.count == sum(card.quantity for card in grid if "land" in card.types)
    assert [bar.value for bar in category_bars] == [key for key, _ in deck_cards.SEEN_ROLES]
    assert all(bar.count for bar in type_bars), "a type nobody plays has no row"


def test_the_template_band_is_labelled_and_inside_the_track(deck):
    _, category_bars = deck_cards.bars(_grid(deck))
    removal = next(bar for bar in category_bars if bar.value == "removal")

    assert removal.band_label == "8-10"
    left, width = removal.band
    assert 0 <= left and left + width <= 100


# --- the filters cannot be abused -------------------------------------------------

def test_search_reads_name_and_text_whatever_the_case(client_in, deck):
    card = next(card for card in _grid(deck) if card.oracle_card.oracle_text)
    word = max(card.oracle_card.oracle_text.split(), key=len).strip(".,:;()")

    by_text = client_in.get(deck.get_absolute_url(), {"q": word.upper()})
    by_name = client_in.get(deck.get_absolute_url(), {"q": card.name.lower()})

    assert card.name in _names(by_text)
    assert card.name in _names(by_name)


def test_a_hostile_query_is_escaped_and_cut(client_in, deck):
    hostile = "<script>alert(1)</script>" + "x" * 500

    response = client_in.get(deck.get_absolute_url(), {"q": hostile})
    body = response.content.decode()

    assert response.status_code == 200
    assert "<script>alert(1)" not in body
    assert len(response.context["filters"].query) == deck_cards.QUERY_MAX
    assert "No card matches" in body


def test_an_unknown_filter_is_ignored(client_in, deck):
    response = client_in.get(deck.get_absolute_url(), {"type": "'; DROP", "cat": "hug"})

    assert not response.context["filters"].active
    assert len(response.context["grid"]) == response.context["grid_total"]


# --- htmx is an enhancement ---------------------------------------------------------

def test_htmx_gets_the_grid_alone(client_in, deck):
    response = client_in.get(deck.get_absolute_url(), {"type": "land"}, **HTMX)
    body = response.content.decode()

    assert body.lstrip().startswith('<div id="card-grid">')
    assert "<html" not in body
    assert "HX-Request" in response["Vary"]


def test_a_history_restore_is_the_whole_page(client_in, deck):
    response = client_in.get(deck.get_absolute_url(), **HTMX,
                             HTTP_HX_HISTORY_RESTORE_REQUEST="true")

    assert "<html" in response.content.decode()


# --- the page ----------------------------------------------------------------------------

def test_the_actions_come_first(client_in, deck):
    body = client_in.get(deck.get_absolute_url()).content.decode()

    simulate = body.index(">\n            Simulate")
    assert simulate < body.index(">Mana curve</h2>")
    assert reverse("simulations:create", args=[deck.pk]) in body
    assert reverse("playtest:start", args=[deck.pk]) in body
    assert body.index("Options") < body.index('name="games"')


def test_simulate_without_opening_the_options_sends_a_valid_run(client_in, deck):
    """One click has to be enough: what the folded selects hold is a valid run.

    Starting the run itself (quota, queue) is `test_simulations_runs`' job.
    """
    form = client_in.get(deck.get_absolute_url()).context["run_form"]
    defaults = {name: field.initial for name, field in form.fields.items()}

    assert RunForm(data=defaults).is_valid()


def test_the_tiles_say_it_in_words(client_in, deck):
    response = client_in.get(deck.get_absolute_url())
    body = response.content.decode()
    analysis = response.context["analysis"]

    assert "Average mana value" in body
    assert str(analysis.average_mv) in body
    assert ("✓ in band" if analysis.lands_verdict.ok else "! outside band") in body
    problems = response.context["legality_problems"]
    assert (f"{problems} problem" if problems else "Legal") in body


def test_folded_is_not_gone(client_in, deck):
    body = client_in.get(deck.get_absolute_url()).content.decode()

    for kept in ("Commander legality", "Combos", "What the engine cannot model",
                 "rows matched", "Karsten"):
        assert kept in body


def test_a_deck_without_a_commander_asks_for_one_at_the_top(client_in, deck):
    type(deck).objects.filter(pk=deck.pk).update(commander=None)

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert body.index("commander-select") < body.index(">Mana curve</h2>")


def test_somebody_elses_deck_is_not_found_fragment_or_not(client, deck):
    stranger = User.objects.create_user(email="stranger@example.com", password="pw-x")
    client.force_login(stranger)

    assert client.get(deck.get_absolute_url()).status_code == 404
    assert client.get(deck.get_absolute_url(), **HTMX).status_code == 404


def test_the_methodology_page_names_the_template_and_its_land_band(client):
    body = client.get(reverse("methodology")).content.decode()

    low, high = deck_cards.TEMPLATE["type:land"]

    assert "not a rule" in body
    assert f"{low} to {high} lands" in body


def test_the_commander_picture_opens_its_card_page(client_in, deck):
    """Phase 9 I: the commander is not in the grid (not one of the 99), and
    the card list that did show it is gone - so its picture is the way in."""
    assert deck.commander.image_uri, "the fixture commander has no picture"

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert reverse("simulations:annotate", args=[deck.pk, deck.commander.pk]) in body
