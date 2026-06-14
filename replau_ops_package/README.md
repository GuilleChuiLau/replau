# Replau Ops Package

Adds:

1. Web health dashboard on `http://127.0.0.1:8793`
2. Daily PostgreSQL backup with systemd timer
3. Stuck WhatsApp/email monitor with systemd timer
4. WhatsApp gateway watchdog with systemd timer

## Install

```bash
chmod +x install_replau_ops.sh
./install_replau_ops.sh
```

## Check

```bash
sudo systemctl status replau-health-dashboard --no-pager
curl http://127.0.0.1:8793/health | jq
systemctl list-timers replau-daily-backup.timer replau-stuck-monitor.timer replau-whatsapp-watchdog.timer --no-pager
```

Open:

```text
http://127.0.0.1:8793
```

## Config

```bash
sudo nano /etc/replau-ops.env
```

For local use keep:

```ini
REQUIRE_OPS_TOKEN=true
```

If you expose it outside localhost:

```ini
REQUIRE_OPS_TOKEN=true
OPS_TOKEN=some-long-random-token
```

## Run backup manually

```bash
sudo systemctl start replau-daily-backup.service
journalctl -u replau-daily-backup.service -n 80 --no-pager
sudo ls -lh /var/backups/replau-localapi
```

## Run stuck monitor manually

```bash
sudo systemctl start replau-stuck-monitor.service || true
journalctl -u replau-stuck-monitor.service -n 80 --no-pager
```

## Run WhatsApp watchdog manually

```bash
sudo systemctl start replau-whatsapp-watchdog.service || true
journalctl -u replau-whatsapp-watchdog.service -n 80 --no-pager
```

The watchdog reports `connected`, `degraded`, `impacted`, or `stale`.
`degraded` means reconnect churn is present but messages are not stuck.
`impacted` means the WhatsApp outbox has pending/sending/error rows that need attention.

## Restore test example

```bash
sudo -u postgres createdb localapi_restore_test
sudo -u postgres pg_restore -d localapi_restore_test /var/backups/replau-localapi/localapi_api_YYYYMMDD_HHMMSS.dump
```
