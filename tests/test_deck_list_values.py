"""Paket 2, PR 9 (Themen-Doc row 13): three values per deck in "Your decks".

What matters:

1. **Colour identity** in WUBRG order, the commanders' and every card's, as the
   deck page counts it; colourless when there is none.
2. **The bracket as the tile says it**: the Game Changers' floor, and with a
   finished run the speed together with it - the same number on both pages.
3. **The last simulation's date**, a link to that run; a run that has not
   finished is not the last simulation.
4. **No query per deck**: more decks with runs, not more queries.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from decks import analysis as deck_analysis
from decks import services as deck_services
from decks.models import DeckCard
from decks.views import deck_values
from simulation import analysis
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()
ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="values@example.com", password="pw-for-test-only")


def _deck(owner, name="Chainer"):
    return deck_services.import_deck(
        owner=owner, raw=ARCHIDEKT_CSV.read_bytes(), name=name, filename="sample.csv"
    ).deck


def _finished(owner, deck, status=SimulationRun.Status.DONE):
    result = analysis.as_json(analysis.run(40, turns=9, seed=3))
    return SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=40, games_done=40, turns=9, seed=3,
        status=status, finished_at=timezone.now(), result=result,
    )


def _values(body, deck):
    """The values row of `deck` in the list."""
    row = body[body.index(deck.get_absolute_url()):]
    return row[row.index("data-deck-values"):row.index("</p>", row.index("data-deck-values"))]


def _card(name, colours, changer=False):
    return SimpleNamespace(name=name, color_identity=colours, game_changer=changer)


def _bare(cards, commanders=()):
    entries = [SimpleNamespace(oracle_card=card) for card in cards]
    return SimpleNamespace(entries=SimpleNamespace(all=lambda: entries),
                           commanders=list(commanders))


def test_identity_is_wubrg_order_from_commanders_and_cards():
    deck = _bare([_card("A", ["G"]), _card("B", ["W", "B"])], [_card("C", ["U"])])

    deck_values(deck, None)

    assert [letter for letter, _name in deck.identity] == ["W", "U", "B", "G"]
    assert deck.identity[0][1] == "White"


def test_a_deck_without_colours_is_colourless():
    deck = _bare([_card("Sol Ring", [])])

    deck_values(deck, None)

    assert deck.identity == [("C", "Colourless")]


def test_game_changers_raise_the_floor_without_a_run():
    deck = _bare([_card("A", [], changer=True)], [_card("C", [], changer=True)])

    deck_values(deck, None)

    assert deck.floor == 3
    assert deck.speed is None and deck.last_run is None


def test_the_list_shows_identity_bracket_and_not_simulated(client, owner):
    deck = _deck(owner)
    client.force_login(owner)

    values = _values(client.get(reverse("decks:list")).content.decode(), deck)

    expected = [c for c in "WUBRG" if c in deck_analysis.analyse(deck).color_identity]
    assert re.findall(r'class="pip pip-(\w)"', values) == expected
    assert "Colour identity" in values, "the pips are read out in words"
    floor = deck_analysis.analyse(deck).bracket_floor
    assert f"Bracket {'1–2' if floor == 1 else f'{floor}+'}" in values
    assert "not simulated yet" in values


def test_a_game_changer_shows_on_the_list_as_on_the_tile(client, owner):
    deck = _deck(owner)
    entry = DeckCard.objects.filter(deck=deck).select_related("oracle_card").first()
    type(entry.oracle_card).objects.filter(pk=entry.oracle_card.pk).update(game_changer=True)
    client.force_login(owner)

    values = _values(client.get(reverse("decks:list")).content.decode(), deck)

    assert "Bracket 3+" in values


def test_with_a_run_the_bracket_matches_the_tile_and_the_date_links_to_it(client, owner):
    deck = _deck(owner)
    run = _finished(owner, deck)
    client.force_login(owner)

    values = _values(client.get(reverse("decks:list")).content.decode(), deck)
    page = client.get(deck.get_absolute_url()).content.decode()
    tile = re.search(r"data-bracket-floor>\s*([^<]+?)\s*</p>", page).group(1)

    assert f"Bracket {tile}" in values
    assert run.get_absolute_url() in values
    assert "simulated " in values and "not simulated" not in values


def test_an_unfinished_run_is_not_the_last_simulation(client, owner):
    deck = _deck(owner)
    _finished(owner, deck, status=SimulationRun.Status.RUNNING)
    client.force_login(owner)

    values = _values(client.get(reverse("decks:list")).content.decode(), deck)

    assert "not simulated yet" in values


def _count(client):
    client.get(reverse("decks:list"))  # stores the open-question counts once
    with CaptureQueriesContext(connection) as queries:
        client.get(reverse("decks:list"))
    return queries


def test_more_decks_with_runs_do_not_mean_more_queries(client, owner):
    client.force_login(owner)
    _finished(owner, _deck(owner, "One"))
    one = _count(client)

    _finished(owner, _deck(owner, "Two"))
    _deck(owner, "Three")  # the free plan's third deck, never simulated
    three = _count(client)

    assert len(three) == len(one), "\n".join(q["sql"][:160] for q in three.captured_queries)
