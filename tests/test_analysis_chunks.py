"""The histogram aggregation and the chunking contract.

Phase 3 splits a run across workers. That only means anything if three things
hold, and this file is where each of them is asserted rather than assumed:

1. a chunk is **reproducible** - the same (seed, index, size) replays exactly;
2. chunks **merge in any order** - workers finish in whatever order they like;
3. a chunked run and a single run of the same size are **the same experiment**.

Point 3 is the subtle one. They are deliberately not bit-identical; see
`analysis.chunk_seed` for why, and `test_a_chunked_run_is_not_bit_identical`
for the assertion that pins the decision so nobody quietly "fixes" it later.
"""

import random

import pytest

from simulation import analysis
from simulation.analysis import Histogram

# --- Histogram -------------------------------------------------------------


def test_a_histogram_still_behaves_like_the_list_it_replaced():
    """`tests/test_statistics.py` is vendored and does `sum(stats["lands"])`.

    That file may not be edited, so the replacement has to answer `sum()` and
    `len()` the way a list of per-game values did. A `Counter` would not:
    `sum(Counter)` adds the keys and returns a plausible, wrong number.
    """
    values = [3, 1, 4, 1, 5, 9, 2, 6]
    histogram = Histogram.of(values)

    assert sum(histogram) == sum(values)
    assert len(histogram) == len(values)
    assert list(histogram) == sorted(values)
    assert analysis.mean(histogram) == pytest.approx(sum(values) / len(values))


def test_a_histogram_costs_the_same_at_any_iteration_count():
    """The whole reason for the change: memory is O(distinct), not O(games)."""
    small = Histogram.of(random.Random(1).choices(range(12), k=100))
    large = Histogram.of(random.Random(1).choices(range(12), k=100_000))

    assert len(large) == 100_000
    assert len(large.counts) <= 12
    assert len(large.counts) >= len(small.counts)


def test_an_all_zero_histogram_is_still_a_histogram():
    """Turn one has 0 mana in many games; `len()` there is 0 but it is not empty."""
    histogram = Histogram.of([0, 0, 0])

    assert sum(histogram) == 0
    assert len(histogram) == 3
    assert bool(histogram) is True
    assert bool(Histogram()) is False


def test_percentiles_are_whole_observed_values():
    """Nearest rank, not interpolation: a median game is a game that happened."""
    histogram = Histogram.of([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])

    assert histogram.percentile(50) == 5
    assert histogram.percentile(90) == 9
    assert histogram.percentile(100) == 10
    assert histogram.percentile(1) == 1
    assert Histogram().percentile(50) == 0


def test_a_histogram_survives_json():
    """A chunk travels through a broker as JSON; the keys become strings."""
    histogram = Histogram.of([0, 2, 2, 7])
    restored = Histogram(histogram.to_json())

    assert restored == histogram
    assert histogram.to_json() == {"0": 1, "2": 2, "7": 1}


def test_histograms_add_up():
    assert Histogram.of([1, 2]) + Histogram.of([2, 3]) == Histogram.of([1, 2, 2, 3])


# --- Chunk seeds -----------------------------------------------------------


def test_a_chunk_seed_is_stable_across_processes():
    """Pinned to a literal on purpose.

    `hash()` on a string is randomised per interpreter, so deriving a chunk
    seed from it would give a different stream on every worker start - and the
    stored seed of a finished run would no longer reproduce it. Only a value
    that a fresh process computes identically can be pinned this way, so this
    literal failing is either a deliberate change of the derivation or a bug.
    """
    assert analysis.chunk_seed(20260917, 0) == analysis.chunk_seed(20260917, 0)
    assert analysis.chunk_seed(20260917, 0) != analysis.chunk_seed(20260917, 1)
    assert analysis.chunk_seed(20260917, 0) == 8232018162448528618


def test_two_runs_of_the_same_chunk_are_identical():
    """The reproducibility a stored run promises."""
    first = analysis.run_chunk(120, run_seed=7, index=2, turns=2)
    second = analysis.run_chunk(120, run_seed=7, index=2, turns=2)

    assert first == second


def test_different_chunks_of_one_run_are_different_games():
    """Otherwise a 50,000 game run would be one 1,000 game run counted 50 times."""
    first = analysis.run_chunk(120, run_seed=7, index=0, turns=2)
    second = analysis.run_chunk(120, run_seed=7, index=1, turns=2)

    assert first != second


# --- Merging ---------------------------------------------------------------


def _chunks(count: int, size: int, seed: int = 99, turns: int = 2):
    return [
        analysis.run_chunk(size, run_seed=seed, index=index, turns=turns)
        for index in range(count)
    ]


def test_merging_is_independent_of_the_order_chunks_come_back_in():
    """Workers finish whenever they finish. The report may not depend on that."""
    chunks = _chunks(4, 100)
    shuffled = list(chunks)
    random.Random(4).shuffle(shuffled)

    assert analysis.merge(shuffled) == analysis.merge(chunks)


def test_merging_is_associative():
    """Merge as they arrive, or all at the end - the same run either way."""
    a, b, c = _chunks(3, 100)

    left = analysis.merge([analysis.merge([a, b]), c])
    right = analysis.merge([a, analysis.merge([b, c])])

    assert left == right == analysis.merge([a, b, c])


def test_a_merged_run_counts_every_game():
    merged = analysis.merge(_chunks(5, 80))

    assert merged["iterations"] == 400
    assert sum(merged["mulligans"].values()) == 400
    for stats in merged["turn_stats"]:
        assert len(Histogram(stats["mana"])) == 400


def test_merging_one_chunk_changes_nothing():
    """The edge case a single-chunk run takes."""
    only = _chunks(1, 60)[0]

    assert analysis.merge([only]) == only


def test_chunks_from_different_scenarios_refuse_to_merge():
    """Averaging a six-turn run into a three-turn one looks fine and means nothing."""
    three = analysis.run_chunk(40, run_seed=1, index=0, turns=3)
    six = analysis.run_chunk(40, run_seed=1, index=1, turns=6)

    with pytest.raises(ValueError, match="different scenarios"):
        analysis.merge([three, six])


def test_merging_nothing_is_an_error_rather_than_an_empty_report():
    with pytest.raises(ValueError, match="nothing to merge"):
        analysis.merge([])


# --- The chunked-vs-monolithic contract ------------------------------------


def test_a_run_survives_a_round_trip_through_json():
    result = analysis.run(80, turns=2, seed=5)
    restored = analysis.from_json(analysis.as_json(result))

    assert restored["mulligans"] == result["mulligans"]
    assert restored["opening_lands"] == result["opening_lands"]
    for got, want in zip(restored["turn_stats"], result["turn_stats"], strict=True):
        assert got == want


def test_a_chunked_run_is_not_bit_identical_to_a_single_run():
    """The documented half of the contract, asserted so it stays documented.

    Making these equal would mean seeding every game separately, which changes
    the random stream of every run that already exists - including the golden
    parity snapshot. The decision is to keep the stream and give up bitwise
    equality between a chunked run and a monolithic one.
    """
    chunked = analysis.merge(_chunks(4, 250, seed=20260917, turns=3))
    single = analysis.as_json(analysis.run(1000, turns=3, seed=20260917))

    assert chunked["iterations"] == single["iterations"] == 1000
    assert chunked["turn_stats"] != single["turn_stats"]


def test_a_chunked_run_and_a_single_run_are_the_same_experiment():
    """The half that has to hold, or chunking would be a lie.

    Same deck, same rules, same number of games: the two have to agree to
    within sampling noise. The seeds are fixed, so this is deterministic - it
    does not flake, it either holds or it is a real disagreement.
    """
    chunked = analysis.from_json(analysis.merge(_chunks(8, 250, seed=4242, turns=3)))
    single = analysis.run(2000, turns=3, seed=4242)

    for index, (got, want) in enumerate(
        zip(chunked["turn_stats"], single["turn_stats"], strict=True)
    ):
        for field in ("mana", "lands"):
            assert got[field].mean == pytest.approx(want[field].mean, abs=0.25), (
                f"turn {index + 1} {field}"
            )
