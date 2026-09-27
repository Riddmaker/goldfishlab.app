#!/usr/bin/env bash
#
# Rotate the offsite backups - NOT on the production node.
#
# Needed only if the S3 target cannot do lifecycle rules (see backup.sh: the
# node may only write, on purpose). Runs from the operator's machine with its
# OWN credentials that may list and delete - exactly the rights a compromised
# node must never have.
#
# The defaults keep the privacy policy's promise of at most 30 days: seven
# dailies and four weeklies (28 days).
#
# Usage:
#   rotate.sh            shows what would be removed (dry run, the default)
#   rotate.sh --apply    removes it
#
# Environment:
#   BACKUP_S3_BUCKET BACKUP_S3_ENDPOINT
#   AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION  (list + delete)
# Optional (with defaults):
#   BACKUP_KEEP_DAILY=7  BACKUP_KEEP_WEEKLY=4  BACKUP_PREFIX=goldfishlab

set -euo pipefail

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() {
  log "ERROR: $*" >&2
  exit 1
}

APPLY=0
case "${1:-}" in
  --apply) APPLY=1 ;;
  "") ;;
  *) die "Unknown argument: $1 (allowed: --apply)" ;;
esac

PREFIX="${BACKUP_PREFIX:-goldfishlab}"
for var in BACKUP_S3_BUCKET BACKUP_S3_ENDPOINT AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
  # Only the NAME is reported, never the value.
  [ -n "${!var:-}" ] || die "Environment variable ${var} is missing."
done
command -v aws > /dev/null || die "The aws CLI is not installed here."

rotate_class() {
  local class="$1" keep="$2" base listing
  base="s3://${BACKUP_S3_BUCKET}/${class}"
  # A failed listing aborts. Run inside a process substitution, whose failure
  # `set -e` does not see, missing rights once read as "nothing to remove".
  listing="$(aws --endpoint-url "${BACKUP_S3_ENDPOINT}" s3 ls "${base}/")" \
    || die "Listing ${base}/ failed."
  local -a existing=()
  while IFS= read -r name; do
    [ -n "${name}" ] && existing+=("${name}")
  done < <(printf '%s\n' "${listing}" | awk '{print $4}' \
    | grep -E "^${PREFIX}-.*\.dump\.age$" | sort || true)

  local total="${#existing[@]}"
  if [ "${total}" -le "${keep}" ]; then
    log "${class}: ${total} present, keeping ${keep} - nothing to remove."
    return
  fi
  local obsolete=$((total - keep))
  log "${class}: ${total} present, ${obsolete} beyond the ${keep} kept."
  local name
  for name in "${existing[@]:0:${obsolete}}"; do
    if [ "${APPLY}" = "1" ]; then
      aws --endpoint-url "${BACKUP_S3_ENDPOINT}" s3 rm "${base}/${name}"
      log "Removed: ${class}/${name}"
    else
      log "Would remove: ${class}/${name}"
    fi
  done
}

rotate_class daily "${BACKUP_KEEP_DAILY:-7}"
rotate_class weekly "${BACKUP_KEEP_WEEKLY:-4}"

[ "${APPLY}" = "1" ] || log "Dry run - --apply removes for real."
