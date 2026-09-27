# Phase 4 — Role-tag editor & the honesty layer

**Duration:** ~1 week
**Status:** **Complete** (2026-09-18). The account of what actually
happened is at the bottom.

**Why this gets its own phase rather than being an afterthought:** it is what makes the product
*trustworthy* rather than merely impressive. Competitors output an unfalsifiable "power level: 7.3".
This outputs a number *and* states exactly how much of your deck it could actually model.

---

## Scope

**1. `CardAnnotation` editor**
- Per-deck role tags, priority sliders, "not simulated" flags
- User-global defaults (applies to every deck) vs deck-scoped override
- Precedence, already defined in phase 2: `DerivedProfile` → built-in → user-global → deck-scoped `CardAnnotation`

**2. Provenance badges** — per card, per field, from `DerivedProfile.source_map`:
`Scryfall field` · `Tagger` · `regex` · `you`

**3. The "What this simulation does not model" panel** — generated from data, prominent on every
report:

> *17 of your 99 cards have effects the engine cannot represent. They are treated as vanilla
> permanents: they cost mana and do nothing else.*

**4. The priority editor** — addressing what the design pass identified as the single largest risk:
the agent's priority list *is* the deck's strategy. A generic default ("ramp > draw > curve out")
is right for maybe 60% of Commander decks and actively wrong for combo decks that need to hold a
specific pair. Users must be able to reorder it.

Accompanied by honest framing in the UI:
> *These numbers measure your mana and your curve, not your deck's plan.*

**5. Named warnings for known blind spots:**
- **Opponent-dependent cards are worth exactly zero in a goldfish** — Rhystic Study, Smothering
  Tithe, Esper Sentinel, Mystic Remora. A deck built around them will simulate far worse than it plays.
- **Unresolved mana sources** (`needs_review=True`) — Cabal Coffers, Nykthos, Gaea's Cradle read as
  0–1 mana unless annotated.
- **Tag false positives** — "destroy target creature **you control**" is a sac outlet, not removal.

---

## Verification

- Editing an annotation changes the next run's results in the expected direction
- The coverage score is arithmetically correct against the deck contents
- A deck full of unmodelled cards shows a loud, unmissable warning
- Playwright screenshots: annotation editor, provenance badges, the honesty panel on a deck with
  poor coverage

## Definition of done

Someone who does not trust the numbers can find out, in two clicks, exactly which of their cards
the engine ignored and why.

---

## What actually happened

**Finished 2026-09-18. 468 tests green (up from 395), ruff and djlint clean.**

The phase shipped all five items in the scope above, plus the colour work carried forward
from Phase 2. What follows is where the plan met reality.

### The colour item, folded in

`game.black_available` was the last single-colour thing in the engine: it made and spent five
colours and reported one. It is now derived rather than stored — `game.mana_by_color` holds
the whole pool and `black_available` is a read-only property over it, so the two cannot
disagree. `analysis` counts a histogram per colour (`mana_W` … `mana_C`), and the report grows
a **"What the mana was made of"** table showing only the colours the deck actually made.

Two decisions inside that:

- **`black` and `mana_B` hold the same numbers, deliberately.** The golden parity snapshot is
  keyed on `black`, and dropping it would mean regenerating the one piece of evidence that the
  engine still behaves as it used to. Six extra histograms per turn cost a few dozen integers.
- **A colour column is included on `total`, not on truthiness.** Every game contributes a value
  to every colour's histogram, so a colour the deck never makes has a histogram full of zeroes
  — which is a perfectly non-empty histogram. Getting this wrong would have shown a mono-black
  deck four empty columns.

The parity test needed no change: it iterates the fields *the golden fixture has*, so new
fields are additive. That was worth checking before writing a line of it.

### Provenance: one function, two readers

`adapter.readings(deck)` returns the engine `Card` the simulation will actually use, and
`provenance.py` labels each field with where it came from. The panel therefore cannot drift
from the simulation — both are `_card_from` on the same inputs. A second reconstruction "for
display" would have been the obvious shape and would have been wrong within a month.

Five sources, plus one that is not a source: where nobody has said anything, the row reads
**"Nobody — the engine's own rule"**. That row is the point of the whole screen. "Cast
priority 38" looks like a decision until it says the engine made it up out of the mana value.

### The editor, and the two rules it obeys

Eight of `ALLOWED_KEYS`' thirty-five keys are editable. Two rules shape everything:

1. **Blank means "no opinion", never zero.** Every boolean is a three-state select rather than
   a checkbox, because a checkbox cannot say the third thing and would quietly save "no" for
   every card it was never asked about.
2. **The form is never pre-filled from the derived reading.** It is filled from the annotation
   at that scope and from nothing else, with the derived reading shown *beside* it. Otherwise
   saving the page once would freeze a community tag into a human judgement, and the provenance
   panel would then credit somebody with an opinion they never had.

### Three defects the tests found, and one the plan did not anticipate

- **`apply` versus `patch`.** `apply` has whole-form semantics: every editable key it is not
  given is removed. `set_priorities` called it with `{"priority": n}` — which would have
  deleted every role and mana judgement recorded on the card's own page the first time anybody
  saved the casting order. Split into two functions, both pinned.
- **The priority boxes were pre-filled from built-ins.** A built-in is a judgement the
  application ships, not the user's; pre-filling it turns "the application thinks 55" into "you
  said 55" on the next save. The boxes now read the deck's own rows only, and the effective
  number has a column of its own.
- **`mana_produces: {}` does not silence a Swamp.** The engine's rule is that a land with a
  coloured land type taps *as a basic land* and its own mana ability goes unused. So "Taps for:
  nothing" was a lie on exactly the cards somebody would most want to correct. Fixed in two
  places: `Reading.taps_for` now reports the land-type path, and **"Land types" became an
  editable field**, which is the only honest way to let somebody actually turn it off. Caught
  by the end-to-end test in the verification list, which is what that test is for.
- **Built-in annotations are a production state, not a fixture accident.** Six tests passed
  alone and failed in the suite, because `tests/test_engine_adapter.py` seeds the reference
  deck module-scoped and outside a transaction. The tests were wrong, not the suite: assertions
  now scope to the user's own rows, and the handful that genuinely mean "nobody has said" clear
  the built-ins explicitly.

### What the screenshot pass found that 468 green tests did not

Three things, which is why this pass is not optional:

1. **Every text and number input was invisible.** No border and a white background on a
   parchment surface: the annotation editor showed a label, a sentence of help text, and then
   nothing at all where the box should be. `STYLEGUIDE.html` had specified the control styling
   since Phase 0b; the CSS had only ever implemented it under `.auth-form`.
2. **The tune page was 106,000 pixels tall on a phone** — a full eight-row provenance table for
   each of 183 cards. Correct, tested, unusable. It is one row per card now, with the detail one
   click away on the card's own page, where it can also be acted on.
3. **The colour table was missing from the report.** `docker compose restart web` does not
   restart the workers, and the workers are what run the engine. The run had been computed by a
   worker still holding the old `analysis.py` in memory. See trap 19.

### The known gap, since closed

The verification list at the top of this document asks that **a deck full of unmodelled cards
shows a loud, unmissable warning**. When this phase shipped, what was actually tested was
`blindspots.find([]) == []` and the reference deck - the empty case, and a deck that does not
have the problem. There was no low-coverage deck to point the panel at.

**Closed on 2026-09-18 by [test-decks.md](test-decks.md).** The `unmodellable` shape is twelve
cards the deriver refuses to guess at, none of them annotated: 0% coverage, two named
limitations covering eleven of the twelve. It is asserted against the *rendered page* rather
than against `blindspots.find`, because "unmissable" is a claim about what a person sees.

### Deliberately not done

- **The blind-spot detectors report; they never decide.** A detector that quietly set
  `goldfish_castable=False` on everything mentioning an opponent would be making the user's
  judgement for them and hiding it inside a number — the exact failure this phase exists to
  prevent. What they produce is a named card, a reason, and a link to the field that answers it.
- **A stored run is never re-read when an annotation changes.** The result is a record of what
  the engine actually saw. The report says the inputs have moved on and offers a re-run.
