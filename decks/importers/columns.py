"""Which header means which thing, across every export format.

**The discovery that produced this module: several of these formats do not have
a fixed header row at all.** Archidekt lets you tick which columns to export,
and Delver Lens makes you choose the fields *and their order*. So "one parser
per site, keyed on that site's header" was never going to hold - the same site
produces different files for different users, and a parser that assumes
otherwise does not fail, it silently reads every quantity as 1.

That was not hypothetical. Before this module existed, an Archidekt export with
the Quantity column unticked sniffed at 0.75 confidence, parsed without
complaint, and turned 28 Swamps into 1 Swamp.

So the design is **concepts, not columns**:

* a `Column` is a thing a deck list can tell us - how many, which card, which
  printing
* each one carries the header strings the known tools actually use for it
* a file is read by matching its header against those aliases, case- and
  space-insensitively, and ignoring everything it does not recognise
* **a concept nobody may invent a value for raises rather than defaulting**

The last point is this project's honesty rule applied to imports. The deriver
refuses to guess a mana amount; the importer refuses to guess a quantity. Both
would be right often enough to look fine.

--------------------------------------------------------------------------
**Where these aliases come from, and how far to trust them.** The Archidekt row
was read off a real export of the user's own collection. The rest are from
`StepKie/MtgCsvHelper`'s `appsettings.json`, a working conversion tool that
maintains this mapping for its own use - which is much better evidence than
guessing and is still **not a verified export**. Two public sources listing
Moxfield's columns disagreed with each other, which is exactly why every format
below stays out of the sniffing registry until somebody has exported a real
file from it.

Aliases are safe to add ahead of a fixture in a way that a *parser* is not: an
alias that is wrong matches nothing and the column is reported missing, which
is loud. A parser that is wrong imports the wrong cards, which is silent.
--------------------------------------------------------------------------

**2026-09-20, the second change of role.** This module started as a helper
inside the Archidekt parser. It is now the importer's main road: `TabularParser`
reads *any* delimited export through it, and when a concept cannot be matched
the user is shown the mapping and asked, rather than a phase of work being
booked to write a parser for that one site.

That makes `overrides` the important new idea. An alias is this application
guessing what a header means; an override is a person saying so. The two are
kept apart deliberately - `Mapping.confirmed` is true only in the second case,
and it is the one thing that lets `quantity` be legitimately absent. The
honesty rule survives intact: **the application still never invents a
quantity. A person is allowed to tell it there is not one.**
"""

from dataclasses import dataclass, field

from decks.importers.base import MissingColumn


def normalise(header: str) -> str:
    """One header cell, reduced to something comparable.

    Case, surrounding quotes, spaces, underscores and hyphens all vary between
    tools that mean the identical thing - `Set code`, `Set Code`, `SETCODE`,
    `set_code`. None of that difference carries meaning, so none of it survives.
    """
    return "".join(
        character for character in (header or "").strip().strip('"').lower()
        if character.isalnum()
    )


@dataclass(frozen=True)
class Column:
    """One thing a deck list can say, and every way it says it."""

    key: str
    #: Reads inside "this file has no ___ column", so it is a noun phrase and
    #: not a description. "how many" was the first attempt and produced "this
    #: file has no how many column" - the kind of sentence that only survives
    #: when nobody reads the error they wrote.
    label: str
    aliases: tuple[str, ...]
    #: True when the importer may not proceed without it. Only `name` is, today:
    #: a row with no card is not a row. `quantity` is deliberately *not*
    #: required - a plain text list has no such column and one card per line is
    #: a real convention - but a file that HAS a quantity concept and lost it is
    #: a different matter, which is what `require()` is for.
    required: bool = False
    #: The `ParsedRow` field this feeds, when it feeds one directly.
    field_name: str = ""

    @property
    def normalised(self) -> frozenset[str]:
        return frozenset(normalise(alias) for alias in self.aliases)

    def find(self, headers) -> str | None:
        """The header in `headers` that means this, or None.

        Returns the header **as the file spells it**, because that is the key
        `csv.DictReader` will hand back.
        """
        wanted = self.normalised
        for header in headers:
            if normalise(header) in wanted:
                return header
        return None


#: Every alias below is a header some tool actually writes. Sources in the
#: module docstring; the Archidekt ones are the only ones read off a real file.
QUANTITY = Column(
    "quantity", "quantity",
    ("Quantity", "Count", "Qty", "Amount", "Card Count", "groupCount", "QUANTITY"),
    field_name="quantity",
)
#: **`Tradelist Count` is deliberately not a quantity alias.** Deckbox and
#: Moxfield both export it beside `Count`, and it means "how many of these I am
#: willing to trade away", which is routinely zero for a card somebody owns
#: four of. It was in this tuple until 2026-09-20, taken from a conversion
#: tool's mapping without asking what the words meant - and since `find()`
#: returns the first matching header in file order, a file that happened to
#: list it first would have read a whole collection as untradeable, which is to
#: say as empty. Nothing would have errored.
NAME = Column(
    "name", "card name",
    ("Name", "Card Name", "Card", "Simple Name", "CardName", "NAME"),
    required=True, field_name="name",
)
SET_CODE = Column(
    "set_code", "set code",
    ("Edition Code", "Set Code", "Set code", "Edition", "Set ID", "Set",
     "SETCODE", "Set Name"),
    field_name="set_code",
)
COLLECTOR_NUMBER = Column(
    "collector_number", "collector number",
    ("Collector Number", "Collector number", "Card Number", "Collector #",
     "Number", "COLLECTOR NUMBER"),
    field_name="collector_number",
)
SCRYFALL_ID = Column(
    "scryfall_id", "Scryfall printing id",
    ("Scryfall ID", "Scryfall Id", "ScryfallId", "scryfall_id"),
    field_name="scryfall_id",
)
FINISH = Column(
    "finish", "finish",
    ("Finish", "Foil", "Printing", "Premium", "FINISH", "isFoil"),
)
CONDITION = Column("condition", "condition", ("Condition", "CONDITION"))
LANGUAGE = Column("language", "language", ("Language", "LANGUAGE", "Lang"))
#: Not an identifier - the only column that can say which card is the general.
#: Archidekt writes it into a free-text `Tags` cell, which is why the test is a
#: substring search and not equality: a real cell reads `Commander,Ramp`.
TAGS = Column(
    "tags", "tags or category",
    ("Tags", "Tag", "Category", "Categories"),
)

ALL = (QUANTITY, NAME, SET_CODE, COLLECTOR_NUMBER, SCRYFALL_ID,
       FINISH, CONDITION, LANGUAGE, TAGS)

#: What the mapping screen offers, in the order it offers them. Identical to
#: `ALL` today and deliberately a separate name: the day a concept exists that
#: is derived rather than read from a column, it belongs in one and not the other.
MAPPABLE = ALL

#: The value a mapping form sends for "this file has no such column". Distinct
#: from an empty string, which means "no answer given", because the difference
#: between those two is the whole of `Mapping.confirmed`.
ABSENT = "—"


@dataclass
class Mapping:
    """Which header in one particular file means which concept."""

    found: dict[str, str] = field(default_factory=dict)
    #: Every header the file actually had, in file order. Kept so the mapping
    #: screen can offer them as choices and so `get()` can be checked against
    #: them - a header that is not in this list was not in the file, whatever
    #: a form field claims.
    headers: list[str] = field(default_factory=list)
    #: Concepts a person has explicitly said the file does not contain.
    absent: set[str] = field(default_factory=set)
    #: True when a human confirmed this mapping rather than the aliases
    #: guessing it. The only thing that distinguishes "we could not find a
    #: quantity column" from "there isn't one, count each row once".
    confirmed: bool = False

    def has(self, column: Column) -> bool:
        return column.key in self.found

    def declared_absent(self, column: Column) -> bool:
        return column.key in self.absent

    def header_for(self, column: Column) -> str:
        """What this concept is bound to, for redisplaying the mapping form."""
        if column.key in self.absent:
            return ABSENT
        return self.found.get(column.key, "")

    @property
    def unresolved(self) -> list[Column]:
        """Concepts that are neither matched nor explicitly ruled out.

        What the mapping screen exists to empty. Only `quantity` and `name`
        make it worth interrupting somebody for; see `needs_confirmation`.
        """
        return [c for c in MAPPABLE if not self.has(c) and c.key not in self.absent]

    @property
    def needs_confirmation(self) -> bool:
        """Should a person look at this before anything is written?

        Two cases, and only two - being interrupted over a missing `language`
        column would teach people to click through the screen without reading
        it, which is worse than not having it.

        * **No card name.** Nothing can be read at all.
        * **No quantity.** The 28-Swamp bug. This is the one that was live.
        """
        return not self.has(NAME) or not (self.has(QUANTITY) or QUANTITY.key in self.absent)

    def get(self, row: dict, column: Column, default: str = "") -> str:
        """One cell of one row, by concept rather than by header."""
        header = self.found.get(column.key)
        if header is None:
            return default
        value = row.get(header)
        return default if value is None else str(value).strip()

    def require(self, column: Column, *, hint: str = "") -> None:
        """Refuse to continue without this column.

        The whole point of the module. A tool that lets somebody untick
        Quantity produces a file this application can parse perfectly and read
        entirely wrongly, and the only honest response is to stop and say which
        column to put back.
        """
        if self.has(column):
            return
        raise MissingColumn(
            f"this file has no {column.label} column. "
            + (hint or f"Re-export it with {column.aliases[0]} included.")
        )

    @property
    def missing_labels(self) -> list[str]:
        return [c.label for c in ALL if not self.has(c)]


#: Concept keys, for validating whatever a form posts back at us.
KEYS = frozenset(column.key for column in ALL)


def map_headers(headers, overrides: dict | None = None) -> Mapping:
    """Read one file's header row into a concept mapping.

    Unrecognised headers are ignored rather than rejected: a collection export
    carrying purchase prices, tags and a date added is a normal file, and
    refusing it because of a column we have no use for would be pedantry.

    `overrides` is what a person chose on the mapping screen: `{concept key:
    header}`, with `ABSENT` meaning "this file has no such column". It arrives
    straight from a POST, so **nothing in it is trusted**: a key that is not a
    concept is dropped, and a header that is not in this file's own header row
    is dropped. The worst a hostile mapping can do is map nothing, which is the
    same outcome as an empty file.
    """
    headers = list(headers or [])
    mapping = Mapping(headers=headers)
    for column in ALL:
        header = column.find(headers)
        if header is not None:
            mapping.found[column.key] = header

    if overrides is None:
        return mapping

    mapping.confirmed = True
    # By normalised spelling, so a form that round-trips a header through HTML
    # and back still matches the column `csv.DictReader` will hand us.
    known = {normalise(header): header for header in headers}
    for key, choice in overrides.items():
        if key not in KEYS:
            continue
        if choice == ABSENT:
            mapping.found.pop(key, None)
            mapping.absent.add(key)
            continue
        actual = known.get(normalise(choice))
        if actual is not None:
            mapping.found[key] = actual
            mapping.absent.discard(key)
    return mapping
