#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/opt/replau_ops"
ENV_FILE="/etc/replau-ops.env"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER:$USER" "$APP_DIR"
cp replau_health_dashboard.py replau_stuck_monitor.py replau_backup.sh requirements.txt .env.example \
  replau-health-dashboard.service replau-daily-backup.service replau-daily-backup.timer \
  replau-stuck-monitor.service replau-stuck-monitor.timer replau_whatsapp_watchdog.py \
  replau-whatsapp-watchdog.service replau-whatsapp-watchdog.timer test_replau_ops.sh "$APP_DIR/"
cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x replau_health_dashboard.py replau_stuck_monitor.py replau_whatsapp_watchdog.py replau_backup.sh test_replau_ops.sh
if [ ! -f "$ENV_FILE" ]; then sudo cp "$APP_DIR/.env.example" "$ENV_FILE"; fi
sudo chmod 600 "$ENV_FILE"
sudo chown root:root "$ENV_FILE"
sudo cp replau-health-dashboard.service /etc/systemd/system/
sudo cp replau-daily-backup.service /etc/systemd/system/
sudo cp replau-daily-backup.timer /etc/systemd/system/
sudo cp replau-stuck-monitor.service /etc/systemd/system/
sudo cp replau-stuck-monitor.timer /etc/systemd/system/
sudo cp replau-whatsapp-watchdog.service /etc/systemd/system/
sudo cp replau-whatsapp-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable replau-health-dashboard
sudo systemctl enable replau-daily-backup.timer
sudo systemctl enable replau-stuck-monitor.timer
sudo systemctl enable replau-whatsapp-watchdog.timer
sudo systemctl restart replau-health-dashboard
sudo systemctl start replau-daily-backup.timer
sudo systemctl start replau-stuck-monitor.timer
sudo systemctl start replau-whatsapp-watchdog.timer
echo "Installed. Dashboard: http://127.0.0.1:8793"
