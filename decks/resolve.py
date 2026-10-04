"""Turning parsed rows into cards, highest fidelity first.

The ladder, in order. Each rung is tried only when the ones above it fail:

1. `scryfall_id` - a printing id, checked against the one printing each card
   carries from `oracle_cards` (`OracleCard.scryfall_id`). It hits **62 of 214
   rows** on the reference export; the rest fall through to the name, which
   finds them. (Phase 6 matched every printing and the set/collector-number
   pair through a `Printing` table; that went in phase 9 I - only the
   collection's prices needed the printing, and production never loaded it.)
2. `oracle_id` - the card, if the export gave one.
3. exact front-face name.
4. normalised name - casefold, accents stripped, punctuation dropped. This is
   the rung that finds `Lim-Dul the Necromancer` when the list says `Lim-Dul`,
   and it earns its keep on 2 of 214 rows.
5. unresolved - straight to the review screen, with near-miss suggestions.
   **Never a silent drop.**

Everything is resolved in bulk: a handful of queries for a whole deck list, not
a handful per row.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils.translation import gettext_noop

from cards.models import OracleCard
from cards.names import front_face, normalise
from decks.importers.base import ParsedRow

# Rung labels, stored in DeckImport.rung_counts and shown on the review screen.
RUNG_SCRYFALL_ID = "scryfall_id"
RUNG_ORACLE_ID = "oracle_id"
RUNG_EXACT_NAME = "exact_name"
RUNG_NORMALISED_NAME = "normalised_name"
RUNG_UNRESOLVED = "unresolved"

RUNG_ORDER = [
    RUNG_SCRYFALL_ID,
    RUNG_ORACLE_ID,
    RUNG_EXACT_NAME,
    RUNG_NORMALISED_NAME,
]

#: English, translated where shown. The row reasons below are stored
#: (`UnresolvedRow.reason`) and translated by the page the same way, so an old
#: row in a wording since changed simply stays English.
RUNG_LABELS = {
    RUNG_SCRYFALL_ID: gettext_noop("matched on the exact printing"),
    RUNG_ORACLE_ID: gettext_noop("matched on the card id"),
    RUNG_EXACT_NAME: gettext_noop("matched on the exact name"),
    RUNG_NORMALISED_NAME: gettext_noop("matched on the name, ignoring accents and punctuation"),
    RUNG_UNRESOLVED: gettext_noop("not found"),
}

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
                # Without the number: the review shows the quantity beside it,
                # and a fixed sentence is one a page can translate.
                reason=gettext_noop("the quantity is more than any collection holds"),
            ))
            continue
        card, rung = _match(row, lookups)
        if card is not None:
            report.resolutions.append(Resolution(row=row, card=card, rung=rung))
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


def _build_lookups(rows: list[ParsedRow]) -> dict[str, dict]:
    """A handful of bulk queries covering every rung at once."""
    printing_ids = {r.scryfall_id for r in rows if r.scryfall_id}
    oracle_ids = {r.oracle_id for r in rows if r.oracle_id}
    exact_names = {front_face(r.name) for r in rows if r.name}
    folded_names = {normalise(r.name) for r in rows if r.name}

    by_printing, by_oracle, by_name, by_folded = {}, {}, {}, {}

    if printing_ids:
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
        RUNG_EXACT_NAME: by_name,
        RUNG_NORMALISED_NAME: by_folded,
    }


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
        return gettext_noop("the row has no card name")
    if row.scryfall_id:
        return gettext_noop("no card with this name, and the printing id is not in the catalogue")
    return gettext_noop("no card with this name in the catalogue")


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
