"""Development settings. Never used in production."""

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "web"]  # noqa: S104
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Production hashes and compresses static files, which needs a manifest built
# by collectstatic. Requiring that locally would mean running collectstatic
# before every test run, so dev serves the files as they are.
STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# WhiteNoise MUST stay in the middleware chain locally.
#
# django.contrib.staticfiles only serves assets in DEBUG via runserver's
# StaticFilesHandler, and docker compose runs GUNICORN - so without WhiteNoise
# every /static/ request returns the 404 page as text/html and the site loads
# completely unstyled, while every page still answers HTTP 200. A Playwright
# console check caught this; no view test would have.
#
# USE_FINDERS serves straight from STATICFILES_DIRS, so no collectstatic run
# is needed and WhiteNoise stops warning about a missing STATIC_ROOT.
# AUTOREFRESH picks up edited files without a restart.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
