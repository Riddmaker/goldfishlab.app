"""Any delimited export, read by what its columns mean.

This replaced a plan to write seven parsers - one each for Moxfield, Deckbox,
ManaBox, Delver Lens, Dragon Shield, Cardsphere and Helvault - and the argument
for replacing it came from our own repository rather than from taste.

The Archidekt parser was written against a **real export of the user's own
collection**. Every column name in it was read off a file, not guessed. It was
still silently wrong, because Archidekt lets you untick columns: an export
without `Quantity` sniffed at 0.75 confidence, parsed without complaint, and
turned 28 Swamps into one Swamp. A fixture proves what one file looked like on
one day for one user's export settings. Seven fixtures would have been seven
parsers chasing seven sites that change whenever they like.

So the unit of support is no longer a site. It is a **concept** - how many,
which card, which printing - and `columns.py` holds every header string the
known tools use for one. This parser matches a file's header row against that
vocabulary and reads whatever it recognises.

**Where it cannot tell, it asks.** That is the part that makes this safer than
seven parsers rather than looser. A parser decides silently and the person
finds out three screens later; a mapping screen shows them `Quantity <-
"Count"` and a preview of their own first three rows before a single row is
written. The 28-Swamp bug is not fixed here so much as made structurally
impossible: an unmatched quantity column becomes something a person sees,
rather than something an algorithm infers.

Three consequences worth naming:

* **An unknown format costs the user thirty seconds, not us a phase.** Any CSV
  is importable the day somebody uploads it.
* **`preamble_skip` stopped being a per-format constant.** Dragon Shield writes
  junk lines above its header; so the header is *located* rather than assumed
  to be line one, by looking for the line that recognises the most concepts.
* **Nothing here guesses a delimiter by frequency.** Each candidate is tried
  and scored on how many concepts its header yields, which is the only measure
  that is about this file rather than about commas.

No AI anywhere in this path. A person with a dropdown is free, instant,
offline, always available, and does not put anybody's file in front of a third
party.
"""

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field

from django.utils.translation import gettext, gettext_noop

from core.l10n import number
from decks.importers import columns
from decks.importers.base import (
    MalformedTable,
    NotTabular,
    ParsedRow,
    Parser,
    TooManyRows,
)

#: Tried in this order, and scored rather than counted. `;` is not exotic - it
#: is what Excel writes on a machine with a European locale, which is most of
#: the ones this application is aimed at.
DELIMITERS = (",", ";", "\t")

#: How far into the file to look for the header row. Dragon Shield writes a
#: couple of junk lines above its header; ten is generous and bounded.
HEADER_SCAN = 10

#: Lines used to judge a candidate header. Enough to catch a file whose second
#: line happens to have the same field count by coincidence.
WINDOW = 6

#: `MAX_UPLOAD_BYTES` already caps the file at a megabyte - but a megabyte of
#: `1,a` is a quarter of a million rows, and a quarter of a million dataclasses
#: is most of a 128 MiB cloudlet. Real exports do not come close: the reference
#: collection is 214 rows, and a megabyte of rows carrying names, set codes and
#: collector numbers runs out at roughly ten thousand.
MAX_ROWS = 25_000

#: A deck list with more columns than this is not a deck list.
MAX_COLUMNS = 128

#: Rows rendered on the mapping screen. Three is enough to see a quantity in
#: the wrong column, and few enough that nobody scrolls to reach the button.
PREVIEW_ROWS = 3


@dataclass(frozen=True)
class Table:
    """Where the header row is, and how to split it."""

    skip: int
    delimiter: str
    headers: list[str] = field(default_factory=list)
    #: How many concepts this header yielded. Zero is a legitimate answer - it
    #: means "a table whose columns we do not recognise", which is a file the
    #: mapping screen can still rescue and the sniffer must still refuse.
    recognised: int = 0

    @property
    def delimiter_label(self) -> str:
        return {",": "comma", ";": "semicolon", "\t": "tab"}.get(self.delimiter, "unknown")


def _rows_of(lines: list[str], delimiter: str) -> list[list[str]] | None:
    try:
        return list(csv.reader(lines, delimiter=delimiter))
    except csv.Error:
        return None


def _candidate(window: list[str], delimiter: str) -> list[str] | None:
    """The header row this delimiter would give, if it gives a table at all.

    A table is a first line of two or more fields where no later line in the
    window is *wider* and at least one is exactly as wide. Both halves of that
    matter:

    * **At least one full-width row** is what keeps a plain text list out.
      `1 Chainer, Dementia Master` splits into two fields on a comma and
      `1 Sol Ring` splits into one, so nothing in the pair is two fields wide
      and it is not a table. Read as one, every card name would arrive with
      half a row glued to it.
    * **Short rows are allowed**, because they are ordinary. A tool that omits
      the trailing empty cells writes `4,Sol Ring` under a three-column header,
      and demanding equality here refused real exports outright. `Mapping.get`
      already returns the default for a cell that is not there.

    A row *wider* than the header means the delimiter is wrong, so it still
    disqualifies the candidate.
    """
    rows = _rows_of(window, delimiter)
    if not rows:
        return None

    width = len(rows[0])
    if width < 2 or width > MAX_COLUMNS:
        return None

    body = rows[1:]
    if body:
        if any(len(row) > width for row in body):
            return None
        if not any(len(row) == width for row in body):
            return None
    return rows[0]


def locate(text: str) -> "Table | None":
    """Find the header row, or decide this is not a table at all.

    Scored, not taken first-come: every (start line, delimiter) pair that forms
    a consistent table is judged on **how many concepts its header names**, and
    the best wins. That single measure does three jobs at once - it picks the
    delimiter, it skips a tool's preamble junk, and it declines a file whose
    first line merely happens to contain a comma.

    Ties go to the earliest line and then to the wider table, so a file we
    recognise nothing in still yields its own first row rather than a later one
    that happened to split the same way.
    """
    lines = text.lstrip("﻿").splitlines()
    best: Table | None = None

    for start in range(min(HEADER_SCAN, len(lines))):
        if not lines[start].strip():
            continue

        window = [line for line in lines[start:start + WINDOW] if line.strip()]
        for delimiter in DELIMITERS:
            headers = _candidate(window, delimiter)
            if headers is None:
                continue

            recognised = len(columns.map_headers(headers).found)
            # A header row with no data under it is only believable when we
            # recognise something in it; otherwise it is simply the last line
            # of a file that is not a table.
            if len(window) < 2 and not recognised:
                continue

            if best is None or (recognised, len(headers)) > (best.recognised, len(best.headers)):
                best = Table(start, delimiter, headers, recognised)

    return best


class TabularParser(Parser):
    """Reads a delimited file through the concept vocabulary."""

    name = "csv"
    label = gettext_noop("CSV or TSV export (any tool)")

    def __init__(self, overrides: dict | None = None):
        """`overrides` is a person's answer on the mapping screen.

        `None` means nobody has been asked, and the parser is correspondingly
        strict: it refuses a file with no quantity column rather than reading
        every row as a single copy. A dict means somebody looked at the mapping
        and confirmed it, and `columns.ABSENT` is then a legitimate answer.
        """
        self.overrides = overrides

    @classmethod
    def sniff(cls, header: str, preamble: str = "") -> float:
        """Confident only when the header names a card.

        **A table we recognise nothing in scores below the threshold on
        purpose.** We can read that file perfectly well once somebody tells us
        which column is which - but knowing *how* to read a file is not the
        same as knowing we were asked to, and the registry's whole contract is
        that it answers `None` rather than guessing. Such a file reaches the
        format picker, the person chooses this parser by name, and the mapping
        screen does the rest. One click, and nothing was assumed.
        """
        table = locate(preamble or header)
        if table is None:
            return 0.0

        mapping = columns.map_headers(table.headers)
        if not mapping.has(columns.NAME):
            return 0.3
        # Capped below the score a format-specific signature can reach, so a
        # parser that knows *which* tool wrote the file keeps the file.
        return min(0.85, 0.5 + 0.1 * table.recognised)

    def read(self, text: str) -> "tuple[Table, columns.Mapping]":
        """Locate the header and map it, without reading a single card row.

        Separate from `parse` because the mapping screen needs exactly this and
        nothing else, and because a refusal that arrives before any work is a
        refusal a form can render.
        """
        table = locate(text)
        if table is None:
            raise _why_not(text)
        return table, columns.map_headers(table.headers, self.overrides)

    def parse(self, text: str) -> Iterator[ParsedRow]:
        """Refuse eagerly, yield lazily.

        Not a generator itself, so `MissingColumn` and `NotTabular` are raised
        when `parse()` is called rather than when its result is first iterated.
        A refusal that only happens if somebody loops is a refusal that gets
        missed.
        """
        table, mapping = self.read(text)

        mapping.require(
            columns.NAME,
            hint=gettext("Nothing can be read from a file with no card name in it. "
                         "Re-export it with the card name included, or map a column to "
                         "it by hand."),
        )
        if not mapping.confirmed:
            # Only when nobody has been asked. Once a person has seen the
            # mapping screen, "there is no quantity column, count each row
            # once" is a statement they are entitled to make. The rule being
            # enforced is that *the application* may not invent a quantity -
            # not that every file must have one.
            mapping.require(
                columns.QUANTITY,
                hint=gettext("Re-export it with Quantity ticked - without it every "
                             "card would be read as a single copy, and a 28-Swamp deck "
                             "would import as a 28-card deck with one Swamp in it. You "
                             "can also map the column by hand."),
            )
        return self._rows(text, table, mapping)

    def preview(self, text: str, limit: int = PREVIEW_ROWS) -> list[ParsedRow]:
        """The first few rows as this mapping would read them."""
        rows = []
        for row in self.parse(text):
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    def _rows(self, text: str, table: "Table", mapping: columns.Mapping) -> Iterator[ParsedRow]:
        produced = 0

        for offset, raw in enumerate(_records(text, table), start=table.skip + 2):
            name = mapping.get(raw, columns.NAME)
            if not name:
                continue

            produced += 1
            if produced > MAX_ROWS:
                raise TooManyRows(gettext(
                    "that file has more than %(limit)s card rows. The largest real "
                    "collection export is a few thousand, so this is almost certainly "
                    "not a deck list."
                ) % {"limit": number(MAX_ROWS)})

            yield ParsedRow(
                line_number=offset,
                name=name,
                quantity=_int(mapping.get(raw, columns.QUANTITY), default=1),
                scryfall_id=mapping.get(raw, columns.SCRYFALL_ID),
                set_code=mapping.get(raw, columns.SET_CODE).lower(),
                collector_number=mapping.get(raw, columns.COLLECTOR_NUMBER),
                is_commander=_is_commander(mapping.get(raw, columns.TAGS)),
                extra={
                    "finish": mapping.get(raw, columns.FINISH),
                    "condition": mapping.get(raw, columns.CONDITION),
                    "language": mapping.get(raw, columns.LANGUAGE),
                },
            )


def _why_not(text: str) -> "NotTabular | MalformedTable":
    """Which refusal a file that produced no table has earned.

    Worth the extra few lines because the two messages ask for different
    things. "This is not a table, pick plain text" is advice somebody can take;
    saying it about a file whose quoting is broken sends them down a path that
    cannot work. A file every delimiter chokes on is malformed, not prose.
    """
    window = text.lstrip("\ufeff").splitlines()[:WINDOW]
    errors = 0
    for delimiter in DELIMITERS:
        try:
            list(csv.reader(window, delimiter=delimiter))
        except csv.Error as exc:
            errors += 1
            last = exc
    if errors == len(DELIMITERS):
        return MalformedTable(gettext("that file is not valid CSV: %(error)s") % {
            "error": last})

    return NotTabular(gettext(
        "that file is not a table - no line in it splits into columns "
        "consistently. If it is a plain list of card names, choose "
        "'Plain text list' as the format."
    ))


def _body(text: str, table: "Table") -> str:
    """The file from its header row down, with any byte-order mark gone.

    One helper rather than two copies, because the preview and the import
    disagreeing about where the table starts is exactly the class of bug
    this screen exists to prevent - the person would confirm one reading and
    get another.

    When there is no preamble the original text goes through untouched, so a
    quoted field containing a newline survives. Splitting is only done to drop
    a tool's junk lines, and only then does that guarantee lapse - which is the
    right trade, because without it a Dragon Shield export cannot be read at
    all.
    """
    body = text.lstrip("\ufeff")
    if not table.skip:
        return body
    return "\n".join(body.splitlines()[table.skip:])


def sample(text: str, table: "Table", limit: int = PREVIEW_ROWS) -> list[list[str]]:
    """The first few rows of the file exactly as they are, mapped to nothing.

    What the mapping screen shows above the dropdowns. A person choosing which
    column holds the quantity needs to see the columns, and the *parsed*
    preview cannot help them yet - it is blank until they have chosen.
    """
    rows = _rows_of(_body(text, table).splitlines()[1:limit + 1], table.delimiter) or []
    width = len(table.headers)
    return [(row + [""] * width)[:width] for row in rows]


def _records(text: str, table: "Table") -> Iterator[dict]:
    """`csv.DictReader` over the file, starting at the located header."""
    try:
        yield from csv.DictReader(
            io.StringIO(_body(text, table)), delimiter=table.delimiter
        )
    except csv.Error as exc:
        # An unterminated quote, or a single field larger than the csv module's
        # 128 KB limit. Both are bounded by `MAX_UPLOAD_BYTES` already, so this
        # is about answering with a form error instead of a 500 on an upload a
        # stranger controls - not about memory.
        raise MalformedTable(gettext("that file is not valid CSV: %(error)s") % {
            "error": exc}) from exc


def _int(value, *, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _is_commander(tags: str) -> bool:
    """Whether a tags or category cell marks this row as the general.

    A substring test because the real cell reads `Commander,Ramp` rather than
    `Commander`. Nothing else is inferred: a file that does not say has no
    commander, and the deck page says so plainly.
    """
    return "commander" in (tags or "").lower()
