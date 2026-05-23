#!/usr/bin/env bash
set -euo pipefail
sudo systemctl status replau-health-dashboard --no-pager || true
curl http://127.0.0.1:8793/health | jq
systemctl list-timers replau-daily-backup.timer replau-stuck-monitor.timer --no-pager
sudo systemctl start replau-daily-backup.service
journalctl -u replau-daily-backup.service -n 50 --no-pager
sudo systemctl start replau-stuck-monitor.service || true
journalctl -u replau-stuck-monitor.service -n 80 --no-pager
