# RESUME HERE

Everything needed to continue is in files; nothing depends on chat history.

**State 2026-09-27: all code work is done. Start at "➡️ NEXT" below - the go-live with the
user, GO-LIVE.md from step 0.** One decision is still the user's: the Adaptive Pricing / GDPR
point (GO-LIVE, Legal section), needed only before `STRIPE_MANAGED_PAYMENTS` is switched on.

**2026-09-27: the repository goes public under the AGPL-3.0** (user's decision - the product is
the service, and a private repo would have needed GitHub Pro for branch protection plus registry
credentials in Jelastic). Built for it: `LICENSE`, `static/fonts/OFL.txt`, `SECURITY.md`, issue
forms (`.github/ISSUE_TEMPLATE/` - this is the feedback channel), `SOURCE_CODE_URL` → footer
"Report a problem" / "Source code (AGPL)" / "Email us", the terms state the licence (they used
to say the code belongs to the operator), the privacy policy names GitHub when linked, and CI
split into `checks.yml` (push to `dev`, PRs into `main`, called by `deploy-prod.yml`).
`tests/test_open_source.py`. GO-LIVE step 1 has the repo, bot and ruleset sequence.

**Domain: `goldfishlab.app` (bought by the user at Namecheap 2026-09-27; mail DNS options in GO-LIVE step 0b).** `.app` is HTTPS-only in every
browser (HSTS-preloaded TLD), and Jelastic's Let's Encrypt add-on does not install on a custom
Docker node - it needs a load balancer with a public IP in front of `cp`. **New GO-LIVE step 6b,
not built yet: plan the `bl` node with the user before the Jelastic import.** The engine and
`magic-project` are the user's own code, so the AGPL relicensing is clean.

## 2026-09-25: the pre-launch review is DONE - read it before the go-live

A full code review found two things that would have broken the launch outright (production would
never have derived a single card profile, and the Stripe webhook raised on every real event) plus
eighteen more. All twenty are fixed; **[pre-launch-review.md](pre-launch-review.md)** has the list,
the decisions the user made, and what happened. **1000 tests pass**, engine version 3, golden
snapshot unchanged. Traps 44-51 below are what it found.

The docker rebuild and the screenshot pass are done too (2026-09-25): every changed page was
looked at, and the pass found one more bug, B21 (trap 51: the 41 spell//land MDFCs tapped for
nothing), fixed with tests. **The next thing is the go-live, with the user** - GO-LIVE.md
carries the new steps (the `sqldb` password line, `--profiles`, the Stripe portal
configuration, a real-browser checkout check).

## ➡️ NEXT: the go-live, together with the user (GO-LIVE.md from step 0)

**2026-09-25 (after the compaction): Stripe Managed Payments is built** - see
[vat-managed-payments.md](vat-managed-payments.md), every code/legal/doc box ticked. The user asked
"doesn't Stripe handle VAT?": plain Stripe does not - EU and UK VAT are owed from the first sale.
Managed Payments makes Stripe ("Sold through Link") the merchant of record.

* `STRIPE_MANAGED_PAYMENTS` (default false; `.env.example`, jps field on `cp`) adds
  `managed_payments[enabled]` to Checkout; `services.MANAGED_PAYMENTS_FORBIDDEN` lists what Stripe
  refuses alongside it, and a test proves none of it is sent.
* Context processor `core.context_processors.payments` → `sold_through_link`: the terms, the
  privacy policy (Link as independent controller) and the plans page render both states; with it
  off, no page mentions Link.
* **Two things found while building, now in GO-LIVE:** (1) Managed Payments *adds* tax on top
  unless the prices are tax-inclusive - step 10, 2e.3; (2) it always shows EU visitors EUR on
  the checkout page, which weakens the GDPR "not targeted" argument - **a decision for the user
  before switching it on** (GO-LIVE, Legal section).
* Trap 52: **never set `STRIPE_MANAGED_PAYMENTS=true` before Stripe approved the account** -
  Checkout rejects the parameter and every purchase fails.

What the go-live needs from the user: their name and postal address (typed into the Jelastic
form, never the chat), the Infomaniak mailbox, the Stripe account and the Managed Payments
application.

## 2026-09-25 (later): the legal pages are written, and paid plans go live at launch

`/terms/`, `/privacy/` and a new `/imprint/` ("Legal notice") are final drafts, written from
sourced research into the Swiss DSG/UWG/OR, the EDPB scope guidelines, the WotC Fan Content
Policy, Scryfall's and Commander Spellbook's terms, and comparable sites. The operator's name,
address and contact email come from `LEGAL_OPERATOR_*` / `LEGAL_CONTACT_EMAIL` (production will
not boot without them, `core.E003`). The user decided: **operator = the user as a private
person; paid plans at launch** (EDHREC and 17lands charge under the FCP without any public WotC
permission). Checkout now requires the terms checkbox (`consent_collection`), which needs a terms
URL in Stripe's dashboard (GO-LIVE 10, 2c). A daily `core.housekeeping` task enforces the
retention periods the policy states. What was knowingly accepted - the FCP grey area, the GDPR
"not targeted" position, VAT - is in GO-LIVE.md's "Legal" section.

## Phase 8: the local half is DONE — 2026-09-22. Only the go-live is left.

**All twelve items below are finished.** Phase 8 is the only phase that touches production, and
its go-live sequence is gated on four things only the user can do (listed at the end of this
section). Everything else — hardening, legal, methodology — needed no credentials and no
infrastructure, so it was built first, so that the first push lands on something ready rather
than on something needing a week of follow-up commits while the site is already public.

## ➡️ THE NEXT THING TO DO IS [GO-LIVE.md](GO-LIVE.md)

**Open [GO-LIVE.md](GO-LIVE.md) and work through it with the user.** It is the launch runbook,
written for exactly this situation: a session that has just started and remembers nothing, plus
the one person who owns every account and every credential. Each step names who does it, how to
verify it, and what its failure looks like.

**Two things in it are blockers that must be settled before anything is deployed**, and both are
the user's to supply:

1. **The operator's name and postal address** (GO-LIVE step 0a) - typed into the Jelastic
   form, never into the repository. The pages themselves are written (2026-09-25).
2. **An Infomaniak Service Mail mailbox**, CHF 2.29/month - **decided 2026-09-22**, and the
   reasoning is in GO-LIVE.md step 0b. **Signup is broken without one** (trap 43), and
   production now *refuses to boot* rather than serving a 500. Brevo's and Postmark's free
   tiers were rejected deliberately: the contact mailbox has to exist anyway for the privacy
   policy, so the marginal cost of sending is zero, and any other sender would add a
   processor the policy does not name. The same mailbox is the contact address.

Do not start the sequence until those two are in hand.

---

**Nothing here is blocked and nothing is half-finished.** Read the *What actually happened*
section at the bottom of [phase-8-launch.md](phase-8-launch.md) for the account of this work;
the three bugs it found are the interesting part.

**What was done, and the reasoning that is worth keeping:**

- [x] **1. The CI lint gate.** `deploy-prod.yml` gated the deploy on `djlint templates/
      --check`, the *formatter*, which exits 1 on 11 templates. **CI was red before it had ever
      run** and nobody could know, because the repository has never been pushed. Changed to
      `--lint`; the 4 H021 inline-style hits are `ignore`d in `pyproject.toml` with the reason,
      which is that a server-rendered chart's width is a datum and Tailwind emits only classes
      it has scanned. `djlint templates/ --lint` is now 0 errors. **See trap 39.**
- [x] **2. Security headers.** CSP lives in **`base.py`, not `prod.py`** - a policy only
      switched on in production is a policy nobody has ever run, so this one is exercised by
      every request in development and every test in the suite. `style-src-attr
      'unsafe-inline'` is the line to understand: every chart here is a server-rendered div
      whose *width* is the datum, and **a nonce cannot rescue a style attribute** - CSP nonces
      apply to `<style>` and `<script>` elements only. Splitting the directive keeps an
      injected `<style>` block refused. `/admin/` is excluded; Django's own templates carry
      inline scripts and rewriting them is not a security improvement. HSTS (one year,
      subdomains, **preload off**) is in `prod.py` because it is meaningless without TLS.
      `manage.py check --deploy` is clean but for `W021`, which is the deliberate preload
      decision.
- [x] **3. Rate limiting.** `django-ratelimit` on the deck import (10/m), the collection
      import (5/m) and the simulation enqueue (20/m), all keyed on the user because all three
      are login-required. **Registration and password reset needed no code at all** - allauth
      ships `ACCOUNT_RATE_LIMITS` with `signup: 20/m/ip` and `reset_password: 20/m/ip,5/m/key`
      already on. What they needed was a *shared* cache; see trap 40. Both limiters end on
      `templates/429.html` through `handler403`/`handler429`, because `Ratelimited` subclasses
      `PermissionDenied` and would otherwise read as "that is not yours to open".
- [x] **4. Upload limits.** A size cap (1 MB) and the NUL check were already there from trap
      33. **The gap was the row count**, and it is the one that matters: a megabyte of
      `1 x\n` is ~250,000 rows, each looked up against a 35,568-row card table, and the byte
      limit is perfectly happy with that file. `MAX_UPLOAD_ROWS = 50_000`, counted in
      `decode()` so every parser inherits it. Content-type is deliberately **not** checked -
      it is the client's word, and a spreadsheet is already refused by the NUL check. Also
      trap 41: two Django settings that sound like upload limits and are not.
- [x] **5. `pip-audit`** in the CI lint job, against **`requirements.txt`** and not the
      environment - the environment also holds pytest, scipy and playwright, and failing a
      deploy over a test-only advisory that reaches no user is how a security gate gets
      deleted. It blocks the deploy on purpose; the escape hatch is `--ignore-vuln GHSA-xxxx`
      with the reason in the diff. **Audited clean on 2026-09-22**, all 13 production pins.
- [x] **6. Sentry**, env-gated in `prod.py`. The import sits *inside* the `if SENTRY_DSN`
      branch, so an installation with no DSN does not pay for the integrations either.
      `send_default_pii=False` - this application holds other people's decks and collections,
      and a crash report must not carry them out of the country the privacy policy names.
- [x] **7. Structured logging.** `core/logging.py`, twenty lines, no dependency. JSON in
      production only: the reader there is an aggregator, and in development it is a person
      watching `docker compose logs`, for whom JSON is strictly worse. The traceback goes in
      under its own key, which is the whole point - forty physical lines is forty unrelated
      entries to anything that reads by line.
- [x] **8. Legal pages.** `/terms/` and `/privacy/`, linked from the footer on every page
      alongside `/about/methodology/`. The WotC fan-content disclaimer was already in
      `base.html` and stays. *(Superseded 2026-09-25: the placeholders are gone, the operator
      comes from settings - see the top of this file.)* **Both carried a visible block** for
      the operator's legal name, address, contact address and governing law - these are
      careful drafts and **nobody has had a lawyer read them**. The privacy policy names the
      processors (Infomaniak, Stripe), says data is in Switzerland, and states that there are
      no analytics and no third-party scripts, which is true and is worth saying.
- [x] **9. Data export and deletion.** `accounts/privacy.py`. The export walks
      `_meta.concrete_fields` rather than a hand-written field list, so a column added in a
      later phase is covered without anybody remembering - and
      `test_every_user_owned_model_is_exported_or_deliberately_skipped` fails when a whole new
      user-owned **model** appears, which is the case a field walk cannot catch. Verified by
      deleting a line from `EXPORTED` and watching it go red. Stripe ids and the password hash
      are redacted: a downloadable file holding them is a file worth stealing.
      **Deletion is blocked while a paid subscription is live** - see trap 42, which is the
      only genuinely new design decision in this half of the phase.
- [x] **10. `/about/methodology`.** What is simulated and what is not, the hypergeometric
      verification, the full keep rule in prose, the two-number coverage score, what
      "assembled" means for a combo, and the citations. The mulligan section is **pinned to
      the engine by a test** rather than to the prose: `MAX_MULLIGANS` and
      `Game.cards_to_bottom` are asserted against the words on the page.
- [x] **11. Screenshots. 41 pages captured cleanly**, up from 32, and the pass earned its keep
      twice - **two bugs on every page of the site, neither findable by a test**. The signed-in
      header clipped at 390px so **"Sign out" ran off the edge of the page**, on every screen,
      since whenever the nav last grew. And the CSP was refusing **htmx's injected
      stylesheet**: htmx writes its two `.htmx-indicator` rules into a `<style>` element at
      load, `style-src 'self'` refuses it, and every page logged a violation while rendering
      perfectly - because nothing uses the class *yet*. The first loading spinner somebody
      added months later would simply not have worked. Both fixed; the second is pinned by
      three static tests in `tests/test_css_build.py`.
- [x] **12. Docs.** `phase-8-launch.md` expanded from an outline into an account, this file,
      the README, and `.env.example` (`SENTRY_DSN`, `DJANGO_HSTS_SECONDS`). And
      `tests/test_env_example.py`, which makes HABIT 5 a test instead of a memory: every
      variable `goldfishlab/settings` reads must be named in `.env.example`, one
      parametrised case each so a failure names the missing key.

**Verified on 2026-09-22 before any of this started**, so a regression had a baseline: **810
passed, 11 deselected** (= 821), ruff clean, djlint 4 H021. The docs and the repository agreed
with each other on every claim.

**After the local half: 914 passing** (the full suite, statistical validation included), ruff
clean, `djlint --lint` clean, `pip-audit` clean, 41 screenshots with no non-200 and **no console
errors anywhere**, `manage.py check --deploy` clean but for the deliberate `W021`.

**⚠️ Three new dependencies landed in this phase** (`django-csp`, `django-ratelimit`,
`sentry-sdk`), so the image needs **building, not restarting** — trap 32.
`docker compose build web worker worker_short`. Already done here; it matters on any other
machine.

### The four things only the user can do

The list above is finished, so these are now the next thing:

1. Create the GitHub repository under the main account.
2. Make the GHCR package **Public** after the first push — Jelastic cannot pull a private image
   and the only symptom is a node that never comes up.
3. Add the repository secret `JELASTIC_WEBHOOK_PROD`.
4. `cp jelastic.jps.example jelastic.jps`, fill in the username, import it in the console.

## Where the work stands

**Phases 0, 0b, 1, 2, 3, 4, 5, 5b and 6 are complete, so are Phase 7 §1 and §2, and so is
Phase 8's local half** (see the block at the top of this file). Nothing is outstanding and
nothing is half-finished. (**Blocking changed on 2026-09-22**: with Phase 8's local half done, the go-live is all that is left and *every* step of it needs the user - see the top of this file. The "nothing is blocked on the user" note below is the historical record of phases 1-7 and no longer describes today.) **Phase 6 §1 was finished on 2026-09-20** when the printing catalogue landed: 112,577 printings, the import ladder's rungs 1
and 3 both doing what they always claimed, and Cardmarket prices on the collection page. A deck stored in Postgres
simulates **identically** to the hand-written reference deck, the engine makes and spends all
five colours **and reports them separately**, a deck can be simulated in the background with a
progress bar and a report, every value the engine reads off a card can be inspected, sourced
and corrected by hand, there are **seven test decks**, a deck can be **played by hand** with
undo, redo and fork, the community tag catalogue that sat unread since Phase 1 now feeds the
engine, somebody can **pay** for a bigger plan, and a collection can be imported and every deck
checked against it from **any** CSV or TSV export. Combos are looked up against Commander
Spellbook **and timed by the simulator** - how often each one is together by the last turn, and,
for a combo the deck is one card short of, what adding that card would be worth.

**Phase 6 §2 closed on 2026-09-20, and not the way it was planned.** The plan was seven
format-specific parsers, each waiting on a real export file. The user objected that "having a
highly tuned hard coded one for each export seems very unstable and maintenance heavy", and the
repository agreed: the one parser written against a verified real export was *still* silently
wrong. So there is now **one** `TabularParser` that reads any delimited file by what its columns
mean, plus a mapping screen for the cases it cannot place on its own. **The ask for seven export
files is withdrawn** - it was never the small part. Read [importers.md](importers.md).

**No colour work is outstanding.** `game.black_available` was the last single-colour thing in
the engine and is now derived from a full per-colour pool.

| | |
|---|---|
| Location | the `goldfish-lab` folder in the user's Git directory — **not a git repo yet**; it becomes a **public** GitHub repo under the AGPL-3.0 at GO-LIVE step 1 (the agent pushes to `dev` as `Riddmaker-claude-bot`, `main` only via the user's PR approval) |
| Python | **3.13.3 — use `py -3.13`**; the bare `python` on this machine is 3.11 |
| Tests | **see pre-launch-review.md for the current count** (914 before the 2026-09-25 review), ruff clean, `djlint --lint` clean (H021 is `ignore`d with its reason — trap 39), `pip-audit` clean |
| Engine | `simulation/` vendored; its 4 test files are **byte-identical** to `magic-project/tests/` and must stay so |
| Running | `docker compose up` then <http://localhost:8000> (host 8000 to container **8080**) |
| Data | 35,568 cards, **112,577 printings**, 4,544 tags, 429,263 card-tag links, 35,568 profiles |
| Speed | **110 microseconds per game per turn** on this machine; ~980 at 6 turns inside Docker |

## First commands in a new session

```bash
cd goldfish-lab                                       # the project root
docker compose up -d                                  # web, worker, postgres 16, redis
./.venv/Scripts/python.exe -m pytest -m "not slow"    # fast loop
./.venv/Scripts/python.exe -m pytest                  # includes statistical validation
./.venv/Scripts/python.exe manage.py ingest_scryfall  # cards + tags; skips if unchanged
# Printings are OPT-IN and --kind all leaves them out - 78.8 MB, ~2 min, 19.8 MB peak.
./.venv/Scripts/python.exe manage.py ingest_scryfall --kind default_cards
./.venv/Scripts/python.exe manage.py seed_reference_deck   # the Chainer deck + annotations
./.venv/Scripts/python.exe manage.py seed_demo_deck        # a deck to look at (dev only)
./.venv/Scripts/python.exe manage.py seed_test_decks       # the seven shapes (dev only)
./.venv/Scripts/python.exe scripts/demo_screens.py         # 32 screenshots - and LOOK at them

# A NEW DEPENDENCY needs the image rebuilt, not just restarted - see trap 32.
docker compose build web worker worker_short && docker compose up -d

# Adding a template is a CSS change - Tailwind only emits classes it has
# scanned. tests/test_css_build.py fails when this is forgotten.
./.bin/tailwindcss -i assets/css/input.css -o static/css/main.css

# EDITING a template needs the web process restarted - see trap 13.
# Changing the ENGINE needs the workers restarted too - see trap 19.
docker compose restart web worker worker_short

# Arrange the three simulation states and photograph every screen. Generates
# the demo password internally and never prints it.
./.venv/Scripts/python.exe scripts/demo_screens.py --out <scratch dir>
```

If a page looks unstyled in a browser: **hard-reload (Ctrl+Shift+R)**.

**`.data/` is a gitignored local cache of the Scryfall bulk files.** `default-cards.jsonl.gz`
(78.8 MB) is already there, so a re-ingest or a fixture rebuild needs no download:
`--source .data/default-cards.jsonl.gz`. Regenerating only the printings fixture, which is what
you want when prices have moved but no deck has changed, is
`py -3.13 scripts/build_printing_fixture.py .data/default-cards.jsonl.gz` — it reads the
committed card sample and never rewrites it.

## What to do next

**Phase 7 §1 and §2 are both done (2026-09-21).** Combos are looked up per deck through
`combos/spellbook.py` and cached - **no bulk mirror was built**, and the measurement that decided
that is in [phase-7-combos.md](phase-7-combos.md). Then §2 gave them the number the whole product
rests on: **how long each combo takes to come together**, for the ones the deck holds *and* for
the ones it is one card short of.

The one-card-away case is the one with something to say about every deck - the reference deck
contains **zero** Spellbook combos and 76 it is one card from - and it is the only part that
costs anything: the missing card is not in the library to be drawn, so it is measured on a deck
of its own, the real one **plus** that card. That is rationed in `combos/measure.py`: at most half
the run's size again, at most three combos, never fewer than 1,000 games on one. A run too small
to pay for one is told so rather than shown a number made of three hundred games.

**`billing/quotas.py` still documents exactly four `check()` call sites.** The extra games are
recorded through `consume` where the run's own games are, because metering a cost is not gating
it.

**What it actually says, measured on the reference deck (10,000 games, 6 turns):** adding
Phyrexian Altar assembles Gravecrawler + Phyrexian Altar in **0.6%** of games by turn 6, half of
those by **turn 4** - against 0.24% for Vito and 0.06% for Sanguine Bond, both of which arrive on
turn 6 when they arrive at all. Small numbers, and they are the answer: two specific cards in a
99-card singleton deck is ~1.6% before anything has to be cast. **The page prints "under 1%"
rather than a rounded "0%"**, which would read as *never* beside a combo that did come together;
found by looking at the rendered page, not by a test. The table is in
[phase-7-combos.md](phase-7-combos.md).

**§3 and §4 need a Mistral API key nobody has yet**, so they are last and may not happen at all.
§3 (LLM tagging for cards no pattern matched) is the one worth having; §4 (AI prose) is
explicitly not the pitch - read the phase document before anybody leads with it.

**Phase 8 was the realistic next move and its local half is now done** - see the in-progress
block at the top of this file, which is the current state of play. **Go-live happens once, in
Phase 8** - settled, below - and it has not happened: what remains is the seven-step sequence in
[phase-8-launch.md](phase-8-launch.md), every step of which needs the user.

### Nothing was blocked on the user, through Phase 7 (historical)

**The seven-export-files ask is withdrawn, and this is the note that says so** - it stood in
three documents for two phases, so somebody re-reading them deserves to know it was closed
rather than forgotten.

It was closed by deleting the requirement. `decks/importers/tabular.py` reads any CSV or TSV by
what its columns mean; where a card name or a quantity cannot be placed, `decks:map` shows the
person their own file and asks. An unknown format now costs the user thirty seconds instead of
costing this project a phase. **Read [importers.md](importers.md)** - it keeps both the design
that was abandoned and the argument that killed it, because the second only convinces next to
the first.

If a format ever does need a parser of its own, the bar is **something structurally different
from "a table with headers"**. A different header *spelling* belongs in `columns.py`, where a
wrong guess matches nothing and says so.

### Downloads: already authorised, do not ask again

**~~Permission to download the two bulk files.~~ GRANTED 2026-09-20: "yes you can download
both".** The authorisation is recorded here rather than in a chat nobody can read afterwards,
and **one of the two is now done**:

- ~~`default_cards` from Scryfall.~~ **DONE 2026-09-20. Phase 6 §1 is finished.** 78.8 MB,
  **112,577 printings** - and note that every document in this repo previously said "~430,000,
  roughly twelve times the card table", which was `all_cards`, a different and much larger
  file. The real figure is **3.2x the card table and 43 MB on disk**, less than half what the
  card table costs. See trap 35.
- ~~`variants.json` from Commander Spellbook.~~ **MEASURED 2026-09-20, and probably not worth
  downloading at all.** It is **656.8 MB with no gzip offered**, and **70% of every record is
  Scryfall image URLs for cards whose images this database already has**. Meanwhile the
  backend's `find-my-combos/` endpoint answers a whole deck in **37 KiB**, gzipped, and already
  exists. The measurement is written up in [phase-7-combos.md](phase-7-combos.md) §1 — read it
  before writing an ingest. Trap 35 paid for itself a second time.

⚠️ **Cardmarket's website returns 403 to automated requests.** Only the S3 downloads work:
`downloads.s3.cardmarket.com/productCatalog/...`. Paid for once during the deck research; do
not rediscover it. Scryfall's own CDN has never needed more than a User-Agent.

### Phase 7, now that §1 and §2 are shut

- ~~**§1 Commander Spellbook and §2 turns-to-assemble.**~~ **Both done 2026-09-21.** The API is
  public and needs no credentials. §2 is the differentiator the whole product rests on -
  everyone else can say a deck *contains* a combo; only a simulator can say how often it comes
  together, and by which turn. The measured numbers are above.
- ~~**§1's data-volume decision**, the `variants.json` bulk mirror.~~ **Measured and declined**:
  656.8 MB with no gzip, 70% of it image URLs for cards already in the catalogue. The
  deck-scoped endpoint answers a whole deck in 78 KiB. Trap 35 paid for itself a second time.
- **§3 (LLM tagging) and §4 (AI prose) need an API key.** The provider is decided:
  **Mistral**, chosen 2026-09-20 because this is a European project and it keeps card text and
  user decks on EU infrastructure. Nothing reads a key today; `.env.example` carries a
  commented `MISTRAL_API_KEY` saying so. When there is one: an `env()` line in
  `goldfishlab/settings/base.py` beside the Stripe keys, the value in `.env`, never in the
  repository. §4 is explicitly not the pitch and not what justifies the price - read the phase
  document before anybody is tempted to lead with it.

**Left over from Phase 5b**, all three written up at the bottom of
[phase-5b-effects.md](phase-5b-effects.md):

- **A tutor's destination is not editable.** `tutor_count` is, so a wrong tutor can be switched
  off with a 0; correcting *where* it searches needs the admin. The zone comes from a community
  tag that is right far more often than not.
- **`tutor-to-top` is not in the offline fixture.** No sampled card carries it, so that path is
  covered by a unit test over `derive()` rather than end to end. Five `tutor-*` restriction tags
  are in the same position, listed with their live card counts in
  `tests/test_cards_effects.py::VERIFIED_UPSTREAM_ONLY`.
- **`goldfish_castable` is still not derived**, and should stay that way - see the settled
  decisions.

**Left over from Phase 5**, and all three are honest gaps rather than bugs - the full list is
at the bottom of [phase-5-playtest.md](phase-5-playtest.md):

- **A tutor in a playtest picks for the player.** `apply(game, action, policy)` takes the
  player's answer, and nothing asks them yet, so `policy=None` takes the most expensive card.
  That is precisely the quiet judgement this product is not supposed to make. The seam is
  built; the screen is not.
- **`MoveCard` and `TapPermanent` have no buttons.** Both work and both are tested. Moving a
  card between zones by hand is the last piece of "full manual control" and it is a design
  problem, not a plumbing one.
- **Mana empties at the turn boundary, not at each step.** Within a turn the pool floats by
  design, because the engine taps everything once per main phase.

**Two things still deliberately undone** - see the bottom of [test-decks.md](test-decks.md):

- **The demo deck is still the 214-card collection export** - "some black cards I liked", in
  the user's words. It stays because it is the only fixture that exercises the *import* path
  with a real export.
- **`simulation/cards.py` overstates `PER_CONTROLLED`.** Its docstring names Nykthos and
  Gaea's Cradle as examples; the rule counts *lands carrying a subtype*, so devotion and
  creature-count scaling are not expressible in it. Both cards sit in the `unmodellable` test
  deck being honestly reported as unresolved.

## Phase 6 in one screen

Full account in [phase-6-billing.md](phase-6-billing.md). The short form:

- **Hosted Checkout and hosted Portal.** No card number reaches this application, and there is
  no cancel button of our own - a second place that can end a subscription is a second place
  that can disagree with Stripe about whether it ended.
- **The webhook is the only writer of status or plan.** The success page grants nothing and
  says so; a test types its URL to prove it.
- **Idempotency before any work**: record the event id, bail if it existed. Stripe retries until
  it gets a 2xx.
- **Event order is not guaranteed**, so nothing depends on it: the user id goes into
  `subscription_data.metadata` at checkout, which makes every later event self-identifying.
- **`past_due` keeps the plan and shows a banner.** Stripe retries a card for two weeks; the
  phase forbids leaving somebody on a paid plan *silently*, not keeping them there. One line in
  `quotas.plan_for` changes it if the user disagrees.
- **A plan with no `stripe_price_id` cannot be bought**, which is every plan until somebody
  makes the products in Stripe - so the whole integration was built and tested with no keys and
  no mocking.
- **The collection answers "can I build this?"** on its own page and on every deck page. Basic
  lands are counted separately, because "ten Swamps short" is not a number anybody acts on. The
  commander is counted, because it is not a `DeckCard` and forgetting it would be the one
  useless version of the answer.
- **Printings are resolved as well as recorded**, since 2026-09-20. `cards.Printing` holds
  112,577 of them. **Measuring the file first overturned the plan**: it is 3.2x the card table
  and 43 MB, not the "twelvefold" three documents claimed, and `(set_code, collector_number)`
  turned out to be globally unique with zero collisions - which is the only reason rung 3 can
  be an exact match instead of a refusal. Rung 1 went from **62 of 214 rows to 214 of 214** on
  the user's real export.
- **The printing FK is nullable forever and the raw strings are kept beside it.** A text list
  names no printing, and `default_cards` is opt-in - `--kind all` excludes it, and the test
  suite runs *without* printings by default so the degraded path is exercised every run. The
  kept strings paid out inside the phase: migration `collection/0002` backfilled all 214 rows,
  so nobody re-uploads a collection to get prices.
- **Prices: EUR only, per row, dated, never totalled, and “—” is never 0.00.** "Nobody
  publishes a price for this" and "this card is worthless" are different sentences. The user's
  own framing: *"pricing is not the USP of our application."*
- One security bug, found by a test that uploaded nonsense: trap 33.
- Four traps, 32, 33, 35 and 36.

## Phase 5b in one screen

Full account in [phase-5b-effects.md](phase-5b-effects.md). The short form:

- **The coverage score is two numbers now.** `simulations/gaps.py` classifies a gap by its
  **field**: `reading` is what the engine could not read (ours), `judgement` is what only the
  deck's author can answer (`priority`, `accelerant`). Measured first: **161 of 217 gaps were
  `priority`**, so one number was mostly reporting how much hand-annotation a deck had. The demo
  deck went from "31.1% coverage" to "86.9% read, 35.0% judged", which is the same deck
  described usefully.
- **Classifying by field, not by row, is what made it free.** Gaps stored on finished runs and
  open sessions split correctly with **no migration** - a stored run is a record of what the
  engine saw, and back-filling a column would have been editing history.
- **`draw-engine` was the hole.** The report's "A card-advantage engine in play" row could only
  ever fire for the hand-written fixture deck; for every imported deck it was structurally zero.
  One mapping line fixed it. `repeatable-card-advantage` would have had better recall and was
  rejected: **recall is cheap here, precision is not.**
- **Tutors: zone from the tag, count from the text, gap if either is missing.** 442 tutors that
  used to do literally nothing now work; 372 that search to the battlefield are correctly
  refused. The deriver reproduces the reference deck's three hand-written tutors field for field
  without having been shown them.
- **The phase made the reference deck look worse on purpose.** 78.6% -> 74.3%, because three
  cards have an additional casting cost the engine has never paid and never mentioned. The
  golden snapshot is untouched - the engine's behaviour did not change, only its honesty.
- **Printed keywords are shown and never counted.** A goldfish has no opponent; flying and
  deathtouch change no number. They are on the board because a person reads them.
- Two traps, 30 and 31.

## Phase 5 in one screen

Full account at the end of [phase-5-playtest.md](phase-5-playtest.md). The short form:

- `simulation/actions.py` is the seam: thirteen frozen `Action` records, `apply()` and
  `legal_actions()`. `agent.take_turn` **decides** and the actions **do**, so a human and the
  agent run the same code. The golden snapshot was untouched by the split, which is the
  evidence the refactor was honest.
- An action names a card by its **index in a zone**, never by object - which is what lets it be
  a Postgres row. `Card` is a frozen dataclass, so two Swamps compare equal and this is exactly
  what `hand.remove(card)` already did.
- `simulation/serial.py` gets engine objects through JSON by **dataclass reflection**, so a new
  field on `Card` needs no maintenance here. `tests/test_serial.py` builds a `Card` with every
  field set away from its default, and a second test fails if that card stops being exhaustive.
- A session is **a seed plus a list of actions**. State is a replay; `cached_state` is only a
  shortcut and everything that edits the list clears it. Undo marks rows undone, redo un-marks,
  fork copies rows - all three fall out rather than being features.
- **The deck is snapshotted, not referenced.** Annotations can be edited while a session is
  open, and re-deriving would change a card under the player's hands mid-game.
- `playtest/forms.py` is the trust boundary: `kind` picks from `actions.BY_KIND` and only the
  fields that action declares are read, so a stray form key cannot become a keyword argument.
- Three bugs, now traps 27-29.

## The test decks in one screen

Full account in [test-decks.md](test-decks.md). The short form:

- `decks/fixtures.py` holds seven `DeckShape` records **as pure data with no Django import**,
  because `scripts/build_card_fixtures.py` (no settings module), `decks/seeding.py` and the
  test suite all read them. One definition, three readers.
- Each shape carries a `catches` field saying which class of bug it exists to find. A deck
  nobody can say what it would catch is a deck nobody will maintain.
- `decks/seeding.py` builds a shape into the database and is used by **both** the tests and
  `manage.py seed_test_decks`, so a photographed deck and an asserted deck are the same deck.
  Its `resolve()` replaced a near-duplicate in `seed_reference_deck`.
- Seeded annotations are **always deck-scoped**, never built-in, and a test asserts it.
- `build_card_fixtures.py` reads `decks.fixtures.card_names()`, so a card added to a shape
  reaches the offline sample without a second list to remember.
- **The `unmodellable` shape closes Phase 4's open verification item**: 0% coverage, two named
  limitations over eleven of twelve cards, asserted against the *rendered page* because
  "unmissable" is a claim about what somebody sees.
- Three bugs found, all now pinned - see traps 23-25.

## Phase 4 in one screen

Full account at the bottom of [phase-4-honesty.md](phase-4-honesty.md). The short form:

- `adapter.readings(deck)` returns the engine `Card` the simulation will actually use, and
  `provenance.py` labels every field with where it came from. **One function, two readers** -
  the panel cannot drift from the simulation, because both are `_card_from`.
- Five sources, plus one that is not a source: where nobody has said anything the row reads
  **"Nobody - the engine's own rule"**. That row is the point of the screen.
- The editor exposes 8 of `ALLOWED_KEYS`' 35 keys. **Blank means "no opinion", never zero**,
  so every boolean is a three-state select and never a checkbox; and the form is **never**
  pre-filled from the derived reading, only from the annotation at that scope.
- `annotations.apply` is whole-form (an editable key it is not given is *removed*);
  `annotations.patch` changes only the keys named. Single-purpose screens use `patch` - see
  trap 20.
- `blindspots.py` names three limitations - opponent-dependent cards, unresolved mana sources,
  tag false positives - and **reports rather than decides**, with a link to the field that
  answers each one.
- A stored run is never re-read when an annotation changes. The report says the inputs have
  moved on and offers a re-run.
- Colour, carried over from Phase 2: `game.mana_by_color` is the whole pool and
  `black_available` is a read-only property over it. `analysis` counts a histogram per colour;
  the report shows only the colours the deck actually made. `black` and `mana_B` are the same
  numbers on purpose - the golden snapshot is keyed on `black`.

## Phase 3 in one screen

Full account at the bottom of [phase-3-simulation.md](phase-3-simulation.md). The short form:

- Per-game lists became `analysis.Histogram`: O(distinct values), mergeable by addition, JSON
  round-tripping, percentiles for free. **Not** a `Counter` subclass, because the vendored
  `tests/test_statistics.py` does `sum(stats["lands"])` and `sum(Counter)` adds the keys -
  a plausible, silently wrong number under a green test.
- `chunk_seed` uses blake2b, never `hash()` (randomised per process). A chunked run is
  deliberately **not** bit-identical to a monolithic one of the same seed; it replays exactly
  and agrees with one within sampling error. Both halves are asserted.
- `simulations/engine/runner.py` plans the chunks. It imports the engine and never Django, so
  `adapter.py` stays the only module that sees both worlds.
- A chord of `simulate_chunk` tasks, then `finalize_run`. **Progress is written to the
  database**, never read from the broker: `F()` increments survive concurrent chunks and
  worker restarts.
- **Cancellation is purely cooperative** - a flag, and nothing else. See trap 14.
- Quota is debited at enqueue and refunded once on cancel or failure. The concurrency slot is
  a Redis `INCR` with a TTL, released *before* the terminal status is written.
- The report puts the **first seven cards** against the exact hypergeometric (`math.comb`, so
  scipy stays out of the 128 MiB production image) and shows the kept hand separately, with
  no exact column, because no closed form describes it.
- htmx 2.0.10, self-hosted and digest-pinned. There is no hand-written JavaScript anywhere;
  charts are server-rendered divs with widths.

## Phase 2 in one screen

Full account at the bottom of [phase-2-engine.md](phase-2-engine.md). The short form:

- `simulation/fixtures/chainer.py` holds the deck; `simulation/cards.py` holds the model.
  A PEP 562 `__getattr__` shim keeps the old imports working, so the vendored tests never
  changed.
- Cabal Coffers, Urborg and Crypt Ghast are no longer names the code knows. They are
  instances of `PER_CONTROLLED`, `TYPE_ADDING` and `DOUBLE_SUBTYPE`.
- `agent.PRIORITY` (45 names) and `game.ACCELERANTS` (5 names) moved onto `Card`.
- `simulations/engine/adapter.py` is the **only** module that imports both Django and
  `simulation/`. `simulations.CardAnnotation` carries the judgements no derivation can
  supply, in three scopes: built-in → user → deck, narrowest wins.
- **Colour** lives in `simulation/manacost.py`: `COLORS`, `SUBTYPE_COLORS` (swamp→B,
  forest→G, island→U, mountain→R, plains→W) and `normalised()`, which is the one place a
  colour name becomes a letter. `ManaAbility.produces` is a colour→amount mapping;
  `Card.cost` is an optional full `ManaCost` and `Card.mana_cost` is what everything reads.
  A source that could make one of several colours is **not** modelled — the pool counts
  mana rather than holding sources. The adapter picks the deck's colour and reports a gap.
- `manage.py seed_reference_deck` writes the hand-annotated deck into the database, which
  is what makes the round-trip provable.

**The parity harness is the point.** `tests/test_engine_parity.py` checks the engine against
a snapshot of the *original* engine; `scripts/build_engine_golden.py` regenerates it from
the sibling repo. **Never regenerate that snapshot to make a red test go green** — it is the
only evidence that the engine still does what it used to.

**One intended behaviour change:** `draw_engine` now counts Liliana, Dreadhorde General,
which the old hardcoded name list had missed. Pinned and explained in the parity test.

## Traps already paid for — do not rediscover them

1. **`DEBUG=True` made ingestion peak at 95 MB.** Django keeps every SQL statement; a
   1,000-row bulk insert is a megabyte of SQL. `reset_queries()` after each flush → 16 MB.
2. **Double-faced cards keep cost and text on `card_faces`**, leaving the top level empty.
3. **`Gleemax` has mana value 1,000,000** and costs `{1000000}` — it overflows a smallint.
4. **The known-id set must hold strings, not `UUID` objects**, or every tag row is skipped.
5. **The Bash tool collapses backslash escapes** in heredocs — it turned `\b` in a regex
   into a literal 0x08 byte. Write Python source with the Write/Edit tools.
6. **A new template needs a CSS rebuild**, or it renders unstyled at HTTP 200.
   `tests/test_css_build.py` now catches it.
7. **The commander is not a `DeckCard`.** Anything that filters on deck membership misses
   it — that bug cost the commander its priority and 93 casts per 400 games.
8. **Upkeep trigger order changes the numbers.** Arena and Dark Confidant both draw.
   Descending mana value, then name.
9. **`"blue".upper()[0]` is `"B"`.** The pool keyed colours on the first letter, so blue
   mana was silently counted as black. Colour names go through `normalised()`, never
   through a slice.
10. **A hybrid pip in `DerivedProfile.pips` is stored under *every* colour it could be
    paid with.** Right as "may be paid with", wrong as "must be". Parse the printed cost
    instead — `manacost.parse` keeps the choice as a choice.
11. **Quoted oracle text is an ability the card *gives away*.** "Enchanted land has
    `{T}: Add one mana of any color`" is the land's ability, not the aura's, and
    `produced_mana` lists the colour either way — 385 cards read as mana sources they are
    not. The deriver strips quoted spans before looking for an `Add` clause.
12. **"Add one mana of any color **that a land an opponent controls could produce**"**
    opens with the same words as Arcane Signet and makes *nothing* in a goldfish. Nine
    cards. Matching on the opening words alone overstates the deck.
13. **Django 5.2 caches compiled templates even with `DEBUG=True`**, and gunicorn's
    `--reload` watches only `.py` files. A template edit therefore has **no effect** until
    `docker compose restart web`. Cost a whole screenshot pass: the fix was on disk and
    correct, and the browser kept serving the old page.
14. **Never `revoke()` a chord's header tasks.** A revoked chunk makes the whole chord raise
    `TaskRevokedError` instead of calling its callback, so the run never closes - it sits at
    RUNNING for ever with the quota spent. Cancellation is the flag, and only the flag.
15. **Redis redelivers a killed worker's task only after `visibility_timeout`**, and kombu's
    default is one hour. `CELERY_BROKER_TRANSPORT_OPTIONS` now sets 600s, which must stay
    above the 180s hard task limit - otherwise a still-running chunk is handed to a second
    worker and its games are counted twice.
16. **Routing a dispatcher task does not route the tasks it creates.** Chunks inherit the
    *default* queue, so a long run's chunks landed on `sim_short` and would have blocked
    every small run. Every signature carries an explicit queue now.
17. **`{# ... #}` is a single-line comment.** Across lines it is not a comment at all and
    renders into the page - in `base.html`, on every page of the site, invisible to every
    test and to djlint. `tests/test_css_build.py` checks this statically now.
18. **`quotas.plan_for(user)` reads a cached related object.** Changing a plan limit in the
    database is invisible to a `user` instance loaded earlier in the same process. Re-fetch
    the user before checking a limit you have only just changed.
19. **`docker compose restart web` does not restart the workers, and the workers run the
    engine.** A report came back missing a whole table because the run had been computed by a
    worker still holding the previous `simulation/analysis.py` in memory. Any change under
    `simulation/` needs `docker compose restart web worker worker_short`.
20. **A partial `annotations.apply` deletes what it was not given.** It has whole-form
    semantics by design. The casting-order screen called it with one key and would have wiped
    every role and mana judgement on first save; `annotations.patch` is the single-key form.
21. **`mana_produces: {}` does not stop a basic land making mana.** A land with a coloured
    **land type** taps as a basic land and its own mana ability goes unused - that is
    `mana.available_mana`, not a bug. So the land types are editable too, and
    `Reading.taps_for` reports the land-type path, or the panel would say "nothing" while the
    simulation went on making black mana.
22. **Built-in annotations are present in most tests.** `tests/test_engine_adapter.py` seeds
    the reference deck module-scoped and outside a transaction, so its built-ins survive for
    the rest of the session. A test that means "nobody has said" has to clear them, and one
    that counts rows has to scope to the owner - six tests passed alone and failed in the
    suite over this.
23. **`adapter._subtypes` only ever produced `swamp`.** Every basic that was not a Swamp
    reached the engine with no land types at all, leaving four constants in
    `simulation/cards.py` defined and impossible to trigger from a database deck. The colour
    survived - a Forest taps green through its own mana ability - but everything that
    *counts* types did not: Coffers' scaling, Crypt Ghast's doubling, Urborg and Yavimaya.
24. **Coverage could be negative.** `convert()` took `cards_total` from deck entries but
    recorded gaps for the commander, which is not a `DeckCard` (trap 7). A two-card deck with
    an unreadable commander reported **-50%**. The commander now counts toward its own
    denominator, which moved the reference deck from 78.3% to 78.6%.
25. **A land-heavy deck mulligans MORE, not less.** `game.keepable` throws back a hand of
    **0 or 6+ lands**, so 45 lands mulligans into flood far more often than 20 mulligans into
    nothing. Guessed wrong once while writing a test; asserted now in the right direction.
26. **The fixture builder was dragging in art series.** After the two deliberate non-cards,
    `art_series` rows fell through to the name match - and an art series shares its card's
    name, so a third of the sample was padding. Non-card layouts now never reach the match.
27. **Mana used to float between turns.** `BeginTurn` did not empty the pool. The agent could
    never show it - it reopens the pool at every main phase, so a surviving pool was overwritten
    before anything could spend it. A human stepping through phases kept turn one's mana on turn
    three. **Found by looking at a screenshot**, not by a test.
28. **`load_game` restored the generator one shuffle too early.** `Game.__init__` shuffles, so
    setting the random state and *then* constructing the game advances it again. Invisible in a
    goldfish, which shuffles only for its opening hand - and unreproducible once it bit.
29. **Two phases no game could ever be in.** The Phase 5 outline listed all seven; the engine
    resolves untap, the turn's draw and the upkeep triggers together in `Game.begin_turn`, and
    in that order, which is not real Magic's. `PHASES` is now the five that exist, and a test
    walks a turn asserting every one is reachable. Unreachable constants are how four of the
    engine's five land types stayed broken for six phases (trap 23).
30. **A sentence separator in a card-text regex must not require a space.** Oracle text puts a
    bare newline between abilities, with no space after it, so a pattern anchored on
    "period-or-newline **followed by a space**" never matches a sentence that begins a line.
    `_ENTERS_TAPPED` carried that anchor from Phase 1 and silently missed every card shaped like
    Ebondeath, whose "enters tapped" sentence sits on its own line under Flash and Flying. Allow
    optional whitespace instead. Found while writing a test for a *different* pattern, which is
    the argument for a parametrised list of shapes rather than one example per rule.
31. **A derived value nobody can switch off is a judgement made on the user's behalf.** Phase 5b
    derived tutors for 442 cards, and until `tutor_count` was added to the editable judgements
    there was no way for a person to say "that is not a tutor". A `0` means exactly that. Any
    future phase that derives a new field owes the same escape hatch in the same commit - the
    honesty layer is not a thing you add afterwards.

32. **A new dependency needs the image rebuilt, not restarted.** `pip install stripe` into the
    venv makes the tests pass and leaves every container without it, and `docker compose restart`
    does not notice. The web container then dies on import and the next screenshot run fails with
    `ERR_CONNECTION_REFUSED`, which looks like a network problem and is not.
    `docker compose build web worker worker_short` first.
33. **`latin-1` decodes anything, so "it decoded" is not "it is text".** `decks/services.decode`
    accepted a JPEG, which parsed into rows of control characters and died inside the resolver
    with `PostgreSQL text fields cannot contain NUL (0x00) bytes` - **a 500 on a public upload
    form that a stranger can cause on purpose.** Present since Phase 1; found in Phase 6 by a
    test that uploaded nonsense expecting a form error. A NUL byte is refused at the decode
    boundary now.

34. **A "verified" header is worth nothing when the header is a user preference.** The Phase 1
    Archidekt parser keyed on exact column names because that header had been read off a real
    export - and Archidekt lets you tick which columns to include. An export with Quantity
    unticked sniffed at 0.75, parsed happily, and read **28 Swamps as 1**. Delver Lens is worse:
    the user picks the fields *and their order*. Map concepts to alias sets and refuse when a
    concept that may not be invented is absent. See [importers.md](importers.md).

35. **Measure the bulk file before designing a table for it - and check which file you measured.**
    Three phase documents, the README and two model docstrings all said `default_cards` was
    "~430,000 printings, roughly twelve times the card table", and the collection feature shipped
    without printings partly on the strength of that number. It is **112,581 printings, 3.2x the
    card table, 43 MB on disk** - less than half what the card table costs. The 430,000 figure
    belongs to `all_cards`, a different bulk file that is 393 MB because it carries every
    language of every printing. **A cost nobody has measured is not a reason to defer a feature**,
    and the same measurement pass is what discovered that `(set_code, collector_number)` is
    globally unique, which is the fact the whole of rung 3 rests on.
36. **A test that needs a table empty must make it empty, not assume it.** Two ways this bites in
    one afternoon. `tests/test_engine_adapter.py` seeds the reference deck module-scoped and
    outside a transaction, so **the cards table is never empty in a full run** - a printing test
    passed alone and failed in the suite (trap 22 again, from a new direction). And the obvious
    fix, `OracleCard.objects.all().delete()`, raises `ProtectedError`, because
    `DeckCard.oracle_card` is `PROTECT`. **Build the premise instead**: the test now ingests one
    printing whose oracle id belongs to no card, which is what it always meant.
37. **A hypothetical cannot be measured in the real thing's games.** Phase 7 §2 wanted "add this
    card and the combo assembles by turn 6 in X% of games". The card is not in the library, so
    there is no game in which it is drawn, so there is nothing to count - and every cheap way
    round it is an approximation dressed as a measurement. The honest answer costs a second deck
    and a second sample, which then has to be rationed, which then means some runs are too small
    to buy one. **Two thirds of `combos/measure.py` is that chain**, and all of it was forced by
    refusing to fudge the first link.
38. **Add the key only when the feature is used.** `analysis.as_json` omits `combos` entirely
    rather than writing an empty dict, and that one line is what keeps `engine_golden.json` -
    the only evidence that the engine still behaves as it used to - from needing regeneration.
    A result that grows a field whenever a feature is *compiled in* makes every stored run and
    every snapshot a thing that has to be migrated.
39. **A CI gate that has never run is not a gate, and `djlint --check` is not the linter.**
    `--check` is the *formatter's* dry run: it exits 1 when a file would be reformatted, which is
    a whitespace opinion, not a fault. `--lint` is the rule checker. The deploy workflow had been
    gating on `--check` since Phase 0, so **the very first push to the very first repository would
    have failed lint** on 11 templates and ~96 lines of cosmetic whitespace - and no one could
    have known, because the repository does not exist yet and CI has never executed. Found in
    Phase 8 by running the workflow's own commands by hand before trusting them.
    **Every command a workflow runs should be run locally once before the workflow is relied on**,
    and a phase that says "push, and CI runs lint and tests" is exactly where that is cheapest.
40. **A rate limit counted in a per-process cache is a rate limit multiplied by the worker
    count.** Django's default `CACHES` backend is `LocMemCache`, which lives in one process.
    allauth has been rate-limiting login, signup and password reset **since Phase 0** through
    `django.core.cache` - and gunicorn runs several workers, so `signup: 20/m/ip` was really
    20 per minute *per worker*, and the two Celery workers held a third and fourth copy.
    Nothing warns about this: every test passes, because a test suite is one process. The fix
    is one `CACHES` block pointing at Redis, and the reason it took a phase to notice is that
    **the control was somebody else's code and looked like it was already working.**
    Redis **database 1**, not 0: 0 is the Celery broker, and a `FLUSHDB` while debugging a
    queue should not quietly reset every rate limit at the same moment.
41. **Two Django settings are named as though they bound an upload, and neither does.**
    `DATA_UPLOAD_MAX_MEMORY_SIZE` bounds the request body **excluding files** -
    `MultiPartParser` accumulates `num_bytes_read` only for `item_type == FIELD` and never for
    `FILE`, which is worth reading in `django/http/multipartparser.py` rather than believing.
    `FILE_UPLOAD_MAX_MEMORY_SIZE` is a *spool threshold*: above it Django writes the upload to
    a temporary file, so it moves the cost rather than refusing it. Setting either one and
    calling the upload bounded is the kind of mistake that survives a review, because the
    names read like a limit. The ceilings that actually refuse a file are this project's own
    `MAX_UPLOAD_BYTES` and `MAX_UPLOAD_ROWS`, both checked in `decode()` before anything is
    parsed. **Found while writing the comment that claimed the opposite.**
42. **Deleting an account does not cancel a Stripe subscription, and every FK cascades.**
    `user.delete()` is a complete erasure - which is exactly the problem. `billing.Subscription`
    cascades with everything else, so the local record of a live subscription disappears while
    **Stripe goes on charging the card every month**, and there is no longer an account anybody
    can sign in to in order to stop it. The person has done the responsible thing and been
    punished for it.
    This is the settled "Stripe hosts the cancellation" decision arriving somewhere it was not
    designed for, and the fix is to keep obeying it rather than to add a second canceller:
    `privacy.deletion_blockers` refuses while a paid subscription is `active` or `past_due` and
    sends the person to the portal. **Checked on the server, not only in the template** - a
    page that hides a button is not a rule. The general shape is worth remembering: *a cascade
    is only a complete deletion when nothing outside the database is holding a reference.*
43. **Mandatory email verification plus no mail configuration is a broken signup, and eight
    phases did not notice.** `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` has been in `base.py`
    since Phase 0, so allauth sends a confirmation during signup. **No settings module ever
    configured email for production.** Django's default backend is SMTP to `localhost:25`, which
    does not exist in the container, so the signup form would have answered **HTTP 500 to the
    first stranger who tried** - and with nobody able to create an account, the whole site is
    inert.
    It is invisible in every place it would have been cheap to catch: `dev.py` sets the console
    backend, and **Django's test runner substitutes locmem**, so the entire suite passes and the
    account tests happily assert that signup works. That is the general shape worth remembering:
    **a test harness that helpfully substitutes a working stub for a missing dependency is a
    test harness that cannot see the dependency is missing.** The same was true of the cache in
    trap 40, and is true of anything else Django swaps out under test.
    Fixed in two parts: `prod.py` reads the SMTP settings from the environment, and
    `core/checks.py` raises `core.E001` so `manage.py check` **fails**. `start.sh` runs
    `migrate`, which runs system checks, so the container now **refuses to start** rather than
    coming up with a signup nobody can complete. An installation that genuinely sends no mail
    says so with `DJANGO_EMAIL_BACKEND=...console.EmailBackend`. `core.E002` catches
    `EMAIL_USE_TLS` and `EMAIL_USE_SSL` both being on, which Django reports only at send time,
    naming neither.

44. **Nothing outside the test suite ever derived a card profile.** `cards.profiles.rebuild()`
    was called by nineteen test files and by no command, signal or migration, so the go-live's
    only catalogue step - `ingest_scryfall` - would have left production with cards, tags and **no
    `DerivedProfile` at all**. The adapter then builds every card as a colourless artifact: no
    lands, no mana, every hand mulliganed - while every deck page, which reads `OracleCard`, looked
    perfectly healthy. The local database only worked because profiles had once been built by hand.
    The shape: **a step the test fixtures always perform is a step nobody notices production
    skips.** `ingest_scryfall` now rebuilds profiles whenever cards or tags changed or any card
    lacks one, and `--profiles` re-derives alone after a deriver change. Found in the 2026-09-25
    review.
45. **"Exactly what the real one does" was not what the real one does.** The Stripe tests replaced
    `stripe_api.construct_event` with `json.loads`, on the reasoning that the real function returns
    the parsed body once the HMAC checks out. In `stripe==15` it returns a `stripe.Event`, and a
    `StripeObject` is deliberately **not** a dict: `.get()` raises `AttributeError`, `dict(event)`
    raises `TypeError`. Every real webhook would have been a 500, Stripe would have retried for three
    days and disabled the endpoint, and nobody who paid would ever have got their plan. The fix
    verifies with `WebhookSignature.verify_header` and parses the body itself - and **passes the
    tolerance explicitly, because `verify_header` defaults it to `None`, which switches the replay
    window off.** The tests now sign payloads for real with `generate_signature_header`. Read the
    return type off the source, not only the signature (HABIT 4).
46. **The commander was also one of the 99.** Every export lists the commander as a row, and the
    importer wrote that row as a `DeckCard` *and* set `Deck.commander` from it: "101 cards" on a
    correct 100-card deck, the commander shuffled into the library as well as waiting in the command
    zone, and a collection check asking for two copies. Trap 7 is the commander *missing* from
    entries; this is the mirror image. One copy now leaves the list for the command zone at import
    and at "Set commander", and migration `decks/0004` fixes existing decks.
47. **allauth does not use `RATELIMIT_IP_META_KEY`.** Trap 40 made both limiters count in Redis;
    what nobody checked is *which address* allauth counts. It has its own IP lookup, which reads
    `X-Forwarded-For` only when `ALLAUTH_TRUSTED_PROXY_COUNT` is set and otherwise uses
    `REMOTE_ADDR` - the proxy. Every visitor shared one bucket, so ten mistyped passwords a minute
    from anybody locked the whole site out of signing in. One setting, `TRUSTED_PROXY_COUNT`
    (`DJANGO_TRUSTED_PROXY_COUNT`), now feeds both, and a test asserts they agree.
48. **Mana creatures never made mana, and the panel said they did.** `Game.mana()` read lands and
    rocks only. The adapter gave Llanowar Elves a flat ability, so the tune page said "taps for
    1 G" and no gap was recorded - exactly the drift `adapter.readings` exists to rule out. Every
    green deck was under-read with a clean coverage score. Creatures with a flat ability are
    sources now; summoning sickness needs no rule, because the pool opens once at the start of the
    main phase.
49. **The deriver read the half of a mana ability after "Add".** So every cost was free and every
    source permanent: a Signet (`{1}, {T}: Add {U}{B}`) made two of one colour for nothing, Mana
    Vault and the Monoliths made three every turn, Lotus Petal was a rock, and "several abilities of
    different sizes" took the **largest** (Cabal Ritual 5) - a guess, in the module whose rule is
    that it never guesses. Each `Add` is now read with its cost, its colours and its conditions;
    `DerivedProfile.mana_produces` / `mana_activation` / `mana_untaps` carry the result, the engine
    pays the activation and holds a non-untapping source tapped (`Game.stays_tapped`), and a
    self-sacrificing artifact becomes a ritual. Engine version 3. Verified against real Oracle text
    from the local Scryfall cache - which now says "this artifact", not the card's name.
50. **CSP `form-action` is checked against redirects, not only against the form.** The checkout
    and portal buttons POST to our own views, which 302 to Stripe; Chrome and Safari apply
    `form-action` to every redirect a form submission follows (Firefox checks the first URL only).
    With `'self'` alone, paying was refused in two of three browsers - unfindable before launch,
    because no installation had ever had keys. `checkout.stripe.com` and `billing.stripe.com` are
    in the directive now, and GO-LIVE step 10 has a real-browser check.
51. **A modal double-faced "Sorcery // Land" is a land, not a spell, to the mana reader.** The
    type line holds both faces, so `"Sorcery" in type_line` was true for all 41 of them
    (Agadeem's Awakening, Shatterskull Smashing, the Final Fantasy Towns) and the land face's
    `{T}: Add` was dropped as "not a spell's own mana": lands that never tapped, with only a
    vague gap line to show for it. `_kind` already let "Land" win; `_mana_production` now agrees.
    Found on the screenshot pass, not by a test - the tune page listed Agadeem under "mana nobody
    has pinned down".
52. **`STRIPE_MANAGED_PAYMENTS=true` before Stripe approved the account breaks every purchase.**
    Checkout refuses `managed_payments[enabled]` on an account without Managed Payments, so the
    "Choose" button would answer with Stripe's error. It also flips the terms and privacy pages to
    "sold through Link", which would then be false. Switch it on only after the eligibility
    review, with tax-inclusive prices and the tax code on both products (GO-LIVE step 10, 2e).

## Decisions that are settled — do not re-litigate

- **`max_decks` means decks owned** (settled with the user 2026-09-25). Deleting a deck frees a
  slot; a new month does not hand out more. `quotas.DECKS_OWNED` is a live count, never recorded;
  `DECKS_CREATED` is still recorded, as history.
- **The first draw follows the multiplayer rule by default** (CR 103.8c: nobody skips it). The
  engine's `on_the_play=True` is kept as the 1-v-1 option and relabelled everywhere; stored runs
  and the golden snapshot are keyed on the flag, so the flag itself did not change.
- **A paying customer is never sent through Checkout again.** Checkout always creates a new
  subscription; plan changes happen in Stripe's portal (review finding B8).
- **Go-live happens ONCE, in Phase 8.** No step-wise deployment. Deployment artifacts
  stay current but are never executed early.
- **`simulation/` never imports Django.** Only `simulations/engine/adapter.py` sees both
  worlds. If a vendored test needs editing, the boundary leaked — fix the boundary.
- **Meter turns simulated, not iterations.** 10k games already gives about ±0.5pp at
  95% confidence, so selling iteration counts sells a number that does not improve the
  answer.
- **Combos come from Commander Spellbook lookup**, never from parsing card text.
- **A combo counts as assembled only when every card is in the zone the combo needs.** Not
  drawn, not "seen": on the battlefield, in the graveyard, in hand. Measuring "drawn" is easier
  and would inflate every number on the page, and nothing downstream would notice.
- **A hypothetical is stated, never hidden.** A one-card-away combo is measured on the deck
  **plus** that card - one card larger, nothing cut - and the page names the card. Choosing
  which card somebody's deck can spare is not a judgement this application makes.
- **AI is plumbing, not the pitch.** The simulation is the differentiator; LLM prose is
  commodity — six competitors give it away free.
- **Design flows one way:** `DESIGN.md` → `STYLEGUIDE.html` → `assets/css/input.css` →
  contrast test. Never edit a token first.
- **Always show a coverage score, and show it as two numbers.** The adapter reports it per deck
  (74.3% on the reference deck), every stored run keeps the gaps it was computed with, and the
  report shows both. A result must never imply the engine modelled more of a deck than it did.
  It is bounded: a score outside 0-100% is a bug, not a strong opinion - see trap 24. And it is
  **split**, because one number was answering two questions: **`reading`** is what the engine
  could not read and is ours to fix, **`judgement`** is what only the deck's author can answer.
  Three quarters of every gap in the application was the second kind. See `simulations/gaps.py`.
- **A test deck says what it catches.** Every shape in `decks/fixtures.py` carries a `catches`
  field in prose. An odd-looking fixture with no stated purpose is the first thing somebody
  deletes, and these decks are deliberately odd.
- **The agent decides, the actions do.** `simulation/actions.py` is the only implementation of
  what playing a card means, and `agent.take_turn` goes through it like the board does. A second
  implementation of "may I cast this" is the two-sources-of-truth bug that a JavaScript state
  machine was rejected to avoid; putting one back in Python would be no better.
- **A playtest is a seed plus a list of actions.** The state is a replay. `cached_state` is only
  ever a shortcut, and everything that edits the list clears it - a cache that can disagree with
  a replay is worse than no cache, because the disagreement is what a player reports as "it
  forgot my turn" and it does not reproduce.
- **An open session keeps the deck it started with.** Annotations can be edited mid-game, and
  re-deriving would change a card under the player's hands. Same reasoning as a stored
  `SimulationRun` keeping the gaps it was computed with.
- **Legality is advisory, everywhere.** `legal_actions` reports so the UI can highlight;
  `apply` forbids nothing that is mechanically possible. Full manual control is what paper
  gives you, and the alternative is an infinite rules-engine rabbit hole.
- **A seeded annotation is deck-scoped, never built-in.** A built-in row applies to every deck
  of every user, so a fixture writing one would change the reference deck's numbers from
  another test file.
- **Progress lives in the database, not in the Celery result backend.** It survives worker
  restarts, shows up in the admin, and is a real audit trail. The result backend is for
  terminal state and exceptions only.
- **No hand-written JavaScript.** htmx attributes and response headers (`HX-Refresh`) only,
  self-hosted and digest-pinned. Charts are server-rendered divs with widths.
- **The deriver never guesses.** Unresolvable means null plus `needs_review`. Judgement
  calls belong in `CardAnnotation`, not in a longer regex.
- **A tag is a category and never a number.** `tutor-to-graveyard` establishes which zone Buried
  Alive searches to and can never establish that it finds three cards; the printed text does that
  and knows nothing about what the card is for. Where only one half arrives, the answer is a gap.
  Defaulting a count to 1 is right on Demonic Tutor, wrong on every Buried Alive, and looks
  entirely correct on the page.
- **Precision beats recall in every tag mapping.** A missed role under-reports a deck; an
  invented one puts a milestone on a report that never happened. `draw-engine` was chosen over
  `repeatable-card-advantage` on exactly this, measured against the reference deck's author.
- **`goldfish_castable` is never derived from a tag.** 5,460 cards wear `spot-removal` and the
  temptation is real. `simulations/blindspots.py` exists because of it: the detectors report a
  named card, a reason and a link to the field, and they never set it. A detector that quietly
  decided would be hiding the user's judgement inside a number.
- **Printed keywords are display, never simulation.** A goldfish has no opponent, so flying,
  deathtouch, menace and trample change nothing that is measured. Show them where a person reads
  their own card; never let a phase be sold on them.
- **The blind-spot detectors report; they never decide.** A detector that quietly set a field
  because the text mentioned an opponent would be making the user's judgement for them and
  hiding it in a number. They produce a named card, a reason, and a link to the field.
- **A judgement is never invented on the user's behalf.** Blank means "no opinion" and removes
  the override; the annotation form is never pre-filled from the derived reading; a built-in
  is credited to the application and not to the user.
- **A stored run is never re-read.** It is a record of what the engine saw. When the
  annotations move on, the report says so and offers a re-run.
- **An unrecognised deck format asks the user**, rather than importing the wrong cards. This is
  also why the seven remaining importers are **not** written: a parser built against a guessed
  header does not fail, it imports the wrong cards silently. Each needs a real export file.
- **A deck-list column is a concept, not a header string.** Archidekt and Delver Lens let the
  user choose which columns to export, so there is no such thing as "the Archidekt header".
  `decks/importers/columns.py` maps concepts to alias sets; parsers never read a header
  directly. **Aliases are for reading a file we were told to read; sniffing decides whether we
  were told.** The two stay separate, so knowing what `Count` means never becomes a claim that
  a file is a Moxfield export.
- **An importer refuses rather than defaults.** A missing quantity column raises
  `MissingColumn` and names the fix. Defaulting to 1 is the same class of mistake as a deriver
  guessing a mana amount: right often enough to look correct, and silent when it is not.
- **The webhook is the only writer of subscription status or plan.** Never the success redirect,
  which anybody can reach by typing the URL and which can arrive before the money has settled.
  Record the event id first and bail if it existed; Stripe retries until it gets a 2xx.
- **Stripe hosts the payment and the cancellation.** No card data, no PCI scope, no dunning
  code, and exactly one place that can end a subscription.
- **A collection reports basic lands separately.** "Ten Swamps short" is arithmetic, not advice.
  Same rule as the blind-spot detectors: report both numbers, decide neither.
- **The printing catalogue is optional, and everything works without it.** `--kind all` excludes
  `default_cards`, `CollectionItem.printing` is nullable forever, and the test suite runs with no
  printings by default so the degraded path is covered every run rather than at deploy time.
  A command in the boot path must not pull 78.8 MB because somebody upgraded.
- **Counts are computed on cards; prices are read from printings.** The "can I build this?"
  answer must give the same number on an installation that never ingested `default_cards`. A
  test nulls every printing on a collection and asserts the counts are unchanged.
- **Prices are reported, never totalled, and never invented.** EUR only, because Cardmarket is
  the European market and a second currency answers a question nobody asked. Per row and dated
  from the bulk file's own timestamp, because a trend price is one market's snapshot of one day
  and a headline total would read as a valuation. **A missing price is `None` and renders as
  “—”, never `0.00`** - etched foils have no EUR price published anywhere, MTGO-only sets have
  no paper price, and neither is the same claim as "worthless". Settled with the user:
  *"pricing is not the USP of our application."*
- **A collection item keeps the strings it was imported with, beside the printing it resolved
  to.** They are not redundant: they are what a later ingest re-resolves against, which is how
  every existing row got a printing without anybody re-uploading anything.
- **Tests never hit the network.** Committed fixtures, regenerated by
  `scripts/build_card_fixtures.py`.
- **Screenshots are part of finishing any UI phase.**
- **`accelerant` and `priority` are fields, not derived rules.** They are deck-author
  judgements; deriving them mechanically changed the keep rate and every number under it.

## Where the rest of the context lives

| Question | File |
|---|---|
| Product decisions, German, source of truth | `../../../magic-project/instructions.md` section "Teil 2" |
| Full architectural plan | `~/.claude/plans/let-s-take-this-a-hidden-conway.md` |
| Per-phase detail | `docs/phases/phase-*.md` |
| Why the importers are built the way they are | `docs/phases/importers.md` |
| Why the engine is built this way | `../../../magic-project/simulation.md`, `simulation-recherche.md` |
| Design rules | `../../DESIGN.md` and `../../STYLEGUIDE.html` |
| Conventions and gotchas | `../../../magic-project/CLAUDE.md` |
