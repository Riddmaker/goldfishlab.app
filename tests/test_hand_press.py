"""The hand in free play (B-09): a fixed target per card, the hint under the hand,
and a long press on a phone that shows the card instead of playing it.

The sliding itself (one card after the other, no flicker) is a browser check;
here are the rule in static/js/hand-press.js, run under Node, and what the pages
must carry for it.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.urls import reverse

from tests.test_mulligan_quiz import empty_cache, no_worker  # noqa: F401
from tests.test_playtest_free import session, signed_in  # noqa: F401
from tests.test_synergy import client_in, deck, owner  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
PRESS_JS = ROOT / "static" / "js" / "hand-press.js"
INPUT_CSS = ROOT / "assets" / "css" / "input.css"
NODE = shutil.which("node")

PRESS_KIND = """
const { pressKind } = require(process.argv[1]);
console.log(JSON.stringify(JSON.parse(process.argv[2]).map(pressKind)));
"""


def _kinds(*presses):
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - our own script and file, fixed arguments
        [NODE, "-e", PRESS_KIND, str(PRESS_JS), json.dumps(list(presses))],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_a_short_tap_plays_and_a_resting_finger_peeks():
    assert _kinds({"held": 120, "moved": 0}, {"held": 349, "moved": 4},
                  {"held": 350, "moved": 0}, {"held": 900, "moved": 10}) == [
        "tap", "tap", "peek", "peek"]


def test_a_moving_finger_scrolls_the_hand():
    assert _kinds({"held": 50, "moved": 11}, {"held": 900, "moved": 40}) == ["scroll", "scroll"]


def _rule(selector):
    css = INPUT_CSS.read_text(encoding="utf-8")
    match = re.search(r"\n" + re.escape(selector) + r" \{(.*?)\}", css, re.S)
    assert match, selector
    return match.group(1)


def test_a_raised_slot_does_not_move_under_the_pointer():
    # The flicker came from the slot itself turning and lifting on hover.
    assert "transform" not in _rule(".hand-slot:hover,\n.hand-slot:focus-within")
    assert "translate" in _rule(".hand-slot:has(.hand-card-ready)")


def test_the_board_has_the_hint_under_the_hand_and_the_press(signed_in, session):  # noqa: F811
    page = signed_in.get(session.get_absolute_url()).content.decode()

    assert "js/hand-press.js" in page
    assert page.index('class="hand-fan"') < page.index("click a card to put it on the table")


def test_the_tutor_and_the_quiz_load_the_press(client_in, deck, empty_cache, no_worker,  # noqa: F811
                                               monkeypatch):
    monkeypatch.setattr("decks.tutor.ask", lambda *args: None)

    for name in ("decks:tutor", "decks:mulligan"):
        page = client_in.get(reverse(name, args=[deck.pk]), follow=True).content.decode()
        assert "js/hand-press.js" in page, name
