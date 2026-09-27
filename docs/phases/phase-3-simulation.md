# Phase 3 — Background simulation & reports

**Duration:** ~1.5 weeks
**Ships:** the core product. This is the phase that justifies the whole project.

---

## 1. The required `analysis.py` change

Today `analysis.py:97-99` does `stats["mana"].append(snapshot["mana"])` — a list of length N per
metric per turn. That is **unmergeable across chunks** and O(iterations) in memory.

Replace with **histograms + moment accumulators**:
- `Counter` for mana (0–40), lands (0–15), mulligans, opening lands
- `(n, sum, sum_sq)` triples for means

Each chunk then returns a few KB of JSON-serialisable counters, the chord callback merges them
with `Counter.update`, memory becomes O(1) in iterations — **and percentiles come for free**,
which the current mean-only report lacks and should have.

## 2. Celery task design

```python
run_simulation(run_id) →
    chord(
        group(simulate_chunk.s(run_id, chunk_seed(run.seed, i), n) for i in range(k)),
        finalize_run.s(run_id)
    )
```

- **Chunk ≈ 2,000 games** (~15 s worst case on cloudlet CPU). Measured baseline: 370 µs/game at
  6 turns on the dev machine; assume 3× for the generalized engine and 3–5× for cloudlet CPU.
  Calibrate adaptively by timing the first 200 games and storing `usec_per_game` on the Deck.
- **`chunk_seed(run_seed, i)` is a tested public contract.** Decide and document now whether a
  chunked run with seed S is bit-identical to a monolithic run with seed S. Do not discover this later.

### Progress — in the database, not the Celery backend

```python
SimulationRun.objects.filter(pk=...).update(
    games_done=F("games_done") + n, chunks_done=F("chunks_done") + 1)
```

Survives worker restarts, visible in the admin, gives a real audit trail. Use the Celery result
backend only for terminal state and exceptions.

### Cancellation is cooperative

Never `revoke(terminate=True)` — SIGKILL mid-chunk leaves dirty state and can kill the wrong task
after a worker restart. Instead: set `cancel_requested=True`; each chunk checks the flag **before
starting** (one cheap query per ~15 s) and returns early; queued-but-unstarted chunks are
additionally `revoke()`d **without** terminate. `finalize_run` sets `CANCELLED`.
Documented worst-case latency: one chunk.

### Fairness — two queues, and this is the real mechanism

`sim_short` (≤10k games) with a dedicated always-available `-Q sim_short -c 1` worker, and
`sim_long` (>10k). Without this, one 100k run blocks every free user's 1k run — the actual
failure mode, and **no quota system fixes it**.

Per-user concurrency via Redis `INCR`/`EXPIRE` on `sim:active:<user_id>`, checked at enqueue.
Over cap → 429 and a friendly page.

**Quota debited at enqueue, refunded on cancel or failure.** Never per chunk — partial-refund
arithmetic is a bug farm.

## 3. API and polling

Keep the reference project's exact response keys (`task_id`, `status`, `error`, `result`) and add
`progress: {games_done, games_total, pct}`.

**No hand-written JS:** `GET /runs/<id>/progress/` returns an HTML fragment carrying
`hx-get` + `hx-trigger="every 2s"` + `hx-swap="outerHTML"`. When the run finishes, the fragment
renders **without** the trigger attribute and polling stops by itself. This is htmx paying for itself.

## 4. The report page

- Mulligan distribution
- **Opening-land histogram against the exact hypergeometric** — reuse the comparison from
  `run_simulation.py::_opening_section`. This is the most credibility-building element in the
  entire product: it shows the simulation agrees with closed-form mathematics where closed-form
  mathematics applies.
- Mana curve **with percentiles**, now that histograms make them free
- Tag-driven milestone table (replacing the hardcoded `SAC_OUTLETS` groups)
- Example game logs
- **Coverage score displayed beside every result** — the phase 4 honesty layer starts here in
  minimal form: "61 of 99 cards mechanically modelled".

Charts server-rendered. No JS charting library.

## 5. Infrastructure

Worker node added to `docker-compose.yml` and to `jelastic.jps`. Quota check wired into its third
call site: simulate-enqueue.

---

## Verification

- Chunked and monolithic runs agree within tolerance for the same seed (per the documented contract)
- Histogram merge is associative: merging chunks in any order gives identical results
- Cancellation stops a run within one chunk and refunds the quota
- Worker restart mid-run does not lose progress
- **Memory:** a 100k run stays well inside the cloudlet envelope — measure, do not assume
- **Local Docker:** `docker compose up`, run 10k end-to-end, watch progress advance, cancel a run
- **Playwright screenshots:** the run page mid-progress, the finished report, a cancelled run,
  and the report at phone width

## Definition of done

Import a deck, press Run, watch a progress bar advance without any hand-written JavaScript, and
read a report whose opening-land histogram visibly matches the hypergeometric curve.

---

# What actually happened

**Finished 2026-09-18.** Everything above shipped. The deviations from the plan are below,
and every one of them came from running the thing rather than from reading the code.

## The carried-forward item is closed first

`simulation/` is now in English — roughly 2,300 lines of comments, docstrings and game-log
strings. Behaviour-free by construction, and proved so by the parity golden and the four
byte-identical engine test files, which all still pass untouched. Doing it before the Celery
work meant `analysis.py` was not translated twice.

## `Histogram`, and the vendored test that shaped it

Per-game lists became `Histogram` objects: a `Counter` underneath, O(distinct values) rather
than O(games), mergeable by addition, JSON-serialisable, and percentile-capable.

It is **not** a `Counter` subclass, and the reason is `tests/test_statistics.py:114` — which
is vendored, byte-identical and does `sum(play["turn_stats"][2]["lands"])`. On a `Counter`
that sums the *keys*: a plausible number, silently wrong, with a green test above it. So the
class iterates as the observations it replaced and reports `len()` as how many there were.

`test_engine_parity.py::summarise` grew a `Histogram` branch. **The golden fixture itself was
not touched** — that the same numbers still come out of a different representation is exactly
the evidence worth having.

## The seed contract, decided rather than discovered

`chunk_seed(run_seed, index)` derives each chunk's stream with blake2b. Not `hash()`: Python
randomises string hashing per process, so a stored seed would have replayed differently on
every worker start.

A chunked run is **not** bit-identical to a monolithic run of the same seed, deliberately.
Making it so would mean seeding every game separately, which changes the random stream of
every run that already exists, including the golden snapshot. What is guaranteed: the same
`(seed, index, size)` replays exactly, and chunked against monolithic agree within sampling
error. Both halves are asserted in `tests/test_analysis_chunks.py`.

## Five things only the live stack revealed

Each of these passed every unit test and would have shipped.

1. **Chunks went to the wrong queue.** Routing `run_simulation` routes the *dispatcher*; the
   chunks it creates inherit the default queue, which is `sim_short`. A 100,000-game run
   would have put two hundred chunks in front of every free user's 1,000-game run — defeating
   the single mechanism the whole fairness design rests on. Caught by noticing that the long
   worker had *registered* `simulate_chunk` and never executed one. Every signature now
   carries an explicit queue, and two tests assert it.

2. **Revoking a chord's chunks breaks the chord.** The plan said to `revoke()` queued-but-
   unstarted chunks on cancellation. A revoked header task makes the whole chord raise
   `TaskRevokedError` instead of calling its callback, so nothing closes the run out: a
   cancelled 10,000-game run sat at RUNNING for two minutes and simulated every game anyway.
   Cancellation is now **purely** cooperative — the flag, nothing else. The documented cost is
   one chunk of wasted work, which is cheaper than a run that can never finish.

3. **A killed worker froze a run for an hour.** With Redis as the broker an unacknowledged
   message is redelivered only after `visibility_timeout`, and kombu's default is 3600
   seconds. Killing a worker mid-run stalled a 50,000-game run at 40,000 with no way to tell
   it from a hang. Now 600 seconds — which must stay above the 180-second hard task limit, or
   the broker hands a still-running chunk to a second worker and the run counts those games
   twice. Verified end to end: killed at 10,000/50,000, recovered to exactly 50,000.

4. **A failed chunk relied on machinery that may not fire.** `finalize_run` never runs when a
   header task raises; only the callback's errback does. Rather than bet a stuck run and a
   spent quota on that path, `simulate_chunk` now closes its own run out on exception. A
   deliberate failure (an empty deck) went to FAILED in 0.5s with a readable reason and the
   quota back.

5. **Releasing the concurrency slot after writing the status is a race.** For a moment the run
   reads as finished while still holding its slot, so cancelling and immediately starting
   another is refused. The slot is now released first, which inverts the window into the
   harmless direction.

## Two bugs that only a screenshot could find

- **`{# ... #}` does not span lines.** A three-line comment in `base.html` was not a comment:
  it rendered, in full, above the site header, on *every page of the application*. Every view
  test passed, djlint was clean, and the explanation of why htmx is self-hosted was sitting in
  the browser. `tests/test_css_build.py` now checks statically that every `{#` closes on its
  own line.
- **A cancelled run showed a 100% progress bar.** `progress_pct` treated every terminal state
  as complete, so "Cancelled — 5,000 of 10,000 games" sat above a full bar: a screen
  contradicting itself. Only `DONE` reads 100 now; a cancelled or failed run reports how far
  it actually got.

## The report compares the first seven, not the kept hand

The opening-hand table is the credibility centrepiece, and the first version of it could not
have earned any: comparing the *kept* hand against the hypergeometric disagrees by eleven
percentage points, because the mulligan rule throws the tails back. That is the heuristic
working, but on a page it reads as a broken simulation.

The engine now also records the lands in the very first seven, before any mulligan — the one
quantity in the whole simulation with an exact closed-form answer. The report shows that
against the hypergeometric (agreeing to 0.6pp over 10,000 games, live) and the kept
distribution separately, with no exact column, because no closed form describes it.

## Where things ended up

| | |
|---|---|
| `simulation/analysis.py` | `Histogram`, `chunk_seed`, `run_chunk`, `merge`, `as_json`/`from_json` |
| `simulations/engine/runner.py` | chunk planning; imports the engine, never Django |
| `simulations/tasks.py` | the chord, DB progress, cooperative cancel, self-closing failures |
| `simulations/services.py` | plan limits, quota, Redis concurrency slot, queue routing |
| `simulations/report.py` | the hypergeometric comparison, percentiles, milestones |
| `simulations/models.py` | `SimulationRun` — progress, result, gaps, coverage, measured rate |
| `templates/simulations/` | run page, polling fragment, report |
| `scripts/get_htmx.py` | htmx 2.0.10, pinned by digest, self-hosted |
| `scripts/demo_screens.py` | arranges the three run *states* and photographs them |

Measured on this machine: **110 µs per game per turn** (318 µs at 3 turns, 667 at 6, 1,104 at
10 — linear, as the turn loop implies), about 980 µs at 6 turns inside Docker. Chunks are
sized from that, with a 4× allowance for a slower production core, and re-sized from the rate
the previous run of the same deck actually measured.

## Still open

- **`game.black_available` is still a single colour**, and the report shows mana per turn
  without splitting it by colour. Fine for a mono-black deck, misleading for anything else.
- **The demo deck is a 214-card, 9-land collection export**, which makes the demo screenshots
  look strange while being perfectly honest arithmetic. A seed deck that is a legal Commander
  deck would photograph better.
