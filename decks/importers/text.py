"""Plain-text deck lists - the format every site can paste.

    1 Sol Ring
    1x Dark Ritual
    31 Swamp
    // Commander
    1 Chainer, Dementia Master

Supported because it is what a user reaches for when their tool is not in the
list yet, and because it is the only format that can be typed by hand.

What it deliberately does not do: parse set codes in brackets, MTGO sideboard
markers, or the dozen dialects of section headers. A line it cannot read
becomes an unresolved row the user can see and fix, which is a better outcome
than a clever parser that is confidently wrong about `(FDN) 123`.
"""

import re
from collections.abc import Iterator

from decks.importers.base import ParsedRow, Parser

# "1 Sol Ring", "1x Sol Ring", "4 x Sol Ring", or a bare "Sol Ring".
#
# At most six digits. An unbounded `\d+` handed `int()` whatever the file held,
# and Python refuses to convert a string of more than 4,300 digits - a
# `ValueError` nothing caught, which was an HTTP 500 a signed-in user could cause
# with one line. A longer number is simply not read as a quantity, the line
# fails to resolve, and the review screen shows it.
_LINE = re.compile(r"^\s*(?:(\d{1,6})\s*[xX]?\s+)?(.+?)\s*$")

# Section markers and comments. Anything after these is still cards, so they
# are skipped rather than treated as the end of the file.
_COMMENT = re.compile(r"^\s*(?://|#|\*)")
_SECTION = re.compile(r"^\s*(commander|deck|main(?:board)?|sideboard|companion)\s*:?\s*$", re.I)

# A trailing "(SET) 123" or "[SET]" that we choose not to interpret. Stripped
# from the name so the lookup can succeed; the printing is Phase 6's business.
_PRINTING_SUFFIX = re.compile(r"\s*[(\[][A-Za-z0-9]{2,6}[)\]](?:\s+\S+)?\s*$")


class PlainTextParser(Parser):
    name = "text"
    label = "Plain text list (one card per line)"

    #: "1 Sol Ring" or "1x Sol Ring" - a quantity followed by a name is the
    #: one shape no other supported format produces on its first line.
    _QUANTIFIED = re.compile(r"^\s*\d{1,6}\s*[xX]?\s+\S")

    @classmethod
    def sniff(cls, header: str, preamble: str = "") -> float:
        """Confident about text lists, and silent about anything delimited.

        A comma or a tab in the first line means it is probably a CSV or TSV
        whose format we have not verified. Those must reach the format picker,
        never this parser: a delimited file read line-by-line yields card names
        with half a row of prices glued to them, and every one of them then
        fails to resolve for a reason the user cannot act on.
        """
        if "," in header or "\t" in header:
            return 0.0
        if cls._QUANTIFIED.match(header):
            return 0.9
        # A bare name is a plausible text list and nothing else we support, but
        # it is also what the first line of a malformed file looks like.
        return 0.6 if _LINE.match(header) else 0.0

    def parse(self, text: str) -> Iterator[ParsedRow]:
        in_commander_section = False

        for number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue

            if _SECTION.match(line):
                in_commander_section = line.lower().startswith("commander")
                continue
            if _COMMENT.match(line):
                # A "// Commander" marker is a comment *and* a section header.
                in_commander_section = "commander" in line.lower()
                continue

            match = _LINE.match(line)
            if not match:
                continue

            quantity, name = match.groups()
            name = _PRINTING_SUFFIX.sub("", name).strip()
            if not name:
                continue

            yield ParsedRow(
                line_number=number,
                name=name,
                quantity=int(quantity) if quantity else 1,
                is_commander=in_commander_section,
            )
