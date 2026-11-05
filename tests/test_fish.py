"""The goldfish in the big flask on the home page (phase 10 F, T1.4 and T1.6;
moved out of the header in Paket 4, PR 17).

Two halves. Where it lives: only on the home page, in the flask above the
heading, with its script, and named for a screen reader only once the script
makes it a button; the header mark is the still image on every page, the
home page too. And the rules it moves by - how long it waits, which move
comes next, when pokes make a triple - which static/js/fish.js keeps as pure
functions so they can be run here under Node, without a browser. The moves
themselves are CSS, checked by eye and in tests/test_logo.py for reduced
motion.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.urls import reverse

ROOT = Path(__file__).resolve().parent.parent
FISH_JS = ROOT / "static" / "js" / "fish.js"
NODE = shutil.which("node")


# --- where it lives --------------------------------------------------------


def _header(body):
    return body[body.index("<nav"):body.index("</nav>")]


def test_the_home_page_has_the_fish_in_the_big_flask(client, db):
    body = client.get(reverse("home")).content.decode()
    hero = body[body.index('class="home-hero"'):body.index("<h1")]

    assert "data-fish" in hero
    assert 'class="logo logo-home' in hero
    assert 'data-label="Poke the goldfish"' in hero
    assert "js/fish.js" in body


def test_the_flask_is_no_button_without_its_script(client, db):
    body = client.get(reverse("home")).content.decode()
    hero = body[body.index('class="home-hero"'):body.index("<h1")]

    # fish.js makes it a button; before that it is a picture, nothing to press.
    assert "<button" not in hero
    assert 'aria-hidden="true"' in hero


def test_the_header_mark_is_still_on_the_home_page_too(client, db):
    header = _header(client.get(reverse("home")).content.decode())

    assert "img/favicon.svg" in header
    assert "data-fish" not in header
    assert 'class="logo' not in header
    assert 'aria-current="page"' in header


def test_the_fish_is_there_for_a_member_too(client, django_user_model):
    user = django_user_model.objects.create_user(email="f@example.com", password="pw-x-1234")
    client.force_login(user)

    assert "data-fish" in client.get(reverse("home")).content.decode()


@pytest.mark.parametrize("name", ["methodology", "privacy", "account_login"])
def test_every_other_page_keeps_the_still_mark(client, name):
    body = client.get(reverse(name)).content.decode()

    assert "data-fish" not in body
    assert "js/fish.js" not in body
    assert 'aria-current="page"' not in _header(body)
    assert "img/favicon.svg" in body


# --- the rules -------------------------------------------------------------

#: Runs the rules with a fixed sequence of "random" numbers and prints
#: the answers as JSON. `rolls` cycles, so any sequence length works; for a
#: poke it is the earlier pokes' times.
SCRIPT = """
const fish = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([kind, rolls, arg]) => {
  let i = 0;
  const random = () => rolls[i++ % rolls.length];
  if (kind === "poke") return fish.poke(rolls, arg);
  return kind === "wait" ? fish.nextWait(random, arg) : fish.nextMove(random, arg);
});
console.log(JSON.stringify({out, max: fish.MAX_IDLE_MOVES, triple: fish.TRIPLE_MS}));
"""


def _run(cases):
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - our own script and file, fixed arguments
        [NODE, "-e", SCRIPT, str(FISH_JS), json.dumps(cases)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_the_first_wait_is_twenty_to_thirty_seconds():
    answers = _run([["wait", [0.0], True], ["wait", [0.999], True]])["out"]

    assert answers[0] == 20000
    assert 29900 < answers[1] < 30000


def test_later_waits_are_twenty_five_to_sixty_seconds():
    answers = _run([["wait", [0.0], False], ["wait", [0.999], False]])["out"]

    assert answers[0] == 25000
    assert 59900 < answers[1] < 60000


def test_the_first_move_is_always_the_glance():
    answers = _run([["move", [roll], None] for roll in (0.0, 0.7, 0.95, 0.999)])["out"]

    assert answers == ["gaze"] * 4


def test_the_odds_are_sixty_thirty_ten():
    rolls = [i / 1000 for i in range(1000)]
    answers = _run([["move", [roll], "gaze"] for roll in rolls])["out"]

    assert answers.count("gaze") == 600
    assert answers.count("turn") == 300
    assert answers.count("jump") == 100


def test_two_jumps_never_follow_each_other():
    rolls = [i / 1000 for i in range(1000)]
    answers = _run([["move", [roll], "jump"] for roll in rolls])["out"]

    assert "jump" not in answers
    # The odds between the other two stay as they were, two to one.
    assert answers.count("gaze") == pytest.approx(2 * answers.count("turn"), abs=2)


def test_a_page_view_gets_at_most_six_idle_moves():
    assert _run([])["max"] == 6


def test_three_pokes_within_one_and_a_half_seconds_make_a_jump():
    answers = _run([["poke", [], 0], ["poke", [0], 700], ["poke", [0, 700], 1400]])["out"]

    assert [a["triple"] for a in answers] == [False, False, True]
    assert answers[2]["pokes"] == [], "after a triple the count starts again"


def test_a_slow_poke_starts_the_count_again():
    answers = _run([["poke", [0, 700], 1500], ["poke", [0, 700], 2300]])["out"]

    assert answers[0] == {"pokes": [700, 1500], "triple": False}
    assert answers[1] == {"pokes": [2300], "triple": False}
    assert _run([])["triple"] == 1500
