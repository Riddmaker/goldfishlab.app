"""The deck in your hand with JavaScript (B-11, PR 3): static/js/deck-hand.js.

The rules (shuffle, cut, piles, put back, rebuild, which side faces up) run
here under Node; dragging and turning the deck is a browser check. The page
must hand the script the server's order and keep its own controls hidden
until the script shows them, so it stays whole without JavaScript.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.urls import reverse

from decks import in_hand
from tests.test_in_hand import deck, owner  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
HAND_JS = ROOT / "static" / "js" / "deck-hand.js"
NODE = shutil.which("node")

RUN = """
const hand = require(process.argv[1]);
// A fixed sequence for shuffle, so its result can be named.
let state = 7;
const random = () => ((state = (state * 48271) %% 2147483647) / 2147483647);
const cards = (n) => Array.from({ length: n }, (_, i) => ({ id: i, name: `c${i}` }));
const ids = (list) => list.map((card) => card.id);
console.log(JSON.stringify((%s)()));
"""


def _node(body: str):
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - our own script and file, fixed arguments
        [NODE, "-e", RUN % body, str(HAND_JS)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


# --- the rules -------------------------------------------------------------------


def test_a_shuffle_keeps_every_card_and_leaves_the_deck_given_alone():
    out = _node("""() => {
        const deck = cards(60);
        const once = hand.shuffle(deck, random);
        return {kept: ids(once).sort((a, b) => a - b), before: ids(deck),
                moved: ids(once).filter((id, i) => id !== i).length};
    }""")

    assert out["kept"] == list(range(60))
    assert out["before"] == list(range(60))
    assert out["moved"] > 40


def test_a_cut_takes_the_top_cards_and_never_more_than_there_are():
    out = _node("""() => [3, 0, -2, 99, "x"].map((n) => {
        const split = hand.cut(cards(5), n);
        return [ids(split.top), ids(split.rest)];
    })""")

    assert out == [[[0, 1, 2], [3, 4]], [[], [0, 1, 2, 3, 4]], [[], [0, 1, 2, 3, 4]],
                   [[0, 1, 2, 3, 4], []], [[], [0, 1, 2, 3, 4]]]


def test_piles_are_dealt_one_card_each_in_turn_and_stacked_again():
    out = _node("""() => {
        const piles = hand.deal(cards(7), 3);
        return {piles: piles.map(ids), stacked: ids(hand.gather(piles))};
    }""")

    # Card 0 goes down first on pile 0, so it ends at the bottom of that pile.
    assert out["piles"] == [[6, 3, 0], [4, 1], [5, 2]]
    assert out["stacked"] == [6, 3, 0, 4, 1, 5, 2]


def test_a_card_goes_back_on_top_or_at_the_bottom():
    out = _node("""() => {
        const card = {id: 9, name: "x"};
        return [ids(hand.place(cards(3), card, "top")),
                ids(hand.place(cards(3), card, "bottom"))];
    }""")

    assert out == [[9, 0, 1, 2], [0, 1, 2, 9]]


def test_rebuild_puts_the_cards_in_the_list_order_copies_together():
    out = _node("""() => hand.rebuild([{name: "Swamp"}, {name: "Dark Ritual"},
                                       {name: "Swamp"}, {name: "Arcane Signet"}])
                         .map((card) => card.name)""")

    assert out == ["Arcane Signet", "Dark Ritual", "Swamp", "Swamp"]


def test_turned_past_the_side_the_deck_shows_its_bottom_card():
    out = _node("() => [0, 60, 90, 91, 180, 269, 270, 360, -180, 540].map(hand.facing)")

    assert out == ["back", "back", "back", "front", "front", "front", "back", "back",
                   "front", "front"]


# --- the page --------------------------------------------------------------------


def _page(client, owner, deck, **query):  # noqa: F811
    client.force_login(owner)
    return client.get(reverse("decks:hand", args=[deck.pk]),
                      {"seed": 42, "cut": 0, **query}).content.decode()


def test_the_script_gets_the_order_the_server_shuffled(client, owner, deck):  # noqa: F811
    html = _page(client, owner, deck)

    assert "js/deck-hand.js" in html
    data = re.search(r'<script id="in-hand-cards" type="application/json">(.*?)</script>',
                     html, re.S)
    cards = json.loads(data.group(1))
    assert [card["name"] for card in cards] == in_hand.shuffled(in_hand.library(deck), 42)
    assert all(card["image"].startswith("https://") for card in cards if card["image"])


def test_its_controls_wait_hidden_until_the_script_shows_them(client, owner, deck):  # noqa: F811
    html = _page(client, owner, deck)

    controls = re.search(r"<div[^>]*data-in-hand-js[^>]*>", html).group(0)
    assert " hidden" in controls and "data-left-label=" in controls
    for act in ("draw-top", "deal", "gather", "lift", "turn", "rebuild", "draw", "top", "bottom"):
        assert f'data-act="{act}"' in html, act
    assert re.search(r"<a [^>]*data-in-hand-back hidden>", html), "nothing to put back yet"


def test_after_a_cut_put_back_shows_without_javascript(client, owner, deck):  # noqa: F811
    html = _page(client, owner, deck, cut=3)

    assert re.search(r"<a [^>]*data-in-hand-back>", html)
