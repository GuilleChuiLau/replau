# Replau OpenClaw WhatsApp Send Adapter

This service creates a stable local HTTP endpoint:

```text
POST http://127.0.0.1:8792/send/whatsapp
```

Internally it runs:

```bash
openclaw message send --channel whatsapp --target +51999999999 --message "..."
```

## Install

```bash
sudo mkdir -p /opt/replau_openclaw_whatsapp_send_adapter
sudo chown -R "$USER:$USER" /opt/replau_openclaw_whatsapp_send_adapter

cp openclaw_whatsapp_send_adapter.py requirements.txt .env.example replau-openclaw-whatsapp-send-adapter.service \
  /opt/replau_openclaw_whatsapp_send_adapter/

cd /opt/replau_openclaw_whatsapp_send_adapter

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x openclaw_whatsapp_send_adapter.py
```

## Environment

```bash
sudo cp .env.example /etc/replau-openclaw-whatsapp-send-adapter.env
sudo nano /etc/replau-openclaw-whatsapp-send-adapter.env
```

Recommended:

```ini
ADAPTER_HOST=127.0.0.1
ADAPTER_PORT=8792

REQUIRE_HOOK_TOKEN=true
HOOK_TOKEN=RESTRICTED

OPENCLAW_BIN=openclaw
OPENCLAW_CHANNEL=whatsapp
OPENCLAW_ACCOUNT=

OPENCLAW_TIMEOUT=90
LOG_LEVEL=INFO
```

Then:

```bash
sudo chmod 600 /etc/replau-openclaw-whatsapp-send-adapter.env
sudo chown root:root /etc/replau-openclaw-whatsapp-send-adapter.env
```

## Test OpenClaw CLI first

Replace number with a real allowed/pairing-approved WhatsApp number:

```bash
openclaw message send \
  --channel whatsapp \
  --target +51999999999 \
  --message "Test desde OpenClaw CLI" \
  --json
```

## Manual run

```bash
sudo bash -c '
set -a
source /etc/replau-openclaw-whatsapp-send-adapter.env
set +a
cd /opt/replau_openclaw_whatsapp_send_adapter
/opt/replau_openclaw_whatsapp_send_adapter/.venv/bin/python /opt/replau_openclaw_whatsapp_send_adapter/openclaw_whatsapp_send_adapter.py
'
```

In another terminal:

```bash
curl http://127.0.0.1:8792/health | jq
```

## Dry-run adapter test

```bash
curl -X POST "http://127.0.0.1:8792/send/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Token: RESTRICTED" \
  -d '{
    "whatsapp_number": "51999999999",
    "message_text": "Test dry-run desde Replau adapter",
    "event_type": "CUSTOM",
    "dry_run": true
  }' | jq
```

## Real adapter test

```bash
curl -X POST "http://127.0.0.1:8792/send/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Token: RESTRICTED" \
  -d '{
    "whatsapp_number": "51999999999",
    "message_text": "Test real desde Replau adapter",
    "event_type": "CUSTOM"
  }' | jq
```

## Install service

```bash
sudo cp /opt/replau_openclaw_whatsapp_send_adapter/replau-openclaw-whatsapp-send-adapter.service \
  /etc/systemd/system/replau-openclaw-whatsapp-send-adapter.service

sudo systemctl daemon-reload
sudo systemctl enable replau-openclaw-whatsapp-send-adapter
sudo systemctl start replau-openclaw-whatsapp-send-adapter

sudo systemctl status replau-openclaw-whatsapp-send-adapter --no-pager
```

Logs:

```bash
journalctl -u replau-openclaw-whatsapp-send-adapter -f
```

## Connect WhatsApp outbox worker

Edit:

```bash
sudo nano /etc/replau-whatsapp-outbox-worker.env
```

Set:

```ini
WHATSAPP_DRY_RUN=false
OPENCLAW_SEND_URL=http://127.0.0.1:8792/send/whatsapp
OPENCLAW_HOOK_TOKEN=RESTRICTED
```

Restart:

```bash
sudo systemctl restart replau-whatsapp-outbox-worker
```
