#!/usr/bin/env bash
#
# Nightly dump of the production database into ONE file on the PostgreSQL
# node (phase 12 J11). Runs from cron on sqldb at 19:30 UTC:
#
#   30 19 * * * bash $HOME/bin/goldfishlab-dump.sh >> $HOME/goldfishlab-dump.log 2>&1
#
# In the crontab of `postgres`, the user sqldb's web SSH logs in as: it is not
# root, so the script and its log live in its home, not /usr/local or /var/log.
#
# Offsite is not this script's job. Infomaniak's "Swiss Backup" Jelastic
# add-on (restic: encrypted on this node, stored in Switzerland) backs up
# BACKUP_DIR daily at 20:00 UTC and keeps it for days: 23. Its deletion
# runs at most a week late, so no backup is older than 30 days - the privacy
# policy's promise. The add-on's credentials live on the node, so a node taken
# over could delete backups; Infomaniak's Swift storage offers no write-only
# keys, and that is accepted (see RESTORE.md).
#
# Why one file with a fixed name: the history is restic's, not this folder's.
# A dated file per night would make the node keep every dump forever.
#
# The new dump replaces the old one only after `pg_restore --list` has read
# it: a failed or truncated dump never overwrites the last good one.
#
# The password: libpq reads ~/.pgpass (mode 600), so it is on no command
# line and in no crontab (CLAUDE.md HABIT 1). The defaults below fit the
# manifest (infra/jelastic.jps): database and role `goldfishlab` on this node.
#
# No PGHOST by default: libpq then uses the Unix socket. sqldb's pg_hba.conf
# (Jelastic's) answers TCP from 127.0.0.1 with `ident`, which fails without
# an ident server; the socket's line is `md5`, which takes ~/.pgpass.
#
# Environment (all optional):
#   PGHOST (unset: the socket)  PGPORT=5432  PGUSER=goldfishlab  PGDATABASE=goldfishlab
#   BACKUP_DIR=/var/lib/pgsql/backup   the folder the add-on backs up

set -euo pipefail

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() {
  log "ERROR: $*" >&2
  exit 1
}

export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-goldfishlab}"
export PGDATABASE="${PGDATABASE:-goldfishlab}"
BACKUP_DIR="${BACKUP_DIR:-/var/lib/pgsql/backup}"
TARGET="${BACKUP_DIR}/${PGDATABASE}.dump"

for tool in pg_dump pg_restore; do
  command -v "${tool}" > /dev/null || die "${tool} is not installed on this node."
done

# The dump holds every account: readable by its owner only, from the start.
umask 077
mkdir -p "${BACKUP_DIR}"
PARTIAL="$(mktemp "${BACKUP_DIR}/.${PGDATABASE}.XXXXXX")"
trap 'rm -f "${PARTIAL}"' EXIT

log "Dumping ${PGDATABASE} from ${PGHOST:-the local socket}:${PGPORT}"
pg_dump --format=custom --no-owner --no-privileges --file "${PARTIAL}"

entries="$(pg_restore --list "${PARTIAL}" | grep -vc '^;')" \
  || die "The new dump cannot be read; the previous one is kept."
[ "${entries}" -gt 0 ] || die "The new dump is empty; the previous one is kept."

mv -f "${PARTIAL}" "${TARGET}"
log "Done: ${TARGET}, $(wc -c < "${TARGET}" | tr -d ' ') bytes, ${entries} entries."
