#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/replau_ops
ENV_FILE=/etc/replau-backup.env
BACKUP_DIR=/var/backups/replau-localapi

install -d -m 0755 "$APP_DIR"
install -d -o postgres -g postgres -m 0700 "$BACKUP_DIR"
install -m 0755 "$SOURCE_DIR/replau_backup.sh" "$APP_DIR/replau_backup.sh"

cat >"$ENV_FILE" <<'EOF'
DB_NAME=localapi
DB_SCHEMA=api
DB_PORT=5432
BACKUP_DIR=/var/backups/replau-localapi
RETENTION_DAYS=14
EOF
chmod 0600 "$ENV_FILE"
chown root:root "$ENV_FILE"

cat >/etc/systemd/system/replau-daily-backup.service <<'EOF'
[Unit]
Description=Replau LocalAPI Daily PostgreSQL Backup
After=postgresql.service

[Service]
Type=oneshot
EnvironmentFile=/etc/replau-backup.env
ExecStart=/opt/replau_ops/replau_backup.sh
EOF

install -m 0644 "$SOURCE_DIR/replau-daily-backup.timer" /etc/systemd/system/replau-daily-backup.timer
systemctl daemon-reload
systemctl enable --now replau-daily-backup.timer
systemctl start replau-daily-backup.service
systemctl status replau-daily-backup.service --no-pager --lines=12
systemctl list-timers replau-daily-backup.timer --all --no-pager
