"""Plain-text deck lists - the format every site can paste.

    1 Sol Ring
    1x Dark Ritual
    31 Swamp
    // Commander
    1 Chainer, Dementia Master

Supported because it is what a user reaches for when their tool is not in the
list yet, and because it is the only format that can be typed by hand.

**What changed in P20 (F1), and why.** This parser used to strip one bracket
off the end of a line and know five English section words. Lauf 1 pasted the
real exports of Moxfield, Archidekt, ManaBox, TappedOut and Arena into it, and
each one failed differently: `*F*` stayed on the name, `[Commander{top}]` was
read as a set, `[CRÉATURES]` became a card, a sideboard went into the deck,
and `1 Krark, the Thumbless (CMR) 189` was refused outright because its first
line has a comma. The product rule since then is that an import works with
foil and printing information **in whatever form it comes**.

So a line is now read by a small grammar, from the end:

    1x Prosper, Tome-Bound (afc) 2 *F* [Commander{top}] ^Have,#37d67a^ #combo
    ^  ^                   ^    ^  ^   ^                ^              ^
    |  name                set  |  finish  category     label          tag
    quantity                    collector number

Each known tail piece is taken off while one fits; whatever is left is the
name. The set, number and finish are kept on the row (`ParsedRow`) rather than
thrown away, and a commander or maybeboard marker in the tail decides the
row's board. Section headings decide it for the lines below them
(`boards.classify`).

Two things keep the grammar from eating a real name, both checked against the
whole catalogue when this was written: no card name contains `#`, `*`, `[`,
`^`, `<` or `{`; and the ten names that end in `)` - `Hazmat Suit (Used)`,
`Unearth (Theme)` - have a bracket written in title case, which a set code
never is.
"""

import re
from collections.abc import Iterator

from django.utils.translation import gettext_noop

from decks.importers import boards, columns
from decks.importers.base import ParsedRow, Parser

# "1 Sol Ring", "1x Sol Ring", "4 x Sol Ring", or a bare "Sol Ring".
#
# At most six digits. An unbounded `\d+` handed `int()` whatever the file held,
# and Python refuses to convert a string of more than 4,300 digits - a
# `ValueError` nothing caught, which was an HTTP 500 a signed-in user could cause
# with one line. A longer number is simply not read as a quantity, the line
# fails to resolve, and the review screen shows it.
_LINE = re.compile(r"^\s*(?:(\d{1,6})\s*[xX]?\s+)?(.+?)\s*$")

#: A line that starts with a quantity - the shape of a card line.
_QUANTIFIED = re.compile(r"^\s*\d{1,6}\s*[xX]?\s+\S")

#: `1,Sol Ring` or `1;Sol Ring`: a delimited row, never a text line.
_DELIMITED_ROW = re.compile(r"^\s*\d{1,6}\s*[,;]")

#: A comment line. `// Commander` is a comment *and* a heading.
_COMMENT = re.compile(r"^\s*(?://|#|\*(?![A-Za-z]{1,10}\*\s*$))")
_COMMENT_MARK = re.compile(r"^\s*(?://+|#+|\*+)\s*")

#: A whole line in square brackets: ManaBox's `[COMMANDER]`, `[CRÉATURES]`.
_BRACKETED = re.compile(r"^\s*\[([^\[\]]+)\]\s*$")

#: A line without a quantity that ends in a colon: `Comandante:`, `Deck:`.
_COLON_HEADING = re.compile(r"^\s*([^\d\s][^:：]{0,40})[:：]\s*$")

#: Arena's file header: `About`, then `Name <deck name>`.
_ABOUT = "about"
_NAME = re.compile(r"^\s*name\s+(.+?)\s*$", re.I)

# --- the tail of a card line, taken off from the end ------------------------

_FINISH_WORDS = {"f": "foil", "foil": "foil", "e": "etched", "etched": "etched"}

#: `(SET) NUM`. The set is 2-6 letters or digits in one case (`CMR`, `cmr`,
#: `30A`) or Scryfall's lower-case prefix form (`pAFR`); the number is anything
#: with a digit in it (`123a`, `★12`, `C18-150`, `2024-15`).
_PRINTING = re.compile(
    r"\s*\(([A-Za-z0-9]{2,6})\)(?:\s+([0-9A-Za-z★†\-]{0,11}[0-9][0-9A-Za-z★†\-]{0,11}))?\s*$"
)
_STAR = re.compile(r"\s*\*([A-Za-z]{1,10})\*\s*$")
_BRACKET = re.compile(r"\s*\[([^\[\]]*)\]\s*$")
_LABEL = re.compile(r"\s*\^([^\^]*)\^\s*$")
_VARIANT = re.compile(r"\s*<([^<>]{1,20})>\s*$")
_FINISH = re.compile(r"\s*\((foil|etched|f|e)\)\s*$", re.I)
_TAG = re.compile(r"\s+#\s*(!?[^\s#]+)\s*$")


def _set_code(code: str) -> bool:
    """Whether a bracketed word is a set code and not part of a name."""
    return code.isupper() or code.islower() or code.isdigit() or bool(
        re.fullmatch(r"[a-z][A-Z0-9]{2,5}", code))


def _bracket_set(code: str) -> bool:
    """`[CMR]` as an old-style set, as opposed to a category like `[Land]`."""
    return 2 <= len(code) <= 6 and code.isalnum() and (code.isupper() or code.islower())


class _Card:
    """What the tail of one line said, collected while it is taken apart."""

    def __init__(self):
        self.set_code = ""
        self.number = ""
        self.finish = ""
        self.board: str | None = None

    def mark(self, board: str | None) -> None:
        # The first marker read from the end wins, except that a commander
        # marker always does: `*CMDR*` beside a category still means it.
        if board and (self.board is None or board == boards.COMMANDER):
            self.board = board


def _tail(name: str, card: _Card) -> str:
    """Take known pieces off the end of `name` while one fits."""
    while True:
        before = name
        if (match := _FINISH.search(name)) and match.start() > 0:
            card.finish = card.finish or _FINISH_WORDS[match.group(1).lower()]
        elif ((match := _PRINTING.search(name)) and match.start() > 0
              and _set_code(match.group(1)) and not card.set_code):
            card.set_code, card.number = match.group(1).lower(), match.group(2) or ""
        elif match := _STAR.search(name):
            word = match.group(1).lower()
            if word in _FINISH_WORDS:
                card.finish = card.finish or _FINISH_WORDS[word]
            elif word in ("cmdr", "commander"):
                card.mark(boards.COMMANDER)
        elif match := _BRACKET.search(name):
            inner = match.group(1).strip()
            if _bracket_set(inner) and not card.set_code and not boards.classify(inner):
                card.set_code = inner.lower()
            else:
                card.mark(boards.from_tags(inner))
        elif match := _LABEL.search(name):
            pass
        elif match := _VARIANT.search(name):
            word = match.group(1).strip().lower()
            card.finish = card.finish or _FINISH_WORDS.get(word, "")
        elif match := _TAG.search(name):
            card.mark(boards.from_tags(match.group(1).lstrip("!")))
        else:
            return name.strip()
        name = name[: match.start()]
        if name == before or not name.strip():
            return before.strip()


def heading(line: str) -> str | None:
    """The board a heading line opens, `"about"`, or `None` for a card line.

    An unknown heading - `[CRÉATURES]`, `Creatures:` - opens the main deck:
    it is a category, and the cards under it are cards.
    """
    if match := _BRACKETED.match(line):
        return boards.classify(match.group(1)) or boards.MAIN
    if _QUANTIFIED.match(line):
        return None
    word = line.strip()
    if word.casefold() == _ABOUT:
        return _ABOUT
    if board := boards.classify(word):
        return board
    if _COLON_HEADING.match(line):
        return boards.MAIN
    return None


class PlainTextParser(Parser):
    name = "text"
    label = gettext_noop("Plain text list (one card per line)")

    @classmethod
    def sniff(cls, header: str, preamble: str = "") -> float:
        """How much of the start of the file reads as `quantity name ...`.

        A comma is no longer a reason to refuse: `1 Krark, the Thumbless (CMR)
        189` is a text line, and refusing it sent Moxfield's own export to the
        format picker (E1.15). What still refuses is what only a delimited file
        has - a tab, a semicolon, `1,Sol Ring` - and a table the tabular reader
        recognises a card-name column in, which is its file and not ours.

        A list of bare names (`sol ring`) stays at 0.6: it is a plausible text
        list and nothing else we support, but it is also what the first line
        of a malformed file looks like.
        """
        lines = [line for line in (preamble or header).splitlines() if line.strip()]
        if not lines:
            return 0.0
        if any("\t" in line or ";" in line or _DELIMITED_ROW.match(line) for line in lines):
            return 0.0

        from decks.importers.tabular import locate

        table = locate("\n".join(lines))
        if table is not None and columns.map_headers(table.headers).has(columns.NAME):
            return 0.0

        cards = [line for line in lines
                 if not _COMMENT.match(line) and heading(line) is None
                 and not _NAME.match(line)]
        if not cards:
            # Only headings so far (Arena's `About` block): a text list, but
            # not one we have seen a card of yet.
            return 0.7 if any(heading(line) for line in lines) else 0.0
        quantified = sum(1 for line in cards if _QUANTIFIED.match(line))
        if quantified == len(cards):
            return 0.9
        if table is not None:
            # Bare lines that also split into columns: a CSV whose header we
            # do not recognise. The format picker, not this parser.
            return 0.0
        return 0.8 if quantified else 0.6

    def parse(self, text: str) -> Iterator[ParsedRow]:
        board = boards.MAIN
        # A commander block ends at the first blank line after a card in it
        # (ManaBox writes `// COMMANDER`, one card, a blank line, the deck).
        commander_cards = 0

        for number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                if board == _ABOUT or (board == boards.COMMANDER and commander_cards):
                    board = boards.MAIN
                continue

            if _COMMENT.match(line):
                board = boards.classify(_COMMENT_MARK.sub("", line)) or boards.MAIN
                commander_cards = 0
                continue
            if (opened := heading(line)) is not None:
                board, commander_cards = opened, 0
                continue
            if board == _ABOUT:
                if match := _NAME.match(line):
                    self.deck_name = match.group(1)[:120]
                continue

            match = _LINE.match(line)
            if not match:
                continue
            quantity, rest = match.groups()
            card = _Card()
            name = _tail(rest, card)
            if not name:
                continue

            row_board = card.board or board
            if board == boards.COMMANDER:
                commander_cards += 1
            yield ParsedRow(
                line_number=number,
                name=name,
                quantity=int(quantity) if quantity else 1,
                set_code=card.set_code,
                collector_number=card.number,
                finish=card.finish,
                board=row_board,
            )
