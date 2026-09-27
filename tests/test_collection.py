"""Phase 6 §1: what you own, and whether it is enough.

The phase document calls the deck-against-collection diff "the feature the
original deck project actually needed", so most of this file is about that one
question and the two ways of getting it wrong:

**Counting basics with everything else.** A deck wanting thirty-eight Swamps
against a collection holding twenty-eight is ten cards short by arithmetic and
zero cards short by any player's reckoning. Reporting one number would pick a
side; the shortfall reports both and a test asserts they stay apart.

**Forgetting the commander.** It is not a `DeckCard` (trap 7), so a query over
deck entries alone would tell somebody they can build a deck whose commander
they do not own - the one card no proxy and no substitution gets you past.

The import half is thinner on purpose: it reuses `decks.services.decode`,
`decks.parse` and `decks.resolve` wholesale, all of which Phase 1 already
tests. What is new is what the resolved rows get written into, and that a
second import **replaces** rather than merges.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from cards import ingest, profiles
from cards.models import OracleCard
from collection import services
from collection.models import Collection, CollectionItem
from decks import seeding
from decks.fixtures import LAND_LIGHT
from decks.models import Deck, DeckCard
from tests.conftest import PRINTINGS_VERSION

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VERSION = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
PASSWORD = "pw-for-test-only"


@pytest.fixture
def catalogue():
    ingest.ingest_cards(source=FIXTURES / "oracle_cards_sample.jsonl.gz",
                        updated_at=VERSION)
    ingest.ingest_tags(source=FIXTURES / "oracle_tags_sample.jsonl.gz",
                       updated_at=VERSION)
    profiles.rebuild()


@pytest.fixture
def owner(catalogue):
    return User.objects.create_user(email="owner@example.com", password=PASSWORD)


@pytest.fixture
def signed_in(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def export():
    """The committed Archidekt sample - a real collection export's shape."""
    return (FIXTURES / "archidekt_sample.csv").read_bytes()


def _card(name: str) -> OracleCard:
    return OracleCard.objects.get(front_name=name)


def _own(owner, name: str, quantity: int, *, set_code="tst", number="1"):
    """Put `quantity` of one card in this person's collection."""
    collection, _ = Collection.objects.get_or_create(owner=owner)
    CollectionItem.objects.create(
        collection=collection, oracle_card=_card(name), quantity=quantity,
        set_code=set_code, collector_number=number,
    )
    return collection


# --- importing --------------------------------------------------------------

def test_an_export_becomes_a_collection(owner, export):
    outcome = services.import_collection(owner=owner, raw=export,
                                         filename="archidekt_sample.csv")

    assert outcome.collection.owner == owner
    assert outcome.collection.total_cards > 0
    assert outcome.collection.items.exists()
    assert outcome.collection.source_filename == "archidekt_sample.csv"


def test_the_printing_is_recorded_even_when_nothing_resolves_it(owner, export):
    """Storing the strings is the only way to have them later.

    No `printings` fixture here, so nothing resolves - and the set code and
    collector number are still written down, because a second import cannot
    invent what the first one threw away.

    **This is the test that turned out to be worth its keep.** When
    `default_cards` did land, migration `collection/0002` linked every existing
    row to its printing off these strings alone, and nobody had to re-upload a
    collection to get prices.
    """
    outcome = services.import_collection(owner=owner, raw=export)
    item = outcome.collection.items.exclude(set_code="").first()
    assert item is not None
    assert item.set_code
    assert item.collector_number


def test_the_same_card_in_two_sets_stays_two_rows(owner, export):
    """A collection export repeats a card once per printing owned.

    Summing them at import would be a decision this application could not
    undo. `owned_counts` sums at read time instead, where the caller can see
    it happen.
    """
    outcome = services.import_collection(owner=owner, raw=export)
    swamps = outcome.collection.items.filter(oracle_card__front_name="Swamp")

    assert swamps.count() > 1, "the sample has Swamps from more than one set"
    assert len({item.set_code for item in swamps}) > 1
    assert services.owned_counts(outcome.collection)[str(_card("Swamp").pk)] == sum(
        item.quantity for item in swamps
    )


def test_importing_again_replaces_rather_than_adds(owner, export):
    """Merging would turn four Swamps into eight on a corrected re-import."""
    first = services.import_collection(owner=owner, raw=export)
    before = first.collection.total_cards

    second = services.import_collection(owner=owner, raw=export)
    second.collection.refresh_from_db()

    assert second.collection.pk == first.collection.pk
    assert second.collection.total_cards == before
    assert Collection.objects.count() == 1


def test_a_file_that_matches_nothing_is_refused_rather_than_stored(owner):
    """An empty collection that looks imported is worse than a failed import."""
    from decks.services import ImportError_

    nonsense = b"Quantity,Name\n2,Zzzzz Not A Card\n1,Also Not A Card\n"
    with pytest.raises(ImportError_):
        services.import_collection(owner=owner, raw=nonsense)
    assert not Collection.objects.exists()


def test_an_import_is_metered(owner, export):
    from billing.models import UsageRecord

    services.import_collection(owner=owner, raw=export)
    assert UsageRecord.objects.filter(
        user=owner, metric=UsageRecord.Metric.IMPORTS
    ).exists()


# --- any export, not one site's export --------------------------------------
#
# A collection export and a deck export are the same file from the same site,
# so the collection importer reuses the deck pipeline wholesale. These assert
# that the reuse actually holds at the level somebody would notice: the column
# mapping, and the screen that asks about it.


def test_a_collection_export_from_an_unfamiliar_tool_imports(owner):
    """No fixture, no parser, no phase of work. Just columns we know the words for."""
    raw = (
        b"Count,Card Name,Set Code,Collector Number\n"
        b"4,Swamp,tor,341\n"
        b"1,Sol Ring,cmd,263\n"
    )
    outcome = services.import_collection(owner=owner, raw=raw, filename="whatever.csv")

    assert outcome.clean
    assert outcome.collection.total_cards == 5


def test_a_semicolon_separated_export_imports(owner):
    """What Excel writes on a machine with a European locale."""
    raw = b"Quantity;Name;Set code\n2;Swamp;tor\n"
    assert services.import_collection(owner=owner, raw=raw).collection.total_cards == 2


def test_a_collection_import_records_which_column_it_read_as_what(owner):
    outcome = services.import_collection(
        owner=owner, raw=b"Count,Card Name,Set Code\n4,Swamp,tor\n"
    )
    recorded = dict((label, header) for label, header in outcome.collection.column_mapping)

    assert recorded["quantity"] == "Count"
    assert recorded["card name"] == "Card Name"


def test_a_collection_upload_with_no_quantity_column_asks_first(signed_in):
    """The same refusal the deck importer makes, on the same screen.

    A collection is where getting this wrong hurts most: every deck on the site
    is then checked against a holding that says one of everything.
    """
    from decks.models import PendingImport

    upload = SimpleUploadedFile(
        "noqty.csv", b"Name,Set Code\nSwamp,tor\n", content_type="text/csv"
    )
    response = signed_in.post(reverse("collection:import"), {"file": upload}, follow=True)

    assert "Which column is which?" in response.content.decode()
    assert not Collection.objects.exists(), "nothing written until somebody answers"

    pending = PendingImport.objects.get()
    assert pending.kind == PendingImport.Kind.COLLECTION


def test_confirming_the_mapping_finishes_the_collection_import(signed_in, owner):
    """And lands back on the collection, not on a deck."""
    from decks.importers import columns
    from decks.models import PendingImport

    upload = SimpleUploadedFile(
        "noqty.csv", b"Name,Set Code\nSwamp,tor\nSwamp,cmd\n", content_type="text/csv"
    )
    signed_in.post(reverse("collection:import"), {"file": upload})
    pending = PendingImport.objects.get()

    response = signed_in.post(
        reverse("decks:map", args=[pending.id]),
        {"name": "Name", "set_code": "Set Code", "quantity": columns.ABSENT},
    )

    assert response["Location"] == reverse("collection:detail")
    collection = Collection.objects.get(owner=owner)
    assert collection.total_cards == 2, "two rows, one copy each, because that is what was said"
    assert not PendingImport.objects.exists()


# --- the question it exists for ---------------------------------------------

def test_a_deck_you_own_every_card_of_is_buildable(owner):
    deck = seeding.build(LAND_LIGHT, owner).deck
    collection, _ = Collection.objects.get_or_create(owner=owner)
    for entry in deck.entries.select_related("oracle_card"):
        CollectionItem.objects.create(
            collection=collection, oracle_card=entry.oracle_card,
            quantity=entry.quantity, set_code="tst", collector_number="1",
        )
    if deck.commander_id:
        CollectionItem.objects.create(
            collection=collection, oracle_card=deck.commander, quantity=1,
            set_code="tst", collector_number="2",
        )

    result = services.shortfall(deck, collection)
    assert result.buildable
    assert result.cards_short == 0


def test_a_card_you_do_not_own_is_named(owner):
    deck = Deck.objects.create(owner=owner, name="Two cards")
    DeckCard.objects.create(deck=deck, oracle_card=_card("Sol Ring"), quantity=1)
    DeckCard.objects.create(deck=deck, oracle_card=_card("Dark Ritual"), quantity=1)
    collection = _own(owner, "Sol Ring", 1)

    result = services.shortfall(deck, collection)

    assert not result.buildable
    assert [m.name for m in result.missing_nonbasic] == ["Dark Ritual"]
    assert result.cards_short == 1


def test_owning_some_of_them_is_not_owning_all_of_them(owner):
    deck = Deck.objects.create(owner=owner, name="Four rituals")
    DeckCard.objects.create(deck=deck, oracle_card=_card("Dark Ritual"), quantity=4)
    collection = _own(owner, "Dark Ritual", 1)

    result = services.shortfall(deck, collection)
    missing = result.missing_nonbasic[0]

    assert (missing.needed, missing.owned, missing.short_by) == (4, 1, 3)
    assert result.cards_short == 3


def test_copies_across_printings_are_added_up(owner):
    """Four Swamps from four sets are four Swamps."""
    deck = Deck.objects.create(owner=owner, name="Rituals")
    DeckCard.objects.create(deck=deck, oracle_card=_card("Dark Ritual"), quantity=3)

    collection = _own(owner, "Dark Ritual", 1, set_code="one", number="1")
    CollectionItem.objects.create(
        collection=collection, oracle_card=_card("Dark Ritual"), quantity=2,
        set_code="two", collector_number="9",
    )

    assert services.shortfall(deck, collection).buildable


def test_basic_lands_are_counted_apart_from_everything_else(owner):
    """The number that would otherwise be dominated by Swamps.

    Ten missing Swamps and one missing Sol Ring is not "eleven cards short" to
    anybody who has ever built a deck.
    """
    deck = Deck.objects.create(owner=owner, name="Swamps and a rock")
    DeckCard.objects.create(deck=deck, oracle_card=_card("Swamp"), quantity=30)
    DeckCard.objects.create(deck=deck, oracle_card=_card("Sol Ring"), quantity=1)
    collection = _own(owner, "Swamp", 20)

    result = services.shortfall(deck, collection)

    assert result.cards_short == 1, "only the Sol Ring counts"
    assert result.basics_short == 10
    assert [m.name for m in result.missing_nonbasic] == ["Sol Ring"]
    assert [m.name for m in result.missing_basics] == ["Swamp"]


def test_a_deck_missing_only_basics_is_still_buildable(owner):
    deck = Deck.objects.create(owner=owner, name="Just Swamps")
    DeckCard.objects.create(deck=deck, oracle_card=_card("Swamp"), quantity=38)
    collection = _own(owner, "Swamp", 4)

    result = services.shortfall(deck, collection)
    assert result.buildable
    assert result.basics_short == 34


def test_a_basic_is_recognised_by_its_type_line_not_its_name(owner, export):
    """Snow-Covered Swamp and Wastes are basics and neither is called Swamp."""
    assert services.is_basic_land(_card("Swamp"))
    assert not services.is_basic_land(_card("Sol Ring"))


def test_the_commander_counts(owner):
    """It is not a DeckCard, so a query over entries alone would miss it.

    Telling somebody they can build a deck whose commander they do not own is
    the one version of this answer that is useless: there is no proxying past
    the card the deck is named after.
    """
    deck = seeding.build(LAND_LIGHT, owner).deck
    assert deck.commander_id, "this shape has a commander"

    collection, _ = Collection.objects.get_or_create(owner=owner)
    for entry in deck.entries.select_related("oracle_card"):
        CollectionItem.objects.create(
            collection=collection, oracle_card=entry.oracle_card,
            quantity=entry.quantity, set_code="tst", collector_number="1",
        )
    # Everything but the commander.

    result = services.shortfall(deck, collection)
    assert not result.buildable
    assert deck.commander.front_name in [m.name for m in result.missing_nonbasic]


def test_an_imported_commander_is_wanted_once_not_twice(owner):
    """Trap 46, from the collection's side.

    The import used to leave the commander in the 99 as well as in the command
    zone, so a person owning the one copy that exists was told they were one
    short of it.
    """
    from decks import services as deck_services

    deck = deck_services.import_deck(
        owner=owner, raw=(FIXTURES / "archidekt_sample.csv").read_bytes(), name="Chainer"
    ).deck
    collection, _ = Collection.objects.get_or_create(owner=owner)
    CollectionItem.objects.create(
        collection=collection, oracle_card=deck.commander, quantity=1,
        set_code="tst", collector_number="1",
    )

    result = services.shortfall(deck, collection)
    assert deck.commander.front_name not in [m.name for m in result.missing]


def test_no_collection_means_everything_is_missing(owner):
    deck = Deck.objects.create(owner=owner, name="Nothing owned")
    DeckCard.objects.create(deck=deck, oracle_card=_card("Sol Ring"), quantity=1)

    result = services.shortfall(deck, None)
    assert not result.has_collection
    assert not result.buildable
    assert result.cards_short == 1


def test_the_missing_list_puts_the_worst_first(owner):
    deck = Deck.objects.create(owner=owner, name="Ordering")
    DeckCard.objects.create(deck=deck, oracle_card=_card("Swamp"), quantity=10)
    DeckCard.objects.create(deck=deck, oracle_card=_card("Sol Ring"), quantity=1)
    DeckCard.objects.create(deck=deck, oracle_card=_card("Dark Ritual"), quantity=4)

    result = services.shortfall(deck, None)
    names = [m.name for m in result.missing]

    assert names.index("Dark Ritual") < names.index("Sol Ring"), "4 short before 1"
    assert names[-1] == "Swamp", "basics last, whatever the count"


# --- the screens ------------------------------------------------------------

def test_the_page_says_so_when_there_is_no_collection(signed_in):
    response = signed_in.get(reverse("collection:detail"))
    assert response.status_code == 200
    assert b"Nothing here yet" in response.content


def test_uploading_an_export_lands_on_the_collection(signed_in, export):
    response = signed_in.post(
        reverse("collection:import"),
        {"file": SimpleUploadedFile("archidekt_sample.csv", export,
                                    content_type="text/csv")},
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("collection:detail")
    assert Collection.objects.filter(owner__email="owner@example.com").exists()


def test_the_page_answers_the_question_for_every_deck(signed_in, owner, export):
    seeding.build(LAND_LIGHT, owner)
    services.import_collection(owner=owner, raw=export)

    body = signed_in.get(reverse("collection:detail")).content.decode()
    assert "against what you own" in body
    assert LAND_LIGHT.name in body


def test_the_deck_page_answers_it_too(signed_in, owner, export):
    """Where somebody actually asks it - looking at the deck."""
    deck = seeding.build(LAND_LIGHT, owner).deck
    services.import_collection(owner=owner, raw=export)

    body = signed_in.get(deck.get_absolute_url()).content.decode()
    assert "Against your collection" in body


def test_a_deck_page_says_nothing_about_a_collection_nobody_imported(
        signed_in, owner):
    deck = seeding.build(LAND_LIGHT, owner).deck
    body = signed_in.get(deck.get_absolute_url()).content.decode()
    assert "Against your collection" not in body


def test_the_collection_is_never_somebody_elses(client, owner, export):
    """No id in the path at all, which is the strongest form of the rule."""
    services.import_collection(owner=owner, raw=export)
    intruder = User.objects.create_user(email="nosy@example.com", password=PASSWORD)
    client.force_login(intruder)

    body = client.get(reverse("collection:detail")).content.decode()
    assert "Nothing here yet" in body


def test_the_collection_pages_need_signing_in(client):
    for name in ("collection:detail", "collection:import"):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert "login" in response["Location"]


def test_an_unreadable_upload_is_reported_on_the_form(signed_in):
    response = signed_in.post(
        reverse("collection:import"),
        {"file": SimpleUploadedFile("mystery.dat", b"\x00\x01 not a csv",
                                    content_type="application/octet-stream")},
    )
    assert response.status_code == 200
    assert b"not recognised" in response.content or b"errorlist" in response.content


# --- printings, and therefore prices ----------------------------------------
#
# Everything above this line runs with **no printing catalogue**, which is the
# state a fresh installation is in and the one the whole page has to keep
# working in. These tests opt into the `printings` fixture from conftest to
# cover the other half.
#
# The export they use is `archidekt_printings_sample.csv`, generated from the
# printings fixture itself, so every set code, collector number and Scryfall id
# in it names a printing the test database really has. The older
# `archidekt_sample.csv` keeps its invented ids on purpose: it is what proves
# the name rungs still carry a file that identifies nothing.


@pytest.fixture
def real_export():
    """An export in which every printing is one the catalogue has."""
    return (FIXTURES / "archidekt_printings_sample.csv").read_bytes()


def test_an_import_links_each_row_to_the_printing_it_names(owner, printings, real_export):
    services.import_collection(owner=owner, raw=real_export)

    items = CollectionItem.objects.filter(collection__owner=owner)
    assert items.exists()
    assert not items.filter(printing__isnull=True).exists()

    # And it is the printing the row named, not merely some printing of that card.
    for item in items:
        assert item.printing.set_code == item.set_code
        assert item.printing.collector_number == item.collector_number


def test_two_printings_of_one_card_stay_two_rows_and_one_count(
    owner, printings, real_export
):
    """The Phase 6 rule, now with printings that can actually be told apart."""
    services.import_collection(owner=owner, raw=real_export)

    swamps = CollectionItem.objects.filter(
        collection__owner=owner, oracle_card__front_name="Swamp"
    )
    assert swamps.count() == 2
    assert {s.printing.set_code for s in swamps} == {"30a", "dmu"}
    assert services.owned_counts(swamps.first().collection)[
        str(swamps.first().oracle_card_id)
    ] == 31


def test_a_row_with_no_price_reports_none_and_never_zero(owner, printings, real_export):
    """The assertion the whole pricing feature rests on.

    An etched printing has no EUR price published anywhere. Rendering that as
    0.00 would tell somebody their card is worthless, which is a different
    claim from "nobody publishes a price for this" and is not true.
    """
    services.import_collection(owner=owner, raw=real_export)

    etched = CollectionItem.objects.get(
        collection__owner=owner, finish="Etched"
    )
    assert etched.printing is not None
    assert etched.unit_price is None
    assert etched.total_price is None


def test_a_priced_row_multiplies_by_what_is_on_it(owner, printings, real_export):
    services.import_collection(owner=owner, raw=real_export)

    row = CollectionItem.objects.filter(
        collection__owner=owner, printing__price_eur__isnull=False, quantity__gt=1
    ).first()
    assert row is not None
    assert row.total_price == row.unit_price * row.quantity


def test_the_price_summary_counts_what_it_could_not_price(owner, printings, real_export):
    services.import_collection(owner=owner, raw=real_export)
    items = list(
        CollectionItem.objects.filter(collection__owner=owner)
        .select_related("oracle_card", "printing")
    )

    summary = services.price_summary(items)

    assert summary.entries == len(items)
    assert summary.any_prices
    assert summary.unpriced > 0, "the fixture carries unpriced rows on purpose"
    assert summary.priced + summary.unpriced == summary.entries
    # The date comes off the bulk file, never off the clock.
    assert summary.as_of == PRINTINGS_VERSION


def test_the_page_dates_its_prices_and_dashes_the_missing_ones(
    signed_in, owner, printings, real_export
):
    """A price with no date is a number somebody will quote next year."""
    services.import_collection(owner=owner, raw=real_export)

    body = signed_in.get(reverse("collection:detail")).content.decode()

    assert "Cardmarket trend" in body
    assert "20 Sep 2026" in body
    assert "—" in body
    assert "0.00" not in body


def test_the_page_says_so_when_no_printings_are_loaded(signed_in, owner, export):
    """No `printings` fixture: the state most installations are in.

    The page must not show an empty price column, which reads as missing data
    rather than as a feature nobody switched on.
    """
    services.import_collection(owner=owner, raw=export)

    body = signed_in.get(reverse("collection:detail")).content.decode()

    assert "has not loaded the printing catalogue" in body
    assert "Each (€)" not in body


def test_the_deck_answers_do_not_depend_on_printings(owner, printings, real_export):
    """Counts are computed on cards, and must stay that way.

    A shortfall that started reading `printing` would give a different answer
    on an installation that had not ingested `default_cards`, which is the one
    thing the whole nullable-FK design exists to prevent.
    """
    services.import_collection(owner=owner, raw=real_export)
    collection = services.for_user(owner)
    with_printings = services.owned_counts(collection)

    collection.items.update(printing=None)
    assert services.owned_counts(collection) == with_printings
