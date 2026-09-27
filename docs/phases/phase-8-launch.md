# Phase 8 — Launch hardening AND the single go-live

**Duration:** ~1 week
**Status:** **the local half is complete (2026-09-22).** Everything that needs no credentials
and no infrastructure is built, tested and photographed. **The go-live sequence has not been
run** and is gated on four user actions — see *What actually happened*, at the bottom, which is
the part worth reading.

**This is the only phase that touches production.** By explicit decision, everything before it
runs on `docker compose` locally; the repository, the container registry and the Jelastic
environment are all created here, once.

**The local half was built first, deliberately.** Steps 1 and 3 of the go-live sequence below
are user actions, so a session that waits for them delivers nothing. Hardening, the legal pages
and the methodology page need neither, and doing them first means the first push lands on
something ready rather than on something that then needs a week of follow-up commits while the
site is already public.

---

## The go-live sequence

In order, because each step depends on the one before:

1. **Create the GitHub repository** under the main account (user action).
2. Push. The CI workflow runs lint and the full test suite, then builds and pushes to GHCR.
3. **Make the GHCR package Public** (user action). Jelastic cannot pull a private image without
   registry credentials, and the only symptom is a node that never comes up.
4. Add the repository secret **`JELASTIC_WEBHOOK_PROD`**.
5. `cp jelastic.jps.example jelastic.jps`, fill in the username, import it in the Jelastic console.
6. **Verify the three assumptions that have never been tested against real infrastructure:**
   - the public URL answers at all — this is the **port 8080** rule
   - `/healthz/` reports database and Redis healthy
   - a redeploy of the `cp` node does not break database connections
     (`CONN_HEALTH_CHECKS`), and web/worker do not deadlock on the migration lock
7. Run one full simulation in production and confirm it completes inside the cloudlet envelope.

**Known risk of having deferred this:** the deployment path is unproven until now, so problems
surface at the worst moment. Budget real time for this phase rather than treating it as a
formality.

---

## Security (CLAUDE.md HABIT 8 — OWASP)

- CSP, HSTS, `SECURE_*` settings, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`
- Rate limiting on the expensive and abusable endpoints: import, simulate-enqueue, registration,
  password reset
- File upload limits on CSV import (size, row count, content-type) — an unbounded CSV is a
  trivial memory DoS
- Dependency audit; `pip-audit` in CI

## Reliability

- Sentry (or equivalent) with the Celery integration
- Postgres backups from the Jelastic storage node, **and a tested restore** — an untested backup
  is not a backup
- Structured logging; `/healthz/` already exists from phase 0
- Alerting on worker queue depth and on failed simulation runs

## Legal — a real obligation, not a formality

### Wizards of the Coast Fan Content Policy — check this before launch, not after

The [Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy) (last updated
2017) permits fan-made tools provided they are marked unofficial, are not used to harm others or
the company, and carry the required attribution. Deck builders such as Moxfield are generally
treated as permitted fan content.

**The policy does not clearly address commercial monetization**, which is exactly what this
project plans. Established practice cuts in your favour — Moxfield, Archidekt and MTGGoldfish all
run paid tiers openly and have done for years — but "everyone does it" is not a legal opinion.

Concrete consequences for this project:

- **Do not put "Magic: The Gathering" or a card name in the product name.** Those are WotC
  trademarks. Pick a distinct brand.
- Carry the standard disclaimer: *"Unofficial Fan Content permitted under the Fan Content Policy.
  Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the
  Coast. ©Wizards of the Coast LLC."*
- Card data comes from Scryfall under its own terms; card images are hot-linked, never rehosted.
- If revenue becomes material, get an actual legal opinion. This note is not one.

### Data protection

You will hold other people's collection data, which under Swiss DPA and GDPR means:

- Terms of Service
- Privacy policy naming what is stored, why, and for how long
- **Data export and deletion** that actually work
- A support/contact channel
- Cookie/consent handling only if analytics are added — the app itself needs none

## Credibility

An `/about/methodology` page stating plainly:

- What is simulated and what is not (goldfish: no opponents, no interaction, no combat)
- That the opening-hand distribution is verified against the exact hypergeometric
- The mulligan rule implemented (London, free first mulligan in multiplayer Commander)
- Citations, exactly as `simulation-recherche.md` already does: Frank Karsten on manabases,
  the EDHREC article on simulating available mana, the goldfishing-limits discussion

This page is a competitive asset, not boilerplate. It is the thing no "AI power level: 7.3"
competitor can write.

---

## Verification

- Security headers scored by an external scanner
- Restore a backup into a scratch environment and confirm data integrity
- Rate limits provably trigger
- Data export returns everything; data deletion removes everything
- Playwright screenshots of the methodology page and the legal pages

## Definition of done

The app can be linked publicly without embarrassment or legal exposure.

---

## What actually happened — the local half, 2026-09-22

Twelve items, eleven of them done. What follows is the account: what was built, what was
already there, what turned out to be wrong, and what is left.

### The gate that had never run

`.github/workflows/deploy-prod.yml` gated the deploy on **`djlint templates/ --check`**, which
is the *formatter's* dry run and exits 1 whenever a file would be reformatted. Eleven templates
and about ninety-six lines of whitespace. **The first push to the first repository would have
failed lint**, and nobody could have known, because the repository does not exist yet and CI has
never executed once.

Changed to `--lint`, the actual rule checker, which passes. The four `H021` inline-style hits
are now `ignore`d in `pyproject.toml` with the reason written next to them: every chart here is a
server-rendered div whose **width is the datum**, Tailwind emits only classes it has scanned, so
a computed width can never be a class. Trap 39.

The general lesson is cheap and was not: **run a workflow's own commands by hand before relying
on the workflow.** Done for all three gates now — `ruff`, `djlint --lint` and `pip-audit` were
each run locally before being trusted.

### Security headers

The Content-Security-Policy lives in **`base.py`, not `prod.py`**. A policy switched on only in
production is a policy nobody has ever run, and this one is now exercised by every request in
development and every test in the suite — the same argument that keeps the printing catalogue
optional and the degraded path covered.

The directive worth understanding is `style-src-attr`:

```
"style-src": [SELF],
"style-src-attr": ["'unsafe-inline'"],
```

Every chart in this application is a server-rendered div carrying a `style` attribute holding a
computed width, because of the settled *no hand-written JavaScript* rule. **A nonce cannot
rescue that**: CSP nonces apply to `<style>` and `<script>` *elements* and never to a style
*attribute*. Splitting the directive is what allows the attributes while keeping an injected
`<style>` block refused — and the tests assert both halves, because the whole value of the split
is the half that stays shut.

`/admin/` is excluded. Django's own admin ships inline scripts and styles, and rewriting somebody
else's templates to satisfy our header is not a security improvement.

HSTS is in `prod.py`, one year, subdomains included, **preload off**. It is an environment
variable for exactly one situation: a first deploy onto a hostname whose certificate is not
settled, where a browser that has seen the header refuses plain HTTP for a year and cannot be
called back. `manage.py check --deploy` is clean except `W021`, which *is* the preload decision.

### Rate limiting, and the control that was already there and already broken

The phase outline asked for limits on import, simulate-enqueue, registration and password reset.
**Two of the four needed no code at all** — allauth has shipped `ACCOUNT_RATE_LIMITS` since
Phase 0, with `signup: 20/m/ip` and `reset_password: 20/m/ip,5/m/key` already on.

They also did not work, and this is trap 40. allauth counts in `django.core.cache`, and Django's
default cache backend is **`LocMemCache`, which is per process**. Gunicorn runs several workers
and two Celery workers hold copies of their own, so "20 per minute" was really twenty per minute
*per worker*. Nothing warns about it and no test can see it, because a test suite is one process.

One `CACHES` block pointing at Redis fixed all of it at once. **Database 1, not 0**: 0 is the
Celery broker, and a `FLUSHDB` while debugging a queue should not quietly reset every rate limit
in the application at the same moment.

The two endpoints that cost real work got `django-ratelimit`: deck import 10/m, collection
import 5/m, simulation enqueue 20/m, all keyed on the user because all three are login-required.
The quota system already bounds games per *month*; it is perfectly happy with twenty chords of
Celery tasks enqueued in one second, which is the thing that actually falls over. **A budget is
not a valve.**

Both limiters end on the same `templates/429.html`, through `handler403` and `handler429`.
That indirection is needed because `Ratelimited` subclasses `PermissionDenied` and would
otherwise render as *"that is not yours to open"* — which is untrue, and worse, gives no hint
that waiting works. **429 and 403 are different sentences.**

Behind the proxy, `core.ratelimit.client_ip` takes the **right-hand** end of `X-Forwarded-For`.
The left-hand end is the one everybody reaches for and it is the one the client writes: anybody
can send their own header and get a fresh bucket per request, turning the limiter off for exactly
the person it exists to stop. Correct for one trusted proxy, which is what the Jelastic topology
is and what `SECURE_PROXY_SSL_HEADER` already assumes; a CDN in front would need one more hop,
deliberately and with the number of trusted hops written down.

### Upload limits — the gap was not the one the outline named

A size cap (1 MB) and the NUL-byte check were already in `decode()` from trap 33. The outline
asked for size, row count and content-type. Of those:

* **Row count was the real gap**, and it is the one that matters. A megabyte of two-character
  lines is about 250,000 rows, each stripped, parsed and looked up against a 35,568-row card
  table by the resolver's four rungs. The byte limit is perfectly happy with that file; the
  128 MiB cloudlet is where it would have been discovered. `MAX_UPLOAD_ROWS = 50_000`, counted
  in `decode()` so every parser inherits it rather than every parser having to remember it.
* **Content-type is deliberately not checked.** It is the client's word for what a file is. A
  spreadsheet is already refused by the NUL check, which is a fact about the bytes.

And a correction worth more than the feature: **two Django settings are named as though they
bound an upload and neither does.** `DATA_UPLOAD_MAX_MEMORY_SIZE` excludes files —
`MultiPartParser` accumulates `num_bytes_read` only for `item_type == FIELD`.
`FILE_UPLOAD_MAX_MEMORY_SIZE` is a spool threshold, so it moves the cost rather than refusing it.
Setting either and calling the upload bounded is a mistake that survives review, because the
names read like limits. Trap 41, **found while writing the comment that claimed the opposite.**

### Sentry, logging, pip-audit

Sentry is env-gated in `prod.py` with the import **inside** the `if SENTRY_DSN:` branch, so an
installation without a DSN does not pay for the integrations either. `send_default_pii=False`:
this application holds other people's decks and collections, and a crash report must not carry
them out of the country the privacy policy names.

Logging is JSON in production only — twenty lines in `core/logging.py`, no dependency. The
reader in production is an aggregator; in development it is a person watching
`docker compose logs`, for whom JSON is strictly worse. The traceback goes under its own key,
which is the entire point: forty physical lines is forty unrelated entries to anything that reads
by line.

`pip-audit` runs in CI against **`requirements.txt`**, not the environment — the environment also
holds pytest, scipy and playwright, and failing a deploy over a test-only advisory that reaches
no user is how a security gate gets deleted. Clean on all 13 production pins.

### Legal, and the two rights that had to actually work

> **Superseded 2026-09-25:** the placeholder blocks are gone. The pages are final drafts, a
> `/imprint/` legal notice exists, and the operator's details come from settings
> (`LEGAL_OPERATOR_*`, `core.E003`). See GO-LIVE.md step 0a and its "Legal" section.

`/terms/` and `/privacy/`, linked from the footer on every page. Both carried a visible
**`TO BE FILLED BEFORE LAUNCH`** block: operator's legal name, postal address, contact address,
governing law. These are careful drafts and **no lawyer has read them.**

The data export walks `_meta.concrete_fields` rather than a hand-written field list, because six
phases of this project added columns to models that already existed and an export written
against the fields of Phase 8 would quietly have stopped being complete in Phase 9. The models
are listed by hand and a test — `test_every_user_owned_model_is_exported_or_deliberately_skipped`
— fails when a new user-owned *model* appears, which is the case a field walk cannot catch. It
was verified by deleting a line and watching it go red. Stripe identifiers and the password hash
are redacted: a downloadable file holding them is a file worth stealing.

Deletion is `user.delete()`, no soft delete and no tombstone — but it is **blocked while a paid
subscription is live**, and that is trap 42 and the only genuinely new design decision here.
Every user-owned model cascades, so deleting would take the local `Subscription` with it and
leave **Stripe charging the card every month for an account nobody can sign in to stop**. The
person does the responsible thing and is punished for it. The fix keeps obeying the settled
"Stripe hosts the cancellation" rule rather than adding a second canceller: refuse, and send them
to the portal. Checked on the server and not only in the template — *a page that hides a button
is not a rule.* The general shape: **a cascade is a complete deletion only when nothing outside
the database holds a reference.**

### The methodology page

What is simulated and what is not, the hypergeometric verification, the full keep rule in prose,
the two-number coverage score, what "assembled" means for a combo, and the citations. Its
mulligan section is **pinned to the engine by a test** rather than to the prose: `MAX_MULLIGANS`
and `Game.cards_to_bottom` are asserted against the words on the page, so the sentence "this
heuristic decides every number that follows" cannot quietly stop being true.

### Two bugs found by looking at screenshots, as usual

Neither was findable by a test, and both were on **every page of the site**.

1. **The signed-in header clipped at 390px.** "Sign out" ran off the right edge of the page —
   not on a new screen, on all of them, since whenever the nav last grew. `flex-wrap` on both
   rows. A nav that cannot be signed out of answers HTTP 200 all day.
2. **The CSP refused htmx's injected stylesheet.** htmx writes its two `.htmx-indicator` rules
   into a `<style>` element at load; `style-src 'self'` refuses it, and every page logged a
   violation while rendering perfectly, because nothing uses the class *yet*. The first loading
   spinner somebody added months later would simply not have worked, with nothing pointing here.

   Fixed by turning the injection off through the `htmx-config` meta tag and moving the two rules
   into `input.css`. Better than whitelisting htmx's style hash: there is now **no inline
   `<style>` element on any page at all**, and nothing to re-hash when htmx is upgraded. Pinned
   by three static tests in `tests/test_css_build.py`.

   One of those tests immediately failed on `base.html` — because the comment *explaining* why
   there are no inline style elements contains the word itself. It now strips Django comments
   first, which is the correct reading of the question rather than a way round it.

### And one blocker found while writing the runbook

Worth recording how it surfaced: not from a test, not from a screenshot, but from asking *"what
exactly will the user have to type, and when?"* while writing [GO-LIVE.md](GO-LIVE.md). Walking
the steps in order is what exposed a gap that eight phases of building had walked straight past.

**Production had no email configuration at all.** `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` has
been in `base.py` since Phase 0, so allauth sends a confirmation during signup. Django's default
backend is SMTP to `localhost:25`, which does not exist in the container — so **the signup form
would have answered HTTP 500 to the first stranger who tried it**, and with nobody able to create
an account the entire site is inert on day one.

It was invisible in every place it would have been cheap to catch: `dev.py` sets the console
backend, and **Django's test runner substitutes locmem**, so the whole suite passes and the
account tests cheerfully assert that signup works. That is the shape worth carrying forward —
**a harness that helpfully substitutes a working stub for a missing dependency cannot see that
the dependency is missing.** Trap 40 was the same shape one layer down, with the cache.

Fixed in two parts. `prod.py` reads the SMTP settings from the environment, and `core/checks.py`
raises `core.E001` so `manage.py check` **fails** — and since `start.sh` runs `migrate`, which
runs system checks, the container now **refuses to start** rather than coming up with a signup
nobody can complete. Refusing to boot is louder than serving a 500 an hour later, and on a first
deploy there are no users to inconvenience. An installation that deliberately sends no mail says
so with the console backend. `core.E002` catches `EMAIL_USE_TLS` and `EMAIL_USE_SSL` both being
set, which Django otherwise reports only at send time, naming neither. Trap 43.

The manifest gained the fields to match: SMTP host, user, password and From address are now
**required** at import time, and optional Stripe and Sentry fields were added beside them,
because the manifest previously passed neither — so billing would silently have been "not
configured" in production and the Celery integration would have had no DSN to report to.

### Where it stands

* **914 tests passing** (from 821), ruff clean, `djlint --lint` clean, `pip-audit` clean.
* **41 screenshots captured cleanly** — no non-200s and, now, no console errors anywhere.
* `manage.py check --deploy` clean but for the deliberate `W021`.
* Three new production dependencies, so **the image needs building, not restarting** — trap 32.

### What is left, and it is only the go-live

Item 11's screenshots are done; **item 12 is this document.** What remains is the numbered
sequence at the top of this file, and every step of it needs the user:

1. Create the GitHub repository.
2. Push — CI now actually passes.
3. Make the GHCR package **Public** (a private image is a node that never comes up, with no
   other symptom).
4. Add `JELASTIC_WEBHOOK_PROD`.
5. Copy `jelastic.jps.example` to `jelastic.jps`, fill in the username, import it.
6. Verify the three assumptions never tested against real infrastructure — the **port 8080**
   rule, `/healthz/`, and a `cp` redeploy not breaking database connections.
7. Run one real simulation in production.

Plus two things that are decisions rather than tasks: **fill in the legal placeholders**, and
decide whether a lawyer reads the terms and the privacy policy before launch or after. The phase
document's own advice is that "everyone does it" is not a legal opinion.
