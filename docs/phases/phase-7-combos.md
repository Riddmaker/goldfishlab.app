# Phase 7 — Combo detection & AI plumbing

**Duration:** optional
**Status:** **§1 and §2 complete (2026-09-21).** §1 was built against the API rather than the
bulk file — the measurement in §1 says why. §2's scope was decided the same day, after §1
measured something that undercut the original plan; read "§2, decided 2026-09-21" for the
argument and "§2 as built" for what is on disk. **§3 and §4 needed an API key nobody had then.** Update 2026-10-06: §4 (AI prose) was built differently, as the deck summary by Mistral (phase 10 H, 11 D, 12 G; not Pro-only, guests get one). §3 (LLM tagging) stays deliberately later (phase 12 J-F).

---

## 1. Commander Spellbook

**Combos are looked up, not inferred.** A human-curated database is both more accurate and cheaper
than any text-parsing or LLM approach.

- API root: `https://backend.commanderspellbook.com/` — OpenAPI schema, ~80 req/min,
  must handle `429`
- Bulk: `https://json.commanderspellbook.com/variants.json` — **>512 MB**, so stream-parse with
  `ijson`. A naive `json.load` will not survive a cloudlet.
- Prefer the deck-scoped endpoint for per-deck queries; use bulk for the nightly mirror.

### Measured 2026-09-20, before designing anything

Trap 35 says measure the bulk file before designing a table for it, because the last time this
project skipped that step it deferred a feature for two phases over a number nobody had checked.
So, measured:

| | |
|---|---|
| `variants.json` | **656.8 MB**, and the host **serves no gzip** — `Accept-Encoding: gzip` is ignored |
| One variant record | **4,756 bytes** → roughly **138,000 combos** (estimated from the two, not counted) |
| **Of which `uses`** | **3,350 bytes — 70%** |
| What is in `uses` | Ten Scryfall **image URLs per card**, five of them `null`, plus `oracleId` |
| `legalities` | 320 bytes of every format, to answer one boolean about Commander |
| The backend API | **does** serve gzip: a `find-my-combos` call for a 5-card deck is **37 KiB on the wire**, 424 KiB decompressed |
| `find-my-combos/` returns | `included`, `almostIncluded` (one card short), and colour/commander variants — 1 and 18 respectively on the test deck |

**Three-quarters of the largest file this project would ever download is image URLs for cards
whose images we already have.** `OracleCard.image_uri` and `Printing.image_uri` exist, and
`uses[].card.oracleId` joins straight to the catalogue — so the join key is 36 bytes and the rest
of that 3,350 is waste in transit.

The comparison that decides it: `default_cards` was 78.8 MB gzipped and bought printing-exact
matching for every import. This is **eight times the download** for combos, and a single deck's
worth of combos is **37 KiB** from an endpoint that already exists and already compresses.

## 2. The feature no competitor has

Detected combos are fed back into the simulator, which reports **turns-to-assemble** per combo.

This is the compounding differentiator: everyone else can tell you *that* your deck contains a
combo. Only a simulator can tell you *how long it actually takes to come together*.

Grounding from the deck research, which is exactly why this matters:

| | by end of turn 6 |
|---|---|
| Carrion Feeder + Gravecrawler | **1.7 %** |
| …+ Blood Artist | **0.2 %** |
| *Any* sac outlet + *any* recursive creature | **18 %** |

Two specific cards in a 99-card singleton deck is ~1.6 % by pure hypergeometry. **Telling a user
their combo assembles 1.7 % of the time by turn 6 is more useful than telling them it exists.**

### §2, decided 2026-09-21 — measure both, and lead with the one you have not got

§1 measured that **the reference deck contains zero Spellbook combos**, and that the numbers in
the table above are from the deck research's *hand-identified synergies*, not from Spellbook.
Spellbook does not catalogue Carrion Feeder + Gravecrawler at all: it costs `{B}` per loop, so it
is not infinite. Turns-to-assemble over `included` alone would therefore usually have an empty set
to work on. The decision, taken because the user delegated it:

**Both lists get a number, and the same machinery produces both.**

1. **`included` — "you have this, and here is how long it takes."** The original feature. Rare,
   and worth a lot on the decks that have it.
2. **`one card away` — "add this card and it assembles by turn 6 in X % of games."** The one that
   has something to say about **every** deck, including the reference deck's 76. It is also the
   sentence no competitor can write: everyone can list a combo, only a simulator can price the
   card that completes it.

Four rules the implementation has to keep, three of which the schema is already built for:

* **The hypothetical is stated, never hidden.** A one-away combo is measured by **adding** the
  missing card, so the simulated deck is one card larger than the real one. Say so on the page.
  Cutting a card to make room would be this application choosing which card somebody's deck can
  spare, which is exactly the kind of judgement the honesty layer exists to refuse.
* **Zones decide what "assembled" means.** `ComboCard.zones` was stored in §1 for this: a card
  that must be on the battlefield is not assembled by being in hand, and a `Gravecrawler` that
  must be in the graveyard is not assembled by being in the library. Measuring "drawn" instead of
  "in the right zone" would overstate every number on the page.
* **A combo with an unmet template gets no number at all.** "Any persist creature" cannot be
  resolved against a deck without running the Scryfall search the template carries, and until
  something does, the honest output is the requirement in words and no percentage beside it. Same
  rule as `produced_mana` setting `needs_review` rather than guessing a quantity.
* **It rides on an existing simulation run.** Turns are what `billing/quotas.py` meters, and a
  combo measurement taken during a run somebody already started adds no new metric and no fifth
  `check()` call site.

Where it renders: the combo panel already on the deck page, and the run report. Before that panel
has ever seen a run it says so, rather than showing a blank column.

### §2 as built, 2026-09-21

The split is the same one the rest of the project uses — the engine knows nothing about
Spellbook, and Django knows nothing about how a game is played.

| | |
|---|---|
| `simulation/combos.py` | The watcher. `Requirement(name, zones, quantity, must_be_commander)`, `Watch`, and a `Watcher` that looks once per turn and records the **first** turn each combo was whole. No Django, no Spellbook. |
| `simulation/analysis.py` | `run(..., watch=())` and `simulate_game(..., watch=())`. Without a watch the result is what it always was — **the `combos` key is absent, not empty**, which is what keeps the golden parity snapshot valid. |
| `simulations/engine/runner.py` | `Sample(key, deck, watch, games)` and `run_chunk(..., watch=, samples=)`. Re-exports `Watch`, `Requirement` and `Unmeasurable` so the Django side never imports the engine directly. |
| `simulations/engine/adapter.py` | `convert(deck, adding=card)` — the hypothetical deck, **one card larger**. |
| `combos/measure.py` | The planner: which combos get a number, which get a sentence, and what the whole thing is allowed to cost. |
| `combos/models.py` | `ComboMeasurement` — the number, **with its sample size, its turn count and the card that was added to get it**. A percentage without those three is a claim nobody can weigh. |
| `simulations/tasks.py` | Two lines in `simulate_chunk` and one call in `finalize_run`. The measurement rides on the run and never fails it: a finished run whose numbers are good is worth more than a combo percentage. |
| `templates/combos/_measurement.html` | One line, three states — it happens, it never happened, or it was not measured and here is why. Included from both the deck panel and the one-away list, so the two cannot word it differently. |
| `tests/test_combos_measure.py` | 41 tests, and the first seven are all about zones. |

**What it costs, and why that number.** A combo the deck already holds is measured in the run's
own games and costs **nothing**: the games are played either way and a look is a dictionary
lookup per turn. A combo the deck is one card short of cannot be measured that way at all — the
card that completes it is not in the library to be drawn — so it gets a deck of its own and games
of its own. Those are rationed:

- at most **half the run's own size again**, across all hypotheticals together;
- at most **three** of them, most-played first (the panel still *lists* eight; listing is free);
- never fewer than **1,000 games** on any one, because below that the sampling error on a 5%
  answer is wider than the whole percent the page prints — so a 1,000-game run measures **no**
  hypotheticals and says so, and tells the reader what size of run would;
- never more than **5,000**, because past that the precision is below what is displayed.

**Still exactly four `quotas.check()` call sites.** The hypotheticals' games are planned where the
run is planned and recorded where the run's games are recorded — `finalize_run` adds them to the
`GAMES_SIMULATED` usage record. Metering what a run costs is not the same as gating it, and only
the gate is audited.

**What refuses to produce a number**, each with a sentence the page prints:

- a combo needing a **template** — "any creature with persist" is a Scryfall search, and nothing
  runs it yet;
- a missing card **not in this catalogue** — there is nothing to add to the deck;
- a combo **more than one card short** once read against our own record of the deck;
- a zone this engine has not got — Spellbook's vocabulary includes the **stack**, which is never
  a resting place in an end-of-turn snapshot;
- a combo that was simply **not among the three** the run had room for.

**The one assumption that would have inflated every number on the page** is measuring "drawn"
instead of "in the right zone", and it is the first thing `tests/test_combos_measure.py` asserts.
A card that has to be on the battlefield is not assembled by sitting in hand.

### What it says about the reference deck, measured 2026-09-21

Run on the deck this project was built around — 99 cards, 10,000 games, 6 turns, and the three
most-played of its 76 one-card-away combos:

| Add | Together by turn 6 | Half of those by | Games |
|---|---|---|---|
| Phyrexian Altar (with Gravecrawler) | **under 1%** (0.6%) | turn 4 | 1,666 |
| Vito, Thorn of the Dusk Rose (with Exquisite Blood) | **under 1%** (0.24%) | turn 6 | 1,666 |
| Sanguine Bond (with Exquisite Blood) | **under 1%** (0.06%) | turn 6 | 1,666 |

Three things worth keeping from that.

**The numbers are small, and that is the answer.** Two specific cards in a 99-card singleton deck
is ~1.6% by hypergeometry *before* anything has to be cast, and these combos cost nine to eleven
mana between them. "Buying Phyrexian Altar will win you games by turn six" is not true, and the
page now says so instead of implying it.

**Phyrexian Altar is nonetheless six times likelier than Vito and ten times likelier than
Sanguine Bond, and it lands two turns earlier** — because Gravecrawler costs `{B}` and Exquisite
Blood costs six. That is the comparison the feature exists to let somebody make, and no card list
can make it.

**`0%` would have been a lie.** 0.6% rounds to zero, which reads as *never* beside a combo that
did come together. `ComboMeasurement.share_label` prints **"under 1%"** below a whole point -
the bound rather than the rounding, and no decimal place, because at 1,666 games the first
decimal is noise. Found by looking at the rendered page; no test was going to notice.

## 3. LLM tagging fallback (tier 3 of the card model)

Only for cards no pattern and no Scryfall tag matched. **Result cached globally in the DB forever**,
so an odd card costs tokens exactly once across all users. Invisible infrastructure that directly
raises the coverage score.

This is the one clearly good use of an LLM in this product.

## 4. AI prose (Pro) — deliberately not the headline

> **Superseded by phase 10 (K8, 2026-10-02).** The written deck summary is built in phase 10 H:
> for every account rather than Pro only (a guest gets one, everybody else pays one run per new
> summary), and still at the foot of the report rather than the headline. See
> `simulations/summary.py`.

Optional narration on top of simulation results, in the style of the hand-written commentary in
`simulation.md`.

**Positioning constraint, decided in planning:** this is a Pro convenience, **not** what is
advertised and **not** what justifies the price. The 2026 market is saturated with free AI deck
analyzers (DeckStir gives away exactly this). Advertising it invites an immediate unfavourable
comparison on the one axis where the product is weakest. The simulation is the moat.

---

## Verification

- Combo detection matches a hand-checked deck (the Chainer deck is a known-good fixture)
- Bulk stream-parse stays inside the memory envelope — measure it
- LLM fallback is cached: the same unknown card never costs tokens twice
- Rate limiting and 429 handling verified against the live API

## Definition of done

A user sees which combos their deck contains **and how many turns they typically take to assemble**.

---

# WHAT ACTUALLY HAPPENED — §1 (2026-09-21)

**No bulk mirror was built, and that is the finding.** The plan said to stream-parse a 512 MB+
`variants.json` with `ijson`. Measured first, per trap 35, and the numbers are in §1 above: it is
**656.8 MB with no gzip offered**, and **70% of every record is Scryfall image URLs** for cards
whose images are already in `OracleCard.image_uri`. Meanwhile `find-my-combos/` answers a whole
99-card deck in **78 KiB on the wire**, gzipped, and already exists.

So `combos.models.Combo` is a mirror that **accumulates from demand**: every deck somebody looks
up leaves its combos behind, deduplicated on Spellbook's own id. The table grows towards the
combos this application's users actually play instead of towards all ~138,000 of them.

## What was built

| Module | Does |
|---|---|
| `combos/spellbook.py` | The only module that talks to Commander Spellbook. `urllib`, no new dependency. 1 req/s, retries on 429/5xx honouring `Retry-After`, https only, and a **16 MB ceiling on the decompressed body** — the wire format is gzip from a third party |
| `combos/models.py` | `Combo` / `ComboCard` / `ComboTemplate` (global, deduplicated) and `ComboLookup` / `DeckCombo` (per deck) |
| `combos/services.py` | Fingerprinting, staleness, the cooldown, and the only outbound call path |
| `combos/views.py` | One POST. A GET that fetches is a GET a crawler can fire |
| `templates/combos/_panel.html` | Renders from cache on the deck page; the button is the only thing that fetches |

## The honesty trap, and the schema built around it

**A combo is named cards *plus templates*.** `Mikaeus, the Unhallowed` + `Carrion Feeder` also
needs *a creature with persist* — a category defined by a Scryfall search, not a card anybody can
name. A deck holding both named cards and no persist creature **does not have that combo**.

`ComboTemplate` exists so that requirement survives into the page. Dropping it as detail would
have been the combo-detection version of reading 28 Swamps as one: a confident, wrong claim with
nothing to catch it.

## Four decisions worth arguing with

**Nothing fetches during a page render.** The deck page reads cache and says how old it is; the
button is the only outbound call in the application's combo path. A Spellbook outage therefore
costs the page its freshness and not its contents — a failed refresh keeps the previous entries
and records why, because "Spellbook did not answer" and "this deck has no combos" look identical
on a page that only counts rows.

**Not metered, bounded instead.** `billing/quotas.py` documents exactly four `check()` call sites
and a fifth would be a bug. A 14-day `MAX_AGE` and a 2-minute per-deck `COOLDOWN` bound this on
the axis that actually costs something — somebody else's bandwidth — and they bound it per deck
rather than per person.

**Which card you are missing is computed from our record of the deck, not taken from the API.**
Otherwise the shopping list and the deck page could disagree about what is in the deck.

**Combos outside the commander's colour identity are counted, never stored.** 112 of them on the
reference deck. A number is a fact about this deck; a list is advice about a different one.

## What §1 found that §2 has to deal with

**The reference deck contains zero Commander Spellbook combos.** 69 distinct cards, hand-built,
the deck this entire project was written around — `included: 0`, `almostIncluded: 76`.

That is not a bug and not a bad deck. Spellbook catalogues combinations that go **infinite or win
outright**; a deck built to grind does not contain any. But it undercuts the premise §2 was
written on:

> Carrion Feeder + Gravecrawler — **1.7%** by end of turn 6

Those numbers came from the deck research's **hand-identified two-card synergies**, not from
Spellbook's list. Spellbook does not catalogue Carrion Feeder + Gravecrawler at all, because it
costs `{B}` per loop and so is not infinite.

So §2 as written would compute turns-to-assemble for a set that is usually empty. **The
interesting version is the other one:** `almostIncluded` is 76 entries for the reference deck, and
those are combos one card away. "Add Phyrexian Altar and this assembles by turn 6 in X% of games"
is a sentence no competitor can write, it uses the simulator for something only a simulator can
do, and it has something to say about every deck rather than only about combo decks.

**That decision was put to the user on 2026-09-21 and delegated back** — "we do what you think is
best and fits the product". It was taken and then built the same day: **both lists get a number**,
and the one-card-away case is the one that has something to say about every deck. See
"§2 as built" above for what that turned into on disk.

**What it cost to be honest about, in the end:** a one-card-away number cannot be measured in the
deck's own games, so it needs games of its own, so it has to be rationed — and a run too small to
pay for one gets told so rather than shown a number made of three hundred games. That chain is
the whole of `combos/measure.py`, and every link in it was forced by refusing to approximate the
first one.
