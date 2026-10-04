#!/usr/bin/env bash
#
# Restore a dump written by dump.sh. The counterpart of dump.sh; getting the
# file back out of Swiss Backup (restic) comes first and is in RESTORE.md.
#
# The usual case is NOT an emergency but the quarterly restore test into a
# throwaway PostgreSQL 18: a backup that was never restored is not one.
#
# Usage:
#   restore.sh ./goldfishlab.dump
#
# Environment:
#   PGHOST PGPORT PGUSER PGDATABASE   where to restore to
#   PGPASSWORD or ~/.pgpass           read by libpq, never a command line

set -euo pipefail

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() {
  log "ERROR: $*" >&2
  exit 1
}

case "${1:-}" in
  "" | --help | -h)
    sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
DUMP="$1"

[ -r "${DUMP}" ] || die "File ${DUMP} is not readable."
: "${PGHOST:?missing}" "${PGUSER:?missing}" "${PGDATABASE:?missing}"
command -v pg_restore > /dev/null || die "pg_restore is not installed here."

# Check the content BEFORE writing anything: pg_restore --list reads only the
# table of contents. If that fails, the dump is damaged, and the target
# database must not be touched at all.
entries="$(pg_restore --list "${DUMP}" | grep -vc '^;')" \
  || die "The dump cannot be read; nothing was restored."
log "Table of contents read: ${entries} entries"

log "Restoring into ${PGDATABASE} on ${PGHOST} (existing objects are replaced)."
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname "${PGDATABASE}" "${DUMP}"

log "Restore done. Now start the application against it and check."
