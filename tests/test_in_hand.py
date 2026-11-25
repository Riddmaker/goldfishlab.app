"""The deck in your hand (B-11): the library shuffled from a seed, cut, shown.

What these tests hold: the library is one name per copy without the
commander; one seed is one order; the page redirects to a seed, turns the
top `cut` cards face up and is only its owner's; the deck page links to it;
the mat and the sleeve are variables a custom look (B-10) can set.
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from decks import in_hand
from decks import services as deck_services

ARCHIDEKT_CSV = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
CSS = Path(__file__).resolve().parent.parent / "assets" / "css" / "input.css"


@pytest.fixture
def owner(db):
    return get_user_model().objects.create_user(email="in-hand@example.com",
                                                password="pw-test-1234")


@pytest.fixture
def deck(owner, catalogue):
    return deck_services.import_deck(owner=owner, raw=ARCHIDEKT_CSV.read_bytes(),
                                     name="deck", filename="sample.csv").deck


def _tiles(html: str) -> list[tuple[str, str]]:
    """The names of the shown cards, picture or text, in page order."""
    return re.findall(r'<figure class="card-tile[^"]*"[^>]*>\s*'
                      r'<(?:img[^>]*alt="([^"]*)"|span[^>]*>([^<]*))', html)


# --- the order -------------------------------------------------------------------


def test_one_seed_is_one_order_and_every_card_stays():
    names = ["Swamp"] * 5 + ["Sol Ring", "Dark Ritual", "Necropotence"]

    first, again = in_hand.shuffled(names, 7), in_hand.shuffled(names, 7)

    assert first == again
    assert sorted(first) == sorted(names)
    assert names[:2] == ["Swamp", "Swamp"], "the list given is not shuffled itself"
    assert in_hand.shuffled(names, 8) != first


def test_the_library_is_a_name_per_copy_without_the_commander(deck):
    library = in_hand.library(deck)

    assert deck.commander is not None
    assert deck.commander.front_name not in library
    assert len(library) == sum(entry.quantity for entry in deck.entries.all()
                               if entry.oracle_card_id != deck.commander_id)
    assert library.count("Swamp") > 1


# --- the page --------------------------------------------------------------------


def test_without_a_seed_the_deck_is_shuffled_anew(client, owner, deck):
    client.force_login(owner)

    response = client.get(reverse("decks:hand", args=[deck.pk]))

    assert response.status_code == 302
    assert re.search(r"\?seed=\d+&cut=0$", response["Location"])


def test_cut_turns_the_top_cards_face_up_the_same_for_the_same_seed(client, owner, deck):
    client.force_login(owner)
    url = reverse("decks:hand", args=[deck.pk])

    html = client.get(url, {"seed": 42, "cut": 3}).content.decode()

    top = in_hand.shuffled(in_hand.library(deck), 42)[:3]
    revealed = html[html.index("data-in-hand-revealed"):]
    assert ["".join(found) for found in _tiles(revealed)] == top
    left = len(in_hand.library(deck)) - 3
    assert f"--left: {left}" in html and f"{left} cards in the library" in html
    assert client.get(url, {"seed": 42, "cut": 3}).content.decode().count("card-tile-img") \
        == html.count("card-tile-img")


def test_without_a_cut_nothing_is_face_up_but_the_commander(client, owner, deck):
    client.force_login(owner)

    html = client.get(reverse("decks:hand", args=[deck.pk]), {"seed": 1}).content.decode()

    assert "data-in-hand-revealed" not in html
    assert [name for name in map("".join, _tiles(html))] == [deck.commander.front_name]


@pytest.mark.parametrize("query", [{"seed": 1, "cut": in_hand.CUT_MAX + 1}, {"seed": -1},
                                   {"seed": "x"}])
def test_a_wrong_address_is_refused(client, owner, deck, query):
    client.force_login(owner)

    assert client.get(reverse("decks:hand", args=[deck.pk]), query).status_code == 400


def test_only_the_owner_holds_the_deck(client, deck):
    url = reverse("decks:hand", args=[deck.pk])
    assert client.get(url, {"seed": 1}).status_code == 302, "signed out: to the login"
    stranger = get_user_model().objects.create_user(email="other@example.com",
                                                    password="pw-test-1234")
    client.force_login(stranger)

    assert client.get(url, {"seed": 1}).status_code == 404


def test_the_deck_page_has_the_button(client, owner, deck):
    client.force_login(owner)

    html = client.get(reverse("decks:detail", args=[deck.pk])).content.decode()

    assert f'href="{reverse("decks:hand", args=[deck.pk])}" data-in-hand-link' in html


def test_the_mat_and_the_sleeve_are_variables_a_custom_look_can_set():
    css = CSS.read_text()
    table = css[css.index(".in-hand-table {"):]
    table = table[:table.index("}")]
    assert "--mat:" in table and "--sleeve:" in table and "background: var(--mat)" in table
    stack = css[css.index(".in-hand-stack {"):]
    assert "background: var(--sleeve)" in stack[:stack.index("}")]
