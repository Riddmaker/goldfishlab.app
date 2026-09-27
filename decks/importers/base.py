"""The parser contract every deck-list format implements.

Three implementations: `TabularParser` for anything delimited, `PlainTextParser`
for a typed list, and `ArchidektParser`, which is `TabularParser` plus a
signature to recognise it by.

**That list is not expected to grow.** Until 2026-09-20 this docstring said the
seven remaining formats each needed a real export committed as a fixture. They
did, for the design of the day - one parser per site, keyed on that site's
header. That design died of the observation that Archidekt itself has no fixed
header, and with it died the argument for seven parsers. Formats are now read
by concept and confirmed by the person importing them.

**Read `docs/phases/importers.md` before adding one - and the first thing it
says is that you probably should not.** `TabularParser` reads any delimited
export by what its columns mean, and where it cannot tell, it asks. A parser
subclass is now only worth writing when a format needs something structurally
different, not merely different header spellings.

`sniff()` returns a confidence rather than a boolean, and the registry returns
`None` instead of falling back to a best guess, because a format picker costs
the user one click and a silently mis-parsed collection costs them their trust
in every number the site prints afterwards.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field


class ImportRefused(ValueError):
    """The file was readable and we are declining to read it anyway.

    Every subclass is a refusal a person can act on, which is the difference
    between this and a crash. They share a base so `decks.services.parse` can
    catch the category and put the message on the form beside the file field,
    rather than growing an `except` clause per reason.
    """


class MissingColumn(ImportRefused):
    """The file parsed, but a column we may not invent a value for is absent.

    Raised rather than defaulted, because the alternative is the worst kind of
    bug this application can have: an import that succeeds and is wrong. See
    `columns.QUANTITY` for the case that proved it.
    """


class NotTabular(ImportRefused):
    """No delimiter and no header row could be found in the first few lines."""


class TooManyRows(ImportRefused):
    """More rows than any real deck or collection export has.

    A bound on memory, not on ambition. `MAX_UPLOAD_BYTES` already caps the
    file at a megabyte, but a megabyte of `1,a` is a quarter of a million rows
    and a quarter of a million dataclasses, which is most of a 128 MiB
    cloudlet. See `tabular.MAX_ROWS`.
    """


class MalformedTable(ImportRefused):
    """`csv` itself refused the file - an unterminated quote, a huge field."""


@dataclass
class ParsedRow:
    """One line of a deck list, before any card lookup happens.

    Every identifier is optional because every export format omits a different
    set of them. The resolver decides what to do with whatever arrived.
    """

    line_number: int
    name: str
    quantity: int = 1
    scryfall_id: str = ""
    oracle_id: str = ""
    set_code: str = ""
    collector_number: str = ""
    is_commander: bool = False
    extra: dict = field(default_factory=dict)

    #: Longest card name in the game is well under half this. A cell longer
    #: than this is not a card name, and carrying it whole only means a wider
    #: unresolved-rows table and a bigger row in `UnresolvedRow.raw_name`,
    #: which truncates to 256 anyway.
    MAX_NAME = 256

    def __post_init__(self):
        self.name = (self.name or "").strip()[: self.MAX_NAME]
        self.quantity = max(1, int(self.quantity or 1))


class Parser(ABC):
    """Turns a decoded file into `ParsedRow`s."""

    #: Short identifier stored on `DeckImport.parser`.
    name: str = ""

    #: Human-facing label for the manual format picker.
    label: str = ""

    #: Lines to skip before the header row. Some tools write a title first.
    preamble_skip: int = 0

    @classmethod
    @abstractmethod
    def sniff(cls, header: str, preamble: str = "") -> float:
        """How confident this parser is that it owns the file, from 0 to 1.

        1.0 means a signature only this format has. Anything below
        `MIN_CONFIDENCE` in the registry is treated as "I do not know".
        """

    @abstractmethod
    def parse(self, text: str) -> Iterator[ParsedRow]:
        """Yield one row per card line. Malformed lines are skipped, not raised.

        A single unreadable line must not cost the user the other 99.
        """
