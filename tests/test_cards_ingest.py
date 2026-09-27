"""Phase 1: the Scryfall bulk ingestion.

Everything here runs against the committed fixtures in `tests/fixtures/`, never
against the live API. A test suite that downloads 24 MB from a third party is
not a test suite, it is a monitoring check for Scryfall - slow, flaky, and
green for the wrong reasons.

The fixtures are a real slice of the real bulk files (see
`scripts/build_card_fixtures.py`), so these tests exercise the real shapes:
double-faced cards, art series rows, a mana value of one million.
"""

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cards import ingest
from cards.models import BulkImport, OracleCard, OracleCardTag, Tag, TagEdge

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CARDS = FIXTURES / "oracle_cards_sample.jsonl.gz"
TAGS = FIXTURES / "oracle_tags_sample.jsonl.gz"

VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)


@pytest.fixture
def loaded_cards():
    return ingest.ingest_cards(source=CARDS, updated_at=VERSION)


@pytest.fixture
def loaded_tags(loaded_cards):
    return ingest.ingest_tags(source=TAGS, updated_at=VERSION)


def test_fixtures_are_committed_and_small():
    """A fixture nobody can regenerate offline is a liability."""
    for path in (CARDS, TAGS):
        assert path.exists(), f"{path.name} is missing; run scripts/build_card_fixtures.py"
        assert path.stat().st_size < 500_000, "fixture is growing into a download"


# --- loading cards ----------------------------------------------------------


def test_ingest_loads_cards(loaded_cards):
    assert loaded_cards.rows_written == OracleCard.objects.count()
    assert OracleCard.objects.count() > 50


def test_non_card_layouts_are_dropped(loaded_cards):
    """Art series and token rows carry oracle_ids and would import cleanly.

    They are still noise - 3,323 of them in the real file - and no deck list
    will ever reference one.
    """
    assert loaded_cards.rows_skipped >= 2
    assert not OracleCard.objects.filter(layout__in=ingest.NON_CARD_LAYOUTS).exists()


def test_double_faced_cards_keep_their_cost_and_text(loaded_cards):
    """The bug this test exists for: Tergrid looked like a free 5-drop.

    Transform cards leave `mana_cost` and `oracle_text` empty at the top level
    and put them on `card_faces`. Reading only the top level produced a card
    with no cost and no rules text, and nothing errored.
    """
    tergrid = OracleCard.objects.get(front_name="Tergrid, God of Fright")
    assert tergrid.mana_cost == "{3}{B}{B}"
    assert tergrid.oracle_text != ""
    assert "//" in tergrid.name


def test_extreme_values_survive(loaded_cards):
    """Gleemax costs {1000000}. It overflowed a smallint once."""
    gleemax = OracleCard.objects.get(front_name="Gleemax")
    assert gleemax.cmc == 1_000_000
    assert gleemax.mana_cost == "{1000000}"


def test_names_are_stored_folded_for_lookup(loaded_cards):
    """Rung 5 of the import ladder needs an indexable, accent-free form."""
    limdul = OracleCard.objects.get(search_name="lim dul the necromancer")
    assert "û" in limdul.name  # the real name carries a circumflex


# --- idempotency ------------------------------------------------------------


def test_second_run_of_the_same_version_does_nothing(loaded_cards):
    """Safe to call on boot and from a nightly beat task."""
    before = OracleCard.objects.count()
    again = ingest.ingest_cards(source=CARDS, updated_at=VERSION)

    assert again.skipped
    assert again.rows_written == 0
    assert OracleCard.objects.count() == before
    assert BulkImport.objects.filter(status=BulkImport.Status.OK).count() == 1


def test_a_newer_version_reimports_without_duplicating(loaded_cards):
    before = OracleCard.objects.count()
    again = ingest.ingest_cards(source=CARDS, updated_at=LATER)

    assert not again.skipped
    assert OracleCard.objects.count() == before, "updates must not insert duplicates"


def test_force_overrides_the_skip(loaded_cards):
    again = ingest.ingest_cards(source=CARDS, updated_at=VERSION, force=True)
    assert not again.skipped


def test_a_failed_run_does_not_block_the_next_one(tmp_path):
    """Only a successful run counts as 'already imported'."""
    broken = tmp_path / "broken.jsonl.gz"
    with gzip.open(broken, "wt", encoding="utf-8") as handle:
        handle.write("{not json at all}\n")

    with pytest.raises(json.JSONDecodeError):
        ingest.ingest_cards(source=broken, updated_at=VERSION)

    record = BulkImport.objects.get()
    assert record.status == BulkImport.Status.FAILED
    assert not ingest._already_done(BulkImport.Kind.ORACLE_CARDS, VERSION)


# --- the tag rollup ---------------------------------------------------------


def test_tags_and_edges_load(loaded_tags):
    from django.db.models import F

    assert Tag.objects.count() > 100
    assert TagEdge.objects.exists()
    # A tag that is its own parent would make the ancestor walk loop forever.
    # The database constraint forbids it; this checks the loader honours it.
    assert not TagEdge.objects.filter(parent=F("child")).exists()


def test_rollup_reaches_parent_tags_that_have_no_direct_taggings(loaded_tags):
    """The reason the rollup is mandatory rather than an optimisation.

    `removal`, `draw`, `recursion` and `tutor` carry zero direct taggings in
    the real data. Without the rollup, querying for them returns nothing at all
    while the database looks perfectly healthy.
    """
    for slug in ("removal", "recursion", "tutor"):
        tag = Tag.objects.filter(slug=slug).first()
        if tag is None:
            continue
        links = OracleCardTag.objects.filter(tag=tag)
        assert links.exists(), f"{slug} has no cards - the rollup did not run"
        assert not links.filter(is_direct=True).exists(), (
            f"{slug} gained direct taggings upstream; the fixture needs regenerating"
        )


def test_direct_taggings_win_over_rolled_up_ones(loaded_tags):
    """A card can reach the same tag both ways. The stronger claim must survive.

    Order matters in `_write_links`: direct rows first, rolled-up rows second
    with `ignore_conflicts`. Reverse them and a directly tagged card is
    demoted to an inherited one.
    """
    ritual = Tag.objects.filter(slug="ritual").first()
    assert ritual is not None
    dark_ritual = OracleCard.objects.get(front_name="Dark Ritual")
    link = OracleCardTag.objects.get(oracle_card=dark_ritual, tag=ritual)
    assert link.is_direct
    assert link.weight, "a direct tagging carries Scryfall's own weight"


def test_rolled_up_links_never_invent_a_weight(loaded_tags):
    """A weight on an inherited tag would be a fabricated confidence score."""
    assert not OracleCardTag.objects.filter(is_direct=False).exclude(weight="").exists()


def test_a_card_never_holds_the_same_tag_twice(loaded_tags):
    from django.db.models import Count

    duplicates = (
        OracleCardTag.objects.values("oracle_card", "tag")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    assert not duplicates.exists()


def test_taggings_for_unknown_cards_are_counted_not_crashed(loaded_cards):
    """The fixture drops art series rows; their taggings must not break the FK."""
    result = ingest.ingest_tags(source=TAGS, updated_at=VERSION)
    assert result.rows_seen > 0
    assert OracleCardTag.objects.count() > 0


# --- the memory ceiling -----------------------------------------------------


def test_ingestion_streams_rather_than_buffers():
    """The cloudlet constraint, as a test.

    Production has 128 MiB for Django, gunicorn and this. The number that
    matters is that peak allocation does not track file size: the full 24 MB
    bulk file measures ~16 MB peak, and the 54 KiB fixture must measure less
    still. A regression here means something started calling `json.load()`.
    """
    result = ingest.ingest_cards(source=CARDS, updated_at=VERSION, measure=True)
    assert result.peak_memory_kb is not None
    assert result.peak_memory_kb < 50 * 1024, "ingestion stopped streaming"


# --- the command derives profiles -------------------------------------------
#
# The engine reads `DerivedProfile`, never the raw card. Until the 2026-09-25
# review nothing outside this test suite ever derived one, so the go-live's
# only catalogue step would have left production simulating every card as a
# colourless artifact - with every deck page still looking healthy.


def _ingest_command(*args):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("ingest_scryfall", *args, stdout=out)
    return out.getvalue()


def test_the_command_derives_a_profile_for_every_card():
    from cards.models import DerivedProfile

    _ingest_command("--kind", "oracle_cards", "--source", str(CARDS))
    _ingest_command("--kind", "oracle_tags", "--source", str(TAGS))

    assert OracleCard.objects.count() > 50
    assert DerivedProfile.objects.count() == OracleCard.objects.count()


def test_the_profiles_are_rederived_after_the_tags_arrive():
    """Roles come from the tag rollup, so a profile derived before it is stale."""
    from cards.models import DerivedProfile

    _ingest_command("--kind", "oracle_cards", "--source", str(CARDS))
    assert not DerivedProfile.objects.filter(role_tags__contains=["ramp"]).exists(), (
        "no tags yet, so no role that only the tag rollup supplies"
    )

    _ingest_command("--kind", "oracle_tags", "--source", str(TAGS))
    assert DerivedProfile.objects.filter(role_tags__contains=["ramp"]).exists()


def test_missing_profiles_are_healed_even_when_nothing_changed(loaded_cards):
    """A skipped ingest must still notice a catalogue without profiles."""
    from cards.models import BulkImport, DerivedProfile

    BulkImport.objects.update(status=BulkImport.Status.OK)
    assert not DerivedProfile.objects.exists()

    output = _ingest_command(
        "--kind", "oracle_cards", "--source", str(CARDS),
    )
    assert "had no profile" in output or "changed" in output
    assert DerivedProfile.objects.count() == OracleCard.objects.count()


def test_profiles_can_be_rederived_without_downloading_anything(loaded_cards):
    from cards.models import DerivedProfile

    output = _ingest_command("--profiles")
    assert "profiles derived" in output
    assert DerivedProfile.objects.count() == OracleCard.objects.count()
