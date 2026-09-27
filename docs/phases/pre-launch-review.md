# Pre-launch review (2026-09-25) - done

A full review of the code before the go-live, and the fixes that came out of it. Started
2026-09-25 with the user's approval of every batch below. **This file is the checklist**: a box is
ticked only when the code, its tests and the docs it touches are all done.

## Decisions the user made before the work started

- **All five batches** are done before the go-live, not after.
- **`max_decks` means decks owned**, not decks created this month. Deleting a deck frees a slot.
- **"On the play" is relabelled, not removed.** The engine keeps the two-player draw skip as an
  option; the default and the wording become the multiplayer rule (CR 103.8c: in a multiplayer
  game other than Two-Headed Giant, no player skips the draw on their first turn).
- **Mana sources with a cost are modelled properly now**, not merely reported as gaps: a Signet
  nets one mana in both of its colours, Mana Vault taps once and stays tapped, Lotus Petal is a
  one-shot, Cabal Ritual makes three.

## Findings, most severe first

| #   | Severity | Finding |
|-----|----------|---------|
| B1  | critical | `ingest_scryfall` never derives `DerivedProfile`s, and nothing else does outside the tests. A fresh production catalogue would simulate every card as a colourless artifact with no lands. |
| B2  | critical | The Stripe webhook raises on every real event: in `stripe==15` a `StripeObject` is not a `dict` (`dict(event)`, `.get()`), and the test fixture replaced `construct_event` with `json.loads`, which is exactly what hid it. |
| B3  | high     | An imported (or hand-picked) commander stays in the 99 as a `DeckCard`: "101 cards", the commander shuffled into the library, the collection shortfall asking for two copies. |
| B4  | high     | allauth's rate limits key on `REMOTE_ADDR` unless `ALLAUTH_TRUSTED_PROXY_COUNT` is set. Behind the proxy every visitor is one address, so ten wrong passwords in a minute lock everybody out. |
| B5  | high     | `jelastic.jps.example` never gives the database node the password the app connects with, bakes node IPs into the URLs, and mounts NFS over a PostgreSQL data directory that already exists. |
| B6  | high     | Mana creatures never make mana: `Game.mana()` only reads lands and rocks, while the tune page says the card "taps for 1 G" and no gap is recorded. |
| B7  | high     | The deriver reads mana abilities with a cost as free every turn: Signets +2 of one colour, Mana Vault and the Monoliths +3 every turn, Lotus Petal as a permanent rock, and "several abilities of different sizes" as the largest one (Cabal Ritual 5). Mostly with no review reason. |
| B8  | high*    | A paying user can check out a second subscription and be billed twice; the deletion of either then downgrades them. |
| B9  | high*    | `form-action 'self'` makes Chrome and Safari refuse the 302 from our checkout and portal views to Stripe. |
| B10 | medium*  | A late or retried `customer.subscription.updated` processed after `...deleted` restores a paid plan for good. |
| B11 | medium   | The playtest board has a Cast button on every card; casting one the pool cannot pay raises `ValueError`, which is a 500. |
| B12 | medium   | Phyrexian symbols are paid with mana before the generic part, so Phyrexian Metamorph is uncastable from three blue mana. |
| B13 | medium   | The legality panel never checks the 99 for banned or non-Commander cards. |
| B14 | medium   | "On the play" is the two-player draw rule, and the help text argues for it with a multiplayer reason that is backwards. |
| B15 | medium   | Import: signed-in users can cause 500s (a quantity past `smallint`, a number past Python's 4,300-digit limit), a suggestion query per unresolved row (50,000 junk rows outlast the gunicorn timeout), and the quota is checked after the work. |
| B16 | medium   | Deleting a deck mid-run leaks the concurrency slot for three hours; two chunks failing together refund twice. |
| B17 | medium   | Commander Spellbook is called inside the web request with a 30 s timeout, three attempts and an uncapped `Retry-After` (an HTTP-date one is a 500). |
| B18 | medium   | `max_decks` counted decks created this month. |
| B19 | low      | `/admin/login/` has no rate limit at all. |
| B20 | low      | A failed combo refresh re-dates the old answer; `date.today()` instead of `timezone.localdate()`; `jelastic.jps` not ignored; CI's redeploy `curl` without `--fail`; task results never expire; GO-LIVE missed the Stripe portal configuration and the webhook event list; methodology wording. |
| B21 | high     | Found on the screenshot pass: the 41 spell//land MDFCs were read as spells by the mana reader and tapped for nothing. |

\* only matters once Stripe is switched on, and has to be in before it is.

Checked and fine: no `|safe` or `mark_safe` anywhere, every queryset filtered by owner, an empty
webhook secret is refused by the library, the upload NUL/size/row ceilings, CSRF on every htmx
form, every `{% static %}` reference exists, chunk seeds and merging, the hypergeometric column.

## The work

### Batch 1 - go-live blockers and data correctness
- [x] B1 profiles derived by `ingest_scryfall` (+ `--profiles`), GO-LIVE step 7
- [x] B3 the commander is never one of the 99 (import, set-commander, data migration)
- [x] B4 one `TRUSTED_PROXY_COUNT` for allauth and django-ratelimit
- [x] B5 Jelastic manifest: database password, no NFS under PostgreSQL, Redis password note (node IPs kept: they are the documented placeholder and stable per node)
- [x] B11 an unpayable cast is an `IllegalAction`, not a 500

### Batch 2 - billing
- [x] B2 webhook events are plain dicts; a test signs a payload for real
- [x] B9 `form-action` allows Stripe's checkout and portal hosts
- [x] B8 no second subscription; a deletion only ends the subscription it names
- [x] B10 stale subscription events are ignored (`Subscription.last_event_at`)
- [x] GO-LIVE step 10: portal configuration and the six webhook events

### Batch 3 - simulation accuracy (engine version 3)
- [x] B6 creatures with a flat mana ability tap for mana
- [x] B7 costed, one-shot and non-untapping mana sources modelled (deriver, profile, engine, adapter, editor)
- [x] B12 Phyrexian mana paid with life when the mana is needed elsewhere
- [x] B13 banned and non-Commander cards in the 99
- [x] B14 multiplayer draw rule as the default, relabelled everywhere

### Batch 4 - robustness
- [x] B15 import quantities bounded, suggestions capped, quota before work
- [x] B16 closing a run is a conditional update; deleting a run frees its slot
- [x] B17 Spellbook deadline, capped `Retry-After`, refresh rate-limited
- [x] B18 `max_decks` counts decks owned
- [x] B19 admin login rate-limited

### Batch 5 - small things and the documents
- [x] B20 (all items)
- [x] RESUME (traps 44-50, settled decisions), GO-LIVE, README, `.env.example`, project memory

## Left for after the launch, on purpose

- A dual land with two basic types always taps for the first colour in WUBRG order (Watery Grave
  makes blue, never black). The real fix is a pool that holds flexible sources and a payer that
  matches them, which is an engine rebuild rather than a fix.
- Only one `PER_CONTROLLED` land is evaluated per turn; a rock cast this turn adds nothing until
  the next one.
- The collection page loads every item in full (a 25,000-row collection is a lot of memory).
- No request-body guard at the application level; a double click on a playtest button can hit
  the `(session, seq)` unique constraint.

## The visual check - done 2026-09-25

- [x] images rebuilt, stack up (migrations applied on start, Beat running inside `worker_short`)
- [x] `demo_screens.py`: 41 pages, all 200, and the changed ones looked at - plans (periods),
  deck page (First draw, the 1-v-1 checkbox unchecked, "Every card is legal"), the run header
  ("multiplayer draw rule"), methodology's new section, the card page of Arcane Signet and of
  an MDFC land (the two new rows and the two new form fields), combos ("Looked up" after a
  success)
- [x] **B21, found by looking**: every spell//land modal double-faced card (41 in the
  catalogue: Agadeem's Awakening, Shatterskull Smashing, the Final Fantasy Towns) was read as
  a spell by the mana reader, so its land face's `{T}: Add` was dropped and the land tapped
  for nothing. Fixed in `cards/profiles._mana_production`, two real-text cases in
  `test_cards_profiles.py`, profiles re-derived (all 41 now tap for one), trap 51
- [x] `pytest -m "not slow"`: 989 passed (+11 slow = 1000), ruff clean, golden md5 unchanged

The steps that were followed, kept for the next such pass:

1. `docker compose up -d db redis` if they are not running, then
   `docker compose build web worker worker_short && docker compose up -d` - build, not restart
   (trap 32; engine changes need the workers too, trap 19).
2. In the web container the migrations run on start; the local database is already migrated and
   its profiles re-derived (`ingest_scryfall --profiles`, 35,568 profiles) - no need to repeat.
3. `./.venv/Scripts/python.exe scripts/demo_screens.py --out <scratchpad dir>` and **look** at:
   - `billing/plans` - usage rows now say "(this month)" / "(right now)"; a paid plan shows
     "Switch to it in the billing portal below" instead of a checkout button
   - a deck page - Run form "First draw" with the two new labels; playtest checkbox
     "1-v-1: skip the first draw" (unchecked); legality panel's new "Every card is legal" row
   - a run report header - "multiplayer draw rule" / "1-v-1, first draw skipped"
   - `/about/methodology` - the new "The first draw, and how a turn makes mana" section
   - a tune page and a card page for a deck with a Signet or Mana Vault - the rows
     "Costs to tap for mana" and "Untaps every turn" appear on mana sources only, and the
     annotate form has the two new fields
   - the combos panel - "Looked up ..." only shows once a lookup has succeeded
4. Fix whatever the screenshots show, rebuild CSS if classes changed
   (`.bin/tailwindcss.exe -i assets/css/input.css -o static/css/main.css --minify`), re-run
   `pytest -m "not slow"`, then tick this section off and update RESUME.md's top block.

## What actually happened

**All five batches are done, 2026-09-25. 998 tests pass** (the full suite, statistical
validation included; 914 before), ruff clean, `djlint --lint` clean, `makemigrations --check`
clean, and `tests/fixtures/engine_golden.json` is byte-identical (md5
`a28852be8ffc34f9b3bf63b51b7c8420`) - none of the engine changes touched a card in the reference
deck, which is the evidence nothing else moved.

Three things the plan did not know:

- **B2 had a second half.** `stripe.WebhookSignature.verify_header` defaults its tolerance to
  `None`, and `None` switches the 300-second replay window off. Replacing `construct_event` with
  it naively would have fixed the crash and opened a replay hole. The tolerance is passed
  explicitly and a test replays an hour-old signature.
- **B5 kept the node IPs.** The plan said hostnames; the only evidence for `sqldb`/`cache`
  resolving is one community manifest, while `${nodes.sqldb[0].intIP}` is the documented
  placeholder and stable per node. What changed is the `password:` line, the removed NFS
  storage, and a note for a password-protected Redis.
- **Scryfall's Oracle text now says "this artifact"**, not the card's name ("Sacrifice this
  artifact", "This artifact doesn't untap"). The self-reference patterns accept both, and the
  profile tests use the real 2026 text for exactly that reason.

New migrations: `billing/0004`, `cards/0008`, `combos/0003`, `decks/0004` (data: the commander
out of the 99). **After deploying, run `ingest_scryfall --profiles`** - the bulk files have not
changed, so nothing else would re-derive the mana readings.
