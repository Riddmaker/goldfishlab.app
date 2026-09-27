#!/usr/bin/env bash
#
# Restore an encrypted offsite dump. The counterpart of backup.sh.
#
# The usual case is NOT an emergency but the quarterly restore test into the
# local docker compose database: a backup that was never restored is not one.
#
# Usage:
#   restore.sh --list [daily|weekly]
#   restore.sh --key daily/goldfishlab-20260901T020000Z.dump.age
#   restore.sh --file ./goldfishlab-20260901T020000Z.dump.age
#
# Environment:
#   BACKUP_AGE_IDENTITY   path to the age identity file (the PRIVATE key) -
#                         never on the production node, only where the restore
#                         runs (HABIT 1: separate roles).
#   PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD   where to restore to
# For --list and --key also:
#   BACKUP_S3_BUCKET BACKUP_S3_ENDPOINT AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
#   (credentials that may READ - the production node's may only write)

set -euo pipefail

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() {
  log "ERROR: $*" >&2
  exit 1
}

usage() {
  sed -n '5,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

MODE=""
ARG=""
case "${1:-}" in
  --list)
    MODE=list
    ARG="${2:-}"
    ;;
  --key)
    MODE=key
    ARG="${2:?object key missing}"
    ;;
  --file)
    MODE=file
    ARG="${2:?file path missing}"
    ;;
  --help | -h) usage 0 ;;
  *) usage 1 ;;
esac

s3() {
  : "${BACKUP_S3_BUCKET:?missing}" "${BACKUP_S3_ENDPOINT:?missing}"
  aws --endpoint-url "${BACKUP_S3_ENDPOINT}" s3 "$@"
}

if [ "${MODE}" = "list" ]; then
  for class in ${ARG:-daily weekly}; do
    echo "--- ${class} ---"
    s3 ls "s3://${BACKUP_S3_BUCKET}/${class}/" | awk '{print $1, $2, $3, $4}'
  done
  exit 0
fi

: "${BACKUP_AGE_IDENTITY:?path to the age identity file missing}"
[ -r "${BACKUP_AGE_IDENTITY}" ] || die "The age identity file is not readable."
: "${PGHOST:?missing}" "${PGUSER:?missing}" "${PGDATABASE:?missing}" "${PGPASSWORD:?missing}"

for tool in pg_restore age; do
  command -v "${tool}" > /dev/null || die "${tool} is not installed here."
done

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT
umask 077
ENCRYPTED="${WORKDIR}/dump.age"

if [ "${MODE}" = "key" ]; then
  log "Fetching s3://${BACKUP_S3_BUCKET}/${ARG}"
  s3 cp "s3://${BACKUP_S3_BUCKET}/${ARG}" "${ENCRYPTED}"
else
  [ -r "${ARG}" ] || die "File ${ARG} is not readable."
  cp "${ARG}" "${ENCRYPTED}"
fi

# Decrypted into a umask-077 directory inside mktemp: the plain dump exists
# only for as long as the restore runs.
DECRYPTED="${WORKDIR}/dump.pgcustom"
age --decrypt --identity "${BACKUP_AGE_IDENTITY}" --output "${DECRYPTED}" "${ENCRYPTED}"
log "Decrypted: $(wc -c < "${DECRYPTED}" | tr -d ' ') bytes"

# Check the content BEFORE writing anything: pg_restore --list reads only the
# table of contents. If that fails, the dump is damaged, and the target
# database must not be touched at all.
pg_restore --list "${DECRYPTED}" > "${WORKDIR}/toc.txt"
log "Table of contents read: $(wc -l < "${WORKDIR}/toc.txt" | tr -d ' ') entries"

log "Restoring into ${PGDATABASE} on ${PGHOST} (existing objects are replaced)."
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname "${PGDATABASE}" "${DECRYPTED}"

log "Restore done. Now start the application against it and check."
