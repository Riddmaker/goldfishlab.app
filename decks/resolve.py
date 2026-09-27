"""Turning parsed rows into cards, highest fidelity first.

The ladder, in order. Each rung is tried only when the ones above it fail:

1. `scryfall_id` - the exact printing, looked up in `Printing`. In Phase 1 this
   could only be checked against the single arbitrary printing that
   `oracle_cards` ships, and hit **62 of 214 rows** on the reference export.
   With the printing catalogue loaded it is what it always claimed to be.
2. `oracle_id` - the card, if the export gave one.
3. `(set_code, collector_number)` - **implemented at the end of Phase 6**, when
   `default_cards` landed. Safe as an exact match because the pair is globally
   unique across all 112,581 printings: measured, zero collisions, no language
   to guess and no tie to break. Before this it was a documented hole and rows
   fell through to rung 4, which found the same card but never the printing.
4. exact front-face name.
5. normalised name - casefold, accents stripped, punctuation dropped. This is
   the rung that finds `Lim-Dul the Necromancer` when the list says `Lim-Dul`,
   and it earns its keep on 2 of 214 rows.
6. unresolved - straight to the review screen, with near-miss suggestions.
   **Never a silent drop.**

**The printing is resolved alongside the ladder, not by it.** Which rung named
the *card* and whether we also know the *printing* are two different questions:
a row carrying an `oracle_id` and a set code is matched at rung 2, and still
deserves to know which Swamp it is. So `Resolution.printing` is filled from the
printing id or the set/number pair whenever either is present, whatever rung
answered. It stays `None` on an installation with no printings ingested, which
is a supported state and not a degraded one.

Everything is resolved in bulk: a handful of queries for a whole deck list, not
a handful per row.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Case, Q, Value, When

from cards.models import OracleCard, Printing
from cards.names import front_face, normalise
from decks.importers.base import ParsedRow

# Rung labels, stored in DeckImport.rung_counts and shown on the review screen.
RUNG_SCRYFALL_ID = "scryfall_id"
RUNG_ORACLE_ID = "oracle_id"
RUNG_SET_NUMBER = "set_number"
RUNG_EXACT_NAME = "exact_name"
RUNG_NORMALISED_NAME = "normalised_name"
RUNG_UNRESOLVED = "unresolved"

RUNG_ORDER = [
    RUNG_SCRYFALL_ID,
    RUNG_ORACLE_ID,
    RUNG_SET_NUMBER,
    RUNG_EXACT_NAME,
    RUNG_NORMALISED_NAME,
]

RUNG_LABELS = {
    RUNG_SCRYFALL_ID: "matched on the exact printing",
    RUNG_ORACLE_ID: "matched on the card id",
    RUNG_SET_NUMBER: "matched on the set and collector number",
    RUNG_EXACT_NAME: "matched on the exact name",
    RUNG_NORMALISED_NAME: "matched on the name, ignoring accents and punctuation",
    RUNG_UNRESOLVED: "not found",
}

#: How many (set, number) pairs go into one `WHERE` before it is split. A deck
#: list is a few hundred rows, so this is almost always one query; the cap
#: exists so a 20,000-row collection cannot build a megabyte of SQL.
PAIR_CHUNK = 500

#: How many near-misses to offer per unresolved row.
SUGGESTION_LIMIT = 5

#: Only this many unresolved rows get near-misses. Every suggestion is a query,
#: and a file of 50,000 junk lines was 50,000 of them in one request - far past
#: the gunicorn timeout, with two sync workers for the whole site. A hundred
#: rows is more than anybody reads on the review screen.
SUGGESTION_ROWS = 100

#: The most copies of one card a row may claim. Far above any real collection,
#: and far below the columns it ends up in: `UnresolvedRow.quantity` is a
#: smallint, and "99999 Notacard" overflowed it into an HTTP 500. A row past
#: this is not guessed down to something believable - it is refused, with the
#: reason, like any other row that cannot be read.
MAX_QUANTITY = 9_999


@dataclass
class Resolution:
    """What happened to one parsed row."""

    row: ParsedRow
    card: OracleCard | None = None
    #: Which printing, when the row said enough to know and the catalogue has
    #: it. `None` is an ordinary answer: a plain text deck list names no
    #: printing, and an installation that has not ingested `default_cards` has
    #: none to find. Nothing may treat it as a failure.
    printing: "Printing | None" = None
    rung: str = RUNG_UNRESOLVED
    reason: str = ""
    suggestions: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.card is not None


@dataclass
class ResolutionReport:
    """The outcome of resolving a whole file.

    `rows_total == rows_resolved + rows_unresolved` is an invariant, not an
    accident: it is the promise that nothing was dropped on the floor.
    """

    resolutions: list[Resolution] = field(default_factory=list)

    @property
    def rows_total(self) -> int:
        return len(self.resolutions)

    @property
    def resolved(self) -> list[Resolution]:
        return [r for r in self.resolutions if r.resolved]

    @property
    def unresolved(self) -> list[Resolution]:
        return [r for r in self.resolutions if not r.resolved]

    @property
    def rung_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for resolution in self.resolutions:
            counts[resolution.rung] += 1
        return dict(counts)

    def quantities(self) -> dict[str, int]:
        """Total quantity per oracle id, summing repeated rows.

        A collection export lists a card once per printing owned. Three rows of
        one Swamp printing plus twenty-eight of another is twenty-nine Swamps,
        not two.
        """
        totals: dict[str, int] = defaultdict(int)
        for resolution in self.resolved:
            totals[str(resolution.card.pk)] += resolution.row.quantity
        return dict(totals)


def resolve(rows: list[ParsedRow]) -> ResolutionReport:
    """Resolve every row against the catalogue, in a handful of queries."""
    rows = list(rows)
    report = ResolutionReport()
    if not rows:
        return report

    lookups = _build_lookups(rows)
    suggested = 0

    for row in rows:
        if row.quantity > MAX_QUANTITY:
            report.resolutions.append(Resolution(
                row=row, rung=RUNG_UNRESOLVED,
                reason=f"a quantity of {row.quantity:,} is more than any collection holds",
            ))
            continue
        card, rung = _match(row, lookups)
        printing = _printing_for(row, lookups)
        if card is not None:
            report.resolutions.append(
                Resolution(row=row, card=card, printing=printing, rung=rung)
            )
            continue

        suggested += 1
        report.resolutions.append(
            Resolution(
                row=row,
                rung=RUNG_UNRESOLVED,
                reason=_reason(row),
                suggestions=_suggest(row.name) if suggested <= SUGGESTION_ROWS else [],
            )
        )

    return report


#: Keys of the two printing maps, which sit beside the rung maps rather than
#: inside them: they answer "which printing", not "which card".
BY_PRINTING_ID = "printings_by_id"
BY_SET_NUMBER = "printings_by_pair"


def _build_lookups(rows: list[ParsedRow]) -> dict[str, dict]:
    """A handful of bulk queries covering every rung at once."""
    printing_ids = {r.scryfall_id for r in rows if r.scryfall_id}
    oracle_ids = {r.oracle_id for r in rows if r.oracle_id}
    exact_names = {front_face(r.name) for r in rows if r.name}
    folded_names = {normalise(r.name) for r in rows if r.name}
    pairs = {_pair(r) for r in rows if _pair(r)}

    by_printing, by_oracle, by_name, by_folded = {}, {}, {}, {}
    printings_by_id, printings_by_pair = _printings(printing_ids, pairs)

    # Rung 1 through the printing catalogue, which is the only place that knows
    # all 112,581 of them. `OracleCard.scryfall_id` is the fallback below, for
    # an installation that has not ingested `default_cards`.
    for key, printing in printings_by_id.items():
        by_printing[key] = printing.oracle_card
    if printing_ids and not printings_by_id:
        for card in OracleCard.objects.filter(scryfall_id__in=_uuids(printing_ids)):
            by_printing[str(card.scryfall_id)] = card

    if oracle_ids:
        for card in OracleCard.objects.filter(oracle_id__in=_uuids(oracle_ids)):
            by_oracle[str(card.oracle_id)] = card

    if exact_names or folded_names:
        matches = OracleCard.objects.filter(
            Q(front_name__in=exact_names) | Q(search_name__in=folded_names)
        )
        for card in matches:
            # `setdefault`: when several cards share a normalised name, the
            # first by the model's ordering (alphabetical) wins deterministically
            # rather than by whatever order the database felt like.
            by_name.setdefault(card.front_name, card)
            by_folded.setdefault(card.search_name, card)

    return {
        RUNG_SCRYFALL_ID: by_printing,
        RUNG_ORACLE_ID: by_oracle,
        RUNG_SET_NUMBER: {k: p.oracle_card for k, p in printings_by_pair.items()},
        RUNG_EXACT_NAME: by_name,
        RUNG_NORMALISED_NAME: by_folded,
        BY_PRINTING_ID: printings_by_id,
        BY_SET_NUMBER: printings_by_pair,
    }


def _pair(row: ParsedRow) -> tuple[str, str] | None:
    """The `(set_code, collector_number)` key for a row, or None.

    Case-folded on the set code because exports disagree - `TOR`, `tor` - and
    the collector number left exactly as written, because it is not a number:
    `341`, `341a`, `★12` and `T3` are all real, and stripping a leading zero
    from `007` would resolve it to the wrong card or to nothing.
    """
    set_code = (row.set_code or "").strip().lower()
    number = (row.collector_number or "").strip()
    return (set_code, number) if set_code and number else None


def _printings(printing_ids: set[str], pairs: set[tuple[str, str]]) -> tuple[dict, dict]:
    """Every printing these rows could name, in at most a few queries.

    Returns `({scryfall_id: Printing}, {(set, number): Printing})`. Both are
    empty when `default_cards` has never been ingested, which every caller
    treats as "no printing known" rather than as an error.
    """
    by_id: dict[str, Printing] = {}
    by_pair: dict[tuple[str, str], Printing] = {}

    if printing_ids:
        found = Printing.objects.select_related("oracle_card").filter(
            scryfall_id__in=_uuids(printing_ids)
        )
        for printing in found:
            by_id[str(printing.scryfall_id)] = printing

    for chunk in _chunked(sorted(pairs), PAIR_CHUNK):
        clause = Q()
        for set_code, number in chunk:
            clause |= Q(set_code__iexact=set_code, collector_number=number)
        if not clause:
            continue
        found = (
            Printing.objects.select_related("oracle_card")
            .filter(clause)
            # The pair is unique today - measured across all 112,581 rows, zero
            # collisions - so this ordering never actually decides anything. It
            # is here so that if upstream ever ships a duplicate, the English
            # printing wins and the answer stays the same on every run, instead
            # of changing with whatever order Postgres felt like returning.
            .order_by(Case(When(lang="en", then=Value(0)), default=Value(1)), "scryfall_id")
        )
        for printing in found:
            by_pair.setdefault(
                (printing.set_code.lower(), printing.collector_number), printing
            )

    return by_id, by_pair


def _chunked(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _printing_for(row: ParsedRow, lookups: dict[str, dict]):
    """Which printing this row names, independently of which rung found the card.

    Printing id first, because it is exact and needs no interpretation. The
    set/number pair second, because it is exact too but has to be normalised to
    get there.
    """
    if row.scryfall_id and (found := lookups[BY_PRINTING_ID].get(str(row.scryfall_id))):
        return found
    pair = _pair(row)
    return lookups[BY_SET_NUMBER].get(pair) if pair else None


def _uuids(values: set[str]) -> list[str]:
    """Drop anything that is not a UUID before it reaches the database.

    A malformed id in a CSV is a data problem, not a 500: Postgres raises on an
    invalid uuid literal and would take the whole import down with it.
    """
    import uuid

    valid = []
    for value in values:
        try:
            valid.append(str(uuid.UUID(value)))
        except (ValueError, AttributeError, TypeError):
            continue
    return valid


def _match(row: ParsedRow, lookups: dict[str, dict]) -> tuple[OracleCard | None, str]:
    keys = {
        RUNG_SCRYFALL_ID: row.scryfall_id,
        RUNG_ORACLE_ID: row.oracle_id,
        RUNG_SET_NUMBER: _pair(row),
        RUNG_EXACT_NAME: front_face(row.name),
        RUNG_NORMALISED_NAME: normalise(row.name),
    }
    for rung in RUNG_ORDER:
        key = keys.get(rung)
        if key and (card := lookups[rung].get(key)):
            return card, rung
    return None, RUNG_UNRESOLVED


def _reason(row: ParsedRow) -> str:
    if not row.name:
        return "the row has no card name"
    if row.scryfall_id:
        return "no card with this name, and the printing id is not in the catalogue"
    return "no card with this name in the catalogue"


def _suggest(name: str) -> list[str]:
    """Near misses for the review screen.

    A prefix search on the normalised name, which catches the realistic failure
    modes - a truncated name, a missing subtitle, a typo near the end. Full
    fuzzy matching over 35,000 names is a Phase 6 problem with a trigram index,
    not a Python loop.
    """
    folded = normalise(name)
    if len(folded) < 3:
        return []

    head = folded[: max(4, len(folded) // 2)]
    matches = (
        OracleCard.objects.filter(search_name__startswith=head)
        .order_by("name")
        .values_list("name", flat=True)[:SUGGESTION_LIMIT]
    )
    return list(matches)
