# Phase 9 — UX overhaul: fewer words, more pictures, decks only

**Status: IN PROGRESS (plan approved 2026-09-28, all build decisions made the same day - see
"Decisions").** Batches A, A2, B, C, C2, D and E are live, F (the board) is built (see "... - what
was built"); next is G - re-ordered 2026-09-29, see "Order". Compaction-safe: this file plus
`RESUME.md` is everything needed to continue.

## Why

The first look at the live site (2026-09-28, right after go-live step 8), in the user's words:
no tab icon ("wirkt shady"); on the import page you cannot tell where to click, neither for the
file nor for the format; the Collection is somebody else's job; far too much text. The direction:

> viel weniger Text, mehr Grafiken, und im Falle der Draw-Hand-Variante sogar so schön wie bei
> Hearthstone - muss nicht zu aufwändig sein, aber clean, schöne Karten. Es muss
> anfängerfreundlicher und übersichtlicher werden. Die Collections will ich raus haben, das ist
> Arbeit von anderen Webseiten, ich will nur die Decks haben.

The product is then one loop, and every page serves it:

```
import a deck  ->  answer the cards the engine could not read  ->  simulate  |  draw a hand
                   (red marker on the deck until done - optional)
```

## Principles (apply to every batch)

1. **One sentence per page, at most, above the fold.** Explanations move behind a small
   "Why?" (`<details>`) or to the methodology page, which keeps the full honesty story. Nothing is
   deleted from the methodology; it is moved there.
2. **A number that can be a picture is a picture.** Server-rendered, as today (divs and inline
   SVG, no charting library, no JavaScript needed, CSP unchanged).
3. **Card images wherever a card is named in a place that matters** (hand, review, deck grid).
   Source: the `image_uri` we already store (Scryfall's image CDN, `cards.scryfall.io` - already
   allowed by the CSP and already named in the privacy policy).
4. **Beginner-first wording.** "Ramp", "Card draw", "Removal" - not "role_tags", "provenance",
   "judgement".
5. The honesty guarantees stay: a red marker replaces a paragraph, it does not replace the fact.

## What already exists and gets reused (checked 2026-09-28)

| Need | Already there |
|---|---|
| Card types ("creature, sorcery, ...") | `OracleCard.type_line`, `DerivedProfile.kind` |
| Meta-categories ("ramp, draw, ...") | `DerivedProfile.role_tags` from the Scryfall Tagger DAG (`cards/profiles.py` `ROLE_FROM_TAG`). Verified tag sizes in the local DB: `ramp` 2437 (`mana-rock` 394, `mana-dork` 459, `land-ramp` 664), `draw` 4513, `tutor` 1220, `removal` 6713 (`spot-removal` 5460), `sweeper` 978, `counterspell` 561, `protection` 1356, `recursion` 2346, `reanimate` 1114. No tag for "win condition" - that one stays a user choice. |
| Manual correction of a card | `simulations/annotations.py` (`JUDGEMENTS`, `AnnotationForm`, deck/user scope) and `templates/simulations/annotate.html` |
| "What could the engine not read" | `simulations/gaps.py`, `simulations/provenance.py`, `DerivedProfile.review_reasons` |
| Mana curve, lands, colour charts | `simulations/report.py`, `templates/simulations/_report.html` (div bars) |
| Draw a hand | `playtest/` (htmx board, undo, branch) - today a text list |
| Screenshots of real pages | `scripts/screenshots.py`, `scripts/demo_screens.py`, `seed_demo_deck` |

## The category vocabulary (the user asked to look it up)

**Card types** are printed on the card (rule 205): Land, Creature, Artifact, Enchantment,
Planeswalker, Instant, Sorcery, Battle (+ Kindred). A card can have two (Artifact Creature) -
it counts in both.

**Functional categories** are what the Commander community sorts a deck into. They come from the
"Command Zone" deckbuilding template and are what Archidekt and Moxfield call "categories";
Scryfall calls them Tagger *oracle tags* (`otag:ramp`). Shown and searchable in the app:

| Shown as | From tag(s) | Typical target in a 100-card deck (template, not a rule) |
|---|---|---|
| Ramp | `ramp` (split: rocks, dorks, land ramp) | ~10 |
| Card draw | `draw` | ~10 |
| Removal | `removal` / `spot-removal` | ~8-10 |
| Board wipe | `sweeper` | ~2-4 |
| Tutor | `tutor` | - |
| Counterspell | `counterspell` | - |
| Protection | `protection` | - |
| Recursion | `recursion`, `reanimate` | - |
| Lands | type | ~36-38 |

Counterspell and Protection are new roles (two lines in `ROLE_FROM_TAG` + `ingest_scryfall
--profiles`). The targets appear as a faint band on the deck page, clearly labelled "a common
template", never as a verdict.

## Batches

Each batch is one PR `dev -> main`, tests + ruff + djlint green, a Tailwind rebuild where
templates change, and screenshots checked (`scripts/screenshots.py`) before asking for review.

### A — Quick wins (small, ship first)

1. **Favicon.** One SVG (a goldfish in a flask, drawn in the palette) + 32px PNG +
   180px `apple-touch-icon`, `<link rel="icon">` in `base.html`. Test: the tags exist and the
   files are served.
2. **Header:** `Decks` (list) and `Import` instead of `Collection`; plan and sign-out stay.
   Signed-out header unchanged.
3. **Import page rebuilt:**
   * One big dashed drop zone - "Drop your deck file here, or click to choose" - that *is* the
     file input (a styled `<label>` over the real input; drag and drop works without JavaScript).
     The chosen file name shows in it.
   * A second tab "Paste a list" (textarea) - the fastest path for a beginner, and it goes
     through the same importer (text format). Same size limits as the upload.
   * "Format" moves into "Advanced" (`<details>`), since auto-detect is right almost always.
   * Three lines instead of three paragraphs: **"Any CSV works. It only needs a column with the
     card name - a quantity column is optional."**, a four-line example, and "Export from
     Archidekt / Moxfield / ManaBox" with one-line how-tos. The spreadsheet explanation moves to
     "Why?".
4. **"Remember me" on the sign-in page** - the checkbox sits on its own line, not beside its
   label. Cause: `.auth-form form > p` in `assets/css/input.css` is a flex *column* for every
   field, the checkbox row included. Fix: a row layout for the paragraph holding a checkbox
   (`.auth-form p:has(> input[type="checkbox"])`, centred, small gap). Checked on the screenshots
   at 1440px and 390px.
5. **Styleguide dev-only** - already done locally 2026-09-28 (view 404 in production, footer
   link only with `DEBUG`, test `test_styleguide_is_a_development_tool`). Ships in this PR.
6. Text cut on home, deck list and import (principle 1).

**A - what was built (2026-09-28).**

* Favicon: `static/img/favicon.svg` only, linked from `base.html` and shown beside the
  wordmark; `/favicon.ico` redirects to it (the admin asks the old way). **No PNGs**: the bot's
  push path at the time (GitHub MCP) was text-only, and every current browser takes an SVG tab
  icon. The 180px `apple-touch-icon` is the one gap - since 2026-09-30 the bot pushes with plain
  git, so a PNG can now go in with any later batch.
* Import: `ImportForm` gained `source` (the tab), `text` (the paste box, same 1 MB ceiling,
  `clean_text`) and `payload()`; the view feeds a paste through the same `services.prepare`
  as a file. The tab the person chose decides which input counts. Errors land under the
  control that was used; "More options" (name, format) opens itself when it holds an error.
  Drop zone: the real file input stretched invisibly over a dashed box (`.dropzone` in
  input.css); `static/js/import.js` only shows the file name and the drag highlight. The
  "Where to get it" lines were checked against Archidekt's forum, the Moxfield-import guides
  and ManaBox's own guide (2026-09-28).
* Remember me: box first, in a row (`row-reverse`, because allauth writes the label first).
* Found on the way: the local gunicorn caches templates until `docker compose restart web`
  (its reloader only watches Python files), so a template edit can look like it did nothing.

### A2 — Accounts and mail: nothing may look shady (own PR, right after A)

What the user saw on 2026-09-28: the confirmation mail arrived in the inbox (not spam - SPF, DKIM
and DMARC work), but it "sieht shiet aus", and the page behind the link said *"Please confirm
that X is an email address for user X"* - which reads like phishing. Both are allauth's
defaults, untouched until now (only `templates/allauth/layouts/base.html` is overridden):

* The mail is allauth's `account/email/email_confirmation_signup_message.txt`: plain text, "Hello
  from goldfishlab.app! You're receiving this email because user <your address> has given your
  email address to register an account on goldfishlab.app. To confirm this is correct, go to
  <link>". The subject is `[goldfishlab.app] Please Confirm Your Email Address` (allauth prefixes
  `[<site name>]`; there is no Sites framework, so the name is the request's domain).
* The confirm page is `account/email_confirm.html`: a GET shows a question and a button, only
  the POST confirms (`ACCOUNT_CONFIRM_EMAIL_ON_GET` is False by default).

What changes (allauth **65.19.4**, checked in `.venv`):

1. **Every mail allauth can send gets our own text and a branded HTML version.** The adapter
   (`DefaultAccountAdapter.render_mail`) sends `<prefix>_message.txt` and, if it exists,
   `<prefix>_message.html` as the HTML alternative - so overriding the templates is enough, no
   code. The full list in 65.19.4 (`allauth/templates/account/email/`): `base_message`,
   `base_notification`, `email_confirmation(_signup)`, `password_reset_key`, `password_reset`,
   `password_reset_code`, `unknown_account`, `account_already_exists`, `login_code`,
   `email_confirm`, `email_changed`, `email_deleted`, `password_changed`, `password_set`.
   * One HTML base (`base_message.html`): table layout and inline styles only (mail clients drop
     `<style>` and external CSS), the palette (ink / parchment / blood), "Goldfish Lab" as a text
     wordmark (no remote images - clients block them), one big button, the plain link under it
     for clients that strip buttons, a one-line footer with the legal-notice link. Dark-mode-safe
     colours.
   * Wording: short, human, no "user X". E.g. *"Confirm your email - one click and your
     Goldfish Lab account is ready."* Forgot-password gets the same look ("Reset your password",
     valid for N days, "didn't ask? ignore this").
   * Subjects without the `[domain]` prefix (`ACCOUNT_EMAIL_SUBJECT_PREFIX = ""`), written
     out: "Confirm your email for Goldfish Lab", "Reset your Goldfish Lab password", ...
   * **Security notifications on** (`ACCOUNT_EMAIL_NOTIFICATIONS = True`): "your password was
     changed", "your email was changed" - standard practice, and they get the same template.
2. **Confirmation by link, without the confusing page** (decision D5 = link). The user clicks the
   link and sees "✓ Email confirmed" - no question, no button:
   * The confirm page (`account/email_confirm.html`, overridden) shows "Confirming your email…"
     and **submits its own POST** through a few lines of static JavaScript (`static/js/`, so
     `script-src 'self'` stays as it is). That is allauth's own advice for skipping the question
     without confirming on GET - verify the wording in the allauth docs at implementation
     (HABIT 4). Without JavaScript the same page shows one plain "Confirm my email" button.
   * **Rejected: `ACCOUNT_CONFIRM_EMAIL_ON_GET = True`.** Mail security scanners (Outlook Safe
     Links and others) open every link in a mail; on GET they would confirm an address nobody
     clicked - e.g. an account somebody else registered with your address.
   * `ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True`: in the browser that signed up, the click also
     signs in and lands on the saved deck (guest flow) or the deck list. Opened in another
     browser (the phone's mail app), the page says "✓ Email confirmed - sign in to see your
     deck" - the deck is already on the account, nothing is lost.
   * allauth's success message (`account/messages/email_confirmed.txt`) and the "check your
     inbox" page after sign-up (`account/verification_sent.html`) are rewritten in the same
     tone: "We sent a link to x@y. Click it and you're in."
3. **Sign-up asks for the password once** (`ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]`),
   the headline says "Free account - just an email and a password".
4. Tests: every mail prefix renders subject, text and HTML with a realistic context; none contains
   "user " + the address, "example.com" or a `[`-prefixed subject; the HTML has the link *and*
   the plain URL; and a guard that lists allauth's own `account/email/*_message.txt` and fails if
   one has no override - so an allauth upgrade that adds a mail cannot ship it unstyled.
5. Manual: a small script renders every mail to HTML files for a look in the browser (light and
   dark); then sign-up and forgot-password once for real on production, read on a phone and in
   one desktop client.

### A2 - what was built (2026-09-28)

* allauth 65.19.4's mail list, checked in `.venv`: 13 mails that are sent (each has a
  `_subject.txt`) plus the two bases. All 13 have our subject, text and HTML
  (`templates/account/email/`), the HTML on `base_message.html` with `snippets/button.html` and
  `snippets/code.html`. `email_confirm` is in allauth's template folder but nothing in 65.19.4
  sends it; it is overridden anyway, so the guard stays simple.
* `accounts/mail_samples.py` holds a realistic context per mail (mirroring allauth's
  `send_mail`, `send_confirmation_mail`, `send_notification_mail`); `manage.py preview_mails`
  writes them all to `screenshots/mails/` (gitignored), and `tests/test_mails.py` renders every
  one. The guard lists allauth's `*_subject.txt`, so a new mail in an upgrade fails the build.
* The email-change notice goes to the **old** address, so it says "write to us" instead of
  "choose a new password" (the reset mail would go to the new address).
* allauth's docs, verbatim, on `ACCOUNT_CONFIRM_EMAIL_ON_GET`: "To avoid requiring user
  interaction, consider using POST via Javascript in your email confirmation template as an
  alternative to setting this to True." That is `static/js/confirm-email.js`.
* Checked in the local stack (Playwright): sign-up -> "We sent a link to x" + "Check your inbox";
  the link in the same browser -> confirmed and signed in on the home page; in another browser ->
  confirmed, sign-in page; without JavaScript -> one "Confirm my email" button; a used link ->
  "This link doesn't work any more".
* `ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION` lands on `LOGIN_REDIRECT_URL` (the home page) for now;
  landing on the saved deck belongs to the guest flow (G).
* Found on the way: the simulation-start limit test flaked (404 instead of 429) the same way the
  admin one did - django-ratelimit's fixed window. The window pin is now a fixture for every
  limiter test in `tests/test_security.py`.
* Still open (A2.5, the user's part after the merge): one real sign-up and one forgot-password
  on production, read on a phone and in a desktop client.

### B — Remove the Collection

The production database has no collection rows (fresh since 2026-09-28), so this is a clean cut:

* Delete the `collection` app (models, views, forms, services, urls, templates, admin) with a
  migration that drops its tables; remove it from `INSTALLED_APPS` and `goldfishlab/urls.py`.
* Remove the "Against your collection" shortfall from the deck page, the collection import quota
  and any plan-page line about it (`billing/quotas.py`, `billing/services.py`, `plans.html`).
* `accounts/privacy.py` (data export and deletion) and `seed_demo_deck` stop touching it.
* Legal pages: the privacy policy's collection rows go (and "Last updated" moves); terms if they
  mention it. `tests/test_privacy.py` follows.
* `cards.Printing` / `--kind default_cards` stays (opt-in, unused) - removing ingestion code is
  not needed for the product and would touch the tested importer; documented as unused.
* Tests for the collection are deleted with it; everything else stays green.

### B - what was built (2026-09-29)

* Deleted: `collection/` admin, forms, services, urls, views; `templates/collection/`;
  `tests/test_collection.py`; the deck page's "Against your collection" panel and the collection
  branch of the mapping screen; the collection in the data export, `seed_demo_deck` and the
  screenshot list.
* **Two deploys, as Django's "How to delete a Django application" guide prescribes:**
  `collection/models.py` is empty and migration `collection/0004` drops both tables, but the stub
  app (`apps.py`, `migrations/`) stays in `INSTALLED_APPS` so that migration runs in production.
  Batch I removes the app, the directory and the stale content types
  (`remove_stale_contenttypes`).
* `decks/0005`: `PendingImport.Kind` is deck-only, and any waiting collection upload is deleted
  first (otherwise answering its mapping screen would have imported it as a deck).
* Legal pages: privacy (short version, the uploads row now describes deck files, Cloudflare,
  correction) and terms no longer mention a collection; both "Last updated 29 September 2026".
  403/429/data page wording too, and "spam or junk folder" on the sign-up page.
* Not touched, as planned: `cards.Printing` and `--kind default_cards` (they still resolve
  imports by exact printing); the import quota (it always counted deck imports too).
* 1053 fast tests green (1091 minus the collection's own, plus `test_the_collection_is_gone`).

### C — The casting priority leaves the interface (re-planned 2026-09-29, small)

Why (measured 2026-09-29 on the local decks): of the 183 cards in the Chainer deck, 119 carry an
open question, and every one of those 119 is `priority` ("no one said how early to cast it").
A "N cards need you" marker counting them would say "119" on every deck - useless and
discouraging. And the user does not want to direct a game ("für was ist denn das?"); they want
statistics over the deck: what gets drawn, by type, by community tag, by mana value (= E). The
priority only decides which spell the goldfish casts first when several are affordable; the
engine's default rule (cheapest first, `40 - mana value`) is a fine answer nobody has to give.

1. **No priority question anywhere in the UI.** The priority gap is no longer shown or counted:
   not on the tune page ("waiting on a call only you can make"), not on the report (the
   "calls nobody made" table), not in the blind-spot panel. The engine keeps recording it
   (stored runs and the parity test stay meaningful); only the pages stop showing it.
2. **The casting-order page goes** (`simulations:priority`, `PriorityEditView`, `PriorityForm`,
   `priority.html`, its links). Stored priority annotations keep working (the reference deck
   and `builtin` rows use them), they are just no longer edited through a screen of their own.
   The "How early to cast it" field moves behind "More" on the card page.
3. Docs: methodology page wording, README, the gaps docstring (`simulations/gaps.py`).
   Tests for the removed page (404) and that no report or tune page mentions the priority gap.

### C - what was built (2026-09-29)

* `simulations:priority` is gone: `PriorityEditView`, `PriorityForm`/`PriorityChoice`,
  `services.deck_priorities`/`set_priorities`, `priority.html`, its links on the deck page and
  the tune page, its two screenshots. `/decks/<id>/priority/` answers 404.
* The engine still **records** the priority gap (`_record_gaps` unchanged, so stored runs and
  the golden parity test mean what they meant) - it is just never shown: the run page has one
  score ("The engine read X of Y cards"), the judgement table and "Somebody had decided" are
  gone; the tune page counts and sorts by unreadable only; the card page lists
  `CardProvenance.reading_gaps`. The provenance row "Cast priority" stays but is no longer
  flagged "worth checking" (`provenance.SETTLED_BY_DEFAULT`).
* The card form keeps "How early to cast it" for now (C2 moves it behind "More"). Stored and
  built-in priorities keep working.
* Methodology, README and `simulations/gaps.py` say it: one score, cheapest first, why.
* Tests: the page is 404; neither tune nor card page puts the priority question; the run page
  shows "The engine read" and no "nobody has made".

### C2 — Deck status and the card review ("annotate") redesign (after E)

Counts **only cards whose mana the engine could not read** (the `reading` gaps: ~25 of 183 in
the Chainer deck, a handful elsewhere) - optional, never blocking. Decided 2026-09-29:

* A card counts as answered once the user has said anything about it - a saved value, or a new
  **"Looks right"** button (`CardAnnotation.confirmed`, a row carrying only that flag is kept,
  not deleted as empty). Needed because many reasons (hybrid pips, cost X) cannot be fixed by any
  field, so the engine's warning stays after a save.
* `Deck.open_questions` is nullable: NULL = not computed. Import, commander change, annotation
  save/forget (user scope: every deck of that user holding the card) and
  `ingest_scryfall --profiles` (all decks) set it to NULL; the list and deck page compute it when
  they find NULL. No data migration, nothing stale.

1. **A red marker per deck that is not fully read**, on the deck list and the deck page:
   "3 cards need you" (red) or "Ready" (green). One function in `decks/services.py`. Simulating
   and drawing stay possible either way (the user's "or not even").
2. **Review flow, one card at a time**, entered from the marker (stable queue: every card with
   a reading gap, by name, answered ones ticked; after the last open one back to the deck,
   "Ready"):
   * Right: the card image, and the card text underneath it (Oracle text, readable, not only
     the picture).
   * Left: "What does this card do?" - dropdowns and number fields built from the existing
     `AnnotationForm` (treated as, taps for N mana of which colours, draws N, tutors N to hand,
     enters tapped, ...), only the fields relevant to that card first, the rest behind "More".
   * Buttons: Save and next · Skip · Back. Progress "2 of 5". Deck/all-decks scope stays as a
     small toggle.
   * The field-by-field provenance table moves behind "Why does the engine think this?".
3. The tune page becomes a card grid with the same markers; its long preamble goes to the
   methodology page.

### C2 - what was built (2026-09-29)

* `CardAnnotation.confirmed` ("Looks right", migration `simulations 0003`) and
  `Deck.open_questions` (nullable, migration `decks 0006`). No data migration.
* `simulations/review.py`: `queue(deck)` - every card with a reading gap, by name, each marked
  answered when the owner has any row for it (this deck or all their decks; built-ins are
  nobody's answer) - and `open_questions(deck)`, counted once and stored with `update` so
  `updated_at` (the list order) does not move. It lives in `simulations`, not in
  `decks/services.py` as planned, because `simulations` depends on `decks` and not the other
  way round; `decks/services.recount_later(queryset)` is the one reset, called by the import,
  `set_commander`, `save_annotation`/`confirm_annotation`/`delete_annotation` (user scope: every
  deck of the owner holding the card, commander included) and `ingest_scryfall --profiles`.
* `services.confirm_annotation` sets the flag without touching the stored values (an empty
  form through `annotations.apply` would have removed them all); `save_annotation` keeps a flag
  already there, and a row carrying only it is kept.
* Marker `templates/decks/_status.html` on the deck list, the deck page and the tune page;
  `.deck-status-*` styles. It links to `simulations:review`, which redirects to the first open
  card or back to the deck, "Ready".
* The card page (`annotate`) is the review step: picture and Oracle text (first on a phone),
  the gap reasons, the fields that answer them first (`forms.ANSWERS`, plus land types for a
  land), the rest - "How early to cast it" among them - behind "More"; scope as a link toggle;
  Save and next, Looks right, Skip, Back, "2 of 5". After the last open card: the deck,
  "Ready". The provenance table sits behind "Why does the engine think this?".
* The tune page is a card grid, open cards first; the sources list moved to the methodology
  page (`#reading`).
* Tests: `tests/test_deck_review.py` (22) - the count, the four resets, Looks right keeping
  values, the queue walk, ownership.

### D — Deck page: actions first, pictures instead of paragraphs

Top to bottom:
1. Deck name, commander image, status marker, two big buttons: **Simulate** and **Draw a hand**
   (games/turns settings in a small "Options" fold).
2. Four small stat tiles: lands (with the in-band marker), average mana value, bracket, legality
   (✓ or "2 problems" opening the detail).
3. Mana curve (as now) and a **category bar**: count per type and per functional category, with
   the template band.
4. **Card grid with filter chips and search** - chips for every type and category ("Creature",
   "Ramp", "Removal", ...), a search box over name and text. The categories are searchable here.
5. Combos, legality detail, earlier runs and playtests: collapsed sections.

### D - what was built (2026-09-29)

Decisions by the user: a card in the grid opens its own card page (the one place it can be
corrected); the tune page ("What the engine reads") stays until I decides whether the grid makes
it redundant. Template + view + CSS only - no migration, no engine change.

* **`simulations/deck_cards.py`** (new): `cards(readings, questions)` - the grid, commander left
  out, open cards first; `Filters.from_request` (`?type=&cat=&q=`, unknown values ignored, query
  whitespace-collapsed and cut at `QUERY_MAX` = 100, matched in Python over name + Oracle text,
  case-folded - never SQL); `bars(grid)` - one row per printed type the deck has and always all
  eight categories, counted by quantity on one shared scale; `TEMPLATE` - the band. Types are
  `Card.types`, categories `Card.categories`, so the page and "What you drew" (E) sort a card
  alike, a role the owner replaced included. The land band is `KARSTEN_MIN`-`KARSTEN_MAX`
  (35-38, the constant the land tile judges by - the vocabulary table above said 36-38).
* **`DeckDetailView`**: one `adapter.readings` per request, shared by grid, queue and blind
  spots. An htmx request (not a history restore) gets `decks/_card_grid.html` alone and skips
  the analysis; every response carries `Vary: HX-Request`. `review_cards` is no longer read by
  the page (left in `decks/analysis.py` for I).
* **`decks/detail.html`** rewritten: header with commander picture, marker, **Simulate** (the
  run form, selects folded in "Options", which also holds the playtest's 1-v-1 box via
  `form="deal-hand"`) and **Draw a hand**; no commander -> the select form at the top. Four
  tiles, reasons behind "Why?". Mana curve beside "What is in it" (`decks/_bar.html`, a row
  links to its filter). Card grid with radio chips (the `seen-chip` styles from E) and a search
  box - a plain GET form; htmx swaps only `#card-grid` and pushes the URL. Folds: legality (open
  when there is a problem, the tile links to it), combos, "What the engine cannot model" (the
  old coverage paragraph, blind spots, import match), earlier runs and games. `#play` kept.
* CSS: `.tune-card-count`, `.deck-bar-*`; `.tune-grid` three across on a phone (two made a
  69-card deck ~9000 px). Fixed on the way: the mana curve's longest bar started left of the
  others (a full-width bar squeezed the label column).
* Methodology "What you drew" names the template band; README bullet.
* Tests: `tests/test_deck_page.py` (17); the coverage-panel test in `test_decks_views.py` now
  looks for the fold.

### E — Simulation: what was drawn, by category and by mana value

The engine records per turn today: lands, mana, life, hand size, milestones. It does **not**
record which cards were seen. New:

* **Engine instrumentation** (`simulation/analysis.py`): per game and turn, the cards *seen so
  far* (opening hand after mulligan + draws) counted per card type, per functional category and
  per mana value bucket. Aggregated like `turn_stats`: for each turn the mean count and the share
  of games with at least one. Categories are resolved once per deck in the adapter
  (`simulations/engine`), so the hot loop only adds small integers. `serial.py` and the chunk merge
  learn the new keys.
* **Charts** (server-rendered inline SVG):
  * "Seen by turn N" - one line per functional category, y = % of games with at least one
    (Ramp by turn 2, Draw by turn 3, ...). Chips toggle lines (CSS only).
  * Same for card types.
  * "Mana value of what you drew" - the curve of the cards in hand/played by turn, like the land
    chart, so a clunky hand shows as a bump on the right.
* Runs made before this exist without the data: the section says "Run again to see this" instead
  of breaking. `ENGINE_VERSION` bumps only if game behaviour changes (it should not); the golden
  parity test must stay unchanged - if it goes red, STOP (trap: never regenerate).
* Performance check: the hot loop must stay within ~5% of today's speed (measure before/after).

### E - what was built (2026-09-29)

* **Engine** (`simulation/`, no Django): `Card.types` (the printed front-face types, lower
  case, `cards.CARD_TYPES`; read by no rule). `analysis.seen_groups(deck)` lays out the groups
  once per run: `type:<type>`, `role:<category>` and `mv:0`..`mv:7` (7 = "7+", spells only - a
  land has no mv group). After every turn `_count_seen` counts hand + battlefield + graveyard +
  exile (bottomed cards and the commander are not seen). `run()` stores
  `result["seen"][key] = {"cards": [per turn], "games": [per turn]}` - summed cards (mean after
  / iterations) and games with at least one (share). `as_json`/`from_json`/`merge` carry it;
  a group only one chunk has is kept, a chunk without the block drops it from the merge.
* **Categories come from `Card.categories`, not from `Card.tags`.** Found on the first
  screenshot: card draw at 8% on the reference deck. Cause: **70 built-in annotations replace the
  role list** (from the original hand-written Chainer list, e.g. Phyrexian Arena = `draw_engine`
  only), and built-in annotations apply to every deck of every user. They exist so that the
  reference deck plays exactly as the fixture (game metrics read `tags`), so they stay - but the
  statistics must not follow them. `adapter._categories`: the community roles
  (`profile.role_tags`), unless the **user** replaced them (deck or user scope, an empty list
  included); a built-in replacement is ignored. `analysis.SEEN_CATEGORIES` is then a plain list of
  the eight shown roles (ramp, draw, removal, wipe, tutor, counterspell, protection, recursion) -
  a narrower community tag always carries its broader one, so nothing is folded together. (A
  first attempt folded `draw_engine` into draw etc.; the user asked why a third category, and the
  real cause came out - replaced by this.) Game behaviour unchanged, parity green.
* **New roles** `counterspell` (561 cards) and `protection` (1356) in `ROLE_FROM_TAG`.
  **Production needs `python manage.py ingest_scryfall --profiles` once after the deploy**
  (~45 s locally), or those two lines are simply absent.
* **Adapter**: `adapter.card_types(oracle_card)` - front face only ("Sorcery // Land" is a
  sorcery), Kindred ignored, never overridable (printed, not judged).
* **Page** (`report.seen`, `_report.html` "What you drew", `_line_chart.html`,
  `simulations/charts.py`): categories as % of games with at least one; **card types as the mean
  count** (deviation from the plan: the share is a flat line at 100% for creatures and lands);
  mana-value bars per turn with one shared scale, a turn picked by radio chips. Lines toggle by
  checkbox chips via `:has()` in `input.css` (8 colours, the last four dashed). The numbers sit
  under a "The numbers" `<details>`. An old run says "This run is older than this chart" and links
  to the deck page's new `#play` anchor.
* **Speed**: `scripts/bench_engine.py` (new). Reference deck, 5000 games x 6 turns: before 1004-1046
  usec/game, after 1023-1058 - inside the ~5% bar. `ENGINE_VERSION` unchanged, golden parity
  test unchanged and green.
* Methodology has a "What you drew" section; README mentions the charts and the bench script.
* Tests: `tests/test_draw_statistics.py` (groups, the count = hand kept + draws, counting changes
  no game, chunks in any order, old chunks, storage, type lines, chart geometry, report), plus
  two page tests in `test_simulations_runs.py`.

### F — Draw a hand, Hearthstone-style (clean, not elaborate)

* The hand as a **fan of real card images** at the bottom (CSS transforms; hover or tap lifts and
  enlarges a card). Battlefield above it in rows: lands, mana sources, other permanents - also
  images, smaller. Commander in its own slot. Library and graveyard as small stacks with counts.
* Click a card -> the actions it has (Play land / Cast / ...), as today, still real forms with
  htmx on top (works without JavaScript).
* Mana available as coloured pips; turn and phase as a slim bar. Mulligan / keep as two big
  buttons at the start.
* Phone: the fan becomes a horizontal scroll row.
* No animations library; a short CSS transition at most.

### F - what was built (2026-09-30)

Decisions by the user: a click on a card in hand plays it at once (Undo covers a slip); mana is
drawn as own CSS pips, not Scryfall's symbols; and (the user's addition) a card under the
pointer grows until it can be read. Template + view + CSS only - no migration, no engine change.

* **`playtest/views.py`**: `board_context` gives the battlefield as three rows
  (`BATTLEFIELD_ROWS`: lands, mana sources, creatures and other permanents together), graveyard
  and exile as piles, `pips(pool)` in `PIP_ORDER` (the colour pie, then colourless), and
  `deciding(game, live)` - no turn begun and only mulligans so far - because the engine has no
  "kept" flag. An htmx answer sets `board_messages`, so the fragment shows its own messages;
  the full page shows them once, in the base layout. **Found on the way:** an htmx action's
  error ("costs 4, the floating mana is 2") was never shown until a reload, and the test that
  looked for it passed on the hand's caption, which also said "floating mana".
* **`playtest/_board.html`** rewritten, plus `_pile.html` and `_hand_face.html`: the bar (turn or
  "Opening hand", life, phase stepper, pips, Next phase / "Start turn N", Draw, Undo, Redo); the
  opening as two big buttons, Mulligan and Keep, with how many cards keeping bottoms; the table
  (command zone and library left, rows in the middle, graveyard and exile right; the top card of
  a pile shown, all of it in a fold); the hand as a fan where each card is a submit button
  posting `play_land` or `cast_spell`. Log and branching folded. A draw-step line says to play
  the land first - the engine counts a turn's mana once, when the main phase opens.
* CSS (`.playtest-*`, `.pip-*`, `.board-card*`, `.card-back`, `.hand-*`): the fan turns each
  card about a point below the hand (less per card and more overlap as the hand grows, so
  fifteen fit), hover or focus straightens, lifts and scales it 1.8x with the action label
  below; table cards scale 3x under the pointer. Up to 48rem the fan is a sideways-scrolling row
  of straight cards. `prefers-reduced-motion` drops the transitions.
* Page intro cut to one line. Tests: `tests/test_playtest_board.py` (10); the unaffordable-cast
  test in `test_playtest_views.py` now looks for the message itself.

### G — Onboarding and trying it without an account

1. **Home page:** one sentence, two screenshots (the drawn hand and a simulation chart, real
   pages via `scripts/screenshots.py`, stored as WebP in `static/img/`), a three-step strip
   "Export from Archidekt -> Upload -> Simulate or draw", and a big **"Try it now - no account"**
   plus "Create an account".
2. **Try first, account only to save - decided by the user 2026-09-28** ("das Onboarding muss
   smooth af ablaufen"). The flow, exactly:

   ```
   Home: "Try it with your deck"  ->  drop a CSV (no account, no deck name asked)
     ->  the simulation starts by itself, report appears  (draw a hand works too)
     ->  a clear button: "Save this deck - free account, just email and password"
     ->  one short form: deck name (pre-filled from the file), email, password
     ->  "we sent you a link"  ->  click it (D5)  ->  signed in, on the saved deck's page
   ```

   * The wording says it plainly everywhere: **free**, **only email and password**.
   * **How it is built - a temporary guest user** (the "lazy sign-up" pattern), not decks with a
     nullable owner: on the first upload an unusable-password guest user is created and logged in
     for this browser session. Every existing query is already owner-filtered (the review checked:
     no IDOR), so decks, runs and playtests work unchanged and stay private to that browser. A
     nullable owner would have meant touching every queryset - exactly where an IDOR hides.
   * **Saving = claiming:** the save form creates the real account (allauth sign-up, so its
     validation, rate limits and verification all apply) and moves the guest's deck, runs and
     playtests to it, then deletes the guest. The hook (allauth `user_signed_up` signal vs. a
     custom sign-up view) and the order of allauth's session handling are verified in the
     `.venv` source before writing it - Django's `login()` flushes a session that belonged to
     another user, so the guest id has to be read before that.
   * Guest limits, stricter than the free plan (a "Guest" plan row): one deck, one run at a time
     with small numbers (e.g. 2,000 games, 6 turns) on the short queue, `django-ratelimit` per IP
     on guest creation, upload and run, and a global cap on queued guest runs so nobody can fill
     the workers. Guests never see billing or account pages.
   * Guests (and everything they own) are deleted after 24 hours by the Celery beat that already
     runs in `worker-short`.
   * The header shows a guest "Sign in / Create account" as for anybody signed out, plus the save
     button while a guest deck exists.
   * Privacy policy: what a guest leaves (the list, the run, a strictly necessary session
     cookie), deleted after 24 hours - text change + test.
3. The sign-up page itself also gets the "free, just email and password" line (A2.3).

### H — The text pass

Every remaining page against principle 1: plans, account data, run detail,
legal pages excepted (they are legal text). Removed explanations land on the methodology page.

### I — Clean-up (last, own PR; added at the user's request 2026-09-28)

Goal: code, docs and production carry nothing the overhaul left behind, so whoever reads the
repository next (a person or an agent) reads only what is true.

1. **Dead code.**
   * Find it with `vulture` (run ad hoc via `pipx run vulture`, not added as a dependency) and
     ruff's unused-code rules, then by hand: templates, partials, URL names and context
     variables no view uses any more (old tune page, `decks/_mapping_preview.html` /
     `review.html` if the new flow replaced them, the old playtest board markup), settings
     nothing reads, collection leftovers (quotas, `seed_demo_deck`, importer wording).
   * `cards.Printing` and `ingest_scryfall --kind default_cards` - only the collection's prices
     used them. Removed with a migration (production never ingested printings, so the table is
     empty), plus their tests and the step-7 note in GO-LIVE.
   * Hand-written CSS in `assets/css/input.css` nothing uses; Tailwind rebuild; `main.css` size
     before/after.
   * `requirements*.txt`: whatever nothing imports any more; `pip-audit` clean.
   * Celery tasks and beat entries of removed features; admin registrations of removed models.
   * Left by C (priority out): the judgement half of the score API that no page reads any more -
     `SimulationRun.judgement_gaps` / `cards_unjudged` / `cards_judged` / `judged_pct`, the same
     on `PlaytestSession` and `adapter.Conversion` (`cards_unjudged`, `judged`),
     `Reading.unjudged` - and their tests in `test_gaps.py`. `annotations.patch` stays if C2's
     review flow uses it (it posts only the fields it shows), else it goes too.
2. **Tests.** Tests of removed behaviour go; duplicate fixtures merge; every batch's new code
   has its tests. The fast suite took 6.5 minutes on 2026-09-28 - the 20 slowest
   (`--durations=20`) get a look.
3. **Docs (HABIT 5).** README (features, fresh screenshots, no collection), `DESIGN.md` and a
   regenerated `STYLEGUIDE.html` (new components: drop zone, card fan, markers, charts), the
   methodology page reading as one text after H, `docs/phases/README.md`, RESUME's traps
   consolidated (obsolete ones marked), GO-LIVE (collection and printings mentions),
   `.env.example`, `CLAUDE.md` if a convention changed, the project memory.
4. **A review pass over the whole phase-9 diff**, like the pre-launch review of 2026-09-25:
   * security - guest endpoints, paste import, review wizard: owner filters, rate limits, the CSP
     without any new exception;
   * accessibility - alt text on every card image, the hand usable by keyboard, contrast, focus;
   * performance - `loading="lazy"` on card images, query counts on the deck page and the card
     grid, simulation speed against the baseline measured in E.
5. **Screenshot pass** of every page at 1440px and 390px (`scripts/screenshots.py`) - signed
   out, as a guest, signed in - and every mail rendered once more.
6. **Production.** B's and I's migrations applied and the tables gone; `manage.py check
   --deploy` clean; the privacy policy's "Last updated" matches the last legal change; the go-live
   checks the user deferred (6.4, 6.6-6.9) done at the latest here.
7. Not done, on purpose: squashing migrations. Production exists and the gain is cosmetic.

## Order (confirmed by the user 2026-09-28)

A -> A2 -> B -> C -> D -> E -> F -> G -> H -> I, one PR each. **Re-ordered 2026-09-29** (user:
"nimm die drei punkte und passe den plan an"): **A -> A2 -> B -> C (priority out) -> E (what was
drawn) -> C2 (status marker + review) -> D -> F -> G -> H -> I** - the draw statistics are the
value the user asked for, so they come before the review flow. A, A2 and B first because they are
small, remove the worst first impressions and shrink the code the later batches touch. E before F
because the charts are the paid value; F and G are the "wow" for newcomers. I (clean-up) closes
the phase, at the user's request.

## Decisions

| # | Question | Answer |
|---|---|---|
| D1 | Guest mode | **Decided 2026-09-28:** try without an account (upload + simulation), then "Save" -> deck name -> email + password -> free account. See G2. |
| D2 | Collection | **Out** ("die Collections will ich raus haben") - deleted completely, code and tables (production has no data). |
| D3 | Launch timing (announcing the site) | Open - the user's call, does not block any batch. Recommended: after A-C. |
| D4 | Order | **As proposed** (A2 inserted for the mail findings). |
| D5 | Email confirmation | **Decided 2026-09-28: link** (not a 6-digit code). One click, "✓ Email confirmed", signed in when it is the same browser. Built as an auto-submitting confirm page, not as confirm-on-GET - see A2.2. |
| D6 | Casting priority | **Decided 2026-09-29: out of the interface** (engine default rule stays). The simulation's purpose is deck statistics - curve, what is drawn by type/tag/mana value - not steering a game. See C. The mana simulation itself stays. |

## Not in this phase

Opponent modelling, deck building/editing inside the app, prices, collection anything, a
JavaScript charting library, a native app.

## Still open from the go-live (before or alongside batch A)

* Step 9: the sign-up mail **arrived in the inbox, not spam** (user, 2026-09-28) - done. Still to
  see: one real simulation reaching `completed` on production.
* **Deferred by the user (2026-09-28):** step 6.4 (client address check with a phone), 6.6
  (`https://<env>.jcloud.ik-server.com` must not serve the site), 6.7 (restart `sqldb`, the
  superuser is still there), 6.9 (rollback test). 6.8 (`PUBLIC_BASE_URL` + the first pipeline
  deploy) is done: the deploy after PR #7 went green, public health check included.
* **Hotmail puts the account mails in Junk** (2026-09-28). The headers of the junked mail
  (2026-09-29): `spf=pass`, `dmarc=pass`, `compauth=pass`, but **`dkim=none (message not
  signed)`** and SCL 5. The DKIM key is published (selector `20260928`), so Infomaniak is not
  signing what the application sends through SMTP - DKIM signing has to be switched on for the
  domain in the Infomaniak Manager (the user's step), then one mail to Hotmail checked for
  `dkim=pass`. **Done 2026-09-29:** DKIM enabled; a fresh mail passes spf, dkim
  (`d=goldfishlab.app`), dmarc and compauth - and still lands in Junk. Authentication is
  complete; what is left is the new domain's reputation on a shared sending IP. Not code: mark
  "Not junk" and add the sender to safe senders; move DMARC to `p=quarantine` after about two
  weeks of clean reports; a dedicated sending service only if Junk persists for weeks (rejected
  by the user on 2026-09-22).
* Optional: Cloudflare redirect rule `www` -> apex.
* The styleguide change and the `prod.py` comment shipped with batch A's PR.
