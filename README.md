# Goldfish Lab

**It actually plays your deck.**

Most Magic deck tools look at your list and guess. Goldfish Lab shuffles it, keeps or mulligans a
real opening hand, plays land drops, casts spells in a sensible order and does it again thousands
of times — then reports what actually happened.

The simulation engine is validated against the exact hypergeometric distribution, so its numbers
agree with closed-form mathematics wherever closed-form mathematics applies.

> Unofficial Fan Content permitted under the Wizards of the Coast Fan Content Policy.
> Not approved or endorsed by Wizards. Portions of the materials used are property of
> Wizards of the Coast. ©Wizards of the Coast LLC.

---

## Status

**Phases 0 (Foundations), 0b (Design system), 1 (Card database & deck import), 2 (engine generalization), 3 (background simulation & reports), 4 (the honesty layer), 5 (interactive playtest) and 5b (the effect catalogue) are complete, and 6 (collection & billing) are complete, and Phase 7 §1 (combo lookup) and §2
(turns-to-assemble) both landed on 2026-09-21:** a deck stored in Postgres simulates identically to the hand-written reference deck, the engine makes, spends and now reports all five colours separately, a deck can be simulated in the background — thousands of games spread across workers — with a progress bar that needs no hand-written JavaScript and a report whose opening-hand distribution visibly matches the exact hypergeometric, and every value the engine reads off a card can be inspected, traced to its source and corrected by hand. A deck can also be **played by hand**, one game at a time, with undo, redo and fork. Seven **test decks** cover shapes the reference deck does not have — five colours, no commander, twenty lands, forty-five, and one made entirely of cards the engine cannot read.

A collection export answers the question people actually ask before sleeving a deck up — **can I build this?** — on the deck's own page, counting basic lands separately because "ten Swamps short" is arithmetic rather than advice. Paid plans go through hosted Stripe Checkout, so no card number reaches this application and the signed webhook is the only thing that may change what somebody is entitled to.

Combos come from Commander Spellbook's curated list, and then the simulator does the thing only a
simulator can: it says **how long each one takes to come together**. Not "your deck contains this"
but "it is together by turn six in four percent of games" — and, for a combo you are one card
short of, "add Phyrexian Altar and it would be". A card counts only when it is in the zone the
combo needs it in, the added card is named on the page because the simulated deck was one card
larger than the real one, and a combo that needs *any creature with persist* gets the requirement
in words and no percentage at all.

The coverage score every result carries is **two numbers, not one**: how much of the deck the engine could *read*, which is this application's limit to fix, and how much of it somebody has *decided*, which only the deck's author can answer. They were one number until they were measured, and three quarters of every gap turned out to be the second kind.
See [docs/phases/](docs/phases/README.md) for how it was built, phase by phase.

**Phase 8's local half landed on 2026-09-22**: a Content-Security-Policy on every response, rate limits that are counted somewhere shared rather than per worker, an upload row ceiling, `pip-audit` in CI, env-gated Sentry, JSON logging in production, terms and a privacy policy, a data export and an account deletion that both actually work, and an [`/about/methodology`](docs/phases/phase-8-launch.md) page saying plainly what is simulated and what is not.

**Go-live itself has not happened, and happens once, in Phase 8.** Everything until then runs on `docker compose` locally. Product decisions live in the sibling `magic-project` repository's
`instructions.md`, section "Teil 2".

## Requirements

- **Python 3.13** (`py -3.13` on Windows; the bare `python` may resolve to an older version)
- Docker + Docker Compose
- No Node.js — Tailwind runs from a pinned standalone binary

## Local development

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
.venv/Scripts/python scripts/bootstrap_env.py                 # writes .env with generated secrets

# Build the CSS once (or add --watch while working on templates).
# Adding a template is a CSS change: Tailwind emits only the classes it finds
# by scanning source files, so a new template without a rebuild renders
# unstyled while still answering HTTP 200. tests/test_css_build.py catches it.
.venv/Scripts/python scripts/get_tailwind.py
.bin/tailwindcss -i assets/css/input.css -o static/css/main.css --watch

# htmx is committed, so this is only needed when upgrading it.
.venv/Scripts/python scripts/get_htmx.py

docker compose up
```

**Editing a template needs `docker compose restart web`.** Django 5.2 caches compiled templates
even with `DEBUG=True`, and gunicorn's `--reload` only watches `.py` files — so a template fix
sits on disk, correct, while the browser keeps showing the old page.

**Changing the engine needs `docker compose restart web worker worker_short`.** The workers are
what run `simulation/`, and they do not reload. A report once came back missing a whole table
because the run had been computed by a worker still holding the previous version in memory.

Then open <http://localhost:8000>. Host port 8000 maps to container port **8080**.

## Loading the card catalogue

```bash
# Both Scryfall bulk files. Skips the download entirely when nothing changed
# upstream, so it is safe to run on boot or from a nightly task.
.venv/Scripts/python manage.py ingest_scryfall

.venv/Scripts/python manage.py ingest_scryfall --kind oracle_tags --force --measure
```

Roughly 35,500 cards and 4,500 Oracle tags, rolled up to ~430,000 card-tag links. The
whole run streams: peak memory stays around 16 MB regardless of file size, which is
checked by a test because the production cloudlet has 128 MiB in total.

**The same command derives the card profiles the engine reads**, whenever cards or tags
changed or any card has none. After a change to `cards/profiles.py` with no new bulk file,
re-derive them on their own:

```bash
.venv/Scripts/python manage.py ingest_scryfall --profiles
```

Then, for a deck to look at locally:

```bash
.venv/Scripts/python manage.py seed_demo_deck    # development only; refuses if DEBUG is off
```

## Playing a deck by hand

Open a deck and press **Deal a hand**. There is no management command for this on purpose: a
playtest is a session somebody is sitting in front of, not a batch job.

The simulation answers "what usually happens". The playtest is for the turn you want to look at
yourself. It is **server-authoritative and event-sourced**: a session stores the shuffle's seed
and the list of actions taken, and the board is that list replayed. Undo marks actions undone,
redo un-marks them, and **fork** copies the first *n* into a new session — so "what if I had
kept the land instead" costs one INSERT per action rather than a second engine.

Every action is a real `<form method="post">`. htmx swaps the board in place when it is
available, and the identical POST redirects and re-renders when it is not; every action is
tested both ways. Legality is **advisory** — the board highlights what the floating mana can pay
for and forbids nothing, because full manual control is what a paper playtest gives you.

The agent and the player drive the same code. `simulation/actions.py` is the only implementation
of what casting a spell means, and `agent.take_turn` chooses from the same `legal_actions` a
person clicks.

## Your collection, and paid plans

```bash
# Nothing to configure. Sign in, open Collection, upload any CSV or TSV
# collection export - Moxfield, Deckbox, ManaBox, Archidekt, anything.
# Columns are read by what they mean; where that is not enough you are
# shown your own file and asked. seed_demo_deck imports one automatically.
```

Every deck then carries an answer to **can I build this?** — on the collection page for all of
them at once, and on each deck's own page. Basic lands are counted separately, because "ten
Swamps short" is arithmetic rather than advice, and the commander is counted, because it is not
a `DeckCard` and forgetting it would give the one useless version of the answer.

It can also tell one printing from another, which needs a second bulk file:

```bash
py manage.py ingest_scryfall --kind default_cards   # 78.8 MB, ~2 minutes
```

**That is opt-in and `--kind all` deliberately leaves it out**, because a command in the boot
path should not pull 78.8 MB because somebody upgraded. Everything works without it — the
collection page says which half of itself is switched off, and deck answers are counted on
cards and never on printings, so they do not change either way.

With it loaded there are 112,581 printings against 35,568 cards. That is **3.2x** the card
table and 43 MB on disk, which is less than half what the card table costs — the "roughly
twelve times" that earlier drafts of this file and three phase documents quoted was the size of
`all_cards`, a different and much larger file. It buys the exact printing on every import
(rung 1 went from 62 of 214 rows on a real export to 214 of 214), the set/collector-number rung
that had been a documented hole since Phase 1, and Cardmarket trend prices in euros — shown per
row with the date they were true, never totalled into a valuation, and shown as “—” rather than
as zero on the 18 rows nobody publishes a price for.

**Payments are off unless `STRIPE_SECRET_KEY` is set**, which is every environment but
production. The plans page then says so and offers nothing to buy; the limits are still
enforced and everyone is on Free. That is also how the whole integration is tested: no keys, no
mocking, no network.

With keys set, upgrading goes through **hosted Stripe Checkout** and everything afterwards —
card, invoices, cancelling — through the **hosted Customer Portal**. No card number reaches this
application, and the signed webhook at `/billing/webhook/` is the only thing that may change
what somebody is entitled to. Never the success redirect: anybody can reach that by typing the
URL.

```bash
# Point Stripe's CLI at the local endpoint. It prints its OWN webhook secret,
# which is not the dashboard's - mixing them up fails every signature check
# with a message that looks like an attack.
stripe listen --forward-to localhost:8000/billing/webhook/
```

## The reference deck, and the odd ones

```bash
# Write the hand-annotated reference deck into the database.
.venv/Scripts/python manage.py seed_reference_deck

# And seven decks with shapes it deliberately does not have (development only).
.venv/Scripts/python manage.py seed_test_decks
```

The reference deck is the fixed point: real, hand-annotated card by card, and the thing
`adapter.deck_definition(deck) == chainer.DECK` is asserted against. It is also **one shape**,
and a tidy one — one colour, a commander, a sane land count, a human judgement on every card
that needs one. A whole class of bug cannot be seen from it.

The test decks in `decks/fixtures.py` are the missing shapes, and each one records in prose
which class of bug it exists to catch. Within an hour of the first one existing they had found
three: four of the engine's five basic land types unreachable from any stored deck, a coverage
score that could go negative, and — not a bug, but a property worth knowing — that a
forty-five land deck mulligans *more* than a twenty land deck, because the keep rule throws
back a flooded hand just as readily as an empty one.

The engine lives in `simulation/` and **never imports Django**; the only module that sees
both worlds is `simulations/engine/adapter.py`. That separation is what lets the four
vendored engine test files stay byte-identical to their originals, which in turn is what
makes "all the old tests still pass" mean anything.

## Verification

```bash
.venv/Scripts/python -m pytest -m "not slow"    # fast loop
.venv/Scripts/python -m pytest                  # includes the statistical validation
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m djlint templates/ --lint
.venv/Scripts/python -m pip_audit -r requirements.txt   # production pins only

# Visual check against the running stack. Screenshots go to a scratch
# directory, never into the repository. Add --email/--password to include the
# deck screens, which need a session.
.venv/Scripts/python scripts/screenshots.py --out /tmp/shots
.venv/Scripts/python scripts/screenshots.py --out /tmp/shots \
    --email demo@goldfishlab.test --password "$GOLDFISH_DEMO_PASSWORD" 
```

`scripts/screenshots.py` fails on any non-200 response **or any browser console error**. That is
deliberate: during Phase 0 it caught two bugs that HTTP 200 checks and a green test suite both
missed — static files not being served at all, and the account pages not inheriting the site
layout.

### Design system

```bash
.venv/Scripts/python scripts/build_styleguide.py   # regenerate STYLEGUIDE.html
```

`STYLEGUIDE.html` is **generated** from `assets/css/input.css` and from the role pairings in
`tests/test_design_tokens.py`, so it cannot misreport a hex value or a contrast ratio. A test
fails if it goes stale. Rules live in `DESIGN.md`, and decisions flow one way:
`DESIGN.md` → `STYLEGUIDE.html` → `input.css` → contrast test.

A PDF is produced on demand and **never committed**:

```bash
msedge --headless --print-to-pdf=styleguide.pdf STYLEGUIDE.html
```

## Architecture

| Path | Responsibility |
|---|---|
| `simulation/` | **The engine. Pure Python; never imports Django.** |
| `goldfishlab/` | Settings (split base/dev/prod), URLs, Celery app |
| `core/` | Landing page, `/styleguide/`, `/healthz/`, `/about/methodology/`, terms, privacy, design-token reader, the rate-limit address rule and the production log formatter |
| `accounts/` | Custom user identified by email, plus the data export and account deletion (`privacy.py`) |
| `cards/` | The Scryfall catalogue, role tags and the derived card profiles |
| `decks/` | Decks, the importer and the per-deck analysis screens |
| `simulations/` | Runs: the engine boundary, Celery tasks, reports, annotations |
| `combos/` | Commander Spellbook lookups, cached per deck. **No bulk mirror** — see `combos/spellbook.py` |
| `billing/` | Plans, subscriptions, usage records, quota enforcement |
| `DESIGN.md` | **Normative design rules** |
| `STYLEGUIDE.html` | Binding rendered reference, standalone, print CSS included |
| `assets/css/input.css` | **Design-token source of truth** |
| `tests/` | Engine tests (vendored, byte-identical) + Django tests |

**The one architectural rule:** `simulation/` never imports Django, and the Django apps never
import `simulation` except through `simulations/engine/`. `adapter.py` there is the only module
in the codebase that imports both worlds; `runner.py` beside it imports the engine and no
Django, which is what lets the Celery tasks drive a simulation without widening the rule. That
boundary is what keeps the engine's vendored tests meaningful — they are byte-identical to their
originals. If one of them needs editing, the boundary has leaked: fix the boundary, not the test.

## Running a simulation

A run is a chord of chunks. Each chunk plays a few thousand games and returns a few kilobytes
of histograms, and one callback merges them — so memory is flat in the number of games, a lost
worker costs one chunk, and progress is real rather than estimated.

Two things are worth knowing before changing any of it:

- **Progress is written to the database, not read from the Celery result backend.** It survives
  worker restarts, shows up in the admin, and gives the run page something cheap to poll.
- **Cancellation is cooperative: a flag, and nothing else.** Revoking a chord's chunks makes
  the chord raise instead of calling its callback, which leaves the run at RUNNING for ever
  with the user's quota spent. The documented cost of doing it properly is one chunk of wasted
  work.

There are two queues and two workers. `sim_short` (runs up to 10,000 games) has a worker of its
own that never touches long work, because one 100,000-game run would otherwise sit in front of
every small one — and no quota system fixes that.

The progress bar is htmx polling an HTML fragment that carries its own trigger; when the run
finishes, the fragment comes back without it. **There is no hand-written JavaScript anywhere in
this application**, and the charts are server-rendered divs with widths.

## Checking the numbers

The part of this application that is hardest to copy is not the simulation. It is that the
simulation says what it could not model, and lets you fix it.

Every deck has a **"what the engine reads"** page listing each card as the engine will actually
play it, with the source of every value: a Scryfall field, the community tagger, a regular
expression over English prose, a built-in default, something you said — or **nobody**, where the
engine simply applied its own rule. That last one is the point. "Cast priority 38" looks like a
decision until it says the engine made it up out of the mana value.

Three rules hold the layer together, and all three are about not inventing a judgement:

- **Blank means "no opinion", and removes the override.** Every boolean is a three-state choice,
  never a checkbox: a checkbox cannot say "nobody has said" and would quietly save "no" for
  every card it was never asked about.
- **The form is never pre-filled from the derived reading.** The derived reading sits *beside*
  it. Otherwise saving a page once would freeze a community tag into a human judgement, and the
  provenance panel would credit somebody with an opinion they never had.
- **A stored run is never re-read.** It is a record of what the engine saw at the time. When the
  annotations move on, the report says so and offers a re-run.

Alongside it, the **"what this simulation does not model"** panel names the limitations that
more games will never fix — cards that need an opponent, mana sources nobody has pinned down,
community roles worth a second look — and links each one to the field that answers it. Those
detectors report; they never decide. One that quietly set a field because the card text
mentioned an opponent would be making your judgement for you and hiding it inside a number.

## Deployment

Production runs on Infomaniak's Jelastic Cloud in Switzerland: a web node, two Celery worker
nodes (long and short runs), PostgreSQL and Redis, with a Cloudflare Tunnel as the only way in -
no node is reachable from the internet directly. Every node runs a supported base system
(Debian 12 or Alpine 3); Jelastic refuses others as custom containers.

Work happens on `dev`; `checks.yml` runs lint, the dependency audit and the full test suite on
every push there and on every pull request into `main`, which only merges green and approved.
A merge to `main` runs the same checks again, builds the image, pushes it to GHCR tagged with the
commit, signs it, and redeploys through the Jelastic API - a deploy is always an immutable commit
tag, and a rollback is the same path pointed at an older one.

The operator's runbook, the production manifest and the restore procedure are kept outside this
repository on purpose.

## Feedback and security

Problems and ideas: the issue forms ("Report a problem" in the site's footer). A simulation that
looks wrong is the most useful report there is - the form asks for the engine version and seed
printed under the results, which reproduce the run exactly. Security problems never go in an
issue: see [SECURITY.md](SECURITY.md).

## Licence

Goldfish Lab is free software under the **GNU Affero General Public License v3.0** - see
[LICENSE](LICENSE). You may run, study, change and share it; if you run a changed copy as a
website, you must offer its users your source (`SOURCE_CODE_URL` points the footer at it). The
licence does not cover the name "Goldfish Lab".

Bundled third-party parts keep their own licences: EB Garamond (SIL Open Font License 1.1,
[static/fonts/OFL.txt](static/fonts/OFL.txt)), htmx (Zero-Clause BSD), Tailwind CSS (MIT, build
time only). Card data and images come from Scryfall and remain Wizards of the Coast's; Goldfish
Lab is unofficial Fan Content permitted under the Fan Content Policy. Combo data comes from
Commander Spellbook.
