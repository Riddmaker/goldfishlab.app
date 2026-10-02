"""Phase 9 E: what a player had seen by each turn, by type, category and mana value.

What these tests are for, in order of how much they matter:

1. **The count is the real count.** Every card out of the library is seen, a
   card a mulligan put back is not, and the counts add up to the hand kept
   plus the cards drawn - checked against that arithmetic, not against the
   counting code itself.
2. **Nothing else moved.** Counting draws no random number, so the games are
   the games they were; the golden parity test says so for the whole engine,
   and one test here says it for a single seed.
3. **Chunks add up**, in any order, and a chunk from before the count existed
   does not produce half a measurement.
4. **Old runs are not broken** - the page asks for a re-run instead. The two
   page tests live in `test_simulations_runs.py`, beside the run fixtures.
"""

import random
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from simulation import analysis
from simulation.cards import CREATURE, LAND, SORCERY, Card, DeckDefinition
from simulation.game import Game
from simulations import charts, report
from simulations.engine import runner
from simulations.engine.adapter import card_types


def _card(name, kind, mv=0, *, types=(), categories=(), tags=()):
    return Card(name, mv, 0, mv, kind, frozenset(tags), types=frozenset(types),
                categories=frozenset(categories))


LAND_CARD = _card("Wastes", LAND, types={"land"})
BEAR = _card("Bear", CREATURE, 2, types={"creature"}, categories={"ramp"})
GOLEM = _card("Golem", CREATURE, 9, types={"artifact", "creature"})
BLAST = _card("Blast", SORCERY, 1, types={"sorcery"},
              categories={"removal", "draw"})

#: Nothing in it draws, tutors or mills, so every card seen is one from the
#: hand kept or one draw step - the arithmetic the count is checked against.
PLAIN_DECK = DeckDefinition(
    name="plain", commander=None,
    library=(LAND_CARD,) * 38 + (BEAR,) * 40 + (GOLEM,) * 11 + (BLAST,) * 10,
)


# --- the groups --------------------------------------------------------------

def test_every_card_is_filed_under_its_types_roles_and_mana_value():
    keys, table = analysis.seen_groups(PLAIN_DECK)

    def groups_of(card):
        return {keys[slot] for slot in table[card.name]}

    assert groups_of(BEAR) == {"type:creature", "role:ramp", "mv:2"}
    # Two types on one face count twice; mana value nine is "7+".
    assert groups_of(GOLEM) == {"type:artifact", "type:creature", "mv:7"}
    assert groups_of(BLAST) == {"type:sorcery", "role:draw", "role:removal", "mv:1"}


def test_the_categories_are_read_from_categories_not_from_the_game_tags():
    """`tags` is what the game's metrics read, and the built-in annotations
    may rewrite it; the statistics must not follow them there."""
    arena = _card("Arena", SORCERY, 3, tags={"draw_engine"}, categories={"draw"})
    keys, table = analysis.seen_groups(DeckDefinition(name="d", commander=None,
                                                      library=(arena,)))

    assert {keys[slot] for slot in table[arena.name]} == {"role:draw", "mv:3"}


def test_a_role_that_is_no_category_is_not_counted():
    """The engine's own roles - a sac outlet, a drain payoff - are milestones
    on the report already, and no deck list sorts cards by them."""
    outlet = _card("Altar", SORCERY, 3, categories={"sac_outlet", "draw_engine"})
    keys, _ = analysis.seen_groups(DeckDefinition(name="d", commander=None,
                                                  library=(outlet,)))

    assert keys == ("mv:3",)


def test_every_category_has_a_name_on_the_page():
    assert [key for key, _ in report.SEEN_ROLES] == list(analysis.SEEN_CATEGORIES)


def test_a_land_has_no_mana_value_group():
    """Thirty-eight zeroes would bury the curve the chart is there to show."""
    keys, table = analysis.seen_groups(PLAIN_DECK)

    assert {keys[slot] for slot in table[LAND_CARD.name]} == {"type:land"}
    assert "mv:0" not in keys


def test_a_group_the_deck_has_no_card_in_does_not_exist():
    keys, _ = analysis.seen_groups(PLAIN_DECK)

    assert "type:planeswalker" not in keys
    assert "role:tutor" not in keys


def test_the_layout_does_not_depend_on_the_order_the_cards_came_in():
    shuffled = DeckDefinition(name="plain", commander=None,
                              library=tuple(reversed(PLAIN_DECK.library)))

    assert analysis.seen_groups(shuffled) == analysis.seen_groups(PLAIN_DECK)


# --- the count ---------------------------------------------------------------

@pytest.mark.parametrize("on_the_play", [False, True])
def test_the_count_is_the_hand_kept_plus_every_draw(on_the_play):
    groups = analysis.seen_groups(PLAIN_DECK)
    keys = groups[0]
    type_slots = [slot for slot, key in enumerate(keys) if key.startswith("type:")]
    rng = random.Random(7)

    for _ in range(200):
        game = analysis.simulate_game(rng, on_the_play=on_the_play, turns=4,
                                      deck=PLAIN_DECK, groups=groups)
        kept = 7 - Game.cards_to_bottom(game["mulligans"])
        for turn, snapshot in enumerate(game["per_turn"], start=1):
            draws = turn - 1 if on_the_play else turn
            # Golem is two types, so it is counted twice among the types.
            golems = snapshot["seen"][keys.index("mv:7")]
            seen = sum(snapshot["seen"][slot] for slot in type_slots) - golems
            assert seen == kept + draws


def test_a_tutored_card_is_not_a_drawn_card():
    """Phase 10 T5.1: the tutors in this deck find cards every game, and the
    count is still exactly the hand kept plus the draws."""
    from simulation import agent
    from simulation.cards import TutorSpec
    from simulation.fixtures import chainer

    swamp = next(card for card in chainer.DECK.library if card.name == "Swamp")
    tutor = Card("Tutor", 1, 0, 1, SORCERY, tutor=TutorSpec(to_hand=True, count=1),
                 types=frozenset({"sorcery"}))
    deck = DeckDefinition(name="tutors", commander=None,
                          library=(swamp,) * 38 + (tutor,) * 30 + (BEAR,) * 31)
    rng = random.Random(3)
    tutored = 0
    for _ in range(100):
        game = Game(rng, deck=deck)
        game.take_opening_hand()
        for _turn in range(5):
            agent.take_turn(game)
        kept = 7 - Game.cards_to_bottom(game.mulligans)
        draws = 4  # on the play: no draw on turn one
        assert len(game.drawn) == kept + draws
        tutored += deck.size - len(game.library) - len(game.drawn)

    assert tutored > 100, "the tutors found nothing, so this proved nothing"


def test_counting_changes_no_game():
    """No random number is drawn to count, so the same seed plays the same game."""
    counted = analysis.simulate_game(random.Random(11), turns=5, deck=PLAIN_DECK,
                                     groups=analysis.seen_groups(PLAIN_DECK))
    plain = analysis.simulate_game(random.Random(11), turns=5, deck=PLAIN_DECK)

    assert "seen" not in plain["per_turn"][0]
    for with_count, without in zip(counted["per_turn"], plain["per_turn"], strict=True):
        assert with_count["permanent_list"] == without["permanent_list"]
        assert with_count["mana"] == without["mana"]
    assert counted["opening_hand"] == plain["opening_hand"]


def test_what_has_been_seen_is_never_unseen():
    result = analysis.run(300, turns=5, seed=3, deck=PLAIN_DECK)

    for entry in result["seen"].values():
        for field in ("cards", "games", "squares"):
            assert entry[field] == sorted(entry[field])
        assert all(games <= 300 for games in entry["games"])
        assert all(games <= cards for cards, games in zip(entry["cards"], entry["games"],
                                                          strict=True))


def test_the_spread_is_counted_beside_the_mean():
    """Phase 10: the sum of squares, so the page can show "3.2 ± 1.1"."""
    result = analysis.run(300, turns=5, seed=3, deck=PLAIN_DECK)

    for entry in result["seen"].values():
        for cards, squares in zip(entry["cards"], entry["squares"], strict=True):
            # Cauchy-Schwarz: n * sum(x^2) >= (sum x)^2, so a variance >= 0.
            assert 300 * squares >= cards * cards
    # Some games hold two or more creatures, so the squares outgrow the sum.
    creatures = result["seen"]["type:creature"]
    assert creatures["squares"][0] > creatures["cards"][0]


def test_a_chunk_from_before_the_spread_leaves_the_mean_alone():
    first, second = _chunks(2)
    for entry in second["seen"].values():
        del entry["squares"]

    merged = analysis.merge([first, second])["seen"]["type:creature"]

    assert "squares" not in merged
    assert merged["cards"] == [a + b for a, b in zip(
        first["seen"]["type:creature"]["cards"], second["seen"]["type:creature"]["cards"],
        strict=True)]


def test_the_hand_written_deck_still_gets_its_curve():
    """It carries neither type lines nor categories, only the curve counts."""
    result = analysis.run(50, turns=3, seed=1)

    assert "mv:2" in result["seen"]
    assert all(key.startswith("mv:") for key in result["seen"])


# --- chunks and storage --------------------------------------------------------

def _chunks(count=3, games=60):
    return [analysis.run_chunk(games, 5, index, turns=3, deck=PLAIN_DECK)
            for index in range(count)]


def test_chunks_add_up_in_any_order():
    chunks = _chunks()
    forward = analysis.merge(chunks)
    backward = analysis.merge(list(reversed(chunks)))

    assert forward["seen"] == backward["seen"]
    bear = [chunk["seen"]["type:creature"]["cards"][2] for chunk in chunks]
    assert forward["seen"]["type:creature"]["cards"][2] == sum(bear)


def test_a_group_only_one_chunk_has_is_kept():
    first, second = _chunks(2)
    del second["seen"]["role:ramp"]

    merged = analysis.merge([first, second])

    assert merged["seen"]["role:ramp"] == first["seen"]["role:ramp"]


def test_a_chunk_from_before_the_count_makes_no_half_measurement():
    first, second = _chunks(2)
    del second["seen"]

    assert "seen" not in analysis.merge([first, second])
    assert "seen" not in analysis.merge([second, first])


def test_the_count_survives_being_stored():
    stored = analysis.merge(_chunks())

    assert analysis.as_json(analysis.from_json(stored))["seen"] == stored["seen"]


# --- the adapter ---------------------------------------------------------------

@pytest.mark.parametrize(("type_line", "expected"), [
    ("Artifact Creature — Golem", {"artifact", "creature"}),
    ("Legendary Planeswalker — Liliana", {"planeswalker"}),
    ("Kindred Instant — Elf", {"instant"}),
    ("Basic Land — Swamp", {"land"}),
    # The front face only: a modal card counts as what its front says.
    ("Sorcery // Land", {"sorcery"}),
    ("Legendary Creature — Human Wizard // Legendary Planeswalker — Jace",
     {"creature"}),
    ("", set()),
])
def test_the_type_line_gives_the_printed_types(type_line, expected):
    assert card_types(SimpleNamespace(type_line=type_line)) == frozenset(expected)


@pytest.mark.parametrize(("tags", "scope", "expected"), [
    # Nobody replaced the roles: the community's.
    (None, None, {"draw", "draw_engine"}),
    # The built-in annotation replaced them for the game - not for the chart.
    (["draw_engine"], "builtin", {"draw", "draw_engine"}),
    # The user did, on this deck or on all of theirs: their list, as given.
    (["draw_engine"], "deck", {"draw_engine"}),
    (["removal"], "user", {"removal"}),
    # An empty list is an answer too: "this card has no role at all".
    ([], "deck", set()),
])
def test_only_the_users_own_roles_replace_the_community_ones(tags, scope, expected):
    from simulations.engine.adapter import _categories

    profile = SimpleNamespace(role_tags=["draw", "draw_engine"])
    overrides = {} if tags is None else {"tags": tags}

    assert _categories(profile, overrides, scope) == frozenset(expected)


# --- the chart geometry --------------------------------------------------------

def test_a_line_runs_from_the_first_turn_to_the_last():
    chart = charts.line_chart([("a", "A", [0, 50, 100])], ["1", "2", "3"],
                              top_value=100, y_ticks=charts.PERCENT_TICKS)
    points = [tuple(map(float, point.split(","))) for point in chart.lines[0].points.split()]

    assert points[0] == (chart.left, chart.bottom)
    assert points[-1] == (chart.right, chart.top)
    assert points[1][1] == pytest.approx((chart.top + chart.bottom) / 2)


def test_a_value_off_the_scale_stays_on_the_chart():
    chart = charts.line_chart([("a", "A", [250])], ["1"], top_value=100, y_ticks=[])
    _, y = chart.lines[0].points.split(",")

    assert float(y) == chart.top


def test_a_series_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError, match="2 values for 3"):
        charts.line_chart([("a", "A", [1, 2])], ["1", "2", "3"], top_value=1, y_ticks=[])


@pytest.mark.parametrize(("largest", "top", "ticks"), [
    (0.0, 1, [0, 1]),
    (2.4, 3, [0, 1, 2, 3]),
    (4.0, 4, [0, 1, 2, 3, 4]),
    (13.2, 16, [0, 4, 8, 12, 16]),
])
def test_a_count_scale_stops_at_a_round_number(largest, top, ticks):
    scale_top, scale_ticks = charts.count_scale(largest)

    assert scale_top == top
    assert [value for value, _ in scale_ticks] == ticks


# --- the report ----------------------------------------------------------------

def _result(**overrides):
    result = analysis.from_json(analysis.merge(_chunks(1, games=100)))
    result.update(overrides)
    return result


def test_the_report_draws_only_the_categories_the_deck_has():
    seen = report.seen(_result())

    assert [line.label for line in seen["roles"].lines] == ["Ramp", "Card draw", "Removal"]
    assert [line.label for line in seen["types"].lines] == [
        "Creature", "Artifact", "Sorcery", "Land"]


def test_the_curve_shares_one_scale_across_turns():
    seen = report.seen(_result())
    last = seen["curve"][-1]["bars"]

    assert max(bar.height for bar in last) == pytest.approx(100.0)
    assert [bar.label for bar in last] == ["0", "1", "2", "3", "4", "5", "6", "7+"]
    for step in seen["curve"]:
        assert all(0 <= bar.height <= 100 for bar in step["bars"])


def test_a_run_from_before_the_count_has_no_section():
    result = _result()
    del result["seen"]

    assert report.seen(result) is None


# --- phase 10 D: lines that explain themselves ---------------------------------

def test_a_band_is_the_mean_plus_and_minus_the_spread_and_stops_at_zero():
    points = charts.band([1.0, 3.0], [2.0, 1.0], top_value=4.0).split()

    upper, lower = points[:2], points[2:]
    assert [float(p.split(",")[1]) for p in upper] == [
        charts._y(3.0, 4.0), charts._y(4.0, 4.0)]
    # Back from right to left; the left edge would be -1, so it sits on zero.
    assert [float(p.split(",")[1]) for p in lower] == [
        charts._y(2.0, 4.0), charts._y(0.0, 4.0)]


def test_the_typical_turn_is_said_in_words_and_no_longer_drawn():
    # Phase 11 K14: the dashed marker went; the info sentence carries the turn.
    assert report._typical_sentence([10.0, 49.9, 50.0, 80.0], "have one", 4) == (
        "Half your games have one by turn 3.")
    assert report._typical_sentence([10.0, 20.0], "have one", 2) == (
        "Fewer than half your games have one by turn 2.")
    html = render_to_string("simulations/_line_chart.html", {
        "chart": report.seen(_result())["roles"], "name": "t", "title": "t"})
    assert "seen-marker" not in html


def test_every_line_on_the_report_says_what_it_counts():
    seen = report.seen(_result())

    for line in seen["roles"].lines:
        assert line.info.startswith(report.SEEN_ROLE_INFO[line.key])
        assert "by turn 3" in line.info
    for line in seen["types"].lines:
        assert line.band, "a run that counted squares draws a band"
        assert "±" in line.info


def test_a_run_without_the_spread_has_no_band_and_no_plus_minus():
    result = _result()
    for entry in result["seen"].values():
        entry.pop("squares", None)

    seen = report.seen(result)

    assert not any(line.band for line in seen["types"].lines)
    assert not any("±" in line.info for line in seen["types"].lines)


def test_every_category_type_and_milestone_has_a_sentence():
    assert set(report.SEEN_ROLE_INFO) == {key for key, _ in report.SEEN_ROLES}
    assert set(report.SEEN_TYPE_INFO) == set(runner.SEEN_CARD_TYPES)
    assert set(report.MILESTONE_INFO) == {key for key, _ in report.MILESTONES}
