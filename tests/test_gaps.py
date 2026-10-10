"""The two kinds of gap, and the numbers built on them.

One percentage was answering two questions. Measured over the nine decks in the
development database, **161 of 217 gaps were `priority`** - a field that by
settled decision nothing may ever derive. So a deck of perfectly readable cards
that nobody had annotated scored the same as a deck the engine could barely read
at all, and "coverage" mostly meant "hand-annotated".

What these tests hold:

1. The classification is a property of the **field**, so gaps already stored on
   finished runs and open sessions split correctly with no migration and no
   rewriting of history.
2. The reading score is bounded, and the priority gaps (recorded, but shown
   nowhere since phase 9 C) never pull it down.
3. The decks whose whole purpose is one of the two kinds land on the right side
   of the line. `unmodellable` is meant to be unreadable; the 183-card demo
   deck is readable and simply unjudged, which is exactly the distinction the
   old single number hid.
"""


import pytest
from django.contrib.auth import get_user_model

from decks import seeding
from decks.fixtures import BY_KEY, LAND_LIGHT, UNMODELLABLE
from simulations import gaps
from simulations.engine import adapter
from simulations.models import SimulationRun

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="gaps@example.com",
                                    password="pw-for-test-only")


@pytest.fixture
def build(owner):
    def _build(shape):
        return seeding.build(BY_KEY[shape] if isinstance(shape, str) else shape,
                             owner).deck

    return _build


# --- the classification itself ---------------------------------------------

def test_the_two_fields_nothing_may_derive_are_judgement():
    assert gaps.kind_of("priority") == gaps.JUDGEMENT
    assert gaps.kind_of("accelerant") == gaps.JUDGEMENT


def test_everything_else_is_the_applications_own_limit():
    for field in ("profile", "mana_abilities", "ritual_gain", "anything_new"):
        assert gaps.kind_of(field) == gaps.READING


def test_a_field_nobody_has_seen_counts_as_readable():
    """The default has to be the half we are responsible for.

    A new gap field added by a later phase and forgotten here should land in
    "the engine could not read it", because that is the list somebody works
    through. Landing in "only the author can answer this" would quietly excuse
    it.
    """
    assert gaps.kind_of("a_field_from_phase_nine") == gaps.READING


def test_the_judgement_list_is_the_adapters_list():
    """Two copies of this list is one copy too many."""
    assert set(adapter.JUDGEMENT_FIELDS) == set(gaps.JUDGEMENT_FIELDS)


# --- reading both live gaps and stored ones --------------------------------

def test_a_stored_gap_classifies_exactly_like_a_live_one():
    """The property that makes a migration unnecessary.

    Runs finished before this split existed carry `{"card", "field", "reason"}`
    and nothing else. They have to split correctly from the field alone, or
    every historical report would have to be either rewritten or wrong.
    """
    live = adapter.Gap("Dark Ritual", "priority", "nobody said")
    stored = {"card": "Dark Ritual", "field": "priority", "reason": "nobody said"}

    assert live.kind == gaps.JUDGEMENT
    assert gaps.kind_of(gaps.field_of(stored)) == gaps.JUDGEMENT
    assert gaps.card_of(live) == gaps.card_of(stored)


def test_counting_is_by_distinct_card_not_by_gap():
    """One card with three gaps is one card, or the percentages exceed 100."""
    rows = [
        adapter.Gap("Cabal Coffers", "profile", "scales with the board"),
        adapter.Gap("Cabal Coffers", "mana_abilities", "how much could not be read"),
        adapter.Gap("Sol Ring", "priority", "nobody said"),
    ]
    assert gaps.cards_with(rows) == {"Cabal Coffers", "Sol Ring"}
    assert gaps.cards_with(rows, gaps.READING) == {"Cabal Coffers"}
    assert gaps.cards_with(rows, gaps.JUDGEMENT) == {"Sol Ring"}


def test_a_card_can_be_in_both_halves_at_once():
    """Smothering Tithe is unreadable AND unjudged. It counts in both."""
    rows = [
        adapter.Gap("Smothering Tithe", "profile", "grants an ability away"),
        adapter.Gap("Smothering Tithe", "priority", "nobody said"),
    ]
    assert gaps.cards_with(rows, gaps.READING) == {"Smothering Tithe"}
    assert gaps.cards_with(rows, gaps.JUDGEMENT) == {"Smothering Tithe"}
    assert len(gaps.cards_with(rows)) == 1


def test_a_share_is_never_negative_and_never_over_one():
    assert gaps.share(0, 0) == 0.0
    assert gaps.share(4, 0) == 1.0
    assert gaps.share(4, 4) == 0.0
    # The commander bug (trap 24) in its general form: more gaps than cards.
    assert gaps.share(2, 3) == 0.0


# --- the numbers on a real deck --------------------------------------------

def test_the_reading_score_is_bounded_and_ignores_the_priority_gaps(build):
    conversion = adapter.convert(build(LAND_LIGHT))
    assert conversion.cards_unreadable <= conversion.cards_with_gaps
    assert 0.0 <= conversion.readable <= 1.0
    # At least as good as the number that mixes both kinds of gap.
    assert conversion.readable >= gaps.share(conversion.cards_total, conversion.cards_with_gaps)


def test_the_unreadable_deck_is_unreadable_and_not_merely_unjudged(build):
    """The shape exists to be unreadable, so it has to fail the reading half.

    The old single score could not tell this deck from one the engine reads
    perfectly and nobody has annotated - which is precisely the demo deck, and
    precisely the confusion this split exists to end.
    """
    conversion = adapter.convert(build(UNMODELLABLE))
    assert conversion.readable < 0.6, "the deck of unreadable cards reads too well"
    # Four since engine version 19 read Toxic Deluge, Phyrexian Tower and
    # Ashnod's Altar (P19 R15): what the fixture still holds that it cannot.
    assert conversion.cards_unreadable >= 4


def test_a_deck_nobody_annotated_still_reads_well(build):
    """The other side of the same line.

    Every card here is an ordinary card the deriver handles; what is missing is
    a person's opinion on casting order, which no page counts any more (phase 9
    C). Before the split this deck and the one above were both simply "low
    coverage".
    """
    conversion = adapter.convert(build(LAND_LIGHT))
    mixed = gaps.share(conversion.cards_total, conversion.cards_with_gaps)
    assert conversion.readable > mixed


# --- what a stored run reports ---------------------------------------------

def test_a_finished_run_splits_the_gaps_it_was_computed_with(build, owner):
    """Straight off the JSON column, with no adapter in sight."""
    deck = build(LAND_LIGHT)
    conversion = adapter.convert(deck)
    run = SimulationRun.objects.create(
        deck=deck, owner=owner, games_total=1, seed=1,
        gaps=[{"card": gap.card, "field": gap.field, "reason": gap.reason}
              for gap in conversion.gaps],
        cards_total=conversion.cards_total,
        cards_with_gaps=conversion.cards_with_gaps,
    )

    assert run.cards_unreadable == conversion.cards_unreadable
    assert run.cards_read + run.cards_unreadable == run.cards_total
    assert run.readable_pct == 100.0 * conversion.readable


def test_the_two_stored_lists_partition_the_whole(build, owner):
    deck = build(UNMODELLABLE)
    conversion = adapter.convert(deck)
    run = SimulationRun.objects.create(
        deck=deck, owner=owner, games_total=1, seed=1,
        gaps=[{"card": gap.card, "field": gap.field, "reason": gap.reason}
              for gap in conversion.gaps],
        cards_total=conversion.cards_total,
        cards_with_gaps=conversion.cards_with_gaps,
    )

    judgement = gaps.of_kind(run.gaps, gaps.JUDGEMENT)
    assert len(run.reading_gaps) + len(judgement) == len(run.gaps)
    assert not set(map(id, run.reading_gaps)) & set(map(id, judgement))


def test_a_run_from_before_the_split_still_splits(build, owner):
    """A row written by an older version of this application.

    Three keys, no `kind`, no engine version. It has to answer the new question
    anyway, because the alternative was a data migration that edited a record of
    what the engine saw.
    """
    run = SimulationRun.objects.create(
        deck=build(LAND_LIGHT), owner=owner, games_total=1, seed=1,
        cards_total=4,
        cards_with_gaps=2,
        gaps=[
            {"card": "Cabal Coffers", "field": "mana_abilities",
             "reason": "makes mana, but how much could not be read"},
            {"card": "Sol Ring", "field": "priority",
             "reason": "no one said how early to cast it"},
        ],
    )

    assert run.cards_unreadable == 1
    assert run.cards_read == 3
    assert run.readable_pct == 75.0


def test_a_run_counts_copies_and_an_older_one_keeps_its_distinct_pair(build, owner):
    """Phase 10 T5.9: "of 100" on a new run; an old run is never mixed up."""
    common = {"deck": build(LAND_LIGHT), "owner": owner, "games_total": 1, "seed": 1,
              "cards_total": 4, "cards_with_gaps": 1,
              "gaps": [{"card": "Cabal Coffers", "field": "mana_abilities",
                        "reason": "makes mana, but how much could not be read"}]}
    new = SimulationRun.objects.create(**common, copies_total=100, copies_unreadable=2)
    old = SimulationRun.objects.create(**common)

    assert (new.coverage_read, new.coverage_total, new.readable_pct) == (98, 100, 98.0)
    assert (old.coverage_read, old.coverage_total, old.readable_pct) == (3, 4, 75.0)
