# Phase 6 — Collection, remaining importers, billing

> **2026-09-29: §1 (the collection) was removed in Phase 9 batch B** - see
> [phase-9-ux-overhaul.md](phase-9-ux-overhaul.md). This file stays as the historical account;
> the printing catalogue and the importer from §1/§2 are still in use.

**Status:** **complete.** §1 and §3 landed on 2026-09-20 with the printing catalogue and Stripe;
**§2 landed the same day**, by abandoning its design rather than finishing it. **757 tests
green.**

---

## What was done, and what was not

| | |
|---|---|
| §1 Collection | **Done**, including the printing catalogue and prices |
| §2 The seven other CSV importers | **Done — as one generic importer, not seven parsers.** [importers.md](importers.md) |
| §3 Billing | **Done**: hosted Checkout, hosted Portal, the webhook, 29 tests |

### §2 was blocked for two phases on the wrong requirement

The instruction this document gave itself was right:

> Headers for all of these are **unverified** — do not guess.

The conclusion drawn from it was not. It concluded that each of the seven formats needed a real
export committed as a fixture — an ask that sat on the user across two phases. The user pushed
back on the maintenance cost, and **this repository was the evidence against the plan**: the one
parser written against a genuinely verified real export was *still* silently reading every
quantity as 1, because Archidekt lets you untick columns. A fixture proves what one file looked
like on one day for one user's export settings.

So the unit of support was wrong. It is no longer a website; it is a **concept**, and the words
tools use for it. One `TabularParser` reads any delimited export, a mapping screen asks when a
card name or a quantity cannot be placed, and an unknown format costs the user thirty seconds
rather than costing this project a phase. **The seven-export ask is withdrawn.**

Full account, including the design that was abandoned: [importers.md](importers.md).

---

## §3 Billing

Quotas have enforced the free plan since Phase 1, so this added only the paid half.

### Hosted everything

Hosted Stripe Checkout and the hosted Customer Portal. **No card number reaches this
application** — no PCI scope, no dunning code, no custom billing UI, no proration arithmetic,
and no cancel button of our own. That last one matters more than it looks: a second place that
can end a subscription is a second place that can disagree with Stripe about whether it ended.

`billing/stripe_api.py` is the only module that imports `stripe`, and every signature in it was
**read off the installed source of `stripe==15.6.1`** rather than remembered — including
`subscription_data.metadata`, which the next section turns out to depend on.

### The webhook is the only writer

`billing/services.py::apply_event` is the only thing that sets `Subscription.status` or
`Subscription.plan`. Not the success redirect, which a person can reach by typing the URL and
which can arrive before the payment has settled. `templates/billing/done.html` therefore grants
nothing and says so in as many words, and a test types the URL to prove it.

**Idempotency first, before any work.** `StripeEvent.objects.get_or_create(stripe_event_id=…)`
and bail if it existed. Stripe retries until it gets a 2xx, so this turns at-least-once
delivery into exactly-once processing. It is the single most commonly missed piece of a Stripe
integration and it is the first section of the test file.

### Event order is not guaranteed, so nothing depends on it

`customer.subscription.created` can arrive before `checkout.session.completed`. A handler that
learned the customer id only from the checkout session would silently drop the first one.

So **the user id is written into `subscription_data.metadata` at checkout**, which makes every
later subscription event self-identifying and ordering irrelevant. `_find()` reads metadata
first and the customer id second. Pinned by
`test_a_subscription_event_arriving_first_still_finds_its_user`.

An event for a customer this database has never seen — a test-mode endpoint pointed at a live
database, usually — returns **200 and is recorded**, not 500. Retrying cannot fix it, and a
non-2xx would turn one misdirected event into days of retries.

### Three decisions worth arguing with

**`past_due` keeps the plan.** Stripe retries a failed card for about two weeks. Cutting
somebody off on the first failure punishes an expired card more harshly than a cancellation
does. The phase document forbids leaving somebody on a paid plan *silently*, and the emphasis is
the whole instruction: the entitlement survives, `Subscription.needs_attention` is true, and the
plans page carries a banner. The downgrade happens on `customer.subscription.deleted`, which is
what Stripe sends once the retries are exhausted. **If the user disagrees, one line in
`quotas.plan_for` changes it.**

**An invoice never changes which plan somebody is on.** It says whether money arrived, not what
was bought. Letting `invoice.payment_failed` touch `plan` is how a failed renewal could swap
somebody's tier for whatever the fallback happened to be.

**A plan with no `stripe_price_id` cannot be bought.** That is the state of every plan until
somebody creates the products in the Stripe dashboard, and the page says "payments are not
switched on in this installation" rather than showing three buttons that 500. **This is what
every test, every screenshot and every development environment sees**, and it is why the whole
integration needed no mocking to build.

### Swiss VAT

Flagged, not coded, exactly as the outline asked. Stripe Tax can handle CHF VAT at 8.1%, and
whether this business sits under the CHF 100k registration threshold is an accountant's
question. `automatic_tax` is deliberately not passed.

### A quota refusal now points somewhere

`billing/views.py::upgrade_prompt` answers **HTTP 402**, which is the one status code that means
exactly this, and links to the tier that has more. It used to be a red message on the deck page:
true, and useless to act on.

---

## §1 Collection

### What it answers

> **"Can I build this deck from my collection?"** — the feature the original deck project
> actually needed.

**That answer is computed on cards and never on printings**, which is why loading the printing
catalogue changed none of it. A test pins this by nulling every `printing` on a collection and
asserting the counts are identical.

On the collection page for every deck at once, and on each deck's own page, because that is
where somebody actually asks it.

### Two ways to get it wrong, both avoided

**Counting basic lands with everything else.** A deck wanting 38 Swamps against a collection
holding 28 is ten cards short by arithmetic and zero by any player's reckoning. Reporting one
number picks a side, so `Shortfall` reports `cards_short` and `basics_short` separately and
`buildable` ignores basics. Report, never decide — the same rule as the blind-spot detectors.

**Forgetting the commander.** It is not a `DeckCard` (trap 7), so a query over deck entries
alone would tell somebody they can build a deck whose commander they do not own — the one card
no substitution gets you past.

### Printings: deferred, then measured, then built

§1 shipped without the printing catalogue and said so on its own page. It was finished on
2026-09-20, and **the first thing that happened was that the measurement contradicted the plan.**

| | This document, before | Measured |
|---|---|---|
| Printings in `default_cards` | "~430,000, roughly **twelvefold**" | **112,581 — 3.2x** |
| Cost on disk | an ongoing cost worth deferring for | **43 MB**, against the card table's 101 MB |
| `(set_code, collector_number)` | never checked | **globally unique, zero collisions** |

The 430,000 figure was `all_cards`, which is 393 MB and carries **every language of every
printing**. `default_cards` is one row per printing. So the decision this document had carefully
framed as expensive and the user's to take was, in fact, cheap — and it had been deferred for two
phases on a number nobody had ever measured. **Measure the file before designing a table for it**
is now trap 35.

The uniqueness result is the load-bearing one. It is what makes rung 3 an exact match rather
than a guess: a row saying `tor 341` names exactly one printing, with no language to infer and
no tie to break. Without that measurement the honest implementation would have been "find the
printings matching this pair and refuse if there are several".

**What it bought, all three of the things this document predicted:**

1. **Rung 1 became what it always claimed to be.** It matched against the single arbitrary
   printing `oracle_cards` ships, so on the user's real 214-row export it hit **62 rows**. It
   now hits **214 of 214**, and 0 rows are unresolved.
2. **Rung 3 exists.** It was a documented hole from Phase 1 — named in `resolve.py` so nobody
   would think it was forgotten — and is now an exact lookup.
3. **Prices.** 196 of the 214 rows carry a Cardmarket trend price in euros.

**⚠️ The Cardmarket website returns 403 to automated requests.** Only the S3 downloads work:
`downloads.s3.cardmarket.com/productCatalog/…`. Paid for once during the deck research; do not
rediscover it. Scryfall's own CDN (`data.scryfall.io`) has never needed anything but a
User-Agent.

### Three decisions about prices

**EUR only, and never a total.** Cardmarket is the European market and this is a European
project, so a second currency answers a question nobody asked. But the sharper decision is that
the page prices each row and refuses to add them up: a trend price is one market's snapshot of
one day, and a headline "your collection is worth CHF X" invites somebody to treat the sum of
two hundred of those as a valuation. `PriceSummary` reports how many rows it could price, how
many it could not, and the date — and the reader adds them up themselves if that is what they
came for. **The user's own framing when this was put to them: "pricing is not the USP of our
application."**

**A missing price is `None`, and `None` renders as “—”.** Never zero. "Nobody publishes a price
for this printing" and "this card is worthless" are different sentences and only one of them is
ever true. Three ways to land there, all normal: etched foils, for which Scryfall publishes
`usd_etched` and no EUR equivalent anywhere; MTGO-only sets, which have no paper price because
there is no paper; and printings Cardmarket has simply never traded. 18 of the user's 214.

**Nothing shows a price without showing its date.** `prices_updated_at` is the bulk file's own
timestamp, not `now()` — a price is as old as the file it came from.

### The printing is nullable, permanently, and that is the design

`CollectionItem.printing` is a nullable FK **beside** the raw `set_code`, `collector_number` and
`scryfall_id` strings, which are kept and never overwritten from it. Three reasons it can be
null and none of them is a failure: a plain text deck list names no printing; an export can name
one Scryfall has retired; and **an installation may never have ingested `default_cards` at all**,
which is the supported default.

That last one is enforced rather than hoped for. `--kind all` deliberately excludes
`default_cards`, and the test suite runs in the no-printings state by default — the `printings`
fixture in `conftest.py` is opt-in, so several hundred existing tests go on exercising the
degraded path every run instead of it being something nobody looks at until a deployment.

Keeping the strings is what made the backfill possible: migration `collection/0002` links every
existing item to its printing by id, so nobody has to re-upload a collection to get prices. All
214 of the user's rows linked. **A later ingest cannot invent what the first import discarded**
— that was the stated reason for keeping them, and it paid out within the phase.

### What it still cannot do

Tell you that a specific *copy* is damaged, or worth anything other than what Cardmarket
thought on one day. `condition` and `language` are recorded and used for nothing.

### One bug found, and it predates this phase

`decks/services.decode()` accepted binary. `latin-1` decodes *any* byte sequence, so a JPEG
parsed into rows full of control characters and died deep inside the resolver with
`PostgreSQL text fields cannot contain NUL (0x00) bytes` — **a 500 on a public upload form that
a stranger can cause on purpose.** Present since Phase 1, found by a collection test that
uploaded nonsense. A NUL byte is now refused at the decode boundary, which is where "is this
text?" belongs.

---

## Verification

| Claim from the outline | How it was checked |
|---|---|
| "Webhook replay changes nothing the second time" | The same event applied twice; the row is untouched and `StripeEvent` holds one |
| "Downgrade at period end correctly reduces quota" | Through `quotas.check`, not by reading a field |
| "A failed payment does not silently leave a user on a paid plan" | Both halves: `needs_attention` during retries, downgrade on deletion |
| "The webhook is the only writer of `Subscription.status`" | The success page is loaded and grants nothing |
| Signature verification | The real `construct_event`, with a forged signature, no monkeypatching |
| "Each importer's fixture round-trips" | **Not applicable — §2 not built** |
| "Playwright screenshots" | 32 pages, desktop and phone, looked at by eye |
| "Printing-exact matching" | The real 214-row export: 62 rows on rung 1 before, **214 after** |
| "Per-printing prices" | 196 of 214 priced; the other 18 render “—” and a test forbids `0.00` |
| "Ingestion stays inside the memory budget" | **19.8 MB peak** over 118,609 rows, measured with `--measure` |

The screenshot pass found two things again, both invisible to every test: **"CHF 400 cents /
month"**, and a collection page **7,958 pixels tall** because it rendered all 183 entries and
pushed the deck answers off the screen. On the printings pass it found a third, smaller one: the
section headed *"What this cannot tell you"* now opened with three sentences about what it
**can**, because the prices had been written into it and the heading had not been reread.

## Left open

- **A collection has no review screen.** A row that matches no card is counted and reported in a
  message rather than listed on a page, which is weaker than what the deck importer does.
- **`condition` and `language` are recorded and unused.** Both are on every row and nothing
  reads them. Honest storage, no feature.
- **Prices never refresh on their own.** `ingest_scryfall --kind default_cards` updates them in
  place and is safe to re-run, but nothing schedules it. A nightly beat task is the obvious
  Phase 8 line, and deliberately not written now: a scheduled 78.8 MB download is an operational
  decision, not a feature.
- **`tutor_to_hand` is still not editable** (carried over from Phase 5b).
