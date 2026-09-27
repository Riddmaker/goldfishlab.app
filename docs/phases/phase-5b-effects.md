# Phase 5b — The effect catalogue

**Status:** **Complete**, 2026-09-18. Agreed with the user after the test-deck work, built the
same day. **616 tests green.**

---

## The idea, and the correction to it

The user asked: should we have a catalogue of every card effect — flying, deathtouch, and the
rest — and is there one online we can take instead of building it?

**Yes to the catalogue, and we already took it.** Phase 1 ingested Scryfall's `oracle_tags`
bulk data: a community-maintained tag DAG with parent/child edges, rolled up in
`cards/ingest.py` so that a tag on a card implies its ancestors. It was in the database the
whole time:

| | before | after |
|---|---|---|
| Tags in the DAG | 4,544 | 4,544 |
| Cards | 35,568 | 35,568 |
| Taggings | 429,263 | 429,263 |
| Tags mapped to an engine role | **16** | **17** |
| Tag→field mappings outside the role list | 0 | **10** |

The catalogue was never missing. It was present, downloaded, indexed, and unread.

## Three things this document got wrong before the work started

Written into the outline without checking, found by checking. Recorded because the pattern is
the lesson: **every claim in a plan about what the code does is a claim that can be measured
before it is acted on.**

1. **"`OracleCard` has no `keywords` field."** It has had one since migration `0001`, populated
   on every one of the 35,568 cards, read by nothing. The work was not to store keywords; it was
   to *show* them.
2. **"the 34 fields in `ANNOTATION_FIELDS`."** There is no `ANNOTATION_FIELDS`. It is
   `simulations/models.py::ALLOWED_KEYS`, and 34 was right — while `simulations/annotations.py`
   opened by calling it thirty-five. Both now say 34.
3. **"17 of 4,544 mapped."** Sixteen. Counted by hand, once, and repeated in three files.

A fourth, in the code rather than here: `simulations/annotations.py` asserted that `draw_engine`
"is a pure judgement that no tag supplies". The DAG has `draw-engine` on **1,667 cards**, and
its definition is the metric almost word for word.

## What the phase actually found, before it built anything

Every gap on all nine decks in the development database, counted:

| | |
|---|---|
| Gaps, all decks | **217** |
| …that were `priority` | **161 (74%)** |
| …that were "the engine cannot read this card" | 56 |

**Three quarters of every gap in the application was one field that nothing may ever derive.**
So "78.6% coverage" mostly meant "78.6% hand-annotated", and a deck of perfectly readable cards
that nobody had annotated scored the same as a deck the engine could barely read. The single
number was answering two questions and pointing at neither.

That reshaped the phase. **Phase 5b could not be sold on the coverage score and did not try.**

## What was built

### 1. The coverage score, split in two

`simulations/gaps.py` is new and is the whole idea: a gap is classified by its **field**, into

- **`reading`** — the engine could not read the card. *The application's limit.*
- **`judgement`** — nobody has said. *Only the deck's author can answer, and no card text ever will.*

Classifying by field rather than by row is what makes it free: **gaps already stored on finished
runs and open playtest sessions split correctly, with no migration and nothing rewritten.** A
stored run is a record of what the engine saw; a data migration that back-filled a `kind` column
would have been a migration that edited history. `test_a_run_from_before_the_split_still_splits`
pins it.

What the two numbers do on the 183-card demo deck:

| | before | after |
|---|---|---|
| "Coverage" | 31.1% | 29.5% |
| The engine read it | — | **86.9%** |
| Somebody had decided | — | 35.0% |

The old number said the application could barely read that deck. It reads it almost perfectly;
what is missing is a person's opinion. Those are different problems with different owners, and
the report, the tune page and the playtest board now say which is which.

**The playtest board shows only the reading half.** "Nobody has said how early to cast it" is
advice to a simulation, and there is no agent on that page — the player decides what to cast by
clicking it. The warning went from *"could not read 126 of 183"* to the true *"could not read
24 of 183"*.

### 2. `draw-engine`, the role the report could never show

`simulation/analysis.py` counts a `draw_engine` milestone and `simulations/report.py` prints it
as "A card-advantage engine in play". The only cards carrying that role were in the hand-written
`chainer.py` fixture, so **for every deck a user imported, that row was structurally always
zero** — a report line that could not fire, on every real deck, in every run ever computed.

Measured against the one hand-curated ground truth this project has — the reference deck, whose
author tagged eight cards a draw engine — `draw-engine` agrees on six and claims **nothing they
did not**. Six of six precision, no over-reach.

**`repeatable-card-advantage` would have found all eight** and added Phyrexian Reclamation, which
returns creatures from the graveyard and draws no cards. It was rejected, and the reason is the
phase's governing trade: **recall is cheap here and precision is not.** A missed engine
under-reports a deck. An invented one puts a milestone on a report that never happened.

### 3. Tutors: the zone from the tag, the number from the text

`adapter._tutor()` returned `None` without a `tutor_count` annotation, so **every tutor in every
deck but the reference one did nothing at all.** Demonic Tutor was a sorcery that cost two mana.

The division of labour is the phase in miniature. A community tag is a **category** and can never
be a number: `tutor-to-graveyard` knows which zone Buried Alive searches to and cannot know it
finds three cards. The printed text is the opposite. So:

- the **zone** comes from the tag (`tutor-to-hand` 633 cards, `tutor-to-graveyard` 35,
  `tutor-to-battlefield` 508)
- the **count** comes from a pattern over `Search your library for …`
- **either half missing is a gap**, never a `TutorSpec` with a plausible 1 in it

The result: **442 tutors the engine can now play** and **372 it correctly refuses to**, because
`tutor-to-battlefield` is a move the engine does not have and a tutor quietly redirected to the
graveyard would be a Natural Order that reanimates nothing.

**The evidence it is right:** the reference deck's author hand-annotated three tutors, and the
deriver — never shown their answers — reproduces all three field for field, Buried Alive's three
creatures to the graveyard included.

### 4. Three honesty checks that make the numbers worse

The phase deliberately increased the reported gap count in places. All three came out of the
tutor work, and each is a statement anybody can check against the card:

- **An additional casting cost the engine never pays.** "As an additional cost to cast this
  spell, sacrifice a creature" — 360 cards. Diabolic Intent was about to become a free Demonic
  Tutor. This mattered only once the tutors worked: an unpaid cost on a card that does nothing
  costs nothing.
- **A contradiction, reported rather than resolved.** A card the community tagged
  `adds-multiple-mana` that the regexes read as exactly one mana is a card probably read wrong.
  "More than one" is not a number, so the honest move is to say there is a disagreement, not to
  pick a side.
- **Gamble's tax.** It opens with Demonic Tutor's exact sentence and then discards at random.

**This is why the reference deck's coverage fell from 78.6% to 74.3%.** Three cards — Phyrexian
Tribute, Toxic Deluge and Ritual of the Machine — each have an additional cost the engine has
never paid and never mentioned. **The engine's behaviour is bit-identical**: the golden snapshot
is untouched at `a28852be8ffc34f9b3bf63b51b7c8420` and `deck_definition(deck) == chainer.DECK`
still holds. What changed is that the application now admits something that was always true.

A deck looking worse because the application got more honest is the phase working.

### 5. `skips_draw_step`, read off the sentence and not off the tag

Necropotence. The tag `skip-draw-step` exists and covers 20 cards — and files three different
things together: Necropotence skips every draw step, Ivory Gargoyle skips exactly one, and
**Fatigue makes somebody else skip one**. The engine's flag means the first, so only the first
may set it, and the reading is anchored on the printed sentence.

Finding that exposed a pre-existing bug of the same shape: `_ENTERS_TAPPED` required
`[.\n] ` — a separator *followed by a space*. Oracle text separates abilities with a bare
newline, so every card whose sentence began a line was missed. Three cards, including
"Flash\nFlying\nEbondeath enters tapped." Both patterns now use `[.\n]\s*`.

### 6. Printed keywords, shown and never counted

Ingested since Phase 1 and displayed for the first time: on the card page under **what the card
says**, and on the playtest board under each card in hand.

They are worth **nothing** to a simulation and the code says so in both places. A goldfish has no
opponent: nothing blocks, nothing attacks, no damage is prevented. Flying, deathtouch, menace and
trample change not one number. They are here because a person reading their own board wants them,
and that is the entire argument and the entire limit.

### 7. The derived tutor is correctable in one click

The phase's own rule — *a person must be able to disagree* — made this mandatory rather than
optional. `tutor_count` is the ninth editable judgement, and **0 is how somebody says "this is
not a tutor at all"**, which is the same blank-means-no-opinion rule as everywhere else. A
derived value a user cannot switch off would be a judgement made on their behalf.

## Verification

| Claim | How it was checked |
|---|---|
| The engine still does exactly what it did | Golden snapshot md5 unchanged; four vendored test files byte-identical |
| The reference deck round-trips | `adapter.deck_definition(deck) == chainer.DECK`, green |
| The reference deck's *numbers* moved, and why | Three named cards, each with an unpaid additional cost — §4 |
| A tag never claims what a person did not | `draw-engine` ⊆ the author's eight, asserted |
| The deriver agrees with a human | Three hand-annotated tutors reproduced field for field |
| The mapping and the catalogue cannot drift | Both directions, plus a spelling check on every slug |
| `unmodellable` is still unreadable, not merely unjudged | `readable < 0.6`, `cards_unreadable >= 5` |
| Stored runs split correctly | A three-key gap row from before the split |
| Nothing is unstyled | 28 pages photographed, desktop and phone, and looked at |

## What this phase deliberately did not do

- **No LLM tagging.** Phase 7 §3, with its global cache.
- **No combat modelling.** Keywords are displayed, never simulated.
- **No widening of the engine's rule vocabulary.** `PER_CONTROLLED` still cannot express
  devotion; Nykthos and Gaea's Cradle are still honestly unresolved. Widening the engine and
  widening the data that feeds it are two changes, and doing both at once means neither can be
  blamed when a number moves.
- **No tutor to the battlefield or the top of the library.** Both are reported as gaps. Giving
  `TutorSpec` a third destination is an engine change, which is the bullet above.
- **`goldfish_castable` is still not derived from `spot-removal`.** 5,460 cards wear that tag and
  the temptation is real. `simulations/blindspots.py` was written specifically against it:
  a detector that quietly set the field "would be making a judgement on the user's behalf and
  hiding it inside a number". The detectors still report and still never decide.

## What is left open

- **`tutor_to_hand` is not editable.** `tutor_count` is, so a wrong tutor can be switched off but
  its destination cannot be corrected without the admin. The zone comes from a tag that is right
  far more often than not; the form field is a later phase.
- **The `Searches your library for` row reads "nothing" sourced from `regex`** on a card like
  Imperial Seal, where a pattern did read a number and the zone could not be used. True, and
  slightly odd to look at. The card carries a visible gap saying why.
- **`tutor-to-top` is not in the offline fixture**, because no sampled card carries it. Covered
  by a unit test over `derive()` rather than end to end. Five `tutor-*` restriction tags are in
  the same position and are listed, with their live card counts, in
  `tests/test_cards_effects.py::VERIFIED_UPSTREAM_ONLY`.
