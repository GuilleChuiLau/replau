#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this restore test with sudo." >&2
  exit 1
fi

BACKUP_DIR=/var/backups/replau-localapi
DUMP=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'localapi_api_*.dump' -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)
if [[ -z ${DUMP:-} ]]; then
  echo "No Replau backup dump found in $BACKUP_DIR" >&2
  exit 1
fi

TEST_DB="replau_restore_test_$(date +%Y%m%d_%H%M%S)"
cleanup() {
  /usr/sbin/runuser -u postgres -- dropdb --if-exists "$TEST_DB" >/dev/null
}
trap cleanup EXIT

echo "Validating archive: $DUMP"
/usr/sbin/runuser -u postgres -- pg_restore --list "$DUMP" >/dev/null

echo "Creating temporary database: $TEST_DB"
/usr/sbin/runuser -u postgres -- createdb "$TEST_DB"
/usr/sbin/runuser -u postgres -- pg_restore \
  --exit-on-error \
  --no-owner \
  --dbname="$TEST_DB" \
  "$DUMP"

TABLE_COUNT=$(/usr/sbin/runuser -u postgres -- psql -XAt -d "$TEST_DB" -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='api' AND table_type='BASE TABLE';")
if [[ ! $TABLE_COUNT =~ ^[0-9]+$ ]] || (( TABLE_COUNT == 0 )); then
  echo "Restore validation failed: no base tables found in schema api" >&2
  exit 1
fi

/usr/sbin/runuser -u postgres -- psql -X -d "$TEST_DB" -c \
  "SELECT table_schema, count(*) AS base_tables FROM information_schema.tables WHERE table_schema='api' AND table_type='BASE TABLE' GROUP BY table_schema;"

echo "RESTORE_TEST_OK: restored $TABLE_COUNT api tables; temporary database will now be removed."
