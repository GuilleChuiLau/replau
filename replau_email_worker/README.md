# Replau Email Worker

This worker sends logistics emails created by your WhatsApp order backend.

It reads:

GET /email_logistica_log?status=eq.PENDING

Then sends email through SMTP and updates:

status = SENT

or:

status = ERROR

## Install

```bash
sudo mkdir -p /opt/replau_email_worker
sudo chown -R "$USER:$USER" /opt/replau_email_worker

cp email_worker.py requirements.txt /opt/replau_email_worker/
cd /opt/replau_email_worker

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x email_worker.py
```

## Environment file

```bash
sudo cp .env.example /etc/replau-email-worker.env
sudo nano /etc/replau-email-worker.env
sudo chmod 600 /etc/replau-email-worker.env
sudo chown root:root /etc/replau-email-worker.env
```

Start with:

```ini
EMAIL_DRY_RUN=true
```

## Test manually in dry-run mode

```bash
sudo bash -c '
set -a
source /etc/replau-email-worker.env
set +a
cd /opt/replau_email_worker
WORKER_MODE=once /opt/replau_email_worker/.venv/bin/python /opt/replau_email_worker/email_worker.py
'
```

It should print the pending email body but leave it as PENDING.

## Real sending

Edit:

```bash
sudo nano /etc/replau-email-worker.env
```

Set:

```ini
EMAIL_DRY_RUN=false
```

Then test again:

```bash
sudo bash -c '
set -a
source /etc/replau-email-worker.env
set +a
cd /opt/replau_email_worker
WORKER_MODE=once /opt/replau_email_worker/.venv/bin/python /opt/replau_email_worker/email_worker.py
'
```

## Install as service

```bash
sudo cp replau-email-worker.service /etc/systemd/system/replau-email-worker.service
sudo systemctl daemon-reload
sudo systemctl enable replau-email-worker
sudo systemctl start replau-email-worker
sudo systemctl status replau-email-worker --no-pager
```

Logs:

```bash
journalctl -u replau-email-worker -f
```

## Verify email log

```bash
curl "http://localhost:3000/email_logistica_log?order=id.desc&limit=5" | jq
```

## Reset one email to PENDING

```bash
curl -X PATCH "http://localhost:3000/email_logistica_log?id=eq.1"   -H "Content-Type: application/json"   -d '{
    "status": "PENDING",
    "sent_at": null,
    "error_message": null
  }'
```
