"""The printing catalogue: ingestion, the two new rungs, and prices.

Three things are being defended here, in descending order of how expensive
they would be to get wrong:

1. **An installation with no printings still works.** `default_cards` is an
   optional 78.8 MB download. Every rung, every page and every count has to
   behave when the table is empty, and the rest of the suite runs in exactly
   that state - see the note in `conftest.py`.
2. **A price is never invented.** No price and a price of zero are different
   claims, and only one of them is ever true.
3. **The set/number pair means one printing.** Measured across all 112,581
   rows of the real file: zero collisions. The tests below pin the *behaviour*
   that rests on it rather than re-measuring the file, which is not committed.
"""

import gzip
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cards import ingest, profiles
from cards.models import BulkImport, OracleCard, Printing
from decks import importers, resolve
from tests.conftest import FIXTURES, PRINTINGS_FIXTURE, PRINTINGS_VERSION

pytestmark = pytest.mark.django_db

VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)


@pytest.fixture
def catalogue():
    ingest.ingest_cards(source=FIXTURES / "oracle_cards_sample.jsonl.gz", updated_at=VERSION)
    ingest.ingest_tags(source=FIXTURES / "oracle_tags_sample.jsonl.gz", updated_at=VERSION)
    profiles.rebuild()


# --- ingestion ---------------------------------------------------------------


def test_printings_load_and_attach_to_their_cards(catalogue, printings):
    assert printings.status == BulkImport.Status.OK
    assert Printing.objects.count() == printings.rows_written
    assert printings.rows_written > 0

    # Every printing has a card, which is the FK doing its job, and several
    # cards have more than one printing, which is the point of the table.
    assert not Printing.objects.filter(oracle_card__isnull=True).exists()
    many = [
        card
        for card in OracleCard.objects.all()
        if card.printings.count() > 1
    ]
    assert many, "a fixture where every card has one printing tests nothing"


def test_a_printing_whose_card_is_absent_is_skipped_rather_than_raising(
    catalogue, tmp_path
):
    """The order dependency made visible: cards before printings.

    One row, pointing at an oracle id nothing in the catalogue has. The correct
    outcome is a run that finishes and counts it - not an IntegrityError that
    takes a nightly ingestion down because somebody loaded the files in the
    wrong order, or because Scryfall shipped a printing of a card that the
    `oracle_cards` file deliberately drops.

    **The premise is built rather than arranged by emptying a table.** Two
    earlier attempts failed for reasons worth recording: the cards table is not
    empty in a full run (`test_engine_adapter` seeds the reference deck outside
    a transaction, so it leaks - trap 22), and emptying it raises `ProtectedError`
    because `DeckCard.oracle_card` is `PROTECT`.
    """
    orphan = {
        "id": "00000000-0000-4000-8000-000000000001",
        "oracle_id": "00000000-0000-4000-8000-0000000000ff",
        "layout": "normal",
        "name": "A Card This Catalogue Does Not Have",
        "set": "zzz", "set_name": "Nowhere", "collector_number": "1",
        "rarity": "common", "lang": "en", "finishes": ["nonfoil"], "prices": {},
    }
    source = tmp_path / "orphan.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(orphan) + "\n")

    result = ingest.ingest_printings(source=source, updated_at=PRINTINGS_VERSION)

    assert result.status == BulkImport.Status.OK
    assert result.rows_seen == 1
    assert result.rows_written == 0
    assert result.rows_skipped == 1
    assert not Printing.objects.filter(scryfall_id=orphan["id"]).exists()


def test_reingesting_the_same_version_downloads_nothing(catalogue, printings):
    again = ingest.ingest_printings(source=PRINTINGS_FIXTURE, updated_at=PRINTINGS_VERSION)
    assert again.skipped
    assert again.rows_written == 0
    assert Printing.objects.count() == printings.rows_written


def test_a_reingest_refreshes_prices_in_place(catalogue, printings):
    """A price refresh is an update, not a second row.

    The realistic use of this table is re-running it for today's prices, so
    the thing that must not happen is 112,577 duplicates.
    """
    before = Printing.objects.count()
    later = datetime(2026, 9, 21, 9, 5, tzinfo=UTC)
    ingest.ingest_printings(source=PRINTINGS_FIXTURE, updated_at=later)

    assert Printing.objects.count() == before
    assert Printing.objects.filter(prices_updated_at=later).count() == before


# --- prices ------------------------------------------------------------------


def test_a_missing_price_is_none_and_never_zero(catalogue, printings):
    """The single most important assertion in this file.

    A zero would render as "€0.00", which reads as "this card is worthless"
    rather than "nobody has traded this printing". They are not the same
    sentence and only one of them is ever true.
    """
    unpriced = Printing.objects.filter(price_eur__isnull=True)
    assert not unpriced.filter(price_eur=0).exists()
    for printing in unpriced[:20]:
        assert printing.price_for("") is None


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        ("", "price_eur"),
        ("Normal", "price_eur"),
        ("nonfoil", "price_eur"),
        ("Foil", "price_eur_foil"),
        ("foil", "price_eur_foil"),
        ("Etched", None),
        ("etched", None),
        ("something nobody writes", "price_eur"),
    ],
)
def test_each_finish_reads_the_price_it_should(finish, expected):
    """Exports disagree about what a finish is called; the mapping absorbs it.

    `Etched` maps to nothing on purpose. Scryfall publishes `usd_etched` and no
    EUR equivalent anywhere in the bulk file, and converting the dollar figure
    would be this application inventing an exchange rate.
    """
    printing = Printing(price_eur=Decimal("1.00"), price_eur_foil=Decimal("5.00"))
    assert printing.price_for(finish) == (getattr(printing, expected) if expected else None)


def test_an_unknown_finish_falls_back_to_nonfoil_rather_than_to_nothing():
    """A card is nonfoil unless somebody says otherwise.

    The opposite default would silently blank the price of every row from a
    format whose finish column we have not seen - which, given that seven
    importers are still unwritten, is a format we will meet.
    """
    printing = Printing(price_eur=Decimal("2.50"))
    assert printing.price_for("Near Mint Nonfoil English") == Decimal("2.50")


# --- the ladder, with printings ----------------------------------------------


def _row(**kwargs):
    kwargs.setdefault("line_number", 1)
    kwargs.setdefault("name", "")
    return importers.ParsedRow(**kwargs)


def test_rung_one_now_finds_any_printing_not_just_the_shipped_one(catalogue, printings):
    """The whole reason the catalogue was worth 78.8 MB.

    `oracle_cards` ships exactly one printing id per card, so rung 1 used to
    hit only when the export happened to name that one - 62 of 214 rows on the
    user's real collection. Any printing id now resolves.
    """
    card = OracleCard.objects.filter(printings__isnull=False).distinct().first()
    other = card.printings.exclude(scryfall_id=card.scryfall_id).first()
    assert other is not None, "need a printing that is not the one oracle_cards ships"

    report = resolve.resolve([_row(name="deliberately wrong", scryfall_id=str(other.pk))])

    assert report.resolved[0].rung == resolve.RUNG_SCRYFALL_ID
    assert report.resolved[0].card == card
    assert report.resolved[0].printing == other


def test_rung_three_resolves_a_set_and_collector_number(catalogue, printings):
    """Rung 3 was a documented hole from Phase 1 until the catalogue landed."""
    printing = Printing.objects.exclude(collector_number="").first()
    row = _row(
        name="not a card name at all",
        set_code=printing.set_code,
        collector_number=printing.collector_number,
    )

    report = resolve.resolve([row])

    assert report.resolved[0].rung == resolve.RUNG_SET_NUMBER
    assert report.resolved[0].card == printing.oracle_card
    assert report.resolved[0].printing == printing


def test_a_set_code_resolves_whatever_case_the_export_wrote_it_in(catalogue, printings):
    """`TOR` and `tor` are one set. Collector numbers are left exactly alone."""
    printing = Printing.objects.exclude(collector_number="").first()
    rows = [
        _row(name="x", set_code=printing.set_code.upper(),
             collector_number=printing.collector_number),
        _row(name="x", set_code=printing.set_code.lower(),
             collector_number=printing.collector_number),
    ]

    report = resolve.resolve(rows)

    assert {r.printing for r in report.resolutions} == {printing}


def test_a_collector_number_is_not_a_number(catalogue, printings):
    """`007` and `7` are different printings, and `341a` is a real one.

    Stripping a leading zero to "tidy up" a collector number resolves the row
    to a different card, or to none, and looks entirely reasonable in a diff.
    """
    printing = Printing.objects.exclude(collector_number="").first()
    padded = _row(name="x", set_code=printing.set_code,
                  collector_number="0" + printing.collector_number)

    report = resolve.resolve([padded])

    assert report.resolutions[0].printing is None


def test_the_printing_is_found_even_when_a_higher_rung_named_the_card(
    catalogue, printings
):
    """Which rung found the *card* and whether we know the *printing* are
    two questions, and a row carrying an oracle id deserves both answers."""
    printing = Printing.objects.exclude(collector_number="").first()
    row = _row(
        name=printing.oracle_card.name,
        oracle_id=str(printing.oracle_card_id),
        set_code=printing.set_code,
        collector_number=printing.collector_number,
    )

    resolution = resolve.resolve([row]).resolutions[0]

    assert resolution.rung == resolve.RUNG_ORACLE_ID
    assert resolution.printing == printing


def test_every_rung_still_works_with_no_printings_at_all(catalogue):
    """No `printings` fixture: this is a fresh installation.

    Rung 1 falls back to `OracleCard.scryfall_id`, rung 3 finds nothing rather
    than raising, and the name rungs are untouched. Nothing here may be an
    error - this is the state most installations will be in.
    """
    # Said rather than assumed, for the reason in the skipped-rows test above.
    Printing.objects.all().delete()
    card = OracleCard.objects.exclude(scryfall_id=None).first()

    by_id = resolve.resolve([_row(name="wrong", scryfall_id=str(card.scryfall_id))])
    assert by_id.resolved[0].card == card
    assert by_id.resolved[0].rung == resolve.RUNG_SCRYFALL_ID
    assert by_id.resolved[0].printing is None

    by_pair = resolve.resolve([_row(name="wrong", set_code="tor", collector_number="1")])
    assert by_pair.unresolved
    assert by_pair.unresolved[0].reason

    by_name = resolve.resolve([_row(name=card.front_name)])
    assert by_name.resolved[0].rung == resolve.RUNG_EXACT_NAME
    assert by_name.resolved[0].printing is None


def test_an_unknown_set_and_number_is_still_refused_not_guessed(catalogue, printings):
    """Rung 3 is an exact match or nothing. It never finds the nearest set."""
    report = resolve.resolve(
        [_row(name="Not A Real Magic Card", set_code="zzz", collector_number="999")]
    )

    assert report.unresolved
    assert report.unresolved[0].printing is None
