# Automation and business (Themen-Doc, Paket 5)

Started 2026-11-06. Rows 9, 44, 46, 47, 48, 49, 50 and 51 of the Themen-Doc: the weekly Search
Console review, the weekly news, link import, the plans, the browser simulation and the new
vision ("know your deck like you played it since childhood"). Research in
`docs/research/paket5/` (import-links, automation, news, pricing, vision, pyodide); concept v1
in the Themen-Doc; every recommendation accepted by the owner on 2026-11-06.

| PR | What | Status |
|---|---|---|
| 20 | Search Console review: a weekly GitHub Action writes the report as an issue | Planned |
| 21 | Hand tutor: "Which plan does this hand have?", rules only, three steps | Planned |
| 22 | Weekly news: the page, the AI label, a Claude routine that opens the PR | Planned |
| 23 | Archidekt link import (after Archidekt agrees) and text-export guides; importer check in Celery Beat | Planned |
| 24 | Pricing page: yearly plans first; a founder price only if the owner asks for it | Planned |
| 25 | "Keep or mulligan?" for advanced players; every decision stored from day one | Planned |

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

**The owner's steps** (needs your login; after PR 20 is on main, because scheduled workflows run
from the default branch):

1. Search Console: Add property → Domain → `goldfishlab.app`. Copy the TXT value. At Cloudflare:
   goldfishlab.app → DNS → Records → Add record, type TXT, name `@`, content the value → Save.
   Back in Search Console → Verify. Leave the record in place.
2. Google Cloud console:
   - New project "goldfishlab-seo".
   - APIs & Services → Library → "Google Search Console API" → Enable.
   - IAM & Admin → Service accounts → Create, no roles.
   - Keys → Add key → JSON.
3. Search Console → Settings → Users and permissions → Add user: the service account's email,
   permission "Restricted".
4. GitHub: Settings → Secrets and variables → Actions:
   - New repository secret `GSC_SERVICE_ACCOUNT_JSON` with the whole JSON file.
   - Variable `SEO_REVIEW` = `on`.
   - Then delete the downloaded file.
5. Actions → "Search Console review" → Run workflow, once, to try it.
