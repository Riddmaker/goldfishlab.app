"""The goldfish on the home page (phase 10 F, T1.4 and T1.6).

Two halves. Where the living logo is: only on the home page, as a link that
says it is the current page, with its script; every other page keeps the
still image and loads nothing. And the rules it moves by - how long it waits,
which move comes next - which static/js/fish.js keeps as pure functions so
they can be run here under Node, without a browser. The moves themselves are
CSS, checked by eye and in tests/test_logo.py for reduced motion.
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


def test_the_home_page_has_the_living_logo(client):
    body = client.get(reverse("home")).content.decode()

    assert "data-fish-link" in body
    assert 'aria-current="page"' in body
    assert 'class="logo logo-home' in body
    assert "js/fish.js" in body


def test_the_living_logo_is_a_link_home_without_javascript(client):
    body = client.get(reverse("home")).content.decode()
    link = body[body.index("<a", body.index("<nav")):body.index("data-fish-link")]

    assert f'href="{reverse("home")}"' in link


def test_the_living_logo_is_there_for_a_member_too(client, django_user_model):
    user = django_user_model.objects.create_user(email="f@example.com", password="pw-x-1234")
    client.force_login(user)

    assert "data-fish-link" in client.get(reverse("home")).content.decode()


@pytest.mark.parametrize("name", ["methodology", "privacy", "account_login"])
def test_every_other_page_keeps_the_still_mark(client, name):
    body = client.get(reverse(name)).content.decode()

    assert "data-fish-link" not in body
    assert "js/fish.js" not in body
    assert 'aria-current="page"' not in body
    assert "img/favicon.svg" in body


# --- the rules -------------------------------------------------------------

#: Runs the two rules with a fixed sequence of "random" numbers and prints
#: the answers as JSON. `rolls` cycles, so any sequence length works.
SCRIPT = """
const fish = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([kind, rolls, arg]) => {
  let i = 0;
  const random = () => rolls[i++ % rolls.length];
  return kind === "wait" ? fish.nextWait(random, arg) : fish.nextMove(random, arg);
});
console.log(JSON.stringify({out, max: fish.MAX_IDLE_MOVES}));
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
