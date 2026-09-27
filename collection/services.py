"""Importing a collection, and answering the question it exists for.

**"Can I build this deck from what I own?"** is, per the phase document, the
feature the original deck project actually needed. Everything here serves that
one sentence.

The import reuses the deck pipeline wholesale - `decks.services.decode`,
`decks.parse`, `decks.resolve` - because a collection export and a deck export
are the same file from the same site, and a second parser would be a second
place for the same CSV to be read differently. What differs is only what the
resolved rows are written into.

Two decisions the shortfall makes, and both are "report, never decide":

**Basic lands are counted and listed separately.** A collection export says
somebody owns 28 Swamps; a deck may want 38. Arithmetic says ten missing, and
a person says "those are basics, I have a box of them". Neither is wrong, so
the answer reports both numbers and lets the reader pick - the alternative is
either a shortfall dominated by Swamps or an application that silently decides
which cards do not count.

**Quantity is summed across printings.** Four Swamps from four sets are four
`CollectionItem` rows and four Swamps. The summing happens here at read time
rather than at import, so the printings survive - and now that `cards.Printing`
exists, they are told apart: each row carries the printing it resolved to and
therefore a Cardmarket price and a finish.

**Prices are reported per row and never totalled into a headline.** See
`PriceSummary`.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from billing import quotas
from billing.models import UsageRecord
from collection.models import Collection, CollectionItem
from decks import resolve
from decks.services import ImportError_, decode, describe, parse


@dataclass
class ImportOutcome:
    """What an import did, in the same shape the deck importer reports."""

    collection: Collection
    report: resolve.ResolutionReport

    @property
    def clean(self) -> bool:
        return not self.report.unresolved


@transaction.atomic
def import_collection(*, owner, raw: bytes = b"", filename: str = "",
                      parser_name: str = "", overrides: dict | None = None,
                      text: str | None = None) -> ImportOutcome:
    """Replace this person's collection with the contents of one export.

    **Replace, not merge.** An import is somebody saying "this is what I own
    now"; merging would make a corrected re-import silently additive and turn
    four Swamps into eight. Same rule as `decks.services._write_entries`, and
    for the same reason.

    Atomic, because a half-imported collection is worse than a failed one: it
    looks finished, and the deck diff it feeds would quietly understate what
    the person owns.
    """
    if text is None:
        text = decode(raw)
    parser_class, rows = parse(text, parser_name, overrides)

    # Before the resolver, which is the work being metered - see the same call
    # in `decks.services.import_deck`.
    quotas.check(owner, UsageRecord.Metric.IMPORTS)
    report = resolve.resolve(rows)

    if not report.resolved and report.rows_total:
        raise ImportError_(
            "Nothing in that file matched a card. It parsed as "
            f"{parser_class.label}, so either the format was guessed wrongly or "
            "the card catalogue has not been ingested."
        )

    # Call site 3 of the four `check()` is allowed, made above, before the
    # resolver: the same one the deck importer uses, because the work being
    # metered - parsing and resolving a couple of hundred rows - is identical.
    collection, _ = Collection.objects.get_or_create(owner=owner)
    collection.items.all().delete()

    CollectionItem.objects.bulk_create(_items(collection, report))

    collection.source_filename = filename[:255]
    collection.imported_at = timezone.now()
    collection.column_mapping = describe(text, parser_name, overrides)
    collection.save(update_fields=[
        "source_filename", "imported_at", "column_mapping", "updated_at",
    ])

    quotas.consume(owner, UsageRecord.Metric.IMPORTS)
    return ImportOutcome(collection=collection, report=report)


def _items(collection: Collection, report) -> list[CollectionItem]:
    """One row per printing, with duplicate printings summed.

    An export can list the same printing twice - two rows for the same Swamp
    from the same set, added on different days. The unique constraint forbids
    two such rows, and dropping one would lose a card, so they are added
    together. That is the one place this function decides anything.
    """
    merged: dict[tuple, CollectionItem] = {}

    for resolution in report.resolved:
        row = resolution.row
        extra = row.extra or {}
        key = (str(resolution.card.pk), row.set_code, row.collector_number,
               extra.get("finish", ""))

        if key in merged:
            merged[key].quantity += row.quantity
            continue

        merged[key] = CollectionItem(
            collection=collection,
            oracle_card=resolution.card,
            # None whenever the row named no printing, or named one this
            # catalogue does not have. An ordinary outcome, not a failure -
            # the raw strings below are kept precisely so a later ingest can
            # still answer what this import could not.
            printing=resolution.printing,
            quantity=row.quantity,
            scryfall_id=row.scryfall_id[:64],
            set_code=row.set_code[:16],
            collector_number=row.collector_number[:16],
            finish=extra.get("finish", "")[:32],
            condition=extra.get("condition", "")[:32],
            language=extra.get("language", "")[:16],
        )

    return list(merged.values())


# --- the question this app exists to answer ---------------------------------

@dataclass(frozen=True)
class Missing:
    """One card the deck wants and the collection does not have enough of."""

    name: str
    oracle_id: str
    needed: int
    owned: int
    is_basic_land: bool

    @property
    def short_by(self) -> int:
        return self.needed - self.owned


@dataclass
class Shortfall:
    """What stands between a collection and a deck.

    Deliberately not a single number. "You are 12 cards short" reads very
    differently from "you are 2 cards short, plus 10 basic lands", and only the
    person reading it knows which one they meant.
    """

    deck_cards: int = 0
    owned_cards: int = 0
    missing: list[Missing] = field(default_factory=list)

    @property
    def buildable(self) -> bool:
        """Every non-basic card present. Basics are assumed, never counted."""
        return not self.missing_nonbasic

    @property
    def missing_nonbasic(self) -> list[Missing]:
        return [m for m in self.missing if not m.is_basic_land]

    @property
    def missing_basics(self) -> list[Missing]:
        return [m for m in self.missing if m.is_basic_land]

    @property
    def cards_short(self) -> int:
        """Copies missing, basics excluded. The number worth acting on."""
        return sum(m.short_by for m in self.missing_nonbasic)

    @property
    def basics_short(self) -> int:
        return sum(m.short_by for m in self.missing_basics)

    @property
    def has_collection(self) -> bool:
        return self.owned_cards > 0


@dataclass
class PriceSummary:
    """How much of a collection this application can put a number on.

    Deliberately **not** "your collection is worth EUR X". A trend price is one
    market's daily snapshot of one printing, and a headline total invites a
    person to treat the sum of two hundred of those as a valuation. So the page
    prices each row it can, says how many it could not, and says when the
    numbers were true - and the reader adds them up themselves if that is what
    they came for.
    """

    entries: int = 0
    priced: int = 0
    #: The bulk file's own timestamp, from the printings. Never `timezone.now()`
    #: - a price is as old as the file it came from, not as fresh as the page.
    as_of: object = None

    @property
    def unpriced(self) -> int:
        return self.entries - self.priced

    @property
    def any_prices(self) -> bool:
        """False on an installation with no printings ingested.

        The page shows no price column at all in that case, rather than a
        column of dashes that looks like missing data instead of a feature
        nobody switched on.
        """
        return self.priced > 0


def price_summary(items) -> PriceSummary:
    """Count how many rows carry a price, and how old the prices are.

    Takes the already-fetched item list rather than the collection, so the page
    that renders the rows and the line that describes them cannot disagree
    about which rows they are talking about.
    """
    summary = PriceSummary()
    for item in items:
        summary.entries += 1
        if item.unit_price is None:
            continue
        summary.priced += 1
        stamp = item.printing.prices_updated_at
        if stamp and (summary.as_of is None or stamp < summary.as_of):
            # The oldest, not the newest. If a re-ingest ever leaves a mixture,
            # the honest claim is the weakest one on the page.
            summary.as_of = stamp
    return summary


def owned_counts(collection: Collection) -> dict[str, int]:
    """How many of each card this person owns, summed across printings."""
    counts: dict[str, int] = defaultdict(int)
    for oracle_id, quantity in collection.items.values_list("oracle_card_id", "quantity"):
        counts[str(oracle_id)] += quantity
    return dict(counts)


def shortfall(deck, collection: Collection | None) -> Shortfall:
    """What `deck` needs that `collection` does not have.

    The commander is included, and it is easy to leave out: it is not a
    `DeckCard` (trap 7), so a query over deck entries alone would tell somebody
    they can build a deck whose commander they do not own - which is the one
    card they certainly cannot proxy past.
    """
    result = Shortfall()
    owned = owned_counts(collection) if collection is not None else {}
    result.owned_cards = sum(owned.values())

    wanted: dict[str, tuple[object, int]] = {}
    for entry in deck.entries.select_related("oracle_card"):
        card = entry.oracle_card
        held = wanted.get(str(card.pk))
        wanted[str(card.pk)] = (card, (held[1] if held else 0) + entry.quantity)

    if deck.commander_id:
        held = wanted.get(str(deck.commander_id))
        wanted[str(deck.commander_id)] = (deck.commander, (held[1] if held else 0) + 1)

    for oracle_id, (card, needed) in wanted.items():
        result.deck_cards += needed
        have = owned.get(oracle_id, 0)
        if have >= needed:
            continue
        result.missing.append(Missing(
            name=card.front_name,
            oracle_id=oracle_id,
            needed=needed,
            owned=have,
            is_basic_land=is_basic_land(card),
        ))

    result.missing.sort(key=lambda m: (m.is_basic_land, -m.short_by, m.name))
    return result


def is_basic_land(card) -> bool:
    """A land anybody can have any number of, and nobody buys.

    Read off the type line rather than a name list, because Snow-Covered Swamp
    and Wastes are both basics and neither is called Swamp.
    """
    return (card.type_line or "").startswith("Basic Land")


def for_user(user) -> Collection | None:
    """This person's collection, or None if they have never imported one."""
    try:
        return user.collection
    except Collection.DoesNotExist:
        return None
