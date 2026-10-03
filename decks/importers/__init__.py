"""Deck-list parsers and the registry that picks one.

The registry's contract is the important part: when no parser is confident, it
returns `None` and the user is asked. It never falls back to "probably text".

**What it now asks for has changed, and that is the point.** Until 2026-09-20
an unrecognised file meant "this format is not supported yet, come back after
the next phase". It now means "pick CSV and confirm which column is which",
which is a thirty-second answer instead of a feature request. `TabularParser`
reads any delimited export by what its columns mean, so the registry's `None`
stopped being a dead end.

Adding a parser is therefore rarely the right move. Do it only when a format
needs something structurally different from "a table with headers" - a
different header *spelling* belongs in `columns.py`, where a wrong guess
matches nothing and says so, instead of in a parser, where a wrong guess
imports the wrong cards in silence.
"""

from django.utils.translation import gettext

from decks.importers import columns
from decks.importers.archidekt import ArchidektParser
from decks.importers.base import (
    ImportRefused,
    MalformedTable,
    MissingColumn,
    NotTabular,
    ParsedRow,
    Parser,
    TooManyRows,
)
from decks.importers.tabular import TabularParser
from decks.importers.text import PlainTextParser

#: Ordered by specificity. The registry takes the highest score, not the first.
#: `ArchidektParser` sits above `TabularParser` because it can reach 1.0 and
#: `TabularParser` is capped at 0.85, so a file we can name keeps its name.
PARSERS: list[type[Parser]] = [ArchidektParser, TabularParser, PlainTextParser]

#: Below this, "I do not know" is the honest answer.
MIN_CONFIDENCE = 0.5

#: Re-exported so callers import the whole parser contract from one place and
#: never reach into a submodule. `columns` is part of that contract now, not an
#: implementation detail of one parser.
#:
#: There used to be two `__all__` assignments in this file and the second one
#: silently won, dropping `columns` and `MissingColumn` from the public surface
#: of the package that defines both. Nothing broke, because nothing in this
#: codebase uses a star import - which is exactly why it went unnoticed.
__all__ = [
    "MIN_CONFIDENCE",
    "PARSERS",
    "ArchidektParser",
    "ImportRefused",
    "MalformedTable",
    "MissingColumn",
    "NotTabular",
    "ParsedRow",
    "Parser",
    "PlainTextParser",
    "TabularParser",
    "TooManyRows",
    "by_name",
    "choices",
    "columns",
    "sniff",
]


def sniff(text: str) -> type[Parser] | None:
    """Pick a parser for this file, or `None` to ask the user.

    `None` is a feature. A wrong parser does not raise - it imports the wrong
    cards, and the user finds out three screens later when the mana curve looks
    strange.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines:
        return None

    header = lines[0]
    preamble = "\n".join(lines[:5])

    scored = sorted(
        ((parser.sniff(header, preamble), parser) for parser in PARSERS),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best_parser = scored[0]
    return best_parser if best_score >= MIN_CONFIDENCE else None


def by_name(name: str) -> type[Parser] | None:
    """Look up a parser the user picked by hand."""
    for parser in PARSERS:
        if parser.name == name:
            return parser
    return None


def choices() -> list[tuple[str, str]]:
    """Options for the manual format picker."""
    return [(parser.name, gettext(parser.label)) for parser in PARSERS]
