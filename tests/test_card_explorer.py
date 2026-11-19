"""The card explorer (deck page v2, PR F).

What these tests hold:

1. **Chips are chosen together**: `?cat=ramp&cat=draw` shows every card that
   wears either, grouped under each, a card under every chip it wears; an
   unknown chip is ignored and a chip given twice counts once.
2. **The explorer's library is the grid**: copies and chips only, no
   commander, and the JSON it travels in cannot close its script tag.
3. **The page** gives the chips as switches (checkboxes) a plain form can
   send, the theme roles the deck has, and the explorer hidden until the
   script shows it.

The curve itself is the browser's (static/js/card-explorer.js); the browser
check compares it with the exact hypergeometric chance.
"""

import json
from types import SimpleNamespace

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.utils import translation

from simulations import deck_cards
from tests.test_deck_page import HTMX, _grid, client_in, deck, owner  # noqa: F401


def _filters(query):
    return deck_cards.Filters.from_request(QueryDict(query))


def _card(name, types=(), categories=(), quantity=1):
    oracle = SimpleNamespace(name=name, front_name=name, oracle_text="")
    card = SimpleNamespace(types=frozenset(types), categories=frozenset(categories))
    reading = SimpleNamespace(oracle_card=oracle, card=card, quantity=quantity, primary="",
                              gaps=())
    return deck_cards.GridCard(reading=reading, open=False, answered=False)


# --- the filters -------------------------------------------------------------------


def test_chips_are_read_in_the_page_s_order_each_once_unknown_ones_ignored():
    filters = _filters("cat=draw&cat=ramp&cat=ramp&cat=hug&type=land&type=x&cat=treasure")

    assert filters.kinds == ("land",)
    assert filters.categories == ("ramp", "draw", "treasure")
    assert filters.chosen == ("type:land", "role:ramp", "role:draw", "role:treasure")


def test_a_card_shows_when_it_wears_any_chosen_chip():
    grid = [_card("Sol Ring", ["artifact"], ["ramp"]), _card("Divination", ["sorcery"], ["draw"]),
            _card("Bear", ["creature"])]

    shown = deck_cards.shown(grid, _filters("cat=ramp&cat=draw"))

    assert [card.name for card in shown] == ["Sol Ring", "Divination"]


def test_the_search_still_narrows_the_chips():
    grid = [_card("Sol Ring", categories=["ramp"]), _card("Arcane Signet", categories=["ramp"])]

    shown = deck_cards.shown(grid, _filters("cat=ramp&q=sol"))

    assert [card.name for card in shown] == ["Sol Ring"]


def test_a_card_is_grouped_under_every_chip_it_wears():
    both = _card("Mind Stone", ["artifact"], ["ramp", "draw"], quantity=2)
    grid = [both, _card("Divination", categories=["draw"])]

    groups = deck_cards.groups(grid, _filters("cat=ramp&cat=draw&cat=wipe"))

    assert [(group.key, group.count) for group in groups] == [
        ("role:ramp", 2), ("role:draw", 3), ("role:wipe", 0)]
    assert groups[0].label == "Ramp" and groups[0].icon


def test_theme_chips_are_the_theme_roles_the_deck_has():
    grid = [_card("Viscera Seer", categories=["sac_outlet"]), _card("Sol Ring",
                                                                    categories=["ramp"])]

    assert [value for value, _label, _icon in deck_cards.theme_chips(grid)] == ["sac_outlet"]


def test_the_explorer_library_is_copies_and_chips_only():
    grid = [_card("Mind Stone", ["artifact"], ["ramp", "draw", "not_a_chip"], quantity=2)]

    assert deck_cards.explorer_cards(grid) == [
        {"n": "Mind Stone", "q": 2, "k": ["role:draw", "role:ramp", "type:artifact"]}]


def test_the_explorer_json_cannot_close_its_script_tag():
    hostile = [{"n": "</script><script>alert(1)</script>", "q": 1, "k": []}]

    html = render_to_string("decks/_card_explorer.html", {"explorer_cards": hostile})

    assert "<script>alert(1)" not in html
    data = html[html.index('id="explorer-cards"'):]
    data = data[data.index(">") + 1:data.index("</script>")]
    assert json.loads(data) == hostile


# --- the page ----------------------------------------------------------------------


@pytest.mark.django_db
def test_two_chips_group_the_grid(client_in, deck):  # noqa: F811
    response = client_in.get(deck.get_absolute_url(), {"cat": ["ramp", "draw"]}, **HTMX)
    body = response.content.decode()

    groups = response.context["grid_groups"]
    assert [group.key for group in groups] == ["role:ramp", "role:draw"]
    assert body.count("<h3") == 2
    assert {card.name for card in response.context["grid"]} == {
        card.name for group in groups for card in group.cards}


@pytest.mark.django_db
def test_one_chip_is_not_grouped(client_in, deck):  # noqa: F811
    response = client_in.get(deck.get_absolute_url(), {"cat": "ramp"})

    assert response.context["grid_groups"] == []
    assert all("ramp" in card.categories for card in response.context["grid"])


@pytest.mark.django_db
def test_the_page_gives_switches_and_the_hidden_explorer(client_in, deck):  # noqa: F811
    response = client_in.get(deck.get_absolute_url(), {"cat": "ramp"})
    body = response.content.decode()

    assert 'type="checkbox" name="cat" value="ramp"' in body
    assert 'data-chip="role:ramp"\n                 checked' in body
    assert 'name="type" value=""' not in body, "no 'All types' radio any more"
    assert "data-card-explorer hidden" in body
    assert "js/card-explorer.js" in body
    library = response.context["explorer_cards"]
    assert sum(card["q"] for card in library) == sum(card.quantity for card in _grid(deck))
    assert deck.commander.name not in {card["n"] for card in library}


def test_the_explorer_s_words_are_translated():
    """Regression: `{% translate %}` doubles a "%" before it looks a string
    up, so a "%(turn)s" placeholder was never found in the catalogue and the
    explorer spoke English on a German page. Its placeholders are {turn}."""
    with translation.override("de"):
        html = render_to_string("decks/_card_explorer.html", {"explorer_cards": []})

    assert 'data-t-by="Bis Zug {turn}: {share}"' in html
    assert "mit Tutoren {share}" in html
    assert "%(" not in html
