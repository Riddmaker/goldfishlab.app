# Phase documents — MTG Deck Lab

One file per phase. Each phase is independently shippable and testable, and leaves the
application more useful than it was before.

These documents are **English**, because they belong to the application (which is English-only),
not to the German deck-purchase project. They move to the new repository together with
`simulation/` once it exists.

The German summary and all product decisions live in `../../instructions.md`, section
**"Teil 2: Die Applikation"**. That file stays the source of truth for decisions;
these files describe execution.

> The operator's working notes, the launch runbook and the pre-launch review are kept outside
> the public repository (2026-09-27); this folder holds how the application was built.

## Status

| Phase | File | Status |
|---|---|---|
| 0 | [phase-0-foundations.md](phase-0-foundations.md) | **Complete** (2026-09-17) |
| 0b | [phase-0b-design.md](phase-0b-design.md) | **Complete** (2026-09-17) |
| 1 | [phase-1-card-data.md](phase-1-card-data.md) | **Complete** (2026-09-18) |
| 2 | [phase-2-engine.md](phase-2-engine.md) | **Complete** (2026-09-18), colour included |
| 3 | [phase-3-simulation.md](phase-3-simulation.md) | **Complete** (2026-09-18); `simulation/` translated as part of it |
| 4 | [phase-4-honesty.md](phase-4-honesty.md) | **Complete** (2026-09-18); the carried-forward colour item went in with it |
| — | [test-decks.md](test-decks.md) | **Complete** (2026-09-18); seven deck shapes, and the three bugs they found |
| 5 | [phase-5-playtest.md](phase-5-playtest.md) | **Complete** (2026-09-18); the engine split, and the three bugs it surfaced |
| 5b | [phase-5b-effects.md](phase-5b-effects.md) | **Complete** (2026-09-18); the coverage score split in two, and 442 tutors that used to do nothing |
| 6 | [phase-6-billing.md](phase-6-billing.md) | **Complete** (2026-09-20): collection, printing catalogue and prices, Stripe, and §2 closed by replacing seven planned parsers with one generic CSV importer — [importers.md](importers.md) |
| 7 | [phase-7-combos.md](phase-7-combos.md) | **§1 and §2 complete** (2026-09-21): combos looked up per deck and cached, **no bulk mirror** — 656.8 MB, no gzip, 70% image URLs; then **turns-to-assemble** for both the combos a deck holds and the ones it is one card short of, the second measured on the deck **plus** that card. §3 and §4 need an API key nobody has |
| 8 | phase-8-launch.md (operator-only) | **Local half complete (2026-09-22); live at goldfishlab.app since 2026-09-28** (a few checks and Stripe still open) — **the only phase that touches production** |
| 9 | [phase-9-ux-overhaul.md](phase-9-ux-overhaul.md) | **In progress** (2026-09-28): decks only, fewer words, more pictures, a guest trial. Batch A (favicon, header, import page) done |

**Update the status column when a phase starts and when it finishes.** Phases 0–4 are detailed
and each ends with a "What actually happened" account of where the plan met reality; phases
4–8 are outlines that get expanded when they are reached, so that the detail reflects what was
actually learned in the phases before them.

## Rules that apply to every phase

1. **`simulation/` never imports Django.** `cards/` and `decks/` never import `simulation`.
   The only module that sees both worlds is `simulations/engine/adapter.py`.
2. **The existing 65 tests stay green, unedited.** Needing to change a test is the signal that
   the boundary above has leaked.
3. **Every phase ends with a working `docker compose up`** and, for phases with a UI,
   Playwright screenshots verified by eye. Screenshots go to the scratchpad, never into the repo.
   This is not ceremony: in Phase 0 the screenshot check caught two bugs that HTTP 200 responses
   and a fully green test suite both missed - static files not being served at all under gunicorn,
   and the account pages not inheriting the site layout. Run `scripts/screenshots.py` and LOOK at
   the output.
4. **Secrets never get hardcoded** and never get echoed into the terminal. `.env` is gitignored,
   `.env.example` is kept in sync in the same commit.
5. **`ruff check` and `djlint` clean** before a phase is called done.
6. **No step-wise go-live.** Every phase up to and including 7 runs on `docker compose` locally.
   The GitHub repository, the GHCR package and the Jelastic environment are created **once**, in
   Phase 8. Deployment artifacts are kept current throughout, but never executed early.
7. **Design decisions flow one way:** `DESIGN.md` -> `STYLEGUIDE.html` -> `assets/css/input.css`.
   Never edit a token first and document it afterwards.
