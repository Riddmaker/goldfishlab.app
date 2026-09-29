# The remaining CSV importers — a design, and why it was abandoned

**Status:** built and shipped, 2026-09-20. **Phase 6 §2 is complete.** No format is waiting on a
file, because no format needs one any more.

This document has been rewritten twice in one day. Both rewrites were caused by the same
question from the user, asked in two halves, and the record of both is kept because the second
answer is only convincing next to the first.

---

## Rewrite one: the design that survived four hours

> Archidekt lets you customise the fields to export. I don't know about the others.

**Two of the seven formats had no fixed header row at all.** Archidekt lets you tick which
columns to include; Delver Lens makes you choose the fields *and their order* — the MTGstand
import guide instructs users to select five specific options "in the following order", which is
only necessary because the order is not fixed.

So "one parser per site, keyed on that site's header" could not work. The same site produces
different files for different users, and every phase document up to then had assumed otherwise.

### The bug that was already live

Measured on the shipped Phase 1 parser:

```
header:  Name,Edition Code,Scryfall ID,Collector Number     (Quantity unticked)
sniff:   0.75 confidence — above the 0.5 threshold, so it parsed
result:  every row's quantity = 1
```

**A 28-Swamp collection imported as one Swamp.** No error, no warning, a deck page that looked
finished. In the one format we thought was safe — because the reasoning was "we verified this
header", and a verified header is worth nothing when the header is a user preference.

The fix was `decks/importers/columns.py`: a `Column` is **a thing a deck list can tell us**, and
it carries every header string the known tools use for it. Each remaining format then became "a
`sniff()` and a fixture" instead of a fourth way of reading a quantity.

## Rewrite two: the fixtures were not the small part

> can't you export that yourself? maybe it would make sense to just accept one export format, or
> maybe even build an intelligent import […] because having a highly tuned hard coded one for
> each export seems very unstable and maintenance heavy.

No, and the "no" is the useful half. Exporting from Moxfield, Deckbox, ManaBox, Delver Lens,
Dragon Shield, Cardsphere and Helvault needs a logged-in account on each with cards in it.

But the maintenance objection was the right one, and **our own repository was the evidence**:

> The Archidekt parser was written against a real export of the user's own collection. Every
> column name in it was read off a file, not guessed. It was still silently wrong.

A fixture proves what one file looked like on one day for one user's export settings. Seven
fixtures would have been seven parsers chasing seven sites that change whenever they like — and
each one would have carried the same class of bug the verified one did.

So the fixtures were never the small part. **The per-site parser was the wrong unit**, and one
more of them would have been one more thing to be wrong about.

---

## What was built instead

`decks/importers/tabular.py`. One `TabularParser` that reads **any** delimited export by what its
columns mean, and a mapping screen for the files where that is not enough.

### The reading

* **Concepts, not columns.** A file's header row is matched against the alias vocabulary with
  case, spacing, punctuation and quoting normalised away. Unrecognised headers are ignored,
  because a real export carries purchase prices and tags and a date added.
* **The delimiter is scored, not counted.** Every candidate (`,` `;` `\t`) is tried and judged on
  how many concepts its split yields. `;` is not exotic — it is what Excel writes on a machine
  with a European locale.
* **The header row is located, not assumed.** Dragon Shield writes junk lines above its header,
  so the best-scoring line within the first ten wins. `preamble_skip` stopped being a per-format
  constant that somebody had to know, which means it also works for tools nobody has told us
  about.
* **A table is not a text list.** A candidate needs two or more fields, no later row wider, and
  at least one row exactly as wide. `1 Chainer, Dementia Master` splits on a comma and
  `1 Sol Ring` does not, so the pair is not a table. Short rows *are* allowed — a tool that omits
  trailing empty cells writes `4,Sol Ring` under a three-column header, and demanding equality
  refused real exports outright.

### The asking

**Where it cannot tell, it asks.** This is what makes the design safer than seven parsers rather
than looser — a parser decides silently and the person finds out three screens later; the mapping
screen shows them `Quantity ← "Count"` and a preview of their own first three rows before a
single row is written.

Deliberately narrow: it appears only when **card name** or **quantity** is unmatched. Being
interrupted over an unmatched `language` column would teach people to click through the screen
without reading it, which is worse than not having it.

And it **refuses to be clicked through.** Whichever of the two we could not match starts blank
and required, so the lazy path is a validation error rather than a silently wrong deck.
Defaulting the missing quantity to "not in this file" would have re-created the original bug
behind one extra button.

> **The honesty rule, stated precisely.** The application may not invent a quantity. *A person is
> allowed to tell it there is not one.* `Mapping.confirmed` is the whole of that difference.

### The recording

Most imports never see the mapping screen, because most files answer the question themselves. So
the reading is recorded on `DeckImport.column_mapping` (and was on `Collection.column_mapping`
until the collection went in Phase 9 B) and shown
on the screen afterwards. Otherwise the quiet path would be the unaccountable one.

---

## What is deliberately *not* here

**No AI.** It was on the table — the user suggested it for hard cases, and Mistral is the decided
provider. It was not built, and the reason is that the mapping screen made it redundant before it
made it cheap: a person with a dropdown is free, instant, offline, always available, and does not
put anybody's file in front of a third party. An LLM that proposes a mapping the user has to
confirm anyway saves them one dropdown.

If it is ever added, the shape is settled: **header row only, never card rows**; only when a
required concept is unmatched; and it returns *suggestions that populate dropdowns* and can never
auto-import.

**No spreadsheets.** CSV and TSV only, at the user's request and for a reason worth writing down:
`.xlsx` is a zip archive and `.xls` is OLE with macro surface, so accepting either means running a
large parser over a stranger's upload. Text is a shape that can be validated cheaply. Every tool
that exports a spreadsheet also exports a CSV.

**No format-specific parsers beyond one.** `ArchidektParser` survives as `TabularParser` plus a
signature, because recognising the tool is still worth two things: the import is recorded as
`archidekt` rather than as a generic CSV, and a recognised file skips the mapping screen. It owns
no reading logic at all.

---

## Bounds, and what they are for

`MAX_UPLOAD_BYTES` (1 MB) already caps the file. These cap what the *parse* can do with it.

| Bound | Value | Why |
|---|---|---|
| `MAX_ROWS` | 25,000 | A megabyte of `1,a` is a quarter of a million rows and a quarter of a million dataclasses — most of a 128 MiB cloudlet. Real exports: the reference collection is 214 rows |
| `MAX_COLUMNS` | 128 | A deck list with more columns than this is not a deck list |
| `ParsedRow.MAX_NAME` | 256 | Longest card name is well under half that, and `UnresolvedRow.raw_name` truncates there anyway |
| `csv.Error` → `MalformedTable` | — | An unterminated quote or an over-limit field is **legal input a stranger can send**. Answering with a sentence instead of a 500 |

Two notes on what these are *not*. The `csv` module's 128 KB field limit is left at its default:
lowering it is a process-global mutation in a threaded server, and the upload cap already bounds
the damage — what was missing was catching the error, not moving the line. And **CSV injection is
an export concern, not an import one**: if this application ever grows a "download my collection"
button, a cell beginning `=`, `+`, `-` or `@` executes when opened in Excel, and that is the day
to sanitise. Nothing here writes CSV.

---

## The alias table, and how far to trust it

| Format | quantity | card name | set code | collector number | finish |
|---|---|---|---|---|---|
| **Archidekt** ✅ | `Quantity` | `Name` | `Edition Code` | `Collector Number` | `Finish` |
| Moxfield | `Count` | `Name` | `Edition` | `Collector Number` | `Foil` |
| Deckbox | `Count` | `Name` | `Edition Code` | `Card Number` | `Foil` |
| ManaBox | `Quantity` | `Name` | `Set code` | `Collector number` | `Foil` |
| Dragon Shield | `Quantity` | `Card Name` | `Set Code` | `Card Number` | `Printing` |
| Topdecked | `QUANTITY` | `NAME` | `SETCODE` | `COLLECTOR NUMBER` | `FINISH` |
| MTGGoldfish | `Quantity` | `Card` | `Set ID` | `Collector Number` | `Foil` |
| TCGplayer | `Quantity` | `Simple Name` | `Set Code` | `Card Number` | `Printing` |
| MTGO | `Quantity` | `Card Name` | `Set` | `Collector #` | `Premium` |
| Delver Lens | *user-configured* | *user-configured* | — | — | — |
| Cardsphere, Helvault | unknown | unknown | unknown | unknown | unknown |

✅ = read off a real export. Everything else comes from
[`StepKie/MtgCsvHelper`](https://github.com/StepKie/MtgCsvHelper)'s `appsettings.json`.

**The unknowns stopped mattering.** An alias that is wrong or missing means a column is reported
missing, which is loud and which the mapping screen fixes in one dropdown. That is the whole
difference between a vocabulary and a parser.

### One alias that was wrong, and what it would have cost

`Tradelist Count` was a `QUANTITY` alias, taken from that same mapping without anybody asking what
the words meant. It means **"how many of these I am willing to trade away"**, which is routinely
zero for a card somebody owns four of. Since `find()` returns the first matching header in file
order, a file listing it before `Count` — which Deckbox and Moxfield both export — would have read
a whole collection as untradeable, which is to say as empty. Nothing would have errored.

Pinned by `test_tradelist_count_is_never_read_as_a_quantity`.

## Sources

- [MtgCsvHelper — format mappings in `appsettings.json`](https://github.com/StepKie/MtgCsvHelper)
- [MTGstand collection import guide — Delver Lens field selection and ordering](https://www.mtgstand.com/collection-import-guide)
- [Moxfield CSV columns, via Codidact](https://software.codidact.com/posts/294785)
- [Scrytics — importing and exporting MTG collections](https://scrytics.com/blog/mtg-collection-import-export)
