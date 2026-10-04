#!/bin/sh
# Container entrypoint.
#
# ROLE=web     : run migrations, then gunicorn on 8080
# ROLE=worker  : wait for the schema, then a Celery worker
#
# Migrations run from the WEB role only. Web and worker both calling migrate
# on a fresh database deadlock on the migration lock, and the symptom is an
# environment that simply never finishes starting.
set -e

ROLE="${ROLE:-web}"

wait_for_db() {
  echo "Waiting for the database ..."
  until python -c "
import sys, django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'goldfishlab.settings.prod')
django.setup()
from django.db import connection
try:
    connection.ensure_connection()
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; do
    sleep 2
  done
  echo "Database reachable."
}

case "$ROLE" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    exec gunicorn goldfishlab.wsgi:application \
      --bind 0.0.0.0:8080 \
      --workers "${GUNICORN_WORKERS:-2}" \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    wait_for_db
    # Do not start until the web role has applied the schema, otherwise the
    # first task hits a table that does not exist yet.
    echo "Waiting for migrations to be applied ..."
    until python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'goldfishlab.settings.prod')
django.setup()
from django.db.migrations.executor import MigrationExecutor
from django.db import connections, DEFAULT_DB_ALIAS
executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
raise SystemExit(1 if executor.migration_plan(executor.loader.graph.leaf_nodes()) else 0)
" 2>/dev/null; do
      sleep 3
    done
    echo "Schema ready."
    # CELERY_BEAT=1 embeds the beat scheduler in THIS worker. Exactly one node
    # may set it (the manifest sets it on worker-short, which has one node):
    # two beats would schedule every periodic task twice. Its jobs are in
    # CELERY_BEAT_SCHEDULE in base.py, plus Celery's own result cleanup
    # (CELERY_RESULT_EXPIRES).
    BEAT=""
    if [ "${CELERY_BEAT:-0}" = "1" ]; then
      BEAT="--beat --schedule /tmp/celerybeat-schedule"
    fi
    # $BEAT is deliberately unquoted: empty, it must vanish rather than become
    # an empty argument.
    # shellcheck disable=SC2086
    exec celery -A goldfishlab worker \
      --loglevel=info \
      --queues="${CELERY_QUEUES:-sim_short,sim_long}" \
      --concurrency="${CELERY_CONCURRENCY:-2}" \
      $BEAT
    ;;
  *)
    echo "Unknown ROLE: $ROLE" >&2
    exit 1
    ;;
esac
