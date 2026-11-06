"""Measuring engines in the simulation (Themen-Doc, Paket 1, PR 5).

An engine runs when every role of it has a card out, any card of the deck
that fills the role. It rides along with the combos: no extra games.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.utils import timezone

from cards import synergy
from decks import engine_watch
from decks.themes import Reading
from simulation import analysis
from simulation import combos as engine_combos
from simulations import tasks
from simulations.models import SimulationRun
from tests.test_combos_measure import FakeCard, FakeGame, fake_redis  # noqa: F401
from tests.test_synergy import _aristocrats, _card, _read, client_in, deck, owner  # noqa: F401


def _role(*options):
    return engine_combos.AnyOf(tuple(
        engine_combos.Requirement(name=name, zones=(zone,)) for name, zone in options))


def _engine_watch(*roles):
    return engine_combos.Watch(key="engine:x", requirements=roles, combo=False)


# --- 1. the watcher: any card of a role, each in its own zone ------------------------


def test_any_card_of_a_role_fills_it():
    watcher = engine_combos.Watcher([_engine_watch(
        _role(("Viscera Seer", "B"), ("Carrion Feeder", "B")), _role(("Blood Artist", "B")))])
    game = FakeGame(creatures=[FakeCard("Blood Artist")])

    watcher.look(game, 1)
    assert watcher.first["engine:x"] == 0, "a role with no card out is not filled"

    game.creatures.append(FakeCard("Carrion Feeder"))
    watcher.look(game, 2)
    assert watcher.first["engine:x"] == 2, "the second outlet fills the role as well"


def test_each_card_of_a_role_keeps_its_own_zone():
    role = _role(("Animate Dead", "B"), ("Reanimate", "H"))
    watcher = engine_combos.Watcher([_engine_watch(role)])

    watcher.look(FakeGame(hand=[FakeCard("Animate Dead")]), 1)
    assert watcher.first["engine:x"] == 0, "a permanent in hand does not run"

    watcher.look(FakeGame(hand=[FakeCard("Reanimate")]), 2)
    assert watcher.first["engine:x"] == 2, "a sorcery in hand is ready to cast"


def test_a_run_that_watched_only_engines_has_no_answer_about_wins():
    watched = _engine_watch(_role(("Swamp", "B")))

    result = analysis.as_json(analysis.run(20, turns=3, seed=1, watch=[watched]))

    assert "wins" not in result, "no combo was watched, so none can have won"
    assert result["combos"]["engine:x"]["games"] == 20


# --- 2. which engines, and what each one watches -------------------------------------


@pytest.fixture
def aristocrats(monkeypatch):
    """A deck with one complete aristocrats engine, its reading patched in."""
    cards = _aristocrats(repeats=True) + [
        _card("Big Spell", type_line="Sorcery", tags=["repeatable-token-generator"])]
    engines = _read(cards)[2]

    def show(*shown):
        monkeypatch.setattr(engine_watch, "theme_cards", lambda _deck: cards)
        monkeypatch.setattr(engine_watch, "deck_reading",
                            lambda _deck, _cards=None: Reading(engines=list(shown)))
    show(*engines)
    return SimpleNamespace(cards=cards, engine=engines[0], show=show)


def test_every_role_lists_every_card_that_fills_it(aristocrats):
    (watch,) = engine_watch.watches(None)

    assert watch.key == "engine:aristocrats" and not watch.combo and not watch.wins
    names = [set(role.names) for role in watch.requirements]
    assert names == [{f"Outlet {n}" for n in range(3)},
                     {f"Payoff {n}" for n in range(6)},
                     {f"Fodder {n}" for n in range(6)} | {"Big Spell"}]


def test_a_spell_counts_in_hand_and_a_permanent_on_the_battlefield(aristocrats):
    (watch,) = engine_watch.watches(None)

    zones = {option.name: option.zones for option in watch.requirements[2].options}
    assert zones["Big Spell"] == ("H",)
    assert zones["Fodder 0"] == ("B",)


def test_the_commander_counts_on_the_battlefield_only():
    commander = _card("Chainer", commander=True)

    assert engine_watch._zone(commander) == "B"


@pytest.mark.parametrize("change", [{"missing": ("Makes bodies to sacrifice",)},
                                    {"gone": ("Outlet 0",)}])
def test_an_engine_that_cannot_run_is_not_measured(aristocrats, change):
    aristocrats.show(replace(aristocrats.engine, **change))

    assert engine_watch.watches(None) == ()


def test_one_engine_per_theme_and_at_most_five(aristocrats, monkeypatch):
    monkeypatch.setattr(engine_watch, "MAX_ENGINES", 1)
    other = replace(aristocrats.engine, steps=aristocrats.engine.steps[:2])
    aristocrats.show(aristocrats.engine, other, aristocrats.engine)

    assert [watch.key for watch in engine_watch.watches(None)] == ["engine:aristocrats"]


def test_a_theme_the_catalogue_lost_is_not_measured(aristocrats):
    aristocrats.show(replace(aristocrats.engine, theme="gone-theme"))

    assert engine_watch.watches(None) == ()


# --- 3. the result: split off, quoted by turn six ---------------------------------------


def test_split_moves_the_engines_out_of_the_combos():
    result = {"combos": {"held-1": {"games": 4, "by_turn": [1]},
                         "engine:tokens": {"games": 4, "by_turn": [2]}}}

    engine_watch.split(result)

    assert result["combos"] == {"held-1": {"games": 4, "by_turn": [1]}}
    assert result["engines"] == {"tokens": {"games": 4, "by_turn": [2]}}


def test_a_result_with_only_engines_loses_its_combos_key():
    result = engine_watch.split({"combos": {"engine:tokens": {"games": 4, "by_turn": [2]}}})

    assert "combos" not in result and result["engines"]["tokens"]["by_turn"] == [2]


def test_a_result_without_engines_is_left_alone():
    result = {"combos": {"held-1": {"games": 4, "by_turn": [1]}}}

    assert engine_watch.split(dict(result)) == result


def _run(engines):
    return SimpleNamespace(result={"engines": engines})


def test_the_page_quotes_turn_six_or_the_last_turn_played():
    six = _run({"tokens": {"games": 200, "by_turn": [0, 10, 20, 40, 60, 82, 90, 100]}})
    four = _run({"tokens": {"games": 200, "by_turn": [0, 10, 20, 40]}})

    assert engine_watch.running(six, "tokens") == {"turn": 6, "share": "41%"}
    assert engine_watch.running(four, "tokens") == {"turn": 4, "share": "20%"}


@pytest.mark.parametrize("run", [None, SimpleNamespace(result={}), _run({}),
                                 _run({"tokens": {"games": 0, "by_turn": []}})])
def test_a_run_that_did_not_measure_it_says_nothing(run):
    assert engine_watch.running(run, "tokens") is None


def test_the_report_lists_the_engines_most_often_running_first():
    run = _run({"tokens": {"games": 10, "by_turn": [1, 2]},
                "aristocrats": {"games": 10, "by_turn": [5, 8]},
                "lost-theme": {"games": 10, "by_turn": [9, 9]}})

    rows = engine_watch.measured(run)

    assert [(row["label"], row["turn"], row["share"]) for row in rows] == [
        ("Aristocrats", 2, "80%"), ("Tokens", 2, "20%")]


# --- 4. end to end, and on the pages ---------------------------------------------------


def _swamp_engine(_deck):
    return (engine_combos.Watch(key="engine:aristocrats", combo=False,
                                requirements=(_role(("Swamp", "B")),)),)


def test_a_run_measures_its_engines_in_its_own_games(
        owner, deck, fake_redis, monkeypatch):  # noqa: F811
    monkeypatch.setattr(engine_watch, "watches", _swamp_engine)
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=40, turns=3,
                                       seed=7, chunks_total=2)

    chunks = [tasks.simulate_chunk(str(run.pk), index, 20) for index in range(2)]
    tasks.finalize_run(chunks, str(run.pk))

    run.refresh_from_db()
    assert run.status == SimulationRun.Status.DONE
    entry = run.result["engines"]["aristocrats"]
    assert entry["games"] == 40 and entry["by_turn"][-1] > 0
    assert "engine:aristocrats" not in (run.result.get("combos") or {})


def test_an_engine_that_cannot_be_read_does_not_stop_the_run(
        owner, deck, fake_redis, monkeypatch):  # noqa: F811
    def broken(_deck):
        raise RuntimeError("no reading")
    monkeypatch.setattr(engine_watch, "watches", broken)
    run = SimulationRun.objects.create(owner=owner, deck=deck, games_total=20, turns=2,
                                       seed=7, chunks_total=1)

    tasks.finalize_run([tasks.simulate_chunk(str(run.pk), 0, 20)], str(run.pk))

    run.refresh_from_db()
    assert run.status == SimulationRun.Status.DONE and "engines" not in run.result


def _done(owner, deck, engines):  # noqa: F811
    result = analysis.as_json(analysis.run(10, turns=8, seed=3))
    result["engines"] = engines
    return SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=10, games_done=10, turns=8, seed=3,
        status=SimulationRun.Status.DONE, finished_at=timezone.now(), result=result)


def _complete_engine():
    return synergy.Engine("aristocrats", "Aristocrats", "strong",
                          (("Viscera Seer", "%(card)s sacrifices"),
                           ("Blood Artist", "%(card)s pays off each death")),
                          (), ("Blood Artist",))


def test_the_deck_page_says_by_which_turn_an_engine_runs(
        client_in, owner, deck, monkeypatch):  # noqa: F811
    monkeypatch.setattr("decks.views.deck_reading",
                        lambda _deck: Reading(engines=[_complete_engine()]))
    _done(owner, deck, {"aristocrats": {"games": 10, "by_turn": [0, 1, 2, 3, 4, 6, 7, 8]}})

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert 'data-running="aristocrats"' in body
    assert "Running by turn 6 in <strong>60%</strong> of games, in your last run." in body


def test_an_engine_the_last_run_did_not_measure_says_the_next_one_will(
        client_in, owner, deck, monkeypatch):  # noqa: F811
    monkeypatch.setattr("decks.views.deck_reading",
                        lambda _deck: Reading(engines=[_complete_engine()]))
    _done(owner, deck, {})

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "Not measured yet: your next simulation shows by which turn it runs." in body


def test_an_engine_a_piece_short_gets_no_timing_line(client_in, deck, monkeypatch):  # noqa: F811
    short = replace(_complete_engine(), missing=("Makes bodies to sacrifice",))
    monkeypatch.setattr("decks.views.deck_reading", lambda _deck: Reading(engines=[short]))

    body = client_in.get(deck.get_absolute_url()).content.decode()

    assert "data-running" not in body


def test_the_report_shows_the_engines(client_in, owner, deck):  # noqa: F811
    run = _done(owner, deck, {"aristocrats": {"games": 10, "by_turn": [0, 1, 2, 3, 4, 6]}})

    body = client_in.get(run.get_absolute_url()).content.decode()

    assert 'id="engine-timings"' in body
    assert "Aristocrats: running by turn 6 in <strong>60%</strong> of games." in body


def test_marking_an_engine_keeps_its_timing(client_in, owner, deck):  # noqa: F811
    """The htmx answer of the lock button renders the same lines."""
    _done(owner, deck, {})

    response = client_in.post(f"/decks/{deck.pk}/marks/", {"action": "lock", "key": "x"},
                              HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert 'id="engines"' in response.content.decode()
