#!/usr/bin/env bash
#
# Nightly backup of the production database. Runs from cron on the PostgreSQL
# node. Taken from the sibling project wiemeinsch.ch, which runs the same way
# on the same platform.
#
# What it does: pg_dump (custom format, compressed) -> age encryption ->
# upload to an S3-compatible offsite bucket at Infomaniak. Nothing else.
#
# No rotation on the node: rotating means listing and deleting, and
# credentials that may do that would let a compromised node wipe every backup.
# The node therefore has WRITE ONLY; retention is a lifecycle rule on the
# bucket, per prefix (daily/ 7 days, weekly/ 28 days). If the target cannot do
# that, scripts/backup/rotate.sh rotates from the operator's machine with its
# own credentials.
#
# The retention is a promise: the privacy policy says backups stay with
# Infomaniak in Switzerland for at most 30 days. There is deliberately no
# monthly class.
#
# Two properties on purpose:
#
#   1. The dump is NEVER written to disk unencrypted. pg_dump writes to
#      stdout, age encrypts the stream, and only then does a file exist. An
#      aborted run leaves nothing readable on the node.
#   2. The database password lives only in the environment (PGPASSWORD),
#      never on a command line - command lines are visible to every process
#      on the node through `ps` (CLAUDE.md HABIT 1).
#
# Environment (names, never values):
#   PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD
#   BACKUP_AGE_RECIPIENT      public age key (age1...) - not a secret
#   BACKUP_S3_BUCKET          target bucket
#   BACKUP_S3_ENDPOINT        the S3 endpoint at Infomaniak
#   AWS_ACCESS_KEY_ID         write-only (PutObject)
#   AWS_SECRET_ACCESS_KEY
#   AWS_DEFAULT_REGION
# Optional (with defaults):
#   BACKUP_PREFIX=goldfishlab
#   BACKUP_DRY_RUN=1     dumps and encrypts but uploads nothing (local trial)

set -euo pipefail

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() {
  log "ERROR: $*" >&2
  exit 1
}

DRY_RUN="${BACKUP_DRY_RUN:-0}"
PREFIX="${BACKUP_PREFIX:-goldfishlab}"

required=(PGHOST PGUSER PGDATABASE PGPASSWORD BACKUP_AGE_RECIPIENT)
if [ "${DRY_RUN}" != "1" ]; then
  required+=(BACKUP_S3_BUCKET BACKUP_S3_ENDPOINT AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY)
fi
for var in "${required[@]}"; do
  # Only the NAME is reported, never the value.
  [ -n "${!var:-}" ] || die "Environment variable ${var} is missing."
done

for tool in pg_dump age; do
  command -v "${tool}" > /dev/null || die "${tool} is not installed on this node."
done
if [ "${DRY_RUN}" != "1" ]; then
  command -v aws > /dev/null || die "The aws CLI is not installed on this node."
fi

# --- Retention class ----------------------------------------------------------
# One run belongs to exactly one class = one prefix in the bucket, and the
# retention is set per prefix. Sunday's run is the weekly one.
if [ "$(date -u +%u)" = "7" ]; then
  CLASS=weekly
else
  CLASS=daily
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILENAME="${PREFIX}-${STAMP}.dump.age"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT
TARGET="${WORKDIR}/${FILENAME}"

log "Backup ${FILENAME} (class ${CLASS})"

# --- Dump + encryption in one stream -----------------------------------------
# `set -o pipefail` keeps a failing pg_dump from hiding behind a successful age.
umask 077
pg_dump --format=custom --compress=9 --no-owner --no-privileges \
  | age --recipient "${BACKUP_AGE_RECIPIENT}" --output "${TARGET}"

size="$(wc -c < "${TARGET}" | tr -d ' ')"
[ "${size}" -gt 0 ] || die "The dump is empty."
log "Encrypted: ${size} bytes"

if [ "${DRY_RUN}" = "1" ]; then
  # A trial run keeps the file so that it can be inspected.
  cp "${TARGET}" "./${FILENAME}"
  log "Dry run: nothing uploaded. The file is ./${FILENAME}"
  exit 0
fi

# --- Offsite upload -------------------------------------------------------------
S3_BASE="s3://${BACKUP_S3_BUCKET}/${CLASS}"
aws --endpoint-url "${BACKUP_S3_ENDPOINT}" s3 cp "${TARGET}" "${S3_BASE}/${FILENAME}"
log "Uploaded to ${S3_BASE}/${FILENAME}"

log "Backup done."
