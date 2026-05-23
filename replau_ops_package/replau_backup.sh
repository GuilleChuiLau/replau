#!/usr/bin/env bash
set -euo pipefail
DB_NAME="${DB_NAME:-localapi}"
DB_SCHEMA="${DB_SCHEMA:-api}"
BACKUP_DB_HOST="${BACKUP_DB_HOST:-}"
DB_PORT="${DB_PORT:-5432}"
PG_SOCKET="${PG_SOCKET:-/var/run/postgresql/.s.PGSQL.${DB_PORT}}"
WAIT_SECONDS="${WAIT_SECONDS:-60}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/replau-localapi}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
TMP="/tmp/${DB_NAME}_${DB_SCHEMA}_${TS}.dump"
FINAL="${BACKUP_DIR}/${DB_NAME}_${DB_SCHEMA}_${TS}.dump"
echo "[$(date -Is)] Backup started: db=$DB_NAME schema=$DB_SCHEMA port=$DB_PORT"
PG_ARGS=(-p "$DB_PORT" -Fc -d "$DB_NAME" -n "$DB_SCHEMA" -f "$TMP")
if [ -n "$BACKUP_DB_HOST" ]; then
  PG_ARGS=(-h "$BACKUP_DB_HOST" "${PG_ARGS[@]}")
else
  for _ in $(seq 1 "$WAIT_SECONDS"); do
    [ -S "$PG_SOCKET" ] && break
    sleep 1
  done
  if [ ! -S "$PG_SOCKET" ]; then
    echo "PostgreSQL socket not ready after ${WAIT_SECONDS}s: $PG_SOCKET" >&2
    exit 1
  fi
fi
if [ "$(id -u)" -eq 0 ]; then
  /usr/sbin/runuser -u postgres -- pg_dump "${PG_ARGS[@]}"
else
  pg_dump "${PG_ARGS[@]}"
fi
mv "$TMP" "$FINAL"
chmod 600 "$FINAL"
ls -lh "$FINAL"
find "$BACKUP_DIR" -type f -name "*.dump" -mtime +"$RETENTION_DAYS" -print -delete
echo "[$(date -Is)] Backup complete"
