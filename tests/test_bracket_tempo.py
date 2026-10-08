"""P8: how fast a game-ending combo comes together, against the Commander Brackets.

Wizards describes each bracket by how long a game is expected to last (nine,
eight, six and four turns; cEDH any turn). The report times every combo the
deck holds that ends the game on its own and says which brackets that speed
fits. Three things have to stay true for that to be worth printing:

1. **Only a combo that ends the game counts.** An infinite-mana loop wins
   nothing by itself and would make a deck look faster than it is.
2. **Two combos are not added up.** Each game counts once, at the first turn
   any game-ending combo was together - only the game knows which.
3. **A run that did not play the turns does not answer.** A six-turn run says
   nothing about a nine-turn bracket, and the page says so.
"""

from dataclasses import dataclass, field

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from combos import measure
from combos.models import Combo, ends_the_game
from decks import services as deck_services
from simulation import analysis
from simulation import combos as engine_combos
from simulations import report
from simulations.engine import runner
from simulations.models import SimulationRun

User = get_user_model()


@dataclass
class FakeCard:
    name: str


@dataclass
class FakeGame:
    """The zones the watcher reads, and nothing else."""

    creatures: list = field(default_factory=list)
    lands: list = field(default_factory=list)
    graveyard: list = field(default_factory=list)
    hand: list = field(default_factory=list)
    exiled: list = field(default_factory=list)
    library: list = field(default_factory=list)
    deck: object = None

    @property
    def battlefield(self):
        return self.lands + self.creatures


def watch(key, name, wins=True):
    return engine_combos.Watch(
        key=key, requirements=(engine_combos.Requirement(name=name),), wins=wins
    )


# --- which combos end the game ---------------------------------------------


@pytest.mark.parametrize("feature", [
    "Win the game",
    "Win the game at the beginning of your next upkeep",
    "Infinite damage to one opponent",
    "Near-infinite lifeloss",
    "Infinite combat phases",
    "Infinite turns",
    "Infinite mill for target opponent",
    "Each opponent loses the game",
    "Target opponent loses the game at the beginning of their next upkeep",
])
def test_these_end_the_game(feature):
    assert ends_the_game(feature)


@pytest.mark.parametrize("feature", [
    "Infinite colorless mana",
    "Infinite ETB",
    "Infinite self-mill",
    "Infinite damage to creatures",
    "Near-infinite damage to you",
    "Infinite turns for each opponent",
    "You can't lose the game due to having 0 or less life",
    "Permanent with \"You don't lose the game due to having 0 or less life\" (Lich)",
])
def test_these_do_not(feature):
    assert not ends_the_game(feature)


def test_a_combo_ends_the_game_when_one_of_its_results_does():
    assert Combo(produces=["Infinite mana", "Infinite lifeloss"]).ends_the_game
    assert not Combo(produces=["Infinite mana", "Infinite tokens"]).ends_the_game
    assert not Combo(produces=[]).ends_the_game


@pytest.mark.django_db
def test_the_watch_a_run_builds_knows_whether_the_combo_wins(catalogue):
    combo = Combo.objects.create(spellbook_id="w-1", produces=["Infinite lifeloss"])
    combo.cards.create(name="Swamp")
    assert measure._watch_for(combo).wins

    combo.produces = ["Infinite mana"]
    assert not measure._watch_for(combo).wins


# --- the engine: one count per game ----------------------------------------


def test_the_first_win_is_the_earliest_game_ending_combo():
    watcher = engine_combos.Watcher([
        watch("loop", "Basalt Monolith", wins=False),
        watch("slow", "Gravecrawler"),
        watch("fast", "Carrion Feeder"),
    ])
    game = FakeGame(creatures=[FakeCard("Basalt Monolith")])
    watcher.look(game, 1)
    assert watcher.first_win == 0, "a loop that wins nothing is not a win"

    game.creatures.append(FakeCard("Carrion Feeder"))
    watcher.look(game, 3)
    game.creatures.append(FakeCard("Gravecrawler"))
    watcher.look(game, 5)
    assert watcher.first_win == 3


def test_two_combos_in_the_same_games_count_those_games_once():
    """Two combos together in the same games are those games, not twice them."""
    one = analysis.run(40, turns=3, seed=1, watch=[watch("a", "Swamp")])
    both = analysis.run(40, turns=3, seed=1, watch=[watch("a", "Swamp"), watch("b", "Swamp")])

    assert both["wins"] == one["wins"]
    assert one["wins"]["by_turn"] == one["combos"]["a"]["by_turn"]
    assert one["wins"]["by_turn"][-1] <= 40


def test_a_run_whose_combos_win_nothing_says_so_with_zeroes():
    result = analysis.run(20, turns=2, seed=1, watch=[watch("x", "Swamp", wins=False)])

    assert result["wins"] == {"games": 20, "by_turn": [0, 0]}


def test_a_run_that_watches_nothing_has_no_wins():
    """The golden parity snapshot and every old run depend on this."""
    assert "wins" not in analysis.as_json(analysis.run(5, turns=2))


def test_wins_survive_json_and_merge_by_addition():
    win = [watch("x", "Swamp")]
    one = analysis.as_json(analysis.run(30, turns=3, seed=1, watch=win))
    two = analysis.as_json(analysis.run(10, turns=3, seed=2, watch=win))

    merged = analysis.merge([one, two])

    assert merged["wins"]["games"] == 40
    assert merged["wins"]["by_turn"] == [
        a + b for a, b in zip(one["wins"]["by_turn"], two["wins"]["by_turn"], strict=True)
    ]
    assert analysis.from_json(merged)["wins"] == merged["wins"]


def test_a_chunk_without_wins_adds_no_games_to_them():
    """Its games watched nothing, so they are no denominator."""
    one = analysis.as_json(analysis.run(30, turns=2, seed=1, watch=[watch("x", "Swamp")]))
    two = analysis.as_json(analysis.run(10, turns=2, seed=2))

    assert analysis.merge([one, two])["wins"]["games"] == 30
    assert analysis.merge([two, one])["wins"]["games"] == 30


def test_a_combo_a_card_is_missing_for_never_counts():
    """The hypothetical deck has the card; the player's deck does not."""
    sample = runner.Sample(key="away", deck=None, watch=watch("away", "Swamp"), games=10)

    payload = runner.run_chunk(10, run_seed=1, index=0, turns=2, on_the_play=True,
                               deck=None, samples=[sample])

    assert "wins" not in payload
    assert payload["combos"]["away"]["games"] == 10


# --- the report: which brackets that speed fits ----------------------------


def tempo(by_turn, games=100, timed=True):
    return report.bracket_tempo(
        {"turns": len(by_turn), "wins": {"games": games, "by_turn": by_turn}},
        timed_combos=timed,
    )


def test_a_deck_with_no_game_ending_combo_fits_bracket_one():
    result = tempo([0] * 9)

    assert result["verdict"].number == 1
    assert all(row.fits for row in result["rows"])
    assert result["typical"] == "No game-ending combo came together in 9 turns."


def test_a_combo_together_by_turn_five_is_too_fast_for_bracket_three():
    """Together by the end of turn five ends a six-turn game too soon."""
    result = tempo([0, 0, 0, 10, 60, 70, 80, 90, 95])

    rows = {row.number: row for row in result["rows"]}
    assert rows[3].share == 60 and rows[3].fits is False
    assert rows[4].share == 0 and rows[4].fits is True
    assert result["verdict"].number == 4
    assert result["typical"] == "Half your games have a game-ending combo together by turn 5."


def test_half_the_games_is_already_too_many():
    rows = {row.number: row for row in tempo([0, 0, 50, 50, 50, 50, 50, 50, 50])["rows"]}

    assert rows[4].fits is False


def test_a_deck_too_fast_for_every_bracket_reads_as_cedh():
    result = tempo([90] * 9)

    assert result["verdict"] is None
    assert result["cedh"] == (5, "cEDH")
    assert result["unchecked"] == []


def test_a_short_run_leaves_the_lower_brackets_unchecked():
    """Six turns, the guest limit: nine- and eight-turn brackets need more."""
    result = tempo([0] * 6)

    rows = {row.number: row for row in result["rows"]}
    assert rows[1].share is None and rows[2].share is None
    assert result["verdict"].number == 3
    assert [row.number for row in result["unchecked"]] == [1, 2]
    assert result["needs_turns"] == 8


def test_a_short_run_that_is_too_fast_still_reads_as_cedh():
    """Brackets nest: too fast for four turns is too fast for nine."""
    result = tempo([80, 90, 95])

    assert result["cedh"] == (5, "cEDH")
    assert result["unchecked"] == []


def test_a_run_too_short_for_any_bracket_says_nothing_either_way():
    result = tempo([0, 0])

    assert result["verdict"] is None
    assert result["cedh"] is None
    assert len(result["unchecked"]) == 4
    assert result["needs_turns"] == 8


def test_an_old_run_asks_to_be_run_again():
    assert report.bracket_tempo({"turns": 9}, timed_combos=True) == {"rerun": True}


def test_a_run_that_timed_no_combo_has_no_section():
    assert report.bracket_tempo({"turns": 9}, timed_combos=False) is None


# --- the page --------------------------------------------------------------


@pytest.fixture
def finished(catalogue):
    owner = User.objects.create_user(email="tempo@example.com", password="pw-test-only")
    deck = deck_services.import_deck(owner=owner, raw=b"34 Swamp\n", name="Tempo").deck
    result = analysis.as_json(analysis.run(20, turns=9, seed=3, watch=[watch("x", "Swamp")]))
    run = SimulationRun.objects.create(
        owner=owner, deck=deck, games_total=20, games_done=20, turns=9, seed=3,
        status=SimulationRun.Status.DONE, finished_at=timezone.now(), result=result,
    )
    return owner, run


@pytest.mark.django_db
def test_the_report_shows_the_check(client, finished):
    owner, run = finished
    client.force_login(owner)

    body = client.get(run.get_absolute_url()).content.decode()

    assert 'id="brackets"' in body
    assert "By speed alone, this deck reads as Bracket 5" in body
    assert "Half your games have a game-ending combo together by turn 1." in body
    assert report.BRACKETS_SOURCE in body
    assert 'href="/methodology/#report-brackets"' in body or "#report-brackets" in body


@pytest.mark.django_db
def test_the_check_reads_in_german(client, finished, settings):
    owner, run = finished
    settings.LANGUAGES = [("en", "English"), ("de", "Deutsch")]
    client.force_login(owner)
    client.cookies["django_language"] = "de"

    body = client.get(run.get_absolute_url(), follow=True).content.decode()

    assert "Commander-Brackets" in body
    assert "By speed alone" not in body
