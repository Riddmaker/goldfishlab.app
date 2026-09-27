# Phase 1 — Card database & deck import

**Duration:** ~1.5 weeks
**Ships:** a genuinely useful deck viewer — with no simulation at all.
**Out of scope:** simulation, CSV formats other than Archidekt, collection tracking.

---

## 1. Scryfall ingestion

Bulk only. Per-card API is for import gap-fill, never for loading the catalogue.

| Bulk type | Size | Load in this phase? |
|---|---|---|
| `oracle_cards` | 24 MB, ~30k rows | **Yes** — everything the simulator needs |
| `oracle_tags` | 6 MB, 4,543 tags | **Yes** — the role database |
| `default_cards` | 78.8 MB, **112,581 printings** | No — loaded at the end of phase 6 |
| `all_cards` | 393 MB, every language | Never |

> **Corrected 2026-09-20.** This table said `default_cards` held "~430k printings" and that
> figure travelled into three later phase documents, where it became the reason to defer the
> collection's printing catalogue twice. It is the size of `all_cards`. The real file is 3.2x
> the card table and 43 MB on disk. See trap 35.

Files ship as **JSONL** (`jsonl_download_uri`). Stream through `gzip.GzipFile` line by line into
`bulk_create(batch_size=1000, update_conflicts=True, unique_fields=["oracle_id"])`. Peak memory
stays around 30 MB regardless of file size — which is the entire reason to use JSONL over the
monolithic JSON.

**Idempotency** via `BulkImport.scryfall_updated_at`: unchanged means skip. Safe to call on boot
and from a nightly Celery Beat task.

### One client, three traps

Everything Scryfall goes through `cards/scryfall.py`. Nothing else may call it. It enforces:

1. **`User-Agent` *and* `Accept` headers are mandatory** — otherwise `/cards/collection`
   returns HTTP 400. Already hit this during the deck research.
2. 50–100 ms between requests.
3. **Double-faced cards match on the front-face name only**, never the full `Name // Other` string.
   Tergrid is the known example.

## 2. The tag rollup

`oracle_tags` is a tree. **Parent tags carry zero direct taggings** — `tutor`, `removal`, `draw`
and `recursion` all have none. The transitive rollup through `child_ids` is mandatory, not an
optimisation. Verified subtree sizes:

| tag | subtree tags | cards |
|---|---|---|
| removal | 55 | 6,740 |
| draw | 36 | 4,536 |
| ramp | 23 | 2,450 |
| recursion | 95 | 2,353 |
| sacrifice-outlet | 23 | 1,549 |
| tutor | 146 | 1,225 |
| reanimate | 26 | 1,118 |
| mana-rock | 5 | 399 |
| ritual | 1 | 69 |

Store `OracleCardTag(oracle_card, tag, is_direct)` so a rolled-up tag can be distinguished from
a directly assigned one in the provenance UI.

## 3. DerivedProfile

Three sources, each recorded per field in `source_map` so phase 4 can show provenance badges:

1. **Structured Scryfall (high confidence):** `mana_cost` → pips/generic, `type_line` → kind and
   land subtypes, `cmc`, `power`/`toughness`, `color_identity`, `is_game_changer`, commander legality.
2. **Tag rollup (medium):** the table above → `role_tags`.
3. **Limited regex (low, always flagged):** `enters tapped`, `{T}: Add …`, `spells you cast cost
   {N} less`, `draw N cards`, `you lose N life`.

**Known limit, and the likeliest source of quietly wrong numbers:** `produced_mana` gives colour
only, never quantity or activation cost. `Sol Ring → ['C']`, not 2. `Cabal Coffers → ['B']` with
no hint of its `{2}` cost or per-Swamp scaling. **Any mana source whose output the regexes cannot
resolve sets `needs_review=True`** and surfaces in the honesty panel.

Refinement the deck research already identified: distinguish "**you** lose 1 life" (a cost, e.g.
Phyrexian Arena) from "**each opponent** loses 1 life" (a drain payoff). A pronoun check, not AI.

## 4. Deck import — Archidekt and plain text only

`decks/importers/` with a `Parser` ABC: `sniff(header, preamble) -> float`, `parse() -> Iterator[ParsedRow]`,
`preamble_skip: int`.

Resolution ladder, highest fidelity first:
1. `scryfall_id` → printing-exact
2. `(set_code, collector_number)`
3. `oracle_id`
4. exact name
5. normalised name (casefold, strip accents, front face before `//`)
6. unresolved → the import-review screen, never silently dropped

The reference fixture `archidekt-collection-export-2026-09-15.csv` has a populated **`Scryfall ID`
for all 214 rows**, so every row lands on rung 1. That makes Archidekt the reference format.

Other formats (Moxfield, Deckbox, ManaBox, Delver Lens, Dragon Shield, Cardsphere, Helvault) have
**unverified headers** and wait for phase 6 — one at a time, each with a committed fixture CSV.
The registry returns `None` (→ manual format picker) rather than guessing wrong.

> **Superseded 2026-09-20.** Phase 6 §2 did not do this. "One parser per format, each with a
> fixture" was abandoned once it became clear that Archidekt has no fixed header row either — the
> parser written against a verified real export was still silently wrong. There is now one
> generic `TabularParser` and a column-mapping screen, and no format waits on a file. See
> [importers.md](importers.md).

## 5. Deck page

- Mana curve, colour identity, land count against the **Karsten 35–38 benchmark**
- Game-changer count → a bracket hint (max 3 for Bracket 3)
- Commander legality validation: exactly 100 cards, singleton except basics,
  colour identity ⊆ commander's, `legalities.commander == "legal"`

## 6. Quotas go live

Wire `billing/quotas.py::check()` into its first two call sites: **deck create** and **import**.
Everyone is on the free plan. No Stripe.

---

## Verification

- **The regression test with a known-good answer:** the tagger must reproduce the 69 hand
  annotations in `simulation/cards.py`. Sac outlets, recursive creatures and enters-tapped
  already matched exactly during research — that is the bar.
- Importing the real Archidekt CSV rebuilds the exact 99-card Chainer deck, 0 unresolved rows.
- Ingestion is idempotent: running it twice changes no rows and creates no duplicates.
- Memory during ingestion stays under ~50 MB (measure it — this is the cloudlet constraint).
- Playwright screenshots of the deck page and the import-review screen, at desktop and phone width.

## Definition of done

Import your own Archidekt export, see the Chainer deck rendered with an accurate curve, correct
colour identity, and role tags that match what was hand-annotated during the deck research.

---

# WHAT ACTUALLY HAPPENED (2026-09-18)

Phase 1 is complete. This section corrects the plan above against the live data, because a
plan that is quietly wrong is worse than one that is openly revised. **Where the two disagree,
this section is right.**

## Corrections to the plan

| The plan said | The data says |
|---|---|
| `oracle_cards` is ~30k rows | **38,891 rows**; 35,568 after dropping art series and tokens |
| Parent tags carry zero direct taggings | True for `removal`, `draw`, `recursion`, `tutor`. **False** for `ramp` (559), `mana-rock` (104), `ritual` (69), `sacrifice-outlet` (12), `reanimate` (11). The rollup must be a **union**, not a replacement |
| `sacrifice-outlet` identifies sac outlets | It over-reports by 6 on the reference deck — it includes one-shot edicts. Use **`repeatable-sacrifice-outlet`** |
| All 214 CSV rows resolve on rung 1 | **62** resolve on the printing id. 150 on the exact name, 2 on the normalised name. **0 unresolved**, which is the outcome that mattered |
| Rung 3 is `(set_code, collector_number)` | Not implemented: it needs `default_cards`, which is Phase 6. Those rows land on the name rungs instead |
| Tag subtree sizes are fixed numbers | They drift weekly (removal: 6,740 when researched, 6,713 today). Tests assert **shape and relations**, never exact counts |

## What was built

| Module | Does |
|---|---|
| `cards/scryfall.py` | The only module that talks to Scryfall. `urllib`, no new dependency |
| `cards/ingest.py` | Streaming JSONL ingestion; idempotent on `BulkImport.scryfall_updated_at` |
| `cards/profiles.py` | `DerivedProfile` over three confidence layers, with `source_map` per field |
| `decks/importers/` | `Parser` ABC, Archidekt CSV, plain text, and a registry that returns `None` rather than guess |
| `decks/resolve.py` | The resolution ladder, in bulk — four queries for a whole file |
| `decks/analysis.py` | Curve, colour identity, Karsten land band, bracket hint, legality |
| `scripts/build_card_fixtures.py` | Regenerates the committed 94-card / 313-tag test fixtures |
| `manage.py seed_demo_deck` | A demo account with a deck imported, for the screenshot pass |

## Traps paid for, so nobody pays twice

1. **`DEBUG=True` made ingestion peak at 95 MB.** Django keeps every SQL statement it runs and a
   1,000-row bulk insert is a megabyte of SQL. `reset_queries()` after each flush → **16 MB**.
   Nothing about the streaming was wrong; the measurement was measuring Django's query log.
2. **Double-faced cards keep cost and text on `card_faces`.** Reading only the top level made
   Tergrid a free 5-drop with no rules text, and nothing errored.
3. **`Gleemax` has mana value 1,000,000** and costs `{1000000}`. It overflows a smallint.
4. **`"abc" in {UUID("abc")}` is False.** The first tag run wrote 0 rows and reported success.
5. **Scryfall's `updated_at` has no `size` or `download_uri` any more** — only
   `compressed_size` and `jsonl_download_uri`. JSONL is the only format served.
6. **Adding a template is a CSS change.** Tailwind emits only the classes it finds by scanning
   source files, so the deck pages went live without their own utilities — `gap-x-4` was simply
   absent and the deck list rendered with its labels jammed together. Every test passed and every
   page answered HTTP 200; only the screenshot pass caught it, which is precisely what it is for.
   `tests/test_css_build.py` now checks every class used in a template against the stylesheet.

## Verification, as promised in the plan

- **The 69 hand annotations** (`tests/test_cards_reference_deck.py`): mana values, black pips,
  generic costs, basic swamps and enters-tapped agree **exactly**; recursive creatures 4/4 and
  rituals 2/2 exact; sac outlets 5/5 recall with one named extra.
- **Two card kinds diverge and are pinned as judgement calls**, not patched: `Ashnod's Altar`
  (tagged `mana-rock`, a player calls it a sac engine) and `Jet Medallion` (adds no mana, a
  player calls it ramp). Demonic Tutor is a Game Changer on Wizards' published list — there the
  derivation is right and the 2026 hand annotation is stale.
- **The real 214-row export imports with 0 unresolved rows.**
- **Ingestion is idempotent**: an unchanged `updated_at` downloads nothing at all.
- **Memory stays under 50 MB**, asserted by a test rather than remembered.
- **Playwright screenshots** of the deck list, importer, deck page and import review, at 1440px
  and 390px. All 14 pages HTTP 200 with no console errors.

## Deliberately not done

- `(set_code, collector_number)` resolution and all printing-level data — Phase 6.
- Every CSV format except Archidekt and plain text — Phase 6. **Done differently:** one generic
  tabular importer instead of one parser per tool, and no committed fixture per format. See
  [importers.md](importers.md).
- Fuzzy name matching. Suggestions use a prefix search; trigram matching over 35,000 names is a
  Phase 6 problem with a database index, not a Python loop.
- Any interpretation of rules text beyond the five documented regexes. Judgement belongs to the
  Phase 4 role editor, where a human makes the call and the app records that a human made it.
