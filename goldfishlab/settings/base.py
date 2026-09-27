"""Base settings shared by all environments.

All secrets and environment-specific values come from environment variables
(see .env.example). A local .env file is read if present; real environment
variables always take precedence.
"""

from pathlib import Path

import environ
from celery.schedules import crontab
from csp.constants import NONE, SELF

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env("DJANGO_DEBUG")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    # Only for `intcomma`: a plan that allows 1000000 games reads as a typo.
    "django.contrib.humanize",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "allauth",
    "allauth.account",
    "csp",
    "django_celery_results",
    "accounts",
    "billing",
    "cards",
    "decks",
    "simulations",
    "playtest",
    "collection",
    "combos",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "goldfishlab.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.operator",
                "core.context_processors.payments",
                "core.context_processors.source_code",
            ],
        },
    },
]

WSGI_APPLICATION = "goldfishlab.wsgi.application"

# --- Database -------------------------------------------------------------
# CONN_HEALTH_CHECKS is the Django analogue of SQLAlchemy's pool_pre_ping.
# The Jelastic PostgreSQL node restarts on redeploys, cloudlet changes and
# maintenance; without this the pool hands out a dead socket and the first
# request after every redeploy fails.
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# --- Scryfall ---------------------------------------------------------------
# Scryfall requires a descriptive User-Agent AND an Accept header on every
# request; omitting either answers HTTP 400 on /cards/collection. Identifying
# the application is also simply their stated condition of use.
SCRYFALL_USER_AGENT = env(
    "SCRYFALL_USER_AGENT", default="GoldfishLab/0.1 (+https://goldfishlab.app)"
)

# --- Stripe -----------------------------------------------------------------
# Empty by default and empty everywhere but production. `billing.stripe_api`
# asks `is_configured()` before offering anybody a payment, so development, the
# test suite and the screenshot pass all run with no keys and no mocking - an
# upgrade page that says "not configured yet" is a true statement about this
# installation rather than a broken button.
#
# The webhook secret is per endpoint, not per account: a test-mode endpoint and
# a live one have different ones, and using the wrong one fails every signature
# check with a message that looks like an attack.
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
# Publishable, and therefore the one value here that may appear in a page.
# Unused today: hosted Checkout is a redirect, so nothing runs Stripe.js.
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", default="")
# Stripe Managed Payments: Stripe ("Sold through Link") becomes the merchant of
# record and owes, files and pays the EU/UK/... VAT that a Swiss seller of a
# subscription would otherwise owe from the first sale. Off until Stripe's
# eligibility review has approved the account - Checkout refuses the parameter
# before that. The terms, the privacy policy and the plans page read this too,
# because who sells the plan changes with it (the operator's VAT notes have the steps).
STRIPE_MANAGED_PAYMENTS = env.bool("STRIPE_MANAGED_PAYMENTS", default=False)

# --- The operator, for the legal pages ---------------------------------------
# Who runs this installation: the controller in the privacy policy, the
# identity the Swiss UWG Art. 3 para. 1 lit. s requires of anyone offering
# services online, and the address the terms name. Environment variables and
# not template text, so a private person's home address never enters the
# repository - it is public on the site, but it does not need to be public in
# every clone, fork and CI log as well.
#
# The address is a comma-separated list, one entry per rendered line:
# "Musterstrasse 1, 8000 Zürich, Switzerland". `core.E003` refuses to boot
# production while any of the three is blank (`LEGAL_DETAILS_REQUIRED`).
LEGAL_OPERATOR_NAME = env("LEGAL_OPERATOR_NAME", default="")
LEGAL_OPERATOR_ADDRESS = env.list("LEGAL_OPERATOR_ADDRESS", default=[])
LEGAL_CONTACT_EMAIL = env("LEGAL_CONTACT_EMAIL", default="")
LEGAL_DETAILS_REQUIRED = False

# Where this installation's source code is published, e.g.
# "https://github.com/Riddmaker/goldfishlab.app". The code is AGPL-3.0: whoever runs
# a modified copy as a website must offer its users that copy's source, so a
# fork sets its own repository here. The footer then links the code and the
# issue tracker ("Report a problem"); blank hides both links.
SOURCE_CODE_URL = env("SOURCE_CODE_URL", default="").rstrip("/")

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- allauth --------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
# accounts.User sets `username = None`. allauth defaults this setting to
# "username" and calls User._meta.get_field() on it, which raises
# FieldDoesNotExist on every signup/login page. None tells allauth the model
# genuinely has no username field.
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"

# --- Celery ---------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = "django-db"
# Every chunk's result is a row in django_celery_results, kept until something
# deletes it - and before the 2026-09-25 review nothing did: Celery's own
# `celery.backend_cleanup` only runs from beat, and there was no beat. Beat now
# runs embedded in the short-queue worker (`CELERY_BEAT=1`, start.sh), and it
# schedules the cleanup by itself, daily at 04:00, whenever this is set. A day
# is far longer than any run lives, which is all a chord needs its rows for.
CELERY_RESULT_EXPIRES = 60 * 60 * 24
CELERY_TASK_ACKS_LATE = True
# One worker must not hoard 50 chunks of a simulation while another sits idle.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Reclaim memory fragmentation; the reference project hit a 280 MiB idle RSS.
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50
CELERY_TASK_SOFT_TIME_LIMIT = 120
CELERY_TASK_TIME_LIMIT = 180
# What happens to a chunk whose worker is killed mid-flight. With Redis as the
# broker, an unacknowledged message is only redelivered after the visibility
# timeout, and kombu's default is ONE HOUR - so a worker restart left a run
# frozen at 80% with no way to tell it apart from a hang. Measured, not
# assumed: killing a worker mid-run stalled a 50,000-game run at 40,000.
#
# The value must stay comfortably ABOVE the hard task time limit above. If a
# task can still be running when its message becomes visible again, the broker
# hands the same chunk to a second worker, and the run counts those games
# twice.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 600}
# Declared now, used from Phase 3: a dedicated short queue is what stops one
# large run from blocking every small one. No quota system fixes that.
CELERY_TASK_DEFAULT_QUEUE = "sim_short"
# The retention periods the privacy policy states, enforced rather than hoped
# for: expired sessions, abandoned uploads and old Stripe event payloads. Daily,
# on the short queue, from the beat embedded in worker-short. A crontab and not
# a 24-hour interval: beat's schedule file lives in /tmp and starts afresh on
# every deploy, and an interval would restart its count each time - deploy more
# than once a day and it never fires. Celery's own backend_cleanup is a crontab
# for the same reason.
CELERY_BEAT_SCHEDULE = {
    "housekeeping": {
        "task": "core.housekeeping",
        "schedule": crontab(minute="30", hour="4"),
    },
}

# --- i18n / static --------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Zurich"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache ------------------------------------------------------------------
# Redis, and it is NOT an optimisation. Two security controls are counted in
# it - allauth's own login/signup/password-reset limits and the ones this
# project adds in `core/ratelimit.py` - and Django's default cache is
# LocMemCache, which is per PROCESS. With gunicorn running several workers, a
# per-process counter means a "5 per minute" limit is really 5 per minute PER
# WORKER, and nothing anywhere says so. See trap 40.
#
# Database 1: database 0 is the Celery broker, and a `FLUSHDB` while debugging
# a queue should not silently reset every rate limit at the same time.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0").rsplit("/", 1)[0]
        + "/1",
        "KEY_PREFIX": "goldfishlab",
    }
}

# --- Content-Security-Policy ------------------------------------------------
# Configured HERE rather than in prod.py on purpose: a policy that is only
# switched on in production is a policy nobody has ever run. This one is
# exercised by every request in development and by every test in the suite,
# which is the same argument that keeps the printing catalogue optional.
#
# `style-src-attr` is the interesting line. Every chart in this application is
# a server-rendered div whose *width* is the datum (the settled "no
# hand-written JavaScript" rule), so the pages carry inline `style="width: N%"`
# attributes. A nonce cannot help: CSP nonces apply to <style> and <script>
# ELEMENTS, never to a style attribute. Splitting the directive is what buys
# the difference - an injected <style> block is still refused, only the
# attributes are allowed.
#
# `img-src` names Scryfall's image CDN because card images are hot-linked and
# never rehosted, which is a condition of using their data.
CONTENT_SECURITY_POLICY = {
    # Django's admin ships inline scripts and styles of its own. It is
    # staff-only and behind a login, and rewriting somebody else's templates to
    # satisfy our header is not a security improvement.
    "EXCLUDE_URL_PREFIXES": ["/admin/"],
    "DIRECTIVES": {
        "default-src": [SELF],
        "script-src": [SELF],
        "style-src": [SELF],
        "style-src-attr": ["'unsafe-inline'"],
        "img-src": [SELF, "data:", "https://cards.scryfall.io"],
        "connect-src": [SELF],
        "font-src": [SELF],
        # Nothing here is ever framed, and there is no <base> tag anywhere.
        "frame-ancestors": [NONE],
        "frame-src": [NONE],
        "object-src": [NONE],
        "base-uri": [NONE],
        # Stripe's two hosts are here although no form ever names them. The
        # checkout and portal buttons POST to our own views, which answer with
        # a 302 to Stripe - and Chrome and Safari check `form-action` against
        # every redirect a form submission follows, not only the form's own
        # action (Firefox checks the first URL only). With SELF alone, paying
        # would have been refused in two of the three browsers, and it could
        # not show up before the go-live because no installation has had keys.
        # Trap 50.
        "form-action": [
            SELF,
            "https://checkout.stripe.com",
            "https://billing.stripe.com",
        ],
    },
}

# --- Rate limiting ----------------------------------------------------------
# django-ratelimit reads the client address out of REMOTE_ADDR by default.
# Behind the Cloudflare Tunnel REMOTE_ADDR is the TUNNEL NODE, so every visitor
# on earth would share one bucket and the first few would spend it for
# everybody. `core.ratelimit.client_ip` takes the right-hand end of
# X-Forwarded-For instead - see the long comment there about why the right-hand
# end and not the left.
RATELIMIT_IP_META_KEY = "core.ratelimit.client_ip"

# How many reverse proxies in front of the application append to
# X-Forwarded-For. ONE number for BOTH limiters, which is the point: allauth
# counts login, signup and password-reset attempts with its own IP lookup and
# ignores `RATELIMIT_IP_META_KEY` entirely. It reads X-Forwarded-For only when
# `ALLAUTH_TRUSTED_PROXY_COUNT` is set, and otherwise falls back to REMOTE_ADDR
# - which in production is the proxy, so every visitor on earth shared one
# bucket and ten mistyped passwords a minute locked the whole site out of
# signing in. Trap 47. Zero means "no proxy": REMOTE_ADDR is the client.
TRUSTED_PROXY_COUNT = env.int("DJANGO_TRUSTED_PROXY_COUNT", default=1)
ALLAUTH_TRUSTED_PROXY_COUNT = TRUSTED_PROXY_COUNT

# --- Upload limits ----------------------------------------------------------
# **Neither of the first two settings bounds an uploaded file**, and it is worth
# writing that down because both are named as though they do.
#
# `DATA_UPLOAD_MAX_MEMORY_SIZE` bounds the request body EXCLUDING files:
# `MultiPartParser` accumulates `num_bytes_read` only for `item_type == FIELD`
# and never for `FILE` (django/http/multipartparser.py). `FILE_UPLOAD_MAX_
# MEMORY_SIZE` is a spool threshold - above it Django writes the upload to a
# temporary file instead of holding it in memory - so it moves the cost, it
# does not refuse it. **The size ceiling that actually refuses a file is
# `decks.services.MAX_UPLOAD_BYTES`**, checked before anything is decoded, and
# the row ceiling beside it is `MAX_UPLOAD_ROWS`. See trap 41.
#
# What this value does bound is the ordinary POST: the annotation editor and
# the column-mapping screen both post a field per card.
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
# Kept at Django's default deliberately. Raising it would only mean holding
# more of an upload in RAM on a 128 MiB cloudlet, and nothing here needs the
# file in memory - `decode()` reads it once.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440
# Django's default, restated because it is a security control rather than a
# preference: it is what stops a hand-built POST of a hundred thousand fields
# from costing CPU per field. The largest legitimate form here posts one field
# per card of a 99-card deck.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
