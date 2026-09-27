# Phase 2 — Generalizing the engine

**Duration:** ~2 weeks. **This is the hard phase.**
**Ships:** arbitrary decks from the database, driven by a management command.
**Out of scope:** anything user-facing. This phase is verified by tests, not screenshots,
and that is the point.

**The success criterion is absolute: all 65 existing tests green, with zero edits to `tests/`.**
If a test needs changing, the boundary has leaked — stop and fix the boundary instead.

---

## The coupling, precisely

| File | Coupling today | Fix |
|---|---|---|
| `cards.py` | `COMMANDER`, `SWAMP`, `UTILITY_LANDS`, `SPELLS`, `build_deck()` are module constants | move to `simulation/fixtures/chainer.py` |
| `game.py:15-17,37,143` | imports `COMMANDER`/`build_deck` at module scope | `DeckDefinition`, keyword-only |
| `mana.py:65-71` | `ROCK_OUTPUT` keyed by card **name** | `Card.mana_abilities` |
| `mana.py:97-110` | `effective_cost(card, medallion: bool)` | `Card.cost_reduction` |
| `mana.py:74-95,113-190` | Coffers / Urborg / Ghast hardcoded | four general scaling rules |
| `mana.py:19-61` | `ManaPool(black, colorless)` | WUBRGC pool + compat shim |
| `agent.py:16-66` | `PRIORITY` dict keyed by name | `Card.priority` |
| `agent.py:118-140` | `_apply_cast_effect` name-dispatch | `Card.draw_on_cast` / `life_on_cast` / `tutor_spec` |
| `game.py:22,258-289` | `ACCELERANTS`, Necropotence / Arena / Bob hardcoded | `Card.upkeep`, `skips_draw_step` |
| `analysis.py:18-28,54` | `SAC_OUTLETS` etc. + `"Chainer, Dementia Master"` literal | tag-driven metric groups |

## What must not change

- The `Card` frozen dataclass shape and its `is_land` / `is_swamp` properties
- `ManaPool.can_pay` / `pay` semantics — **colourless preferred for generic** (`mana.py:52-54`)
- The London mulligan with the **free first mulligan** (`game.py:113-120`) — a Commander-wide
  rule, not deck-specific
- `_bottom_worst`
- **`Game.mana_available` captured *before* casting** (`agent.py:215-216`) — the documented
  Crypt Ghast trap, found and fixed during the deck research
- The overall turn loop shape

---

## The four techniques that make this safe

### 1. Every new `Card` field gets a default
So the ~70 existing positional `Card(...)` constructions keep working unchanged:

```python
mana_abilities:  tuple[ManaAbility, ...] = ()   # replaces ROCK_OUTPUT + land logic
ritual_gain:     ManaAmount = EMPTY             # replaces  pool.black += 3
cost_reduction:  CostReduction | None = None    # replaces  Jet Medallion
draw_on_cast:    int = 0
life_on_cast:    int = 0
tutor_spec:      TutorSpec | None = None
upkeep:          UpkeepSpec | None = None       # Arena (1/1), Dark Confidant (1/mv)
skips_draw_step: bool = False                   # Necropotence
end_step_draw:   EndStepSpec | None = None      # Necropotence activation
priority:        int | None = None              # replaces agent.PRIORITY
```

### 2. Four mana-ability rules replace every special case

1. `FLAT` — `{T}: Add {B}` / `{T}: Add {C}{C}` (basics, Sol Ring, signets)
2. `PER_CONTROLLED(subtype)` — Cabal Coffers, Nykthos, Gaea's Cradle, Serra's Sanctum
3. `DOUBLE_SUBTYPE(subtype)` — Crypt Ghast, Nirkana Revenant, Zendikar Resurgent
4. `TYPE_ADDING(subtype)` — Urborg, Yavimaya

Coffers, Urborg and Crypt Ghast stop being named special cases and become instances of general
rules — while `tests/test_mana.py`'s 24 tests keep pinning their exact numbers as the regression harness.

### 3. `DeckDefinition` injected keyword-only

```python
class Game:
    def __init__(self, rng, on_the_play: bool = True, *, deck: DeckDefinition | None = None):
        self.deck = deck or chainer.DECK
        self.library = list(self.deck.library)
```

`Game(rng)` and `Game(rng, on_the_play=False)` — every form the tests use — are unaffected.
Note `take_opening_hand` (`game.py:141`) currently calls `build_deck()` on every mulligan;
it becomes a list copy, never a DB query.

### 4. PEP 562 shim keeps the legacy names importable

`tests/test_cards.py:6` and `tests/test_statistics.py:17` import `COMMANDER, SPELLS, SWAMP_COUNT,
UTILITY_LANDS, build_deck, land_count` from `simulation.cards`. Lazy `__getattr__` avoids a
circular import between the model and the fixture, and needs no `# noqa: E402`:

```python
_LEGACY = {"COMMANDER", "SWAMP", "SWAMP_COUNT", "UTILITY_LANDS", "SPELLS",
           "build_deck", "land_count"}

def __getattr__(name):
    if name in _LEGACY:
        from simulation.fixtures import chainer
        return getattr(chainer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

---

## ManaPool: mono-black → WUBRG

Six-slot counter with the old surface kept as a thin shim, so the 24 mana tests never change:

```python
@property
def black(self): return self._pool["B"]
def can_pay(self, pips, generic):            # old 2-arg signature preserved
    return self.can_pay_cost(ManaCost(pips={"B": pips}, generic=generic))
```

New `ManaCost` parses `{2}{B}`, hybrid `{2/B}`, phyrexian `{B/P}`, and `{X}`. Hybrid payment is a
small bipartite matching over ≤6 colours — greedy with backtracking is exact at this size.

**Two heuristics that must be documented in the UI, not just a docstring:**
- **X = all remaining mana**
- **Phyrexian paid with life only when mana is short and life > 25** (mirrors the existing
  Necropotence life floor at `game.py:285`)

---

## The adapter — the only module that sees both worlds

`simulations/engine/adapter.py`, one pure function:

```python
def deck_definition(deck: Deck) -> DeckDefinition:
    """Deck (ORM) → DeckDefinition (frozen, Django-free)."""
```

Merge precedence: `DerivedProfile` → built-in `CardAnnotation` → user-global `CardAnnotation`
→ deck-scoped `CardAnnotation`. One query with `select_related`/`prefetch_related`.
**No model instance ever crosses the boundary.**

*(Corrected in Phase 4: this chain was written as ending in a `DeckCard.priority_override`
field, which was planned and never built. The deck-scoped annotation is where a per-deck
priority actually lives, and it carries every other judgement too, so a column of its own
would have been a second way to say the same thing.)*

---

## Also in this phase

**Translate `simulation/` to English.** The generalization rewrites nearly every line anyway;
doing it later means a second mass diff that obscures nothing useful. The product is English-only.

**Bump `engine_version`.** Every behaviour change bumps it, so the UI can later say
"computed with engine v3, current is v5 — re-run to compare".

---

## Verification — the golden tests

```python
def test_db_deck_matches_hardcoded_fixture(db):
    call_command("seed_reference_deck")
    assert deck_definition(Deck.objects.get(slug="chainer")) == chainer.DECK

def test_db_deck_produces_identical_aggregates(db):
    assert analysis.run(10_000, seed=99, deck=built) == \
           analysis.run(10_000, seed=99, deck=chainer.DECK)
```

Plus a **snapshot test asserting the generated `simulation-daten.md` is byte-identical** to the
currently committed file.

If those three pass together with the untouched 65, the generalization is provably
behaviour-preserving. That is the whole safety argument for this phase.

Also: mark `tests/test_statistics.py` as `slow` (it draws 200,000 samples in `setUpClass`) and run
it in CI only. It stays the most important test in the repo; it just should not be in the inner loop.

## Definition of done

A deck row in Postgres produces bit-identical simulation aggregates to the hardcoded fixture,
and no test file was edited to achieve it.

---

# WHAT ACTUALLY HAPPENED (2026-09-18)

**The definition of done is met and verified:** a deck row in Postgres produces
aggregates identical to the hardcoded fixture, and **no test file was edited** — the four
vendored engine test files are still byte-identical to their originals.

Two items from this document are **not** done and are carried forward; see the end.

## The proof, in three layers

1. **The 54 vendored unit tests** still pass, unedited.
2. **`tests/test_engine_parity.py`** compares the generalized engine against a committed
   snapshot taken from the *untouched original* in `magic-project`
   (`scripts/build_engine_golden.py`). Three scenarios, on the play and on the draw, out to
   turn 6. Every mulligan count, opening-land count, per-turn mean and milestone counter
   matches — with one documented exception, below.
3. **`tests/test_engine_adapter.py`** seeds the reference deck into the database and asserts
   the adapter rebuilds it field for field, then that both decks simulate identically.
   Re-verified by hand against the **full 35,568-card catalogue**, not only the fixture.

## The one intended behaviour change

`draw_engine` was a hand-maintained list of seven card names in `analysis.py`. It now comes
from the cards' own role tags — and the tags include **Liliana, Dreadhorde General**, which
the name list had been missing. She draws a card whenever a creature dies; the hand
annotation in `simulation/cards.py` always said `draw_engine`.

So the new number is the correct one. The old snapshot is kept as **evidence** rather than
regenerated, and `test_the_only_divergence_is_the_corrected_draw_engine_list` pins the
exception so it cannot quietly grow into several.

## Four bugs the parity check caught before they shipped

Each of these passed the 54 unit tests and would have silently changed every published number:

1. **Land scores leaked into spell priority.** Giving `Swamp` the land-choice score of 80 as
   its `Card.priority` also changed what `_best_in_library` ranks — Demonic Tutor would have
   started fetching Swamps over real payoffs.
2. **Cabal Ritual quietly became an accelerant.** A mechanical rule ("cheap and makes mana")
   includes it; the old hand-written list did not. That changes the keep rate, and the keep
   rate determines every downstream number. `accelerant` is therefore a **field**, not a
   derived rule — it is a deck author's judgement.
3. **`Lim-Dul` was typed with a circumflex** in the new priority table, so it silently fell
   back to the default rule instead of its assigned 30.
4. **The commander's annotations were never loaded.** The adapter filtered on deck
   membership, and the commander is not a `DeckCard` — it sits in the command zone. It
   arrived with no priority and was almost never cast: 15 casts instead of 108.

## What replaced what

| Was | Is |
|---|---|
| `ROCK_OUTPUT` keyed by card name | `Card.mana_abilities`, four general rules |
| Coffers / Urborg / Ghast hardcoded | `PER_CONTROLLED`, `TYPE_ADDING`, `DOUBLE_SUBTYPE` |
| `effective_cost(card, medallion: bool)` | `CostReduction`, summed over the battlefield |
| `agent.PRIORITY`, 45 card names | `Card.priority`, with a documented default rule |
| `game.ACCELERANTS`, 5 card names | `Card.accelerant` |
| Necropotence / Arena / Bob hardcoded | `Card.upkeep`, `Card.end_step`, `skips_draw_step` |
| `analysis.SAC_OUTLETS` and friends | role tags, and "scaling mana source" for the ramp engine |
| `COMMANDER` / `build_deck()` module constants | `DeckDefinition`, injected keyword-only |

`simulation/cards.py` keeps a PEP 562 `__getattr__` shim so the vendored tests still import
`COMMANDER`, `SPELLS`, `build_deck` and `land_count` from where they always did.

## Upkeep trigger order is not arbitrary

Phyrexian Arena and Dark Confidant both draw, so the order decides which card each gets, and
therefore every downstream number. The generalized engine resolves upkeep triggers by
**descending mana value, then name** — which is a rule a player could actually follow
(resolve the bigger engine first) and happens to reproduce the order the old hardcoded
sequence used. Iterating the battlefield in list order would have put Dark Confidant first
and changed the results.

## Colour: finished 2026-09-18

The engine makes and spends all five colours. Three steps, in this order, each one kept
honest by the parity harness — the mono-black reference deck produces **identical** numbers
at every step, which is what made it safe to change the thing every other number depends on.

**1. Production.** `ManaAbility` stored two integers, `black` and `colorless`. It now stores
`produces`, a canonical colour→amount mapping, with `black` and `colorless` kept as
properties because the adapter and the analysis still ask for them. The subtype→colour map
(`SUBTYPE_COLORS` in `manacost.py`) is the whole of "Yavimaya works like Urborg": the rule
that made a Swamp tap for black was `pool.black += 1` written into `available_mana`, and a
Forest was simply not a thing the engine could read. Scaling rules (`PER_CONTROLLED`,
`DOUBLE_SUBTYPE`) take their colour from their subtype, so a green Cabal Coffers needs no
new code, and Gaea's Cradle — which counts creatures, not lands — can name its colour
explicitly.

**2. Cost.** `Card.cost` is an optional full `ManaCost`; `Card.mana_cost` returns it, falling
back to `ManaCost.mono(pips, generic)`. `pips` and `generic` stay single integers because
`tests/test_mana.py` builds `Card("Test", 1, 1, 0, LAND)` positionally and that file may not
change. Everything that pays a cost now goes through `mana_cost`, so nothing needs to know
which kind of card it has. `CostReduction` gained a colour: a Jet Medallion no longer makes
green spells cheaper, and two Medallions of different colours both apply to a gold spell,
because that is what the cards say.

**3. The adapter.** It reads the **printed** cost with `manacost.parse` rather than
reassembling `DerivedProfile`'s numbers. The profile stores a hybrid `{W/U}` as one white
*and* one blue pip — true as "may be paid with", wrong as "must be paid with", and the
engine's exact payer is the one thing that can tell the difference. A rainbow source (Arcane
Signet, Command Tower) is read as **the deck's own colour**, because the pool counts mana
rather than holding sources and therefore cannot represent a choice; the choice is recorded
as a gap so a result never implies it was modelled.

### What this deliberately does not model

- **A source that could make one of several colours.** The pool is a count, not a set of
  sources, so "either green or white" cannot be held. The adapter picks from the commander's
  colour identity and reports a gap; `land_color()` breaks a tie in WUBRG order. Modelling it
  properly means solving mana bases per cost, which is its own piece of work.
- **"Add one mana of any *type* that a land you control could produce"** (Reflecting Pool).
  Readable in principle, unresolved today, and honest about it.
- **`game.black_available`** is still one colour, and the analysis reports it. Harmless for
  the reference deck, misleading for any other — it should become a per-colour reading.

### Rainbow sources, and a leak they exposed

Arcane Signet and Command Tower say "add one mana of any color in your commander's color
identity". Two things were wrong with how that arrived in the engine, and both are fixed:

**The deriver could not read the sentence.** It looked for an `Add {X}` symbol clause and
found prose, so `mana_amount` stayed null — honestly reported, but it meant the Signet
tapped for nothing. `_ADD_ANY_COLOR` now reads the amount; the colours were already in
`produced_mana`. That made 517 more cards in the catalogue readable and, on the way, turned
up two things the old symbol-only reader had also been getting wrong:

- **Quoted text is an ability the card gives away.** "Enchanted land has `{T}: Add one mana
  of any color`" is the *land's* ability. 385 cards read as mana sources they are not.
- **Exotic Orchard opens with Arcane Signet's exact words** and then says "that a land an
  opponent controls could produce". In a goldfish there is no opponent, so it makes nothing.

**The stopgap was scoped too widely.** With the Signet underivable, `seed_reference_deck`
papered over it with an annotation saying it produces `{B}` — correct for this deck, written
at *built-in* scope, and therefore applied to every deck of every user. A mono-green deck
built from the live catalogue read its Signet as a black source.

Built-in annotations now drop `mana_produces` for any card Scryfall says can make more than
one colour: what such a card makes is a fact about a deck, not about the card. The adapter
decides it per deck from the **commander's colour identity** — the rule the cards name, and
the one that bounds a Commander deck — and records the choice it could not model as a gap.
The reference deck gets black by derivation rather than by decree, which is why the parity
numbers did not move.

## Still open, carried into a follow-up

- **`simulation/` is still in German.** The translation was planned for this phase and was
  deliberately skipped: doing it in the same pass as the generalization would have buried a
  behaviour-preserving refactor in a whole-file diff, and the parity check would have been
  much harder to trust. It is a mechanical change now that the structure has settled.
