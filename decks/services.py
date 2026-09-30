"""The import pipeline: bytes in, a deck plus an honest tally out.

    decode -> sniff a parser -> [ask which column is which] -> parse rows
           -> resolve against the catalogue -> write DeckCards and
           UnresolvedRows -> record the tally

The bracketed step is skipped whenever the file's own headers answer the
question, which is every Archidekt export and most of everything else. It
exists because the alternative to asking is guessing, and a guess here is not
an error message - it is a deck that imported cleanly and is wrong.

Two rules this module exists to enforce:

1. **Quota is checked before work, never after.** `billing.quotas.check()` is
   called at deck creation and at import - two of its four permitted call
   sites. Checking afterwards means the expensive part already happened.
2. **The tally must add up.** `rows_total == rows_resolved + rows_unresolved`,
   asserted by a test. An importer that quietly drops the rows it cannot read
   produces a deck page that is wrong and confident, which is the failure mode
   this whole application is built to avoid.
"""

from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from billing import quotas
from billing.models import UsageRecord
from decks import importers, resolve
from decks.models import Deck, DeckCard, DeckImport, PendingImport, UnresolvedRow

#: Uploads above this are refused before anything is decoded. The reference
#: Archidekt export is 31 KB; a megabyte is already two orders of magnitude of
#: headroom, and unbounded input is how a 128 MiB cloudlet dies.
MAX_UPLOAD_BYTES = 1_000_000

#: And a second ceiling, because the first one does not bound the WORK.
#:
#: A megabyte of `1 x\n` is roughly 250,000 rows, every one of which is
#: stripped, parsed, and then looked up against a 35,568-row card table by the
#: resolver's four rungs. The byte limit is happy with that file; the resolver
#: is not, and the 128 MiB cloudlet is where it would be discovered.
#:
#: 50,000 is chosen against the thing being bounded rather than against the
#: format: a Commander deck is 100 cards, and the largest real collection
#: export anybody has is a few tens of thousands of rows. A file with more rows
#: than that is not a deck list and not a collection.
MAX_UPLOAD_ROWS = 50_000

#: Encodings tried in order. Exports from Windows tools are frequently cp1252,
#: and a UnicodeDecodeError on line 200 of a working file is a miserable bug.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class ImportError_(Exception):
    """The upload could not be turned into rows."""


class UnknownFormat(ImportError_):
    """No parser was confident. The user picks one by hand."""


@dataclass
class ImportOutcome:
    """What an import did, for the view to render."""

    deck: Deck
    record: DeckImport
    report: resolve.ResolutionReport

    @property
    def clean(self) -> bool:
        return self.record.is_clean


def decode(raw: bytes) -> str:
    """Bytes to text, without dying on a Windows-encoded export."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImportError_(
            f"file is {len(raw) // 1024} KB; the limit is {MAX_UPLOAD_BYTES // 1024} KB"
        )

    # A NUL byte is the cheapest reliable proof that this is not a text file,
    # and latin-1 will happily "decode" any byte sequence at all - so without
    # this check a JPEG parses, produces rows full of control characters, and
    # dies inside the resolver with `PostgreSQL text fields cannot contain NUL
    # (0x00) bytes`. That was a 500 on an upload form, which is a 500 a
    # stranger can cause on purpose.
    if b"\x00" in raw:
        raise ImportError_("that is not a text file - it contains binary data")

    text = None
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ImportError_("could not decode the file as text")

    # Counted here rather than in a parser, for the same reason the size and
    # the NUL check are here: this is the boundary every upload crosses, and a
    # limit each parser has to remember is a limit one of them will not.
    #
    # `count` rather than `splitlines`, which would build the whole list to
    # measure it - the point is to refuse the file without doing its work.
    rows = text.count("\n") + 1
    if rows > MAX_UPLOAD_ROWS:
        raise ImportError_(
            f"file has about {rows:,} lines; the limit is {MAX_UPLOAD_ROWS:,}. "
            "That is far more than a deck or a collection - is it the right file?"
        )
    return text


def build(parser_class, overrides: dict | None = None) -> importers.Parser:
    """Instantiate a parser, handing it a confirmed mapping if there is one.

    Only `TabularParser` has anything to do with column overrides; a plain text
    list has no columns to map. Rather than test for the type at four call
    sites, the choice is made once, here.
    """
    if overrides is not None and issubclass(parser_class, importers.TabularParser):
        return parser_class(overrides)
    return parser_class()


def choose(text: str, parser_name: str = "") -> type[importers.Parser]:
    """Which parser reads this file. Raises rather than guessing."""
    parser_class = importers.by_name(parser_name) if parser_name else importers.sniff(text)
    if parser_class is None:
        raise UnknownFormat("no parser recognised this file")
    return parser_class


def parse(
    text: str, parser_name: str = "", overrides: dict | None = None
) -> tuple[type[importers.Parser], list]:
    """Pick a parser and run it. Raises `UnknownFormat` rather than guessing."""
    parser_class = choose(text, parser_name)

    try:
        rows = list(build(parser_class, overrides).parse(text))
    except importers.ImportRefused as exc:
        # A refusal the person can act on: a column we may not invent a value
        # for, a file that is not a table, more rows than any real export has,
        # or CSV the csv module itself rejects. All four are surfaced as an
        # ordinary import error so they land on the form beside the file field,
        # which is where somebody can do something about them.
        raise ImportError_(str(exc)) from exc

    if not rows:
        raise ImportError_("the file contained no card rows")
    return parser_class, rows


@dataclass
class Preparation:
    """What one upload is, before anything has been written.

    The object the two import views branch on. Its whole job is to answer
    **"can this be read without asking anybody?"** - and to carry the mapping
    to the screen that asks, when the answer is no.
    """

    text: str
    parser_class: type[importers.Parser]
    mapping: "importers.columns.Mapping | None" = None
    table: object = None

    @property
    def needs_mapping(self) -> bool:
        """True when a person must confirm the columns before any import.

        Deliberately narrow - a missing card name or a missing quantity, and
        nothing else. Interrupting somebody over an unmatched `language` column
        would teach them to click through this screen without reading it, which
        is worse than not having the screen.
        """
        return self.mapping is not None and self.mapping.needs_confirmation


def prepare(raw: bytes, parser_name: str = "", overrides: dict | None = None) -> Preparation:
    """Decode an upload and work out whether it can be read unaided.

    Does not touch the database and does not resolve a single card, so it is
    safe to run before the quota check: nothing here is the work being metered.
    """
    return prepare_text(decode(raw), parser_name, overrides)


def prepare_text(
    text: str, parser_name: str = "", overrides: dict | None = None
) -> Preparation:
    """`prepare`, for a file that has already been decoded.

    Which is every file on the second half of a mapping round trip: the
    `PendingImport` holds text, and encoding it back to bytes only to decode it
    again would be a ceremony, not a check.
    """
    parser_class = choose(text, parser_name)
    parser = build(parser_class, overrides)

    if not isinstance(parser, importers.TabularParser):
        return Preparation(text=text, parser_class=parser_class)

    try:
        table, mapping = parser.read(text)
    except importers.ImportRefused as exc:
        raise ImportError_(str(exc)) from exc
    return Preparation(text=text, parser_class=parser_class, mapping=mapping, table=table)


@transaction.atomic
def import_deck(
    *,
    owner,
    raw: bytes = b"",
    name: str = "",
    filename: str = "",
    parser_name: str = "",
    deck: Deck | None = None,
    overrides: dict | None = None,
    text: str | None = None,
) -> ImportOutcome:
    """Create (or refill) a deck from an uploaded file.

    Atomic on purpose: a half-imported deck is worse than a failed import,
    because it looks finished.

    `text` is for the second half of a mapping round trip, where the file was
    decoded before the person was asked about its columns and re-decoding a
    `PendingImport` would only mean encoding it back to bytes first. `raw` is
    the ordinary path.
    """
    if text is None:
        text = decode(raw)
    parser_class, rows = parse(text, parser_name, overrides)

    # Call site 3 of the four `check()` is allowed, and it comes BEFORE the
    # resolver, which is the work being metered - it used to come after, so an
    # account over its limit still had every file resolved before being told.
    # The import is metered even when it refills an existing deck: parsing and
    # resolving 214 rows is the work, not the row in the Deck table.
    quotas.check(owner, UsageRecord.Metric.IMPORTS)

    creating = deck is None
    if creating:
        # Call site 2: deck creation, which the free plan caps by decks OWNED.
        quotas.check(owner, quotas.DECKS_OWNED)
    elif deck.owner_id != owner.pk:
        raise ImportError_("that deck belongs to someone else")

    report = resolve.resolve(rows)

    if creating:
        deck = Deck.objects.create(
            owner=owner, name=name or Path(filename).stem or "Imported deck"
        )
        quotas.consume(owner, UsageRecord.Metric.DECKS_CREATED)
    quotas.consume(owner, UsageRecord.Metric.IMPORTS)

    record = DeckImport.objects.create(
        deck=deck,
        owner=owner,
        filename=filename[:255],
        parser=parser_class.name,
        column_mapping=describe(text, parser_name, overrides),
        rows_total=report.rows_total,
        rows_resolved=len(report.resolved),
        rows_unresolved=len(report.unresolved),
        rung_counts=report.rung_counts,
        status=DeckImport.Status.APPLIED,
    )

    _write_entries(deck, report, commander=_commander_for(deck, report))
    _write_unresolved(record, report)
    _set_commander(deck, report)
    if creating and not name and deck.commander_id:
        # A deck is called after its commander, not after the file: every
        # Archidekt export is named "archidekt-collection-export-<date>.csv"
        # (phase 9 G - the name a guest's save form starts from).
        deck.name = deck.commander.name
        deck.save(update_fields=["name"])
    recount_later(Deck.objects.filter(pk=deck.pk))
    deck.open_questions = None

    return ImportOutcome(deck=deck, record=record, report=report)


def _commander_for(deck: Deck, report: resolve.ResolutionReport):
    """The card this import leaves in the command zone, if any.

    The file's own marker when there is exactly one, and otherwise the
    commander the deck already had - a refill from a list that does not mark
    one keeps the commander somebody picked by hand.
    """
    marked = [r for r in report.resolved if r.row.is_commander]
    if len(marked) == 1:
        return marked[0].card
    return deck.commander if deck.commander_id else None


def _write_entries(deck: Deck, report: resolve.ResolutionReport, *, commander=None) -> None:
    """Replace the deck's contents with what resolved.

    Replace rather than merge: an import is the user saying "this is the deck
    now". Merging would make a re-import after a fix silently additive, and
    turn a 100-card deck into a 200-card one.

    **The commander is not one of the 99** (trap 46). Every export lists it as
    a row - Archidekt tags it, a text list puts it under `// Commander` - and
    writing that row as a `DeckCard` as well as setting `Deck.commander` put
    the card in two zones at once: "101 cards" on the legality panel, the
    commander shuffled into the library as well as sitting in the command
    zone, and a collection check asking for two copies. One copy leaves the
    list for the command zone here.
    """
    DeckCard.objects.filter(deck=deck).delete()
    quantities = report.quantities()
    if commander is not None and str(commander.pk) in quantities:
        quantities[str(commander.pk)] -= 1
        if quantities[str(commander.pk)] < 1:
            del quantities[str(commander.pk)]
    cards = {str(r.card.pk): r.card for r in report.resolved}

    DeckCard.objects.bulk_create(
        [
            DeckCard(deck=deck, oracle_card=cards[oracle_id], quantity=min(quantity, 32_767))
            for oracle_id, quantity in quantities.items()
        ],
        batch_size=500,
    )


def _write_unresolved(record: DeckImport, report: resolve.ResolutionReport) -> None:
    UnresolvedRow.objects.bulk_create(
        [
            UnresolvedRow(
                deck_import=record,
                line_number=resolution.row.line_number,
                raw_name=resolution.row.name[:256],
                # A smallint. The resolver refuses anything past its own limit,
                # and this clamp is what keeps that refusal from being a 500.
                quantity=min(resolution.row.quantity, 32_767),
                reason=resolution.reason[:200],
                suggestions=resolution.suggestions,
            )
            for resolution in report.unresolved
        ],
        batch_size=200,
    )


def _set_commander(deck: Deck, report: resolve.ResolutionReport) -> None:
    """Use the export's own commander marker, and never guess past it.

    If the file does not say, the deck has no commander and the deck page says
    so plainly. Picking "the only legendary creature" would be right often
    enough to be trusted and wrong often enough to hurt.
    """
    marked = [r for r in report.resolved if r.row.is_commander]
    if len(marked) == 1:
        deck.commander = marked[0].card
        deck.save(update_fields=["commander", "updated_at"])


def recount_later(decks) -> None:
    """Forget the stored count of open questions; the next page counts again.

    Called by everything that can change which cards the engine cannot read
    or which of them the owner has answered: an import, a commander change, a
    saved or forgotten annotation, a profile rebuild. `update`, so a count
    going stale is not an edit of the deck and does not reorder the list.
    """
    decks.update(open_questions=None)


@transaction.atomic
def set_commander(deck: Deck, card) -> None:
    """Set the commander by hand, from the deck page.

    Picked out of the deck's own list, so one copy moves from the 99 to the
    command zone - the same rule the importer follows. A commander being
    replaced goes back into the 99 rather than vanishing from the deck: which
    card leaves a deck is its owner's decision, not a side effect of this one.
    """
    previous = deck.commander if deck.commander_id else None
    if previous is not None and previous.pk == card.pk:
        return

    entry = DeckCard.objects.filter(deck=deck, oracle_card=card).first()
    if entry is not None:
        if entry.quantity > 1:
            entry.quantity -= 1
            entry.save(update_fields=["quantity"])
        else:
            entry.delete()

    if previous is not None:
        back, created = DeckCard.objects.get_or_create(
            deck=deck, oracle_card=previous, defaults={"quantity": 1}
        )
        if not created:
            back.quantity += 1
            back.save(update_fields=["quantity"])

    deck.commander = card
    deck.open_questions = None
    deck.save(update_fields=["commander", "open_questions", "updated_at"])


# --- the mapping round trip -------------------------------------------------


def hold(*, owner, preparation: Preparation, kind: str, filename: str = "",
         deck_name: str = "", deck: Deck | None = None) -> PendingImport:
    """Park an upload while its owner is asked what its columns mean.

    Replaces rather than accumulates: one pending import per person per kind,
    enforced by a unique constraint and by this delete. Somebody who uploads
    the wrong file and immediately uploads the right one should not leave a
    stale row behind holding the wrong one.
    """
    PendingImport.objects.filter(owner=owner, kind=kind).delete()
    return PendingImport.objects.create(
        owner=owner,
        kind=kind,
        filename=filename[:255],
        text=preparation.text,
        parser=preparation.parser_class.name,
        deck_name=deck_name[:120],
        deck=deck,
    )


def preview(text: str, parser_name: str = "",
            overrides: dict | None = None) -> tuple[list, str]:
    """The first few rows as a mapping would read them, and why it could not.

    Returns `(rows, problem)` rather than raising, because on the mapping
    screen a refusal is not an error - it is the normal state of the page
    before somebody has finished answering it, and it belongs in the preview
    panel next to the dropdowns rather than on an error page.
    """
    try:
        parser = build(choose(text, parser_name), overrides)
        if not isinstance(parser, importers.TabularParser):
            return list(parser.parse(text))[: importers.tabular.PREVIEW_ROWS], ""
        return parser.preview(text), ""
    except importers.ImportRefused as exc:
        return [], str(exc)
    except UnknownFormat:
        return [], "no parser recognised this file"


def describe(text: str, parser_name: str = "", overrides: dict | None = None) -> list:
    """What each concept was read from, as `[label, header]` pairs.

    Recorded on every import and shown on the screen afterwards, which is the
    other half of the bargain the mapping screen makes. A file whose headers
    answer for themselves is imported without interrupting anybody - so the
    place the reading becomes visible is here, after the fact, rather than
    never.

    Pairs rather than a keyed dict because this is read far more often than it
    is queried, including by a person looking at the row in the admin, and a
    label is a better thing to store than a key nobody outside this package
    knows the spelling of.

    Never raises. It is provenance, not a step of the import: a mapping that
    cannot be described is an empty list and an import that still happened.
    """
    try:
        preparation = prepare_text(text, parser_name, overrides)
    except (UnknownFormat, ImportError_):
        return []

    mapping = preparation.mapping
    if mapping is None:
        return []
    return [
        [column.label, mapping.found[column.key]]
        for column in importers.columns.MAPPABLE
        if mapping.has(column)
    ]
