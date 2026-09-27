# Stage 1: build the Tailwind CSS (design tokens live in assets/css/input.css)
FROM debian:bookworm-slim AS assets
ADD --checksum=sha256:dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a \
    --chmod=755 \
    https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/tailwindcss-linux-x64 \
    /usr/local/bin/tailwindcss
WORKDIR /app
COPY assets ./assets
COPY templates ./templates
COPY core ./core
COPY accounts ./accounts
COPY billing ./billing
COPY goldfishlab ./goldfishlab
RUN tailwindcss -i assets/css/input.css -o static/css/main.css --minify

# Stage 2: the Django application image
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Keep package lists fresh so Jelastic can install its own tooling (ssh, cron)
# on first start.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt only - NEVER requirements-dev.txt. scipy alone is ~90 MB
# and nothing in simulation/ imports it; on a 128 MiB cloudlet that matters.
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

COPY . .
COPY --from=assets /app/static/css/main.css static/css/main.css

# Bake static files into the image. The dummy values never reach runtime and
# no real secret is present at build time (HABIT 1).
RUN DJANGO_SETTINGS_MODULE=goldfishlab.settings.prod \
    DJANGO_SECRET_KEY=build-time-only-dummy \
    DATABASE_URL=postgres://build:build@build:5432/build \
    REDIS_URL=redis://build:6379/0 \
    python manage.py collectstatic --noinput

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# 8080, NOT 8000. Jelastic routes the environment URL to
# JELASTIC_PRIORITY_PORTS=8080; serving anywhere else gives "connection
# refused" on the public URL with no other symptom.
EXPOSE 8080

CMD ["./start.sh"]
