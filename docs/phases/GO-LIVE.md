# GO-LIVE RUNBOOK

**This is the document to open when we do the launch together.** It is written for two people:
the user, who owns every account and every credential, and an agent session that has just
started and remembers nothing.

`RESUME.md` says where the project stands. This says what to *do* next, in order, with the
verification for each step and what its failure looks like. **Every step names who does it.**

Nothing here has been executed. The deployment path is unproven, which is the known and accepted
cost of the settled "go-live happens once, in Phase 8" decision. Budget real time.

---

## Before we start: what only the user can supply

Have these ready, or the sequence stalls in the middle. **Never paste a secret into the chat** —
put it straight into the Jelastic import form or a GitHub secret field (CLAUDE.md HABIT 1).

| Thing | Why | Notes |
|---|---|---|
| A **GitHub account** with a repo to create | Steps 1–4 | Under the main account, not a throwaway. **Public**, AGPL-3.0 (decided 2026-09-27) |
| A **domain or hostname** | `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | **`goldfishlab.app`, bought 2026-09-27** (goldfishlab.com was for sale at ~20,000; `.ch` read too local). `.app` is **HTTPS-only in every browser** - see step 6b |
| An **Infomaniak Service Mail** mailbox on that domain | **Signup does not work without it**, and it is the contact address the legal pages name — see step 0b | **CHF 2.29/month**, decided 2026-09-22. Host, username (the full address), password, From address |
| **Infomaniak Jelastic** access | Steps 5–7 | |
| A **Stripe account** | Step 10 | **Paid plans go live at launch** (decided 2026-09-25, after checking that EDHREC and 17lands charge without any public WotC permission) |
| **Your full name and a postal address** | Step 0a, typed into the Jelastic form at step 5 | A deliverable address - a PO box alone does not satisfy the UWG. Never in the chat, never in the repository |

**Known and accepted, not a blocker:** the terms and privacy policy were written 2026-09-25 against
the Swiss DSG, UWG and OR with sources (the "Legal" section below), but no lawyer has read them.
Worth a review once revenue is real.

---

## Step 0a — The operator's details *(user types them into the form; nothing to edit)*

**The pages are written** (2026-09-25): `/terms/`, `/privacy/` and a new `/imprint/` ("Legal
notice", linked in the footer). They name the operator from three environment variables, so a
home address never enters the repository:

| Variable | What | Jelastic form field |
|---|---|---|
| `LEGAL_OPERATOR_NAME` | Your full name | Operator's full name |
| `LEGAL_OPERATOR_ADDRESS` | Postal address, **comma separated**, one entry per line: `Musterstrasse 1, 8000 Zürich, Switzerland` | Operator's postal address |
| `LEGAL_CONTACT_EMAIL` | The Infomaniak mailbox from step 0b | Contact address |

* **Production refuses to boot while any is blank** (`core.E003`, same mechanism as the mail
  check): a privacy policy that names no controller is not one (DSG Art. 19), and anyone selling
  online must state a name, a postal and an email address (UWG Art. 3 para. 1 lit. s).
* **Verify, after step 6:** open `/imprint/`, `/terms/` and `/privacy/` - each shows your name,
  address and a working `mailto:` link, and none says "Not configured in this installation".

## Step 0b — Mail, or signup is broken on day one *(user supplies, agent configures)*

**Read this before deploying anything.** `ACCOUNT_EMAIL_VERIFICATION` is `"mandatory"`, so
allauth sends a confirmation during signup. With no mail host Django dials `localhost:25`, which
does not exist in the container, and **the signup form answers HTTP 500** — the site is inert
because nobody can create an account.

It was invisible for eight phases: development uses the console backend and Django's test runner
substitutes locmem, so the entire suite passes. Trap 43.

`core/checks.py` now turns this into a **refusal to boot** — `start.sh` runs `migrate`, which
runs system checks, so a production container with no `EMAIL_HOST` does not start at all. That is
deliberate: refusing to start is louder than serving a broken signup.

### DECIDED: Infomaniak Service Mail, CHF 2.29/mailbox/month

Settled with the user on 2026-09-22 — *"I want a serious mail from a serious provider. If
Infomaniak has it as well, perfect."* They do, so this is not a compromise for the sake of
staying on one bill.

**Why this and not the cheaper options.** Brevo's free tier (300/day) and Postmark's free 100/mo
both cover the volume, and both were rejected on purpose:

* **The mailbox has to exist anyway.** Step 0a needs a contact address that reaches a person, and
  the privacy policy *promises* one. So the marginal cost of application sending is **zero**, not
  CHF 2.29.
* **No new processor.** The privacy policy names who sees data - Infomaniak (hosting and mail,
  Switzerland), Stripe (payments), plus Scryfall (the browser loads card images) and Commander
  Spellbook (card names only) - and says the data sits in Switzerland. Any other sender adds a
  processor, a DPA, and an edit to a legal page *before* launch. "Free" stops being free once it
  costs a paragraph of legal copy and a transfer story.
* **Consistency.** The same reasoning picked Mistral over the American providers in Phase 7.

**What Infomaniak actually gives you, stated honestly.** Not a transactional-email platform —
there is no Infomaniak equivalent of SES or Postmark. It is a real mailbox plus **authenticated
SMTP**, which Infomaniak explicitly documents for sending from an application:

| Setting | Value |
|---|---|
| Host | `mail.infomaniak.com` |
| Port | **587 with STARTTLS** — their recommended standard. 465 + implicit TLS is tolerated but is `DJANGO_EMAIL_USE_SSL`, and the two are mutually exclusive (`core.E002`) |
| Username | the **complete** email address, not the local part |
| Password | the mailbox password |

That is exactly what `prod.py` already defaults to, so nothing needs changing in code.

**What you are knowingly giving up:** no bounce webhooks, no suppression list, no per-message
delivery analytics. At confirmation-and-password-reset volume that is telemetry nobody would
read.

**When to revisit.** The sending limits are sized for a person writing mail, not an application.
If signups reach a few hundred a day, or if bounce handling starts to matter, move to a
Postmark- or Mailgun-class service. **Four environment variables and no code** — the settings
are all `env()` reads in `prod.py`. At that point the privacy policy needs the third processor
named, which is the actual work.

**Non-negotiable either way:** a **real mailbox on the sending domain**, never an alias or a
free-provider address, with SPF, DKIM and DMARC correct. Infomaniak configures those for domains
it hosts. A mandatory confirmation that lands in spam is indistinguishable from a broken signup —
the person simply cannot get in and has no way to find out why.

**`goldfishlab.app` is registered at Namecheap (2026-09-27), not at Infomaniak.** So Infomaniak
cannot set the records by itself. Two ways, decide at step 0b:
* **Recommended: point the domain's nameservers at Infomaniak** (Namecheap → Domain List →
  Manage → Nameservers → Custom DNS, with the nameservers the Infomaniak Manager shows when the
  domain is added there). The registration stays at Namecheap; Infomaniak then writes MX, SPF,
  DKIM and DMARC itself, and step 6b's `A` records live in the same zone.
* Or keep Namecheap's DNS and copy every record the Infomaniak Manager lists (MX, SPF `TXT`,
  DKIM `TXT`, DMARC `TXT`) into Namecheap's Advanced DNS by hand. Turn off Namecheap's own
  email forwarding first - its MX records would compete with Infomaniak's.
* **Verify either way:** the Infomaniak Manager shows the domain's mail DNS as correct, and the
  step 6 signup mail lands in the inbox.

### One mailbox, two jobs

The same mailbox **sends** the confirmations and **is the contact address** the legal pages
name (`LEGAL_CONTACT_EMAIL`). So make it a readable address - `hello@<domain>`, not
`noreply@` - and use it as the From address too: people reply to confirmation mail, and the terms
send them to this address for refunds and data requests.

**It comes with duties, because the pages promise them:**

* **Refunds within 14 days of a first payment** (terms, "Refunds"). Refund in the Stripe
  dashboard (Payments → the payment → Refund) **and** cancel the subscription immediately there,
  so the plan ends when the money goes back.
* **Data requests answered within 30 days** (privacy, "Your rights") - most are buttons, the
  rest (e.g. changing the email address) come here.
* **Price or terms changes announced by email at least 30 days ahead** (terms, "When we change
  something").
* **Support mail deleted within 12 months** of the matter closing (privacy table).
* Read it at least weekly.

**The action:**

* **User:** order Service Mail and create the mailbox on the sending domain. Have the host,
  username (the *complete* address), password and From address ready for the Jelastic import
  form at step 5 — the node will not boot without them.
* **User:** confirm SPF, DKIM and DMARC are in place for the domain.
* **Agent:** nothing. `prod.py`, `.env.example` and `jelastic.jps.example` already carry these
  settings, and the manifest already defaults `email_host` to `mail.infomaniak.com`.
* **Verify, once the environment is up (step 6):** sign up with a real address and confirm the
  message arrives **in the inbox, not in spam**. That is the only test that covers DNS, and no
  amount of local testing substitutes for it.

---

## Step 1 — Create the GitHub repository *(user, then agent, then user)*

**Decided 2026-09-27: the repository is public, under the AGPL-3.0** (`LICENSE`). The product is
the running service, not the code; public code makes the simulation's claims checkable; and it
keeps branch protection, Actions minutes and the GHCR image free. The AGPL lets anyone run a
copy, but a copy run as a website must publish its own source - no closed fork. The name is not
licensed. Feedback goes through the issue forms in `.github/ISSUE_TEMPLATE/` (the footer's
"Report a problem"), with the email address in the footer and the legal notice for everyone
without a GitHub account.

The agent works as **`Riddmaker-claude-bot`** (GitHub MCP / git): it pushes to `dev`, never to
`main`. Changes reach `main` only through a pull request that the user approves and merges.

1. **User:** create the repo under the main account, **public, completely empty** - no README,
   no `.gitignore`, no licence (the first commit brings all three). Name it so the GHCR path
   works: the workflow lowercases `github.repository` and strips a leading dash.
2. **User:** Settings → Collaborators → invite `Riddmaker-claude-bot` (it gets write access,
   which the ruleset in 4. limits).
   **Done 2026-09-27: <https://github.com/Riddmaker/goldfishlab.app>**, bot invited. The GHCR
   image is therefore `ghcr.io/riddmaker/goldfishlab.app`.
3. **Agent:** `git init`, first commit, push `main` (this is the first CI run - step 2) and
   create `dev` from it. **Push as the bot, not as the user:** this machine's Git Credential
   Manager holds only the user's account (`Riddmaker`, the repo admin - pushing with it would
   bypass the whole review model). So the remote carries the bot's name,
   `https://Riddmaker-claude-bot@github.com/Riddmaker/goldfishlab.app.git`, and on the first
   push GCM asks for that account's credentials. **User:** choose *Token* in that dialog and
   paste a **classic** personal access token of `Riddmaker-claude-bot` with the scopes
   `public_repo` and `workflow` (the second is required to push `.github/workflows/`). Not a
   fine-grained token: those cannot reach a repository owned by another personal account. The
   token goes into the dialog only - never the chat. The bot must have **accepted the
   invitation** first (github.com/notifications, signed in as the bot). The working tree must be clean: `.gitignore` covers `.env`, `.data/`,
   `static/css/main.css`, `staticfiles/` and `jelastic.jps`.
   * **Verify before pushing:** `git status` shows no `.env`, no `.data/`, no `jelastic.jps`.
     A secret in the first commit is a secret in the history forever - and this history is
     public. The 2026-09-27 scan (names, local paths, email addresses, key-shaped strings) was
     clean; run it again if anything was added since.
4. **User:** Settings → Rules → Rulesets → New branch ruleset, target `main`, **Active**:
   * Restrict deletions; Block force pushes.
   * Require a pull request before merging, **1 approval**, dismiss stale approvals on new
     commits. The bot opens the PR, so it can never approve its own; the user approves.
   * Require status checks to pass: **`test`** (the job in `checks.yml`; it appears in the list
     after its first run). Require branches to be up to date.
   * Bypass list: **Repository admin** only (the user, for emergencies).
5. **User:** Settings → General → Features: Issues on. Settings → Security → **Private
   vulnerability reporting: Enable** (`SECURITY.md` sends reports there). Issues → Labels →
   create **`simulation`** (the "simulation looks wrong" form applies it; a missing label is
   silently dropped - `bug` and `enhancement` exist by default).
* **Verify:** a push by the bot straight to `main` is refused; a PR from `dev` shows the `test`
  check and cannot be merged before it is green and approved.

## Step 2 — Push, and watch CI *(agent watches, user has the browser)*

The workflow runs ruff, `djlint --lint`, `pip-audit`, then the **full** suite including the slow
statistical validation, then builds and pushes to GHCR.

* **This now passes.** It would not have before 2026-09-22: the gate was `djlint --check`, the
  *formatter*, which fails on 11 templates. Trap 39.
* **Verify:** `gh run watch`, or the Actions tab.
* **If the test job fails on CI but passes locally:** CI runs *with* `-m slow` and against a
  fresh Postgres with no printings. Both are deliberate.

## Step 3 — Make the GHCR package **Public** *(user)*

**The single most confusing failure in this list.** Jelastic cannot pull a private image without
registry credentials, and **the only symptom is a node that never comes up** — no error, no log
line, nothing pointing at the registry.

* **User:** GitHub → your profile → Packages → `goldfishlab.app` → Package settings → Change
  visibility → Public.
* **Verify:** `docker pull ghcr.io/riddmaker/goldfishlab.app:latest` from a logged-out shell, or simply
  open the package page in a private window.

## Step 4 — Add the `JELASTIC_WEBHOOK_PROD` secret *(user)*

* **User:** repo → Settings → Secrets and variables → Actions → New repository secret.
* **Careful:** when it is missing, the workflow prints a warning and **exits 0**. A missing secret
  therefore looks exactly like a successful deploy. Do not skip the verification.
* **Verify:** the "Trigger Jelastic redeploy" step does *not* say `skipping redeploy`.

## Step 5 — Import the Jelastic manifest *(user, with the agent reading the form back)*

* **Agent:** `cp jelastic.jps.example jelastic.jps` and replace **`YOUR_GITHUB_USERNAME`** — it
  appears in three `dockerName` lines, in `baseUrl` and in `SOURCE_CODE_URL` (the footer's
  "Source code" and "Report a problem" links). `jelastic.jps` is gitignored; the
  `.example` stays the committed one.
* **User:** Jelastic console → Import → upload `jelastic.jps`, then fill the form:

| Field | Value |
|---|---|
| Django SECRET_KEY | 50+ random characters. Generate it *in the form*, never in the chat |
| PostgreSQL Password | likewise |
| Allowed hosts | comma separated, **no scheme** |
| CSRF trusted origins | comma separated, **with `https://`** |
| SMTP host / user / password / From | from step 0b — **required**, the node will not boot without them |
| Operator's name / postal address / contact address | step 0a — **required** (`core.E003`); the address comma separated |
| Stripe secret + webhook secret | blank is fine; see step 10 |
| Sentry DSN | **leave blank** - the privacy policy says no error reporter is used; naming it there comes first |

* **What the manifest does on install:** creates the database node **with `pg_password` as the
  `webadmin` password** (the node's `password:` field - without it the platform invents its own
  password, emails it, and the app waits for a database it can never log in to), then restarts
  `cp`, `worker` and `worker-short` in that order.
* **There is no NFS storage node any more** (removed in the 2026-09-25 review). The first draft
  mounted one over `/var/lib/pgsql/data` in `onInstall`, which runs *after* the database node has
  initialised its cluster, so the mount would have hidden it behind an empty directory. A
  managed PostgreSQL node keeps its own data directory; losing the node is what backups are for
  (see "Immediately after" #1).
* **Redis may be password-protected.** The manifest's `REDIS_URL` has no password. If `/healthz/`
  says `"redis": "error"` at step 6b, take the password from the platform's email and set
  `REDIS_URL=redis://:<password>@<ip>:6379/0` on `cp`, `worker` and `worker-short`.
* **Migrations run from the web node only** (`start.sh`, `ROLE=web`). The workers poll until the
  schema is applied. Web and worker both calling `migrate` on a fresh database deadlock, and the
  symptom is an environment that simply never finishes starting.

## Step 6 — Verify the three untested assumptions *(agent drives, user has console access)*

These have **never** run against real infrastructure. Do them in order; each tells you something
different.

**6a. The public URL answers at all — the port 8080 rule.**
Jelastic routes the environment URL to `JELASTIC_PRIORITY_PORTS=8080`. Serving anywhere else
gives "connection refused" on the public URL with no other symptom. The Dockerfile exposes 8080
and gunicorn binds `0.0.0.0:8080`; this checks that the routing agrees.

```bash
curl -sI https://<env-url>/ | head -1        # expect HTTP/2 200
```

**6b. `/healthz/` reports both dependencies separately.**

```bash
curl -s https://<env-url>/healthz/
# {"status":"ok","checks":{"database":"ok","redis":"ok"}}
```

`degraded` names which one failed — that is the whole reason it reports them separately. It is
unauthenticated by design so uptime monitors can reach it.

**6c'. The database keeps its data across a restart of its own node.** The manifest no longer
mounts storage under PostgreSQL, so prove the node's own disk is what holds the data: create the
superuser (step 8) or any row first, restart `sqldb` from the console, and check the row is still
there before anybody's deck is.

**6c. A `cp` redeploy does not break database connections.**
`CONN_HEALTH_CHECKS` is on (`base.py`) because the Jelastic PostgreSQL node restarts on
redeploys; without it the pool hands out a dead socket and the first request after every redeploy
fails.

* **User:** restart the `cp` node from the console.
* **Agent:** hit `/healthz/` immediately as it comes back, then again. Both must be `ok`.
* Also confirm `worker` and `worker-short` came back and did not deadlock on the migration lock.

## Step 6b — `goldfishlab.app` with a real certificate *(agent builds, user buys and points DNS)*

**Not built yet - research done 2026-09-27, manifest change still to plan with the user.**
The environment's own `*.jcloud.ik-server.com` address has Infomaniak's certificate and works
as it is. The own domain does not, and on `.app` there is no plain-HTTP fallback at all: the
whole TLD is on the browsers' HSTS preload list, so without a valid certificate the site simply
does not open.

What the sources say:
* Let's Encrypt on Jelastic is an add-on that needs the domain **bound to the public IP of the
  environment's entry node** ([add-on README](https://github.com/jelastic-jps/lets-encrypt/blob/master/README.md)).
* It installs on load balancers (NGINX, HAProxy, ...) and the stock app-server stacks - **not on
  a custom Docker image like `cp`**. The README's answer for other stacks: put a load balancer in
  front and install it there.
* Public IPs and custom SSL need a **paid** Infomaniak Jelastic account
  ([Infomaniak FAQ](https://www.infomaniak.com/en/support/faq/2262/understanding-the-available-jelastic-cloud-resources-by-account-type)).
* The alternative without a public IP, "custom SSL via the shared load balancer", takes an
  uploaded certificate - a manual renewal every 90 days with Let's Encrypt. Rejected.

The plan to confirm before building:
1. Manifest: an NGINX load-balancer node (`bl`) in front of `cp` with a public IPv4, the Let's
   Encrypt add-on on it for `goldfishlab.app` (and `www.`, redirected). Check how the Jelastic
   NGINX balancer forwards to `cp`'s **8080** and that it sets `X-Forwarded-Proto` and
   `X-Forwarded-For` - `SECURE_PROXY_SSL_HEADER` and `DJANGO_TRUSTED_PROXY_COUNT=1` depend on
   both (trap 44 territory: a wrong count rate-limits everybody as one IP).
2. **User:** DNS (Infomaniak zone, or Namecheap if its DNS was kept - step 0b) - `A` record for `goldfishlab.app` (and `www`) to that IP.
3. `DJANGO_ALLOWED_HOSTS=goldfishlab.app,www.goldfishlab.app` (plus the jcloud host while
   testing), `DJANGO_CSRF_TRUSTED_ORIGINS=https://goldfishlab.app,https://www.goldfishlab.app`.
* **Verify:** `curl -sI https://goldfishlab.app/healthz/` → 200 with a Let's Encrypt
  certificate; the add-on's renewal job is listed; a `cp` redeploy keeps the site up.

## Step 7 — Load the card catalogue *(agent, on the `cp` node)*

**Not in the original sequence, and the site is useless without it.** A fresh production database
has the schema and the seeded `Plan` rows (migration `billing/0002`) — and **no cards at all**.
Nobody can import a deck until this runs.

```bash
# On the cp node:
python manage.py ingest_scryfall          # cards + tags; skips if unchanged
```

That is ~35,568 cards, 4,544 tags and 429,263 card-tag links, **and then one derived profile
per card** - the command rebuilds `DerivedProfile` whenever cards or tags changed or any card is
missing one. Until the 2026-09-25 review it did not, and nothing else did either: production
would have simulated every card as a colourless artifact while every deck page looked fine
(trap 44). After a deploy that changes `cards/profiles.py` but not the bulk files, run
`python manage.py ingest_scryfall --profiles`. **Printings are opt-in and
deliberately excluded** (`--kind all` leaves them out): 78.8 MB and worth deferring past the first
deploy. Without them, collection prices show "—" and everything else works — the degraded path is
exercised by the test suite on every run, so this is a supported state rather than a gamble.

To add them later:

```bash
python manage.py ingest_scryfall --kind default_cards
```

* **Verify:** the command's last line says `profiles derived`, and
  `python manage.py shell -c "from cards.models import *; print(OracleCard.objects.count(), DerivedProfile.objects.count())"`
  prints the same number twice. Then sign in and import a small deck: if cards resolve and the
  tune page reads its Swamps as lands, the catalogue is in.
* **Watch:** `DEBUG=True` makes ingestion peak at 95 MB because Django keeps every SQL statement.
  Production is `DEBUG=False`, so this should stay near 16 MB — but it is a 128 MiB cloudlet, so
  watch the node while it runs.

## Step 8 — Create an admin account *(agent runs, user types the password)*

```bash
python manage.py createsuperuser
```

* The user types the password directly into the prompt. **It must not appear in the chat.**
* `/admin/` is excluded from the CSP on purpose — Django's own templates carry inline scripts,
  and rewriting somebody else's templates to satisfy our header is not a security improvement.

## Step 9 — Run one real simulation *(agent + user)*

The last step of the original sequence, and the real end-to-end test: web enqueues, Redis carries,
a worker computes, progress lands in the database, the report renders.

* Import a deck, press Run, watch the progress bar.
* **Verify:** the run reaches `completed` and the report shows the opening-hand distribution
  against the exact hypergeometric.
* **Confirm it fits the cloudlet envelope.** Local speed is ~110 µs per game per turn, ~980 inside
  Docker at 6 turns. If production is far worse, the short queue is the thing to look at first.

## Step 10 — Stripe: paid plans go live at launch *(user)*

**Decided 2026-09-25.** Until this step is done, a plan with no `stripe_price_id` cannot be
bought and the plans page says "not configured yet" - a true statement, not a broken button.

1. **User:** create the products and prices in Stripe; put each price id on its `Plan` row in
   `/admin/`.
2. **User:** register the webhook endpoint at `https://<env-url>/billing/webhook/` and subscribe
   it to exactly the six events `billing/services.py:HANDLED` acts on:
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`,
   `invoice.payment_failed`. Anything else is recorded and ignored, so "all events" works too -
   it is just noise in the table.
2b. **User:** save a **Customer Portal configuration** in the Stripe dashboard (Settings →
   Billing → Customer portal) with *cancel subscription* and *switch plan* enabled and both paid
   products listed. The portal is the only place a subscription is cancelled or changed - a
   paying customer is deliberately never sent through Checkout again, because Checkout always
   starts a *second* subscription (billed twice; review finding B8). Without a saved
   configuration, `billing_portal.Session.create` fails and the "Open the billing portal"
   button answers "Stripe could not open the billing portal just now".
2c. **User:** Stripe dashboard → Settings → **Public details**: set the **Terms of service URL**
   to `https://<domain>/terms/` and the privacy URL to `https://<domain>/privacy/`. **Checkout
   fails without the first:** every session asks for the terms checkbox
   (`consent_collection.terms_of_service = required`, `billing/services.py`), and Stripe refuses
   that without a terms URL on file. Set the public business name to "Goldfish Lab" and the
   support email to the step-0b mailbox.
2d. **User:** in the portal configuration, **plan switches prorate** (the terms promise that
   what is left of the old plan is credited), and Billing → Revenue recovery keeps **Smart
   Retries** on for about two weeks, then **cancels** the subscription - the terms say a
   payment that never succeeds ends on the free plan.
2e. **User and agent: Stripe Managed Payments** (VAT - see
   [vat-managed-payments.md](vat-managed-payments.md); the code is built and tested, off by
   default). In order:
   1. Dashboard → Settings → **Managed Payments**: accept the Managed Payments terms and apply;
      wait for the eligibility review.
   2. Set tax code **`txcd_10103000`** ("SaaS - personal use") on both products.
   3. **Prices must include the tax.** Without a `tax_behavior`, Managed Payments *adds* the tax
      on top of CHF 4 - and the terms say prices "include any taxes that apply". Create the prices
      with "Include tax in price" (`tax_behavior=inclusive`; it cannot be changed on a price
      later), or set Settings → Tax → "Include tax in prices" before creating them. Check one
      test checkout with an EU address: the total must equal the plan price.
   4. Add the terms and privacy URLs in Settings → **Checkout** (Stripe's own buyer terms stay in
      the footer).
   5. **Only after approval** set `STRIPE_MANAGED_PAYMENTS=true` on `cp` and restart it. That also
      switches the terms, privacy and plans pages to "sold through Link" - never set it before,
      Checkout rejects the parameter on an unapproved account and every purchase would fail.
   6. **Test-mode purchase** (test card 4242..., Link passcode `000000`): the plan changes, which
      proves `customer.subscription.created` and `invoice.paid` still reach `/billing/webhook/`
      (inferred from the docs, never observed). Cancel it in the portal: the plan drops back.
   If Stripe declines: stop and decide with the user (the file lists the fallbacks) - do not sell
   to the EU/UK without a VAT answer.
3. **User:** copy that endpoint's signing secret into `STRIPE_WEBHOOK_SECRET` on the `cp` node.
   **It is per endpoint, not per account** — a test-mode endpoint and a live one have different
   ones, and using the wrong one fails every signature check with a message that looks like an
   attack.
4. **Verify:** Stripe's dashboard shows a 2xx for a test event, **and** a test-mode purchase in
   Chrome actually reaches Stripe's checkout page - that redirect is what the CSP `form-action`
   change of 2026-09-25 is for (trap 50), and only a real browser checks it. The webhook is the only writer of
   subscription status or plan; the success redirect grants nothing, and a test proves it by
   typing that URL.

---

## Immediately after: five things that are not optional for long

None of these can be built before the environment exists, which is why they are not done. **Do
them before the link goes anywhere people will actually use it, not after.**

1. **Postgres backups of the `sqldb` node, and a tested restore.** Nothing exists —
   no script, no schedule, no procedure. The phase document's own words: *an untested backup is
   not a backup.* Restore into a scratch environment and confirm data integrity; a backup nobody
   has restored is a belief, not a backup.
2. **Alerting on worker queue depth and failed simulation runs.** Nothing exists. A run that
   fails silently is the worst failure this product has, because the number it would have
   produced is the product.
3. **Backups kept at most 30 days, and logs overwritten within 30.** The privacy policy states
   both. Set the backup schedule's retention to 30 days or less when creating it (#1), and check
   the Jelastic log rotation for the load balancer and `cp` nodes; if a log lives longer, shorten
   it or change the policy the same day.
4. **The Infomaniak data processing agreement.** Generate it in the Infomaniak Manager (owner or
   admin role) and keep the PDF - the privacy policy says Infomaniak processes data under one.
5. **An external security-headers scan.** The headers are asserted against real responses by
   `tests/test_security.py`, but an outside grade checks the thing tests cannot: what a browser
   on the open internet actually receives.

---

## Legal: what was decided, and what was knowingly accepted (2026-09-25)

The pages were written from sourced research (Swiss DSG/UWG/OR/ZPO, the EDPB's territorial-scope
guidelines, the FDPIC cookie guide, the WotC, Scryfall and Commander Spellbook terms, and the
practice of comparable sites). What it came to:

* **WotC Fan Content Policy - a tolerated grey area, accepted.** It says "You can't require
  payments ... subscriptions ... to access your Fan Content" and allows ads, sponsorships and
  donations. Paid *capacity* is not addressed. EDHREC (Patreon-only "Load More" and filters) and
  17lands (Patreon-only stats) cite the FCP and gate features anyway; no public WotC permission
  exists for either, and no paid MTG analytics tool has ever been acted against (the known cases
  are card generators, proxies and NFTs). WotC can still say stop "for any reason". **If it
  does:** switch the paid plans off (clear the `stripe_price_id`s), refund what is open, and
  answer within days. The free plan stays complete, which is the part the FCP protects.
* **Scryfall - compliant.** Card data and images are free on every plan and to free accounts;
  images are hotlinked, never cropped or rehosted.
* **Commander Spellbook - credited and linked** in the footer and the terms, as it asks. It
  publishes no data licence; an email to confirm commercial use would remove the last doubt.
* **GDPR - not targeted, but compatible anyway.** CHF only, no EU marketing, no EU-specific
  pages: by the EDPB's own example (Guidelines 3/2018, Example 16) that is not "offering to" the
  EU, so no EU representative. The policy still carries legal bases, rights and an EU complaint
  route. **Adding EUR prices or EU marketing changes this** and needs a representative (Art. 27).
  **Open point for the go-live (found 2026-09-25 while building Managed Payments):** Managed
  Payments always turns on Adaptive Pricing, so *Stripe's checkout page* shows an EU visitor the
  price in EUR. Our own pages stay CHF-only, and the conversion happens only after someone has
  chosen to buy - the agent's view is that this does not by itself make the site "offer to" the
  EU, but it weakens the Example 16 argument. Options: accept and note it; or appoint an EU
  representative (commercial services, roughly EUR 100-300 a year). **Decide with the user
  before switching `STRIPE_MANAGED_PAYMENTS` on.**
* **EU consumer withdrawal - covered by a voluntary 14-day refund**, worth more than the
  statutory right. The EU's new online "withdraw" button (Art. 11a CRD, from 19 June 2026) is not
  built: it applies only when EU consumers are targeted (see above). With Managed Payments on,
  the consumer buys from Link, which applies statutory cooling-off periods itself.
* **Checkout** asks for the terms checkbox and says "Subscribe" on the button.
* **VAT - researched, solved by Stripe Managed Payments** (details, sources and the build
  checklist: [vat-managed-payments.md](vat-managed-payments.md)). Swiss VAT: nothing below CHF
  100,000 a year. **EU and UK VAT are owed from the first sale** by a non-EU seller of a SaaS
  subscription, and plain Stripe (even Stripe Tax) leaves the registration and filing with us.
  Managed Payments makes Stripe ("Sold through Link") the merchant of record, which registers,
  files and remits everywhere, for +3.5%. **Built 2026-09-25**, behind
  `STRIPE_MANAGED_PAYMENTS` (off by default); switched on only once Stripe's eligibility review
  approves the account (step 10, 2e). The terms, the privacy policy and the plans page render
  both states, and tests pin both.
* **Minimum age 13**; paid plans for adults or with a guardian's permission.

---

## Quick reference

```bash
# Local, before touching production
./.venv/Scripts/python.exe -m pytest              # full suite, 998 passing (2026-09-25)
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m djlint templates/ --lint
./.venv/Scripts/python.exe -m pip_audit -r requirements.txt
DJANGO_SETTINGS_MODULE=goldfishlab.settings.prod \
  DJANGO_ALLOWED_HOSTS=example.ch DJANGO_EMAIL_HOST=mail.example.ch \
  ./.venv/Scripts/python.exe manage.py check --deploy   # only W021 expected

# Production smoke
curl -sI https://<env-url>/ | head -1
curl -s  https://<env-url>/healthz/
curl -sI https://<env-url>/ | grep -i "content-security\|strict-transport"
```

**Failure symptoms worth recognising on sight:**

| Symptom | Cause |
|---|---|
| Node never comes up, no error anywhere | GHCR package is still private (step 3) |
| "Connection refused" on the public URL | Not serving on 8080 |
| Environment never finishes starting | Web and worker both migrating, or the schema poll never satisfied |
| Container exits at boot, complains about email | `core.E001` — no `EMAIL_HOST`. Working as designed (step 0b) |
| Signup answers HTTP 500 | Mail host set but wrong; check the TLS flags aren't both on |
| Deploy "succeeds" but nothing changes | `JELASTIC_WEBHOOK_PROD` missing; the step warns and exits 0 |
| Environment never finishes starting, `start.sh` logs "Waiting for the database" forever | The `sqldb` node's password is not `pg_password` - the manifest's `password:` line is missing |
| `/healthz/` says `"redis": "error"` | The Redis node wants a password; put it into `REDIS_URL` on all three app nodes |
| Cards do not resolve on import | `ingest_scryfall` has not run (step 7) |
| A report shows no lands, or every card "has no derived profile" | Profiles missing - `ingest_scryfall --profiles` |
| Prices all show "—" | Printings not ingested. Supported state, not a bug |
| Everyone rate-limited at once | `client_ip` reading the proxy instead of the client |
