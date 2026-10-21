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

The same cards play the games of the trace decks (card model, phase 4): see
the section at the end and `simulations/games.py`.
"""

import json
import os

import pytest
from django.contrib.auth import get_user_model

from simulation import trace
from simulations import coverage, games
from simulations.engine import adapter
from tests.support import forget_module_rows, require_test_database

# Without it pytest-django creates no test database when this module runs on
# its own, and the module fixture would load into - and empty - the
# development database (it did once; tests/support.require_test_database).
pytestmark = pytest.mark.django_db

WRITE = os.environ.get("COVERAGE_WRITE") == "1"
TRACE_WRITE = os.environ.get("TRACE_WRITE") == "1"


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


def test_a_nested_field_at_its_default_is_no_change():
    from dataclasses import replace

    from simulation.cards import AdditionalCost, Card

    card = Card(name="Tribute", mv=3, pips=1, generic=2, kind="sorcery",
                additional_costs=(AdditionalCost(sacrifice=("creature",)),))
    read = coverage.reading(card, [])
    [cast] = [each for each in read["read"]["abilities"] if each["form"] == "cast_costs"]
    [atom] = cast["options"][0]
    assert "x" not in atom and atom["amount"] == 1
    # A snapshot from before a field existed, or written with every default
    # spelled out, still compares equal.
    spelled = {**read, "read": {**read["read"], "abilities": [
        {**each, "options": [[{**atom, "x": False}]]} if each is cast else each
        for each in read["read"]["abilities"]]}}
    assert coverage.compare({"Tribute": spelled}, {"Tribute": read}).empty
    twice = coverage.reading(replace(card, additional_costs=(
        AdditionalCost(sacrifice=("creature",), sacrifice_count=2),)), [])
    assert coverage.compare({"Tribute": read}, {"Tribute": twice}).changed == ["Tribute"]


# --- the games of the trace decks (card model, phase 4) -------------------------
#
# Every deck of the development database, as card lists from this fixture
# (`trace_decks.json`), and one deck with two commanders play
# `games.CI_GAMES` games of eight turns. Each game's records
# (`simulation.trace`) must give the digests in `game_traces.json`; a game
# saved and restored before every turn must play exactly as one never saved.
# Phase 4 moves no game before PR 17; a change meant to move games rewrites
# them with TRACE_WRITE=1.


@pytest.fixture(scope="module")
def decks(loaded, django_db_blocker):
    with django_db_blocker.unblock():
        owner = get_user_model().objects.create_user(email="traces@example.com",
                                                     password="pw-for-test-only")
        built = coverage.build_decks(owner, games.TRACE_DECKS)
        return {deck.name: adapter.convert(deck).definition for deck in built}


def test_every_game_plays_as_committed(decks):
    found = {name: [trace.digests(trace.play(deck, index, games.TURNS))
                    for index in range(games.CI_GAMES)]
             for name, deck in decks.items()}
    if TRACE_WRITE:
        games.write_traces(found)
    committed = json.loads(games.TRACES.read_text(encoding="utf-8"))
    moved = [f"{name}: game {index}, record {_first(old, new)}"
             for name in sorted(set(committed) | set(found))
             for index, (old, new) in enumerate(zip(committed.get(name, []),
                                                    found.get(name, []), strict=False))
             if old != new]
    assert sorted(committed) == sorted(found)
    assert not moved, "\n" + "\n".join(moved[:40])


def _first(old: dict, new: dict) -> int:
    return next(number for number, (was, now) in enumerate(zip(old["turns"], new["turns"],
                                                                strict=True)) if was != now)


def test_a_restored_game_plays_on_as_before(decks):
    for name, deck in decks.items():
        for index in range(games.CI_RESTORED):
            played = trace.play(deck, index, games.TURNS)
            restored = trace.play_restored(deck, index, games.TURNS)
            for number, (was, now) in enumerate(zip(played, restored, strict=True)):
                assert was == now, (f"{name}, game {index}, record {number}: "
                                    f"{trace.differences(was, now)}")


def test_the_decks_are_the_database_decks_and_a_pair(decks):
    assert len(decks) == len(json.loads(games.TRACE_DECKS.read_text(encoding="utf-8")))
    assert sum(deck.partner is not None for deck in decks.values()) >= 1


def test_every_zone_holds_its_permanents_after_each_turn(decks):
    """Card model phase 4, PR 12: the four lists the engine reads are views
    of its permanents, and stay so whatever a deck plays."""
    import random

    from simulation import agent
    from simulation.game import Game

    for name, deck in decks.items():
        for index in range(3):
            game = Game(random.Random(trace.game_seed(name, index)), deck=deck)
            game.take_opening_hand()
            for _ in range(games.TURNS):
                agent.take_turn(game)
                zones = (game.lands, game.rocks, game.creatures, game.other_permanents)
                assert all(zone.in_step() for zone in zones), (name, index, game.turn)
                pids = [each.pid for zone in zones for each in zone.permanents]
                assert len(pids) == len(set(pids)), (name, index, game.turn)
