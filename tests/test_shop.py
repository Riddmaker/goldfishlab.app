"""The card shop (B-16): an Easter egg behind the goldfish.

Three things matter. It is hidden: not indexed, not in the sitemap, not in a
menu, reachable from the door the fish opens. It is playable without a mouse:
every spot is a button in a list, and the rules - which step happens, what is
carried, when it ends - are pure functions in static/js/shop.js, run here
under Node. And it holds together: every spot and prop sits inside the
picture, every item a step gives has a name, and the game can be won.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import translation

from core import shop

ROOT = Path(__file__).resolve().parent.parent
SHOP_JS = ROOT / "static" / "js" / "shop.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.django_db


# --- the page -----------------------------------------------------------------


def test_the_shop_opens_for_anybody(client):
    response = client.get(reverse("shop"))

    assert response.status_code == 200
    body = response.content.decode()
    assert shop.SHOP_NAME in body
    assert 'id="shop-game"' in body
    assert "js/shop.js" in body


def test_the_shop_is_hidden_from_search_engines(client):
    body = client.get(reverse("shop")).content.decode()

    assert '<meta name="robots" content="noindex">' in body
    assert reverse("shop") not in client.get("/sitemap.xml").content.decode()


def test_the_shop_is_in_no_menu(client):
    body = client.get(reverse("methodology")).content.decode()

    assert reverse("shop") not in body


def test_without_javascript_the_shop_front_and_a_sentence(client):
    body = client.get(reverse("shop")).content.decode()

    assert "data-shop-nojs>" in body.replace(" hidden", "")
    assert "data-shop-game hidden" in body
    assert "needs JavaScript" in body


def test_the_game_speaks_the_page_language():
    with translation.override("de"):
        game = shop.game()

    assert game["rooms"]["floor"]["spots"][0]["name"] == "Die Tür"


# --- it holds together --------------------------------------------------------


def _game():
    return shop.game()


def test_every_spot_and_prop_sits_inside_the_picture():
    game = _game()
    for room in game["rooms"].values():
        for box in room["spots"] + room.get("props", []):
            assert 0 <= box["x"] and box["x"] + box["w"] <= game["width"], box["id"]
            assert 0 <= box["y"] and box["y"] + box["h"] <= game["height"], box["id"]


def test_every_item_and_room_named_in_a_step_exists():
    game = _game()
    for room in game["rooms"].values():
        for spot in room["spots"]:
            assert spot["steps"], spot["id"]
            assert "if" not in spot["steps"][-1], f"{spot['id']}: the last step must always happen"
            for step in spot["steps"]:
                assert step["say"]
                for key in ("give", "take"):
                    if key in step:
                        assert step[key] in game["items"]
                if "go" in step:
                    assert step["go"] in game["rooms"]


def test_every_picture_is_there():
    game = _game()
    for room in game["rooms"].values():
        for picture in [room["picture"]] + [prop["picture"] for prop in room.get("props", [])]:
            assert (ROOT / "static" / picture.removeprefix("/static/")).exists(), picture


# --- the rules ----------------------------------------------------------------

SCRIPT = """
const shop = require(process.argv[1]);
const game = JSON.parse(process.argv[2]);
const moves = JSON.parse(process.argv[3]);
let state = shop.fresh(game);
const lines = [];
for (const id of moves) {
  const spot = game.rooms[state.room].spots.find((s) => s.id === id);
  const result = shop.act(state, spot);
  state = result.state;
  lines.push(result.say);
}
const props = game.rooms[state.room].props.filter((p) => shop.visible(state, p)).map((p) => p.id);
console.log(JSON.stringify({state, lines, props}));
"""


def _play(*moves):
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - our own script and file, fixed arguments
        [NODE, "-e", SCRIPT, str(SHOP_JS), json.dumps(_game()), json.dumps(moves)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_the_game_starts_with_empty_hands_on_the_shop_floor():
    played = _play()

    assert played["state"] == {"room": "floor", "items": [], "flags": [], "ended": False}
    assert played["props"] == ["deckbox"]


def test_the_deck_box_is_found_under_a_chair_and_leaves_the_picture():
    played = _play("tables")

    assert played["state"]["items"] == ["deckbox"]
    assert "deck box" in played["lines"][0]
    assert played["props"] == []


def test_the_shopkeeper_trades_the_deck_box_for_sleeves():
    played = _play("counter", "tables", "counter", "counter")

    assert "Evening" in played["lines"][0]
    assert played["state"]["items"] == ["sleeves"]
    assert "Sam" in played["lines"][2]
    assert "Table three is free" in played["lines"][3]


def test_sleeves_at_the_table_end_the_game():
    played = _play("tables", "counter", "tables")

    assert played["state"]["ended"] is True
    assert played["state"]["items"] == []
    assert "shuffle" in played["lines"][2]


def test_bare_cards_do_not_get_a_seat():
    played = _play("tables", "tables")

    assert played["state"]["ended"] is False
    assert "bare cards" in played["lines"][1]


def test_a_step_given_twice_gives_one_item():
    played = _play("tables", "tables", "tables")

    assert played["state"]["items"] == ["deckbox"]
