"""P19's regression gate: no card may read worse, and no number may move, unseen.

The fixed set - the 2,000 most-played Commander cards and every card of the
development decks, 3,070 in all - is loaded once from
`tests/fixtures/coverage_cards.json.gz`, its profiles derived, and each card
read by the reader and the engine alone. Four real three-colour precons are
built from it and simulated with a fixed seed. Both must equal what is
committed: `coverage_snapshot.json` and `coverage_numbers.json`.

When this fails, the message lists every card and every number that changed.
A card under LOST read before and does not now: that is a bug of the change,
not of the snapshot. Everything else may be the change's intended effect.
Then look at each one, rewrite the snapshots with

    COVERAGE_WRITE=1 pytest tests/test_engine_coverage.py

and commit them with the change, so the PR shows every reading and every
number it altered. See `simulations/coverage.py`.
"""

import json
import os

import pytest
from django.contrib.auth import get_user_model

from simulations import coverage
from tests.support import forget_module_rows, require_test_database

# Without it pytest-django creates no test database when this module runs on
# its own, and the module fixture would load into - and empty - the
# development database (it did once; tests/support.require_test_database).
pytestmark = pytest.mark.django_db

WRITE = os.environ.get("COVERAGE_WRITE") == "1"


@pytest.fixture(scope="module")
def loaded(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        require_test_database()
        # A reused test database can hold what an aborted run left behind.
        forget_module_rows()
        cards = coverage.load_fixture()
        owner = get_user_model().objects.create_user(email="coverage@example.com",
                                                     password="pw-for-test-only")
        decks = coverage.build_decks(owner)
        yield cards, decks
        forget_module_rows()


@pytest.fixture(scope="module")
def readings(loaded, django_db_blocker):
    cards, _decks = loaded
    with django_db_blocker.unblock():
        found = coverage.snapshot(
            type(cards[0]).objects.filter(pk__in=[card.pk for card in cards])
            .select_related("profile")
        )
    if WRITE:
        coverage.write_snapshot(found)
    return found


@pytest.fixture(scope="module")
def simulated(loaded, django_db_blocker):
    _cards, decks = loaded
    with django_db_blocker.unblock():
        found = {deck.name: coverage.numbers(deck) for deck in decks}
    if WRITE:
        coverage.NUMBERS.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n",
                                    encoding="utf-8", newline="\n")
    return found


def test_the_fixed_set_is_the_snapshots(readings):
    assert sorted(readings) == sorted(coverage.load_snapshot())


def test_no_card_reads_worse_or_differently(readings):
    committed = coverage.load_snapshot()
    diff = coverage.compare(committed, readings)
    assert diff.empty, "\n" + "\n".join(diff.lines(committed, readings))


def test_no_number_moved(simulated):
    committed = json.loads(coverage.NUMBERS.read_text(encoding="utf-8"))
    moved = coverage.compare_numbers(committed, simulated)
    assert not moved, "\n" + "\n".join(moved[:80])


def test_the_decks_are_whole(loaded, django_db_blocker):
    _cards, decks = loaded
    with django_db_blocker.unblock():
        for deck in decks:
            assert deck.commander_id, deck.name
            assert sum(deck.entries.values_list("quantity", flat=True)) == 99, deck.name


def test_the_set_holds_what_the_rounds_are_about():
    """The gap classes P19 still has to close are in the set, so a round shows up here."""
    unread = {reason for entry in coverage.load_snapshot().values()
              for reason in entry["unread"]}
    assert any("tutors onto the battlefield" in reason for reason in unread)
    assert any("conditionally" in reason for reason in unread)
    # R1 (engine version 5) closed this one: a choice of colours is read.
    assert not any("cannot hold a choice" in reason for reason in unread)


def test_a_lost_card_is_told_apart_from_a_gained_one():
    read = {"id": "1", "unread": [], "read": {"kind": "land"}}
    unread = {"id": "1", "unread": ["a land that taps for nothing"], "read": {"kind": "land"}}
    other = {"id": "1", "unread": [], "read": {"kind": "rock"}}

    assert coverage.compare({"A": read}, {"A": unread}).lost == ["A"]
    assert coverage.compare({"A": unread}, {"A": read}).gained == ["A"]
    assert coverage.compare({"A": read}, {"A": other}).changed == ["A"]
    assert coverage.compare({"A": read}, {"A": read}).empty


def test_a_moved_number_is_named():
    before = {"D": {"mulligans": {}, "opening_lands": {}, "turns": [{"mana": 1.0}]}}
    after = {"D": {"mulligans": {}, "opening_lands": {}, "turns": [{"mana": 1.5}]}}
    assert coverage.compare_numbers(before, after) == ["D / turn 1 / mana: 1.0 -> 1.5"]
