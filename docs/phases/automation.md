# Automation and business (Themen-Doc, Paket 5)

Started 2026-11-06. Rows 9, 44, 46, 47, 48, 49, 50 and 51 of the Themen-Doc: the weekly Search
Console review, the weekly news, link import, the plans, the browser simulation and the new
vision ("know your deck like you played it since childhood"). Research in
`docs/research/paket5/` (import-links, automation, news, pricing, vision, pyodide); concept v1
in the Themen-Doc; every recommendation accepted by the owner on 2026-11-06.

| PR | What | Status |
|---|---|---|
| 20 | Search Console review: a weekly GitHub Action writes the report as an issue, in the private ops repository | **Done** (main); owner steps open |
| 21 | Hand tutor: "Which plan does this hand have?", rules only, three steps | **Done** (main, PR #88) |
| 22 | Weekly news: the page, the AI label, a Claude routine that opens the PR | **Built** (dev); sources and routine wait for the owner |
| 23 | Archidekt link import (after Archidekt agrees) and text-export guides; importer check in Celery Beat | Guides and check **done** (main, PR #88); link import waits for Archidekt |
| 24 | Pricing page: yearly plans first; a founder price only if the owner asks for it | **Done** (main, PR #88) |
| 25 | "Keep or mulligan?" for advanced players; every decision stored from day one | **Built** (dev) |

Decisions (2026-11-06, Themen-Doc E1-E7):

- The news is written by a Claude routine, not by Mistral on the server.
- Link import: Archidekt only, after a mail asking for permission. The other sites get a short
  "export as text" guide. Cloudflare is never circumvented.
- No plan with the user's own Mistral key: our AI costs about one Rappen per summary.
- No seats and no waitlist.
- Prices stay as they are; the pricing page shows the yearly plans first.
- No browser simulation. Measured: Pyodide runs the engine unchanged, but 1.5 times slower
  than the server, plus 6.4 MB to download. Kept in mind for self-hosters without a worker.
- The hand tutor is free for everyone, guests included. "Keep or mulligan?" comes after it, for
  advanced players. Later the decisions teach the engine:
  - "Our style" and "Your style", per user.
  - Better `Game.keepable` rules, only through a reviewed PR with a new engine version.

Changed from the concept: no workflow opens a pull request. "Allow GitHub Actions to create and
approve pull requests" stays off, as `forge-sync.yml` explains: the same right would let a
workflow approve the pull request into main. A job writes an **issue**. A change it leads to
comes as a normal PR from a session, which the owner merges.

Changed during the build (owner, 2026-11-06): this repository is public, and so would be the
issues, logs and artifacts of its workflows - every query and click. The weekly jobs are
**called workflows** now. The private repository `Riddmaker/goldfishlab-ops` calls them on a
schedule and holds the key, the switches and the issues. The Forge sync moved there too; it no
longer pushes the branch `forge-pin` (the caller's token cannot write here), it keeps the pin
files as an artifact and a session commits them to dev.

## PR 20 plan: the Search Console review

**Goal.** Every Monday, an issue "Search Console review 2026-Wnn" holds last week's numbers,
compared with the week before. It also lists the findings a few fixed rules see, so a session can
turn them into actions. No LLM in the job, no key on the server, nothing written to the repo.

**Steps.**

1. `scripts/search_console.py`. Pure functions, plus a small CLI:
   - Authentication: `google.oauth2.service_account.Credentials.from_service_account_info`
     with the scope `https://www.googleapis.com/auth/webmasters.readonly`, then an
     `AuthorizedSession`. The key comes from the environment variable
     `GSC_SERVICE_ACCOUNT_JSON` and is never printed or logged, not even in an error.
   - It calls the plain REST API, not the discovery client (`google-api-python-client` is large
     and not needed):
     - `searchAnalytics.query` for `sc-domain:goldfishlab.app`
     - `sitemaps.list`
     - The exact request bodies are checked against Google's docs during the build.
   - Windows: the last 7 days ending 3 days ago (the data lag), and the 7 days before them.
   - Findings, fixed rules, each tested:
     1. Totals (clicks, impressions, CTR, mean position) and the change week over week.
     2. The top 10 queries and pages by clicks.
     3. New queries: impressions this week, none last week.
     4. "Almost page one": mean position 8 to 20 with at least 20 impressions.
     5. Low CTR: position 5 or better and CTR under 2 %.
     6. Languages: clicks and impressions per URL prefix. English has none, the others are
        `/de/`, `/fr/`, `/it/`, `/es/`, `/pt-br/` and `/ja/` (read from `settings.LANGUAGES`,
        not hardcoded).
     7. Sitemaps: last read, errors, warnings.
     8. A fixed note: the data is 2 to 3 days late, and rare queries are left out.
   - Output: Markdown for the issue body, and JSON beside it.
   - `--fixture <dir>` builds the same report from recorded sample answers, so tests and a dry
     run need no network.
2. `requirements-seo.txt`, pinned: `google-auth` with `requests`. It is installed only in the
   job, never in the image. `checks.yml` runs `pip-audit` on it as well.
3. `.github/workflows/seo-review.yml`, built on `forge-sync.yml`:
   - Mondays at 05:23 UTC, plus `workflow_dispatch`. It is off until the repository variable
     `SEO_REVIEW` is `on`.
   - Job `review`: `contents: read`. It alone sees the secret, runs the script and uploads the
     report as an artifact.
   - Job `publish`: `issues: write`. It runs no Python and no pip, only `gh`. Every action is
     pinned to a commit. It opens or edits one issue per ISO week, labelled `seo`.
   - A failed run opens or comments on an issue "Search Console review failed".
4. `tests/test_search_console.py`:
   - Unit tests for every rule, the windows and the language split, using fixture answers.
   - A planted fake key must not appear in any output or error.
   - Workflow checks: the permissions are split, the secret appears only in `review`, every
     `uses:` is pinned to a commit, and the job switches on only when the variable is set.
5. Documentation:
   - This file gets "How PR 20 works".
   - `README.md` gets one line on the job.
   - A new `docs/SEQUENCE-SUPPORT-KPI.html` (gitignored, HABIT 5) gets a "Search Console"
     section. Each weekly review is summarised there by a session, with the actions taken.
6. Checks: `ruff check .`, `djlint templates/ --lint`, `pip-audit` on both requirement files,
   and the full suite. PR 20 has no page, so the browser check is a smoke run of `pr17.js` beside
   the suite. Then a dry run with `--fixture`, and push to dev.

**HABIT 1.**
- The service-account key exists only as an encrypted GitHub secret, read-only scope, in one
  job.
- The script reads it from the environment and never echoes it.
- The publishing job holds no secret and runs no third-party code.
- No write token on the server.

**HABIT 4.**
- The workflow copies `forge-sync.yml`: the privilege split, the issue per run and the off
  switch.
- The languages come from settings.
- The REST API is used directly instead of a second client library.

**HABIT 5.**
- This file, the README line and the KPI file.
- The Themen-Doc gets the status line.

**HABIT 7.**
- Tests as above.
- Linters and pip-audit clean.
- The dry run read as a person would read the issue.

**Edge cases.**
- No data yet (a new property): the report says so instead of failing.
- A query or page that exists in only one week: shown as new or gone, never divided by zero.
- An API error or a missing secret: the run fails with the HTTP status only, never the body,
  which could echo the request.
- A second run in the same week edits that week's issue instead of opening another.

**The owner's steps** (need your login; after PR 20 is on main, because the ops repository calls
the workflows from main):

1. GitHub → New repository: owner Riddmaker, name `goldfishlab-ops`, **Private**, "Add a README"
   on. Settings → Collaborators → Add people → `Riddmaker-claude-bot`, role **Write**. The bot
   then pushes the two caller workflows and the README.
2. In `goldfishlab-ops`: Settings → Secrets and variables → Actions → Variables → New repository
   variable `FORGE_SYNC` = `off` (`on` when the Forge sync should run weekly). In
   `goldfishlab.app` the variable `FORGE_SYNC` can then be deleted.
3. Search Console: Add property → Domain → `goldfishlab.app`. Copy the TXT value. At Cloudflare:
   goldfishlab.app → DNS → Records → Add record, type TXT, name `@`, content the value → Save.
   Back in Search Console → Verify. Leave the record in place.
4. Google Cloud console:
   - New project "goldfishlab-seo".
   - APIs & Services → Library → "Google Search Console API" → Enable.
   - IAM & Admin → Service accounts → Create, no roles.
   - Keys → Add key → JSON.
5. Search Console → Settings → Users and permissions → Add user: the service account's email,
   permission "Restricted".
6. In `goldfishlab-ops`: Settings → Secrets and variables → Actions:
   - New repository secret `GSC_SERVICE_ACCOUNT_JSON` with the whole JSON file.
   - Variable `SEO_REVIEW` = `on`.
   - Then delete the downloaded file.
7. `goldfishlab-ops` → Actions → "Search Console review" → Run workflow, once, to try it.

## How PR 20 works

- `scripts/search_console.py` (no Django; google-auth and requests from `requirements-seo.txt`,
  installed only in the job and in development): `windows` picks the seven days ending three
  days before the run and the seven before them; `fetch` asks `searchAnalytics.query` for the
  totals, the queries and the pages of each week (pages of 25,000 rows until a short one) and
  `sitemaps.list`, at `https://www.googleapis.com/webmasters/v3/sites/sc-domain%3Agoldfishlab.app`.
  `build` applies the rules, `markdown` writes the issue body. Out: `report.md`, `report.json`,
  `title.txt` ("Search Console review 2026-W46", the ISO week of the run).
- The rules: totals with the change (no percentage after a zero); new and gone queries; almost
  page one (position 8 to 20, 20+ impressions) and low CTR (position 5 or better, under 2 %,
  20+ impressions), each for queries and pages; clicks and impressions per language, by URL
  prefix (`LANGUAGE_NAMES` read from the settings file as text, so no Django in the job);
  sitemaps with errors and warnings (Google sends the counts as strings). The 20 impressions
  on low CTR were not in the plan: without them one impression at position 3 is a finding.
- Strangers type the queries: each one is a code span, backticks and pipes replaced, one line,
  200 characters at most, so no mention, link or table cell renders.
- The key (`GSC_SERVICE_ACCOUNT_JSON`) is read in one place. Every failure is one fixed
  sentence: not set, not a valid key, HTTP status, or the kind of a connection error; the
  libraries' messages (which may quote the key or Google's answer) are dropped with `from None`.
- `.github/workflows/seo-review.yml` and `forge-sync.yml` are `on: workflow_call` only.
  `review` checks out this repository's main, installs google-auth in one step and runs the
  script in the next, the only step that sees the key. `publish` (`issues: write`, no Python)
  validates the three files and the title, creates the label `seo` and edits that week's issue
  or opens it. `failed` opens or comments on "Search Console review failed". Every action is
  pinned to a commit; no job may write contents.
- The Forge sync: `check` as before; `publish` no longer checks out or pushes, the issue "Forge
  pin ready" names the run and the two commands (`gh run download <run> -R
  Riddmaker/goldfishlab-ops -n forge-pin`, then copy to `cards/data/`). The artifact is kept
  seven days instead of one.
- The callers in `goldfishlab-ops` (`.github/workflows/`): `forge-sync.yml` Mondays 03:17 UTC
  and `seo-review.yml` Mondays 05:23 UTC, each `workflow_dispatch` too, `if: vars.<SWITCH> ==
  'on' || github.event_name == 'workflow_dispatch'`, `permissions: contents: read, issues:
  write`, `uses: Riddmaker/goldfishlab.app/.github/workflows/<name>@main`; the review passes
  `GSC_SERVICE_ACCOUNT_JSON` by name, not `secrets: inherit`.
- `docs/SEQUENCE-SUPPORT-KPI.html` (gitignored) has the Search Console section: what a session
  does with each finding, and one row per week.
- Tests: `tests/test_search_console.py` (46): the windows and ISO weeks, the languages against
  the settings, every rule on `tests/fixtures/search_console`, the escaping, no data, the
  command, the endpoints and paging with a fake session, a planted key in four bad shapes and
  in an HTTP error, and the workflows (only called, pinned, no contents write, the writer
  without Python or secret, the key in one step, the validation before the issue).

## How PR 21 works

Built while the owner was away (2026-11-06, "decide in the project's interest"); the calls made
on the way are listed at the end.

- `decks/tutor.py`. A hand is a deal seed, dealt as the playtest deals (`deal`: the deck in the
  seed's shuffle, seven drawn). `play` keeps that seven in each of `GAMES` = 300 games
  (`Game.take_given_hand`, `analysis.simulate_game(hand=...)`: no mulligan, the rest of the deck
  shuffled anew, the games' seed from the deal's) and counts the games in which each engine of
  the deck page runs by turn 6 (`engine_watch.watches`, `BY_TURN`). `measure` adds the lands,
  the engine's keep rule for a free seven (`keeps_free_seven`) and the cards' costs, and puts
  the answer in the cache for a day, keyed by the engine version and the deck's fingerprint
  (`simulations.summary.fingerprint`: cards, commander, corrections).
- `explain` builds the page: per theme of the deck (strongest first) the cards in hand, those
  playable early (cost 3 or less and no more than the hand's lands and accelerants make), the
  share with this hand and the deck's usual share (its last run). The plan is the engine running
  most often; under 15 % nothing is a plan, and engines within 5 points of the best count as the
  plan too (300 games cannot tell them apart). The deck's own theme leads when it is one of them.
  Every sentence is a template in six languages.
- `decks.tasks.read_hand` plays the hand on the short worker (about a second); `tutor.ask` sends
  it once (`cache.add` as the lock). The page (`HandTutorView`, `/decks/<id>/tutor/?hand=&step=`)
  polls `#tutor-result` every second until the answer is there.
- Steps: 1 "Show me" (the answer), 2 "Guess with help" (the cards per theme, then a guess),
  3 "Guess alone". A guess is a button (`?guess=`), compared with the page's own answers only;
  "I don't know, show me" reveals without a word on it. Nothing is stored.
- The engine change is a new path only: a game without `hand` deals and mulligans exactly as
  before, so `ENGINE_VERSION` stays 38 and no number on the site moves.
- `Deck.card_images` (moved from the playtest view) gives the hand its pictures.
- Tests: `tests/test_hand_tutor.py` (30). Browser: the link, the waiting note, the three steps,
  a guess, "I don't know", a new hand, a deck without an engine that can run, phone, German.

Calls made on the way (PR 21):

- Always on the play: whether the hand is on the play or the draw changes little about which
  plan it has, and one switch less on a page for beginners.
- Signed-in only, guests included (`OwnedDecksMixin`): the guest trial's deck works, a stranger
  without a deck has nothing to read. 30 requests a minute per user.
- The help in step 2 shows the cards per theme but no numbers, so the guess is still a guess.
- A theme without an engine that can run is listed without a number and is never a plan.

## How PR 24 works

- `billing.views._tiers` adds `annual_month_label` (a year's price over twelve, in the
  visitor's currency) and `annual_saving` (how much less than twelve months, a whole percent).
- `billing/_tiers.html`, shared by /pricing/ and /billing/: a plan with a yearly price shows it
  first ("CHF 36 a year"), then "That is CHF 3 a month, 25% less than paying monthly.", then
  "or CHF 4.00 / month". On /billing/ "Pay yearly" is the filled button, "Pay monthly" the
  outlined one below it. A plan without a yearly price reads as before.
- No founder price (E4): only if the owner asks for one.
- Tests: `tests/test_plans.py` (the order, the month's equivalent and the saving).

## How PR 23 works

- `templates/decks/_import_form.html`: "Where do I get my list?", a fold with one line per site
  (Moxfield, Archidekt, ManaBox, TappedOut, MTGGoldfish and Deckstats): which export to choose
  and where it goes. We never fetch another site's pages (E2).
- `core.alerts.importers`, in the hourly alert check (at most one mail a day): over the last
  seven days, a file format with at least 5 imports whose rows went unfound at least 20 % of the
  time, and at least twice as often as in the four weeks before (or with no imports then). The
  mail names the format and the counts, never a file or a deck name. A site changing its export
  shows up as its imports failing.
- The Archidekt link import waits for Archidekt's answer. The mail for the owner to send:

> **To:** Archidekt (contact form or the Discord staff channel)
> **Subject:** Reading a public deck by its link, from goldfishlab.app
>
> Hi Archidekt team,
>
> I run goldfishlab.app, a free Commander goldfish simulator (source: github.com/Riddmaker/goldfishlab.app).
> Today players export their list from Archidekt and paste it into our import. We would like to
> let them paste the deck's link instead.
>
> What we would do: when a player pastes an archidekt.com/decks/<id> link, our server fetches
> that one deck once from archidekt.com/api/decks/<id>/, with a User-Agent naming us and a contact
> address, and keeps the list. No crawling, no search, no bulk requests: one request per paste,
> cached. The imported deck links back to the deck on Archidekt.
>
> Your terms ask not to send automated requests, and your staff said on the forum that the API is
> open for reading. Is this use fine with you? If you would rather we did not, we keep the text
> export.
>
> Thank you,
> [name], goldfishlab.app

## How PR 25 works

The owner accepted every recommendation on 2026-11-08 (E1-E5 below).

- `/decks/<id>/mulligan/?round=<seed>&n=<1..10>` (`MulliganQuizView`); `n=11` is the round's
  end, without a round a new one starts (`secrets`). Linked from the deck page and from the end
  of the hand tutor. Signed in only, guests included (E2); 60 GETs and 30 POSTs a minute.
- `decks/mulligan.py`. A round is 10 hands from one seed (`hand_seed`); every third is
  borderline (2 or 5 lands, up to 60 deals tried). On the play or the draw by lot with the
  hand (`on_the_play`: the seed's lowest bit).
- The comparison (E1): this hand kept, against a fresh seven under the rule for a mulligan
  taken (`Game.take_opening_hand(mulligans=1)`, `simulate_game(mulligans=1)`; the first
  mulligan is free in Commander, so nothing goes to the bottom). 1,000 games each way, 6 turns
  (about 4 s on the short worker). The measure is "a good start": by turn 4, at least 4 mana
  and 2 non-land permanents (the commander counts). An engine of the deck page running by
  turn 6 is shown beside it, but does not decide.
- The verdict: the shares with their 95 % margins; the difference within its 95 % margin, or
  under 3 points (`CLOSE_FLOOR`), is close, and either answer is right. No single game is
  shown as proof.
- The worker (`decks.tasks.read_mulligan`) starts when the hand is shown, so the answer is
  usually ready when the player has decided; it waits in the cache like the tutor's (deck
  fingerprint, engine version). The answer is a POST with a signed note (`shown_note`): which
  hand, shown when; the server measures the time to decide (capped at 10 minutes).
- `decks.models.MulliganDecision` (migration 0010): the user (cascade), the deck (set null,
  E3), round, position, seed, on the play, engine version, deck fingerprint, the hand, hand
  facts (lands, accelerants, mana values), deck facts (cards, lands, accelerants, commanders
  and their mana values, colours - not the list, not the name), the engine's own rule
  (`keeps_free_seven`), the choice, how sure (optional, 3 levels), the time, and once shown
  the result, the verdict and right/wrong. One answer per hand: the first stands. No IP,
  device or language. Read-only in the admin; in the data export.
- The round's end: right, close, still playing; the record over all rounds, by how sure
  (calibration). Only the player sees it.
- Privacy (E4, E5): a new row (purposes: the own record - contract; the mulligan rule from all
  answers together - legitimate interest; kept until the account goes, also after the deck);
  "Decisions about you" now says the quiz keeps a record that only the player sees and
  nothing follows from. The date moved to 2026-11-08; no new consent and no mail, as with the
  summary feedback: nothing changes for whoever does not play. The quiz page says what is
  stored before the first answer.
- Nothing is learnt yet. "Our style", "your style" and a better `Game.keepable` come later,
  a new rule only through a reviewed PR with a new engine version.
- The engine change is a new path only: `ENGINE_VERSION` stays 38.
- Tests: `tests/test_mulligan_quiz.py` (48). Browser: the links, a round of ten (borderline
  hands, polling, reload), the end, a new round, a second deck, phone, German, the privacy page.

Calls made on the way (PR 25):

- On the phone the hand is the sideways row the tutor and the playtest use.
- A decided hand is shown as it was decided; when the deck changed since, it says the hand
  cannot be judged and does not count.
- A judged hand reads its stored result, so a cleared cache never asks the worker again.

## How PR 22 works

Owner's decisions (2026-11-08):

- **N1, languages:** all seven, written by the routine.
- **N2, publishing:** the routine opens a PR into dev, and the week goes live with the next main PR.
- **N3, the page:** like the changelog page, plus a mail subscription. It can be stopped on "Your
  plan" and in every mail, and both mails link to all mail settings.
- **N4, reading:** open the articles and avoid fake news by naming key facts.

**Files and checks:**

- **One file per week,** `news/issues/<YYYY>-W<ww>.json`. Each item has:
  - a headline and a summary per language;
  - 2-5 `facts`;
  - 1-3 links (source, URL, original title).
- **`news/rules.py`** is plain Python and holds the rules. `python -m news.check` runs them (CI
  and the routine). With `--week` and `--sources` it also checks each fact against the text the
  item was written from, and finds any run of 8 words copied from it, in every language.
- **`tests/test_news.py`** loads the repository's own weeks, so CI refuses a broken file.

**The site:**

- **`news/issues.py`** reads the weeks once per process. A week waits for its `published` day.
- **`/news/` and `/news/feed/`** are built like the changelog's.
  - The label is at the top (AI, checked, editor named in the legal notice, takedown address).
  - Links carry `rel="nofollow noopener noreferrer"`.
  - The page is in `PUBLIC_PAGES` (sitemap) and in the footer.
- **The legal notice** names the operator as editorially responsible.
- **The privacy paragraph** names the second optional mail, and the export has the two new fields.

**The mail:**

- **`core/mailing.py`** now holds the subscription rules for both lists (`MailList`).
  - `changelog.services` keeps its old names as aliases. The changelog's salt is unchanged, so old
    mail links still work.
  - The changelog's three switch views take a list, so the news views are subclasses.
- **New account fields:** `news_mail`, `news_mail_since`, `news_mailed` (migration accounts 0009).
- **`news.digest` runs daily at 09:30.** It sends the newest week once while it is at most 7 days
  old, from the changelog's sender.

**The routine:** `news/ROUTINE.md` is its whole instruction. The routine's own prompt only points to
it, so every change to how the news is written is reviewed in a PR.

### Sources: what the terms allow (2026-11-08)

`docs/research/paket5/news-sources.html` and `news-sources-more.html`:

| Source | Finding | Verdict |
|---|---|---|
| EDHREC | terms: personal, noncommercial use only, no automated requests | out (ask by mail) |
| Commander's Herald | same terms as EDHREC | out (ask by mail) |
| Star City Games | terms forbid any scraper or robot | out |
| MTGGoldfish | robots.txt blocks AI agents, `ai-train=no`; terms: personal use | out |
| Card Kingdom blog | terms (read in a browser, 2026-11-09): personal, noncommercial use; robots only for search engines | out |
| Wargamer | terms exempt "use of the RSS feed(s) with the content supplied via those feeds", with a link | **in, feed only** |
| Hipsters of the Coast | robots.txt allows it; no terms found | owner reads terms |
| Draftsim, Dicebreaker, Cardmarket, MTG Arena Zone | terms or robots.txt forbid it | out |

So `SOURCES` starts with Wargamer alone, `articles=False`: the routine writes from what the feed
supplies, and the facts are checked against that text. A source is added, and `articles` switched
on, only once its terms allow it.

### Finding topics (2026-11-09)

More allowed sources were not found (`news-sources-new.html`: podcast feeds carry strategy, not
news; MTGRocks, Valnet, Kotaku and Dexerto forbid it), so the routine now finds topics with its own
web search, a fixed list of queries over the last seven days (step 3 of `news/ROUTINE.md`). A
result only points at a story: the routine never opens it and never writes from it. A story becomes
an item only when a source covers it; otherwise the pull request lists it under *Found, no allowed
source*, so the owner sees what the sources miss and whom to ask. `news.check` still wants every
fact in the saved text of a source, so a result's words cannot reach the page.

Ruled out (`news-discovery.html`, `news-reddit.html`): querying Google itself (its terms forbid
automated queries), Google News RSS (personal use), the Custom Search and Bing APIs (closed),
Reddit's RSS and API (RSS ends 2026-11-13, the API in March 2027, commercial use only under a
contract) and the Reddit archives (no terms). The YouTube Data API allows commercial use and is the
next step if search is not enough; it needs a key.

### Calls made

- **No separate news sender:** the news uses `CHANGELOG_FROM_EMAIL` and its login. A second address
  would need its own SMTP login (YAGNI).
- **Fail loudly:** an invalid week file makes the page fail rather than being skipped silently. CI
  refuses such a file before it can be merged.
- **Japanese:** gets its own summary length, 25-240 characters, because it is far denser than the
  other languages.
- **No sample week in the repository:** the first real week comes from the routine. The browser test
  used a temporary file, deleted afterwards.

