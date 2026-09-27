"""Phase 2 acceptance: the generalized engine still produces the old numbers.

Phase 2 rewrote nearly every line of `simulation/`. Cabal Coffers, Urborg,
Crypt Ghast and Jet Medallion stopped being cards the code knows by name and
became instances of general rules; the priority table moved onto the cards; the
deck became an argument instead of a module constant.

The 54 unit tests check that the parts still work. **This file checks that the
whole still produces the same numbers**, against a snapshot taken from the
untouched original engine in the sibling magic-project repository
(`scripts/build_engine_golden.py`).

Why a committed snapshot rather than importing the original: the sibling
repository will move, and this guarantee has to outlive it.

If one of these fails, the generalization changed behaviour. That may be
intended - but it is never a reason to regenerate the fixture until somebody
has looked at the diff and said so out loud.
"""

import json
from pathlib import Path

import pytest

from simulation import analysis

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "engine_golden.json"


def summarise(result: dict) -> dict:
    """Mirror of `scripts/build_engine_golden.py::summarise`.

    Not a byte-for-byte mirror any more, and the difference is the point. The
    original engine collected one integer per game in a list; Phase 3 counts
    them in a `Histogram` instead, so that a run can be split across workers
    and merged back. Both are read here into the same (mean, count, total),
    and **the golden fixture is untouched** - the evidence that the change was
    a change of representation and not of behaviour is that the numbers coming
    out of this function still match a snapshot taken before it existed.

    The generator script keeps the list branch alone, because it runs against
    the untouched original engine in the sibling repository.
    """
    summary = {
        "iterations": result["iterations"],
        "turns": result["turns"],
        "on_the_play": result["on_the_play"],
        "mulligans": {str(k): v for k, v in sorted(result["mulligans"].items())},
        "opening_lands": {str(k): v for k, v in sorted(result["opening_lands"].items())},
        "turn_stats": [],
    }
    for stats in result["turn_stats"]:
        entry = {}
        for key, value in sorted(stats.items()):
            if isinstance(value, analysis.Histogram):
                entry[key] = {
                    "mean": round(value.mean, 9),
                    "count": len(value),
                    "total": value.total,
                }
            elif isinstance(value, list):
                entry[key] = {
                    "mean": round(sum(value) / len(value), 9),
                    "count": len(value),
                    "total": sum(value),
                }
            else:
                entry[key] = value
        summary["turn_stats"].append(entry)
    return summary


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.exists(), "run scripts/build_engine_golden.py"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_snapshot_covers_both_play_and_draw(golden):
    """On the play and on the draw are different code paths in `begin_turn`."""
    assert {snap["scenario"]["on_the_play"] for snap in golden} == {True, False}


def test_the_snapshot_reaches_past_turn_three(golden):
    """Necropotence, Arena and Dark Confidant only compound over several turns."""
    assert max(snap["scenario"]["turns"] for snap in golden) >= 6


# The one place the generalized engine deliberately disagrees with the old one.
#
# `draw_engine` used to be a hand-maintained list of seven card names in
# analysis.py. It is now read from the cards' own role tags - and the tags
# include `Liliana, Dreadhorde General`, which the name list had simply been
# missing. She draws a card whenever a creature dies; the hand annotation in
# simulation/cards.py has always said `draw_engine`.
#
# So the new number is the correct one, and the old snapshot is kept as
# evidence rather than regenerated. It only shows up from turn 5, because
# nothing casts a six-drop before then.
CORRECTED_FIELD = "draw_engine"
CORRECTED_CARD = "Liliana, Dreadhorde General"


def _divergences(golden):
    """Every (scenario, turn, field) where the current engine differs."""
    found = []
    for snapshot in golden:
        scenario = snapshot["scenario"]
        result = analysis.run(
            scenario["iterations"],
            on_the_play=scenario["on_the_play"],
            turns=scenario["turns"],
            seed=scenario["seed"],
        )
        actual = summarise(result)
        expected = snapshot["summary"]

        for key in ("mulligans", "opening_lands"):
            if actual[key] != expected[key]:
                found.append((scenario["seed"], 0, key, actual[key], expected[key]))

        turns = zip(actual["turn_stats"], expected["turn_stats"], strict=True)
        for index, (got, want) in enumerate(turns):
            for field in want:
                if got[field] != want[field]:
                    found.append((scenario["seed"], index + 1, field, got[field], want[field]))
    return found


def test_aggregates_match_the_original_engine(golden):
    """The headline guarantee of Phase 2.

    Every mulligan count, every opening-land count, every per-turn mean and
    every milestone counter, identical to the engine before generalization -
    except the one documented correction.
    """
    unexpected = [row for row in _divergences(golden) if row[2] != CORRECTED_FIELD]
    assert not unexpected, "\n".join(
        f"seed {seed} turn {turn} {field}: new={new} old={old}"
        for seed, turn, field, new, old in unexpected
    )


def test_the_only_divergence_is_the_corrected_draw_engine_list(golden):
    """Pin the exception so it cannot quietly grow into several.

    If a second field ever starts disagreeing, that is a regression, not
    another improvement - and this test is what says so.
    """
    divergences = _divergences(golden)
    assert {row[2] for row in divergences} <= {CORRECTED_FIELD}

    # The correction only ever adds hits: the old list was a strict subset.
    for _seed, _turn, _field, new, old in divergences:
        assert new >= old, "the corrected list must never report fewer engines"


def test_the_correction_is_caused_by_the_card_we_think_it_is(golden):
    """Name the cause, so the exception above stays anchored to a reason."""
    from simulation.fixtures import chainer

    old_names = {
        "Necropotence", "Phyrexian Arena", "Dark Confidant", "Greed",
        "Dread Presence", "Morbid Opportunist", "Braids, Arisen Nightmare",
    }
    tagged = {card.name for card in chainer.SPELLS if "draw_engine" in card.tags}

    assert tagged - old_names == {CORRECTED_CARD}
    assert not old_names - tagged, "a card left the tag set; the correction changed shape"


def test_a_deck_passed_in_behaves_like_the_default(golden):
    """`Game(rng, deck=...)` and `Game(rng)` must agree on the same deck.

    This is the injection point the database adapter uses. If passing the
    fixture explicitly gave different numbers from letting it default, nothing
    built on top of the adapter could be trusted.
    """
    from simulation.fixtures import chainer

    scenario = golden[0]["scenario"]
    explicit = analysis.run(
        400, on_the_play=scenario["on_the_play"], turns=3, seed=7, deck=chainer.DECK
    )
    default = analysis.run(400, on_the_play=scenario["on_the_play"], turns=3, seed=7)

    assert summarise(explicit) == summarise(default)
