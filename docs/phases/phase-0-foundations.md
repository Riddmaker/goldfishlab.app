# Phase 0 — Foundations & DevOps

**Status: COMPLETE (2026-09-17).**
**Duration:** ~1 week
**Ships:** a styled, authenticated "hello world" running on `docker compose` locally, plus
complete deployment artifacts that are **not yet executed**.

**Out of scope:** all card data, all simulation UI, all deck logic — and, by explicit decision,
**the actual deployment.** Go-live happens exactly once, in Phase 8. Everything up to then runs
locally.

---

## Decisions settled in this phase

- **Python 3.13** (3.13.3), matching `3dnearme`.
  ⚠️ On this machine the bare `python` resolves to **3.11** — always use `py -3.13`.
- **Product name: Goldfish Lab**, directory and GHCR path `goldfish-lab`, Django package
  `goldfishlab`.

---

## Deferred to Phase 8 (was originally in this phase)

These were written here and are ready, but are deliberately **not run** until the end:

1. Create the GitHub repository (user action, main account).
2. Make the GHCR package public — Jelastic cannot pull a private image.
3. Add the GitHub secret `JELASTIC_WEBHOOK_PROD`.
4. Import `jelastic.jps` into the Jelastic console.

*Accepted trade-off:* the deployment path stays unproven until Phase 8, so anything wrong with it
surfaces late. Mitigated by the container serving on **8080 locally every day**, which exercises
the one Jelastic rule most likely to be got wrong.

---

## Tasks

### 1. Project skeleton

Mirror the sibling `3dnearme` project:

```
<project>/                settings/{base,dev,prod}.py, urls.py, celery.py, wsgi.py
accounts/                 custom User (email login)
core/                     base templates, styleguide view, healthz
templates/components/
assets/css/input.css      design tokens
scripts/get_tailwind.py   copy, bump the pinned version
simulation/               vendored unchanged from magic-project
tests/                    the existing 65 + new Django tests
docs/phases/              these files
```

- `django-environ` for settings; **no secret ever has a hardcoded fallback**.
- Custom `accounts.User` with email login **from the first migration**. Swapping `AUTH_USER_MODEL`
  later is one of the genuinely painful migrations in Django.
- `django-allauth` for registration, login, password reset, email verification.
- Pinned `requirements.txt` + `requirements-dev.txt`. **scipy goes in dev only** — nothing in
  `simulation/` imports it, and it costs ~90 MB in an image running on 128 MiB cloudlets.
- `pyproject.toml` with ruff, pytest-django, djlint.

### 2. Billing models — yes, in phase 0

Not the payment flow, only the shapes. Retrofitting these is the expensive mistake:

- `Plan` — seeded by **data migration** with a `free` row. Limits live **in the DB, not
  `settings.py`**, so changing a limit is not a deploy.
- `Subscription` — created for every user at signup, pointing at `free`. If this is added later,
  every quota check owns a nullable branch forever.
- `UsageRecord` — append-only monthly counters.
- `billing/quotas.py::check(user, metric, amount) -> QuotaDecision`, raising `QuotaExceeded`.
  No call sites yet; the function and its tests exist.

### 3. Design system

Follow the `@theme static` / `:root` role-variable pattern from `3dnearme/assets/css/input.css`.

- Tokens: `ink` (near-black ground), `parchment` (warm off-white), a desaturated `blood` accent,
  `--font-display` as a **self-hosted** serif (EB Garamond or Cormorant — self-hosted, not a
  Google Fonts request, for privacy and for offline dev).
- Paper texture as a CSS gradient, **not** an image asset.
- Tailwind v4 standalone binary via `scripts/get_tailwind.py`. No Node, no `package.json`.
- A live `/styleguide/` page showing every token, component and state.
- **Port `tests/test_design_tokens.py` from 3dnearme** — WCAG AA contrast enforcement.
  Parchment-on-dark is exactly where contrast quietly fails, so this test earns its place immediately.

### 4. Docker & local development

- `Dockerfile` — `gunicorn --bind 0.0.0.0:8080`. **Deliberately 8080, not 3dnearme's 8000.**
- `docker-compose.yml` — services `web`, `worker`, `redis`, `db` (postgres:16), `tailwind` (watcher).
  Source bind-mounted for live reload; Postgres healthcheck with
  `depends_on: condition: service_healthy`.
- `docker-compose.prod.yml` — no port exposure on `db`, `restart: always`, no source mounts.
- `.gitattributes` forcing LF, explicitly `*.sh text eol=lf` (shell scripts run in Linux containers).
- `.env.example` complete and commented; `.env` gitignored.

### 5. Database resilience

In `DATABASES["default"]`:

```python
"CONN_MAX_AGE": 60,
"CONN_HEALTH_CHECKS": True,   # the Django analogue of pool_pre_ping
```

Without this, the Jelastic DB node restarting on redeploy hands out dead sockets. This is a
documented failure from the reference project, not a theoretical concern.

### 6. Celery wiring (no real tasks yet)

- `celery.py`, Redis broker, one `ping` task to prove the path end to end.
- `task_acks_late=True`, `worker_prefetch_multiplier=1`, `worker_max_tasks_per_child=50`.
- `close_old_connections` on `task_prerun` / `task_postrun` — long-lived workers are the classic
  source of "server closed the connection unexpectedly".
- Queues `sim_short` and `sim_long` declared now, used in phase 3.

### 7. Health and observability

- `/healthz/` — checks DB and Redis, returns JSON, **no auth**, excluded from logging noise.
- `logging` module only, never `print`. Format matching the reference project.

### 8. CI/CD

`.github/workflows/deploy-prod.yml`, based on the reference project's workflow:

- Trigger on `push: branches: [main]`, `paths-ignore: ['**.md', 'LICENSE', '.gitignore']`.
- `concurrency: {group: deploy-prod, cancel-in-progress: true}`.
- **Add a test job the reference project does not have:** `ruff check`, `djlint --check`,
  `pytest -m "not slow"`. Build only if it passes.
- Lowercase the image name and strip a leading dash — **reuse that step verbatim**, GHCR paths
  must be lowercase.
- Tag both `:latest` and `:${{ github.sha }}`.
- Final step curls `JELASTIC_WEBHOOK_PROD`, with a graceful warning if the secret is unset.

### 9. Jelastic manifest

`jelastic.jps.example` extending the reference topology with two extra node groups:

```
cp      docker, GHCR image, JELASTIC_PRIORITY_PORTS=8080    1 + 4 cloudlets
worker  same image, CMD → celery worker                     1 + 8 cloudlets
sqldb   postgresql 16                                       1 + 6 cloudlets
storage NFS volume /pgdata, mounted before PG first init    1 + 1 cloudlets
cache   redis                                               1 + 2 cloudlets
```

- Secrets as `settings.fields`, entered at import time, **never hardcoded**.
- `onInstall`: stop `sqldb` → mount `/pgdata` → start `sqldb` → restart `cp`.
  The mount must exist before Postgres initialises its data directory the first time.
- **`migrate` runs from the web node's `start.sh` only.** Web and worker migrating concurrently
  on first boot deadlock on the migration lock. The worker's `start.sh` waits for Postgres
  *and* for the latest migration row to appear before starting.

---

## Verification

**Engine untouched:**
```bash
python -m unittest discover -s tests -t .      # 65 tests, green
pytest -m "not slow"                            # same suite under pytest-django
ruff check . && djlint templates/ --check
```

**Local, via Docker — the required path:**
```bash
docker compose up --build
```
- `http://localhost:8000/healthz/` returns DB and Redis healthy
- Register a user, verify email flow, log in, log out
- `/styleguide/` renders every token in light and dark
- `docker compose exec worker celery -A <project> inspect ping` answers

**Playwright screenshots** (to the scratchpad, never the repo) — take and *look at* them:
- `/styleguide/` at 1440px and at 390px (phone)
- Login page, registration page, an authenticated landing page
- Confirm the parchment-on-ink palette actually reads well, and that the serif loads
  (a missing self-hosted font silently falls back and looks wrong)

**Production:**
- Image builds and pushes to GHCR, package set to public
- Jelastic environment imports cleanly from the manifest
- **The public URL answers — this is the port-8080 check, and the single most likely thing
  to fail.** If it says "connection refused", gunicorn is not on 8080.
- Redeploy the `cp` node and confirm the app still reaches the database afterwards
  (the `CONN_HEALTH_CHECKS` check)

---

## Definition of done — all met

- ✅ `docker compose up`, then register, log in, log out, all styled.
- ✅ `/healthz/` reports database and Redis separately.
- ✅ 116 tests green (65 vendored engine tests, byte-identical + 51 new), ruff and djlint clean.
- ✅ 18 WCAG-AA contrast pairings enforced by test, passed first try.
- ✅ `.env.example` documents every variable the app reads.
- ✅ CI workflow and `jelastic.jps.example` written and reviewed (execution deferred to Phase 8).

## What the screenshots caught that nothing else did

All three sat behind **HTTP 200 and a fully green test suite**:

1. **No CSS served at all.** Django serves static files in DEBUG only through `runserver`'s
   handler; compose runs **gunicorn**. Removing WhiteNoise from dev middleware (to silence a
   warning) silently killed every stylesheet — `main.css` returned the 404 page as `text/html`.
   Fix: keep WhiteNoise in dev with `WHITENOISE_USE_FINDERS = True`.
2. **allauth pages did not inherit the layout** — raw browser defaults on white. Fix: override
   `templates/allauth/layouts/base.html`, and override **`main`**, not `content`, because
   allauth's own templates fill `content` and would discard any wrapper placed there.
3. **`{% extends %}` must be the very first tag** in a template; a leading `{% comment %}` raises
   `TemplateSyntaxError`.

## Known gap

The CSS claimed a self-hosted serif; none was added, so Georgia renders via the system stack.
The claim has been corrected in `assets/css/input.css`. Vendoring EB Garamond (OFL) into
`static/fonts/` is a Phase 0b item.
