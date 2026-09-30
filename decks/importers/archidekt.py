"""Archidekt CSV exports.

**This is now a name and a signature, and nothing else.** Every line of reading
logic it used to own moved to `TabularParser`, which reads any delimited export
through the same concept vocabulary. What survives here is the one thing that
was genuinely Archidekt-specific: knowing an Archidekt file when we see one.

That distinction is the whole design. *Aliases are for reading a file we were
told to read; sniffing decides whether we were told.* Recognising the format
buys two things and no others - the import is recorded as `archidekt` on
`DeckImport.parser` rather than as a generic CSV, and a file we recognise skips
the mapping screen because there is nothing to ask about.

The reference file is `archidekt-collection-export-2026-09-15.csv`, a real
export of the user's own collection:

    Quantity, Name, Finish, Condition, Date Added, Language, Purchase Price,
    Tags, Edition Name, Edition Code, Multiverse Id, Scryfall ID,
    Collector Number, Mana Value, Price (Card Kingdom), ...

Two facts about that file worth keeping in mind:

* `Scryfall ID` is a **printing** id, not an oracle id. It matches only the
  one printing `oracle_cards` ships for each card - 62 of 214 rows; the rest
  resolve by name.
* A collection export repeats a card once per printing owned. Quantities are
  summed by the resolver, not here; a parser that deduplicated would be making
  a decision that belongs downstream.

And the fact that ended the one-parser-per-site design: **Archidekt lets you
choose which columns to export**, so the header above is one file's header and
not the format's. Before that was understood, an export with Quantity unticked
sniffed at 0.75, parsed without complaint, and turned 28 Swamps into 1.
"""

from decks.importers.tabular import TabularParser

#: Columns that, taken together, no other export we have seen produces. Note
#: that none of them is required to *read* the file - `TabularParser` would
#: manage with any subset. They are required to be sure it came from Archidekt.
SIGNATURE = {"quantity", "name", "scryfall id", "edition code"}
REQUIRED = {"name"}


class ArchidektParser(TabularParser):
    name = "archidekt"
    label = "Archidekt CSV (deck or collection export)"

    @classmethod
    def sniff(cls, header: str, preamble: str = "") -> float:
        present = {cell.strip().strip('"').lower() for cell in header.split(",")}
        if not REQUIRED <= present:
            return 0.0

        overlap = len(SIGNATURE & present) / len(SIGNATURE)
        # "Date Added" is what distinguishes a collection export from the
        # shorter deck export, but both are ours, so it only adds confidence.
        if "date added" in present:
            overlap = min(1.0, overlap + 0.1)
        return overlap
