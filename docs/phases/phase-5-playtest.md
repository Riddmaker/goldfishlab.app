# Phase 5 — Interactive playtest

**Duration:** ~2 weeks
**Status:** Complete (2026-09-18). See *What actually happened* at the end.
**Ships:** the feature that makes people stay rather than run one report and leave.

---

## Architecture (decided in planning)

**Server-authoritative, event-sourced, forms as baseline with htmx as enhancement.**

A vanilla JS state machine was rejected: full manual control would mean reimplementing card
legality in JavaScript — two sources of truth that will diverge, and zero reuse of the Python
engine that already has 65 tests.

Every action is a real `<form method="post">` whose submit button carries `hx-post` /
`hx-target="#board"` / `hx-swap="outerHTML"`. With JS disabled the same POST redirects and
re-renders the page. One code path, one source of truth, and the Django test client exercises
every action without a browser.

**Pin htmx 2.0.10**, self-hosted in `static/js/`. Not 4.0.0 — it is weeks old.

## State

```
PlaytestSession(deck_snapshot, seed, cached_state, cached_seq, forked_from)
PlaytestAction(seq, kind, payload, undone)
```

Current state = the seeded shuffle replayed through N actions. This only works because the engine
is deterministic given a seed — which `test_reproducible_with_seed` already guarantees. Replaying
~60 pure-Python mutations is sub-millisecond, and `cached_state`/`cached_seq` makes the common
case a single JSON load.

- **Undo** = `actions.filter(seq__gt=n).update(undone=True)`, drop cache
- **Redo** = un-set
- **Fork** ("replay turn 3 differently") = copy actions `1..n` into a new session — falls out for
  free and is genuinely nice
- Storage: ~60 small rows per session instead of ~60 × 20 KB snapshots

Session storage was rejected (opaque, expires, no resume/share/fork). Client-side signed state was
rejected (grows to tens of KB, means validating untrusted game state every request).

## The engine change that enables this

Split `agent.take_turn`, which today both *decides* and *executes*:

- `simulation/game.py` keeps mechanics (already mostly true)
- New `simulation/actions.py`: a closed set of `Action` dataclasses — `PlayLand`, `CastSpell`,
  `TapPermanent`, `MoveCard(from_zone, idx, to_zone)`, `AdvancePhase`, `Mulligan`, `Draw(n)`,
  `SetLife(n)` — plus `apply(game, action)` and `legal_actions(game)`
- `agent.take_turn` refactors to *choose* from `legal_actions` and call `apply`

**The agent and the human then drive identical code.** That is the correctness argument, and the
reason to do the split even though the agent works fine today.

## Full manual control means legality is advisory

`MoveCard(any → any)` is always permitted; the user is the judge, exactly as in paper playtesting.
The UI **highlights** what is castable with current mana but never blocks. This sidesteps the
infinite rules-engine rabbit hole while delivering what was asked for.

Phases are a simple enum (untap/upkeep/draw/main1/combat/main2/end). No stack, no priority, no
combat damage — it is a goldfish.

## The card table

`templates/playtest/components/{card,zone,board,phase_bar}.html`. Card images hot-linked from
Scryfall `image_uris.normal` (permitted; store the URL, never the bytes). Dark-fantasy tokens from
phase 0: ink ground, parchment surfaces, desaturated blood accent, serif display face.

---

## Verification

- Every action reachable and correct **with JavaScript disabled**
- Undo/redo/fork round-trip correctly; replay from actions equals `cached_state`
- Django test client covers every action kind — no browser needed for logic tests
- Playwright screenshots: opening hand, mid-turn board, a forked session, phone width
- Performance: a 60-action session replays in well under 100 ms

## Definition of done

Draw a hand, play three turns by hand, undo two of them, fork, and play them differently.

---

## What actually happened

**Status: Complete (2026-09-18).** Built in the order the plan set out, and the engine split
came first on purpose: it is the only step that could have silently changed every number the
product publishes, and doing it before any UI existed meant the golden snapshot was the only
thing that had to be watched.

### The split, and the evidence it changed nothing

`simulation/actions.py` holds thirteen frozen `Action` records, `apply()` and `legal_actions()`.
`agent.take_turn` now *chooses* — which land, which spell, whether a ritual is worth it — and
every *doing* goes through the same `apply` the board calls. A human and the agent cannot drift
apart about what casting a spell means, because there is one implementation of it.

The evidence that the refactor was behaviour-preserving, checked before a line of UI was
written: **`tests/fixtures/engine_golden.json` untouched, the four vendored engine test files
still byte-identical, 494 tests green.** The snapshot was never regenerated.

Two things moved out of the agent into the action set, because they turned out not to be
decisions at all: cast-time effects (a tutor's search, draw-on-cast) are *mechanics*, and only
*which card the tutor finds* is a judgement. That judgement is now a `policy` argument — the
agent passes its priority list, the playtest will pass the player's answer — which is also what
keeps `simulation/actions.py` from importing the agent.

### `simulation/serial.py`, which the outline did not plan for

A session stores a deck snapshot and a cached state, so engine objects had to survive JSON. It
is generic dataclass reflection rather than a field list, so a new field on `Card` is serialised
by the same code as the rest — with `tests/test_serial.py` building a `Card` with **every field
set away from its default** and asserting the round trip, plus a test that fails if that
exhaustive card ever stops being exhaustive.

The deck is snapshotted rather than re-derived, and that is a correctness decision, not an
optimisation: a deck's annotations can be edited while a session is open, and re-deriving would
change a card under the player's hands mid-game. A test asserts that editing an annotation
leaves an open session's snapshot alone.

### Three bugs

**1. Mana floated between turns.** `BeginTurn` did not empty the pool. The agent could never
reveal this — it reopens the pool at every main phase, so a surviving pool was overwritten
before anything could spend it. A human stepping through phases keeps turn one's mana on turn
three. **Found by looking at a screenshot**, where the board said "Floating: nothing" on a turn
two draw step that should have had no pool at all. Now `BeginTurn` clears it, and two tests pin
both halves: gone at the turn boundary, still floating across the phases within one turn.

**2. Two phases that no game could ever be in.** The outline listed all seven
(untap/upkeep/draw/main1/combat/main2/end). The engine resolves untap, the turn's draw and the
upkeep triggers together in `Game.begin_turn` — and in that order, which is *not* real Magic's
and which every published number rests on. So `PHASES` is the five the engine actually has, the
turn starts at `draw`, and a test walks a turn asserting every constant is reachable. Defining
two that nothing could reach is how four of the engine's five land types stayed broken for six
phases (see [test-decks.md](test-decks.md)).

**3. The restored generator was one shuffle out of step.** `load_game` set the random state and
*then* built the `Game` — whose constructor shuffles, advancing it again. Invisible in a
goldfish, which shuffles only for its opening hand, and impossible to reproduce once it bit.

### Where the plan and reality differ

- **Five phases, not seven** — above.
- **One `_board.html` fragment**, not `components/{card,zone,board,phase_bar}.html`. The board
  is swapped whole by htmx and included whole by the page, which is what guarantees the two
  cannot show different games. Splitting it into four partials would have bought indirection and
  a second way for them to disagree.
- **`simulation/serial.py` was not in the plan** and is the largest unplanned piece.
- **Card art is looked up in the view, not carried by the engine.** A `Card` is a set of
  numbers; giving it a URL would put a web address inside the simulation. The board joins names
  to `OracleCard.image_uri` and hot-links, never storing bytes.

### Verification

- **576 tests green**, up from 494 - 82 new: `test_actions.py` (30), `test_serial.py` (13),
  `test_playtest.py` (20), `test_playtest_views.py` (19).
- Every action exercised **twice** — a plain POST and an `HX-Request` — with a test asserting
  the two reach the same game. Nothing on the board is reachable only by script.
- `ruff check` clean; `djlint` unchanged at the 4 pre-existing H021 hits, none in the new
  templates; `manage.py check` clean; no pending migrations.
- Golden snapshot untouched, four vendored files byte-identical.
- Screenshot pass over **28 pages**, all HTTP 200, no console errors, desktop and phone looked
  at by eye. The playtest shot is taken by *pressing the button on the deck page*, so it is
  evidence the entry point works rather than only that the template renders.
- The phase's own definition of done is a test:
  `test_draw_play_three_turns_undo_two_fork_and_diverge`.

### Deliberately not done

- **No tutor-target prompt yet.** `policy=None` makes a search take the most expensive card, and
  the board never asks. Casting Demonic Tutor in a playtest therefore picks for the player,
  which is exactly the kind of quiet judgement this product is not supposed to make. The seam is
  in place — `apply(game, action, policy)` — and the screen is not.
- **`MoveCard` and `TapPermanent` have no buttons.** Both work, both are tested, and neither is
  on the board: drag-and-drop between zones is a design problem, not a plumbing one.
- **Mana empties at the turn boundary, not at each step.** Real Magic empties it at the end of
  every step and phase. The engine taps everything once per main phase, so within a turn the
  pool floats by design; carrying that to per-step accounting would change how the agent plays
  and move the snapshot.
