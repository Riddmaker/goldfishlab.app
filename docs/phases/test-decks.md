# A set of test decks

**Status: Complete (2026-09-18).**

Not a phase. A piece of groundwork that sits between Phase 4 and Phase 5, agreed with the
user after Phase 4 shipped, because everything built in six phases had been proved against
exactly one deck.

## Why

The mono-black Chainer reference deck is a good fixed point. It is real, it is
hand-annotated card by card, and its numbers were researched rather than invented — which is
what makes `adapter.deck_definition(deck) == chainer.DECK` worth asserting.

It is also **one shape**, and a shape with no awkward properties: one colour, a commander, a
sane land count, and a human judgement recorded on every card that needs one. A whole class
of bug cannot be seen from it, because it does not have the property that would reveal them.
Three such bugs were found within an hour of the first test deck existing.

There was also a specific debt. Phase 4's own verification list asked that **a deck full of
unmodelled cards shows a loud, unmissable warning**. What was actually tested was
`blindspots.find([]) == []` and the reference deck — the empty case, and a deck that does not
have the problem. That item is now closed by a deck that does.

## What was built

### `decks/fixtures.py` — the shapes, as data

Seven `DeckShape` records: a key, a name, the cards, an optional commander, optional
deck-scoped annotations, and a `catches` field saying which class of bug the deck exists to
find. That last field is not decoration — a deck nobody can say what it would catch is a deck
nobody will maintain, and the first thing anyone will want to do with an odd-looking fixture
is delete it.

**The module imports no Django**, because three things with different ideas of what is loaded
read it: `scripts/build_card_fixtures.py` (a plain script, no settings module),
`decks/seeding.py`, and the test suite. One definition, three readers. Had the card lists
lived in the seed command, the fixture builder could not see them and the offline sample would
have drifted — silently, because a card missing from the sample looks exactly like a card
Scryfall does not have, and sends you looking upstream at a problem two files away.

| Shape | Cards | What it catches |
|---|---|---|
| `five_colour` | 59 + commander | Anything still assuming one colour. Five basic land types mapping to five different colours; the per-colour report columns having something to show |
| `no_commander` | 59 | `_deck_colors`' fallback to the deck's own pips. Every other deck in the suite has a commander, so that branch had never executed |
| `land_light` | 60, 20 lands | Opening hands at the bottom of the range |
| `land_heavy` | 60, 45 lands | The same at the top — and flooding, which a goldfish never loses to and so never reports |
| `scaling_ramp` | 49 | `PER_CONTROLLED`, `TYPE_ADDING`, `DOUBLE_SUBTYPE` driven **through the database** |
| `opponent_dependent` | 50 | The blind-spot detector on the four cards its docstring names |
| `unmodellable` | 48 | **The Phase 4 gap.** 0% coverage, two named limitations, twelve cards |

### `decks/seeding.py` — one builder, two callers

`build(shape, owner)` writes `Deck`, `DeckCard` and `CardAnnotation` rows. Both the test suite
and `manage.py seed_test_decks` come through it, so a deck built for a screenshot and a deck
built for an assertion are the same deck — which is the only thing that lets a screenshot be
evidence about what the tests cover.

Its `resolve()` replaced a near-duplicate inside `seed_reference_deck`. There were two copies
of the name-lookup ladder and they did not agree about accent folding.

**Every seeded annotation is deck-scoped.** A built-in row (`owner=None, deck=None`) applies to
every deck of every user, so a fixture that wrote one would change the reference deck's numbers
from another test file — and the reference deck is the fixed point everything else is measured
against. A test asserts that no built-in row appears.

### The offline sample, extended

Tests never hit the network, so a test deck may only name cards already in
`tests/fixtures/oracle_cards_sample.jsonl.gz`. `scripts/build_card_fixtures.py` now reads
`decks.fixtures.card_names()`, so adding a card to a shape puts it in the sample on the next
rebuild with no second list to remember.

The sample went from ~69 real cards to 96 and grew by **2 KiB**, because the rebuild also
fixed a wart: after the first two deliberate non-cards, art-series rows were falling through
to the name match, and every wanted card had been dragging its own art series in behind it.
A third of the fixture was padding.

## Three bugs the decks found

**1. Four of the engine's five land types were unreachable.** `adapter._subtypes` only ever
produced `{"swamp"}`, for a basic Swamp — a leftover from when the engine was mono-black.
Every other basic reached the engine with *no land types at all*, which left
`PLAINS_SUBTYPE`, `ISLAND_SUBTYPE`, `MOUNTAIN_SUBTYPE` and `FOREST_SUBTYPE` in
`simulation/cards.py` defined, documented and impossible to trigger from a database deck.

The colour was not what broke — a Forest still taps for green through its own mana ability.
What broke was everything that *counts* land types: Cabal Coffers' per-swamp scaling, Crypt
Ghast's doubling, and the types Urborg and Yavimaya hand out. Those saw one basic type in the
world. Fixed by reading them off the type line in `_printed_subtypes`.

The fix could not move the golden snapshot, and that was checked before it was written: the
reference deck's only land with a printed subtype is `Swamp`, which already had one.

**2. Coverage could be negative.** `convert()` counted `cards_total` from deck entries but
recorded gaps for the commander too, and the commander is not a `DeckCard` (trap 7). A
two-card deck whose commander the engine could not read reported **-50% coverage**. "Always
show a coverage score" is a settled decision, and a negative percentage is a worse answer
than no answer. The commander now counts toward its own denominator; the reference deck's
score moved from 78.3% to 78.6% as a result, which is the same measurement made honestly.
(Phase 5b moved it again, to 74.3%, for the same kind of reason - see
[phase-5b-effects.md](phase-5b-effects.md) §4.)

**3. Land-heavy decks mulligan *more*, not less.** Not a bug — a property, and one that was
guessed wrong while writing the test. `game.keepable` throws back a hand of **0 or 6+ lands**,
so forty-five lands mulligans into flood far more often than twenty mulligans into nothing.
It is asserted now, in the direction the engine actually goes.

## Verification

- 494 tests green, up from 468. 26 new, in `tests/test_deck_shapes.py`.
- `ruff check` clean. `djlint` unchanged (4 pre-existing H021 inline-style hits in templates
  this work did not touch).
- The four vendored engine test files still byte-identical to `magic-project/tests/`.
- `manage.py check` clean, no pending migrations.
- Screenshot pass over 22 pages, all HTTP 200, no runaway page heights, looked at by eye.
  **The report's "What the mana was made of" table shows all six columns with real numbers
  for the first time** — against a mono-black deck a bug counting every colour as black had
  looked exactly like a correct implementation.

## Deliberately not done

- **No second legal 100-card deck.** The task list carried a row for one. The reference deck
  already *is* one — 99 cards, a commander, hand-annotated — and a second would be another
  thing to keep current in exchange for nothing these seven shapes do not cover. What the
  reference deck cannot be is odd, and odd is what this work was for.
- **The demo deck is still the 214-card collection export.** Replacing it was listed under
  this task. `seed_test_decks` now gives the screenshot pass seven better subjects, and the
  demo deck stays because it is the only fixture that exercises the *import* path — a real
  export, with rows that had to be matched by name. Swapping it would have cost that.
- **Nykthos and Gaea's Cradle are left unmodelled on purpose.** The engine's `PER_CONTROLLED`
  counts lands carrying a subtype; devotion and creature-count scaling are not expressible in
  it. They sit in the `unmodellable` deck being honestly reported as unresolved, which is a
  better outcome than an annotation that quietly makes them mean something they do not.
  Note that `simulation/cards.py`'s docstring names both as `PER_CONTROLLED` examples, which
  overstates what that rule does.
