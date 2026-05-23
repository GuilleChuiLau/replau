# Replau Kitchen WhatsApp Notifications

This module connects Kitchen UI status changes to WhatsApp notifications using an outbox table.

## What it adds

Table:

```text
api.whatsapp_outbox
```

View:

```text
api.v_whatsapp_outbox
```

Functions:

```text
api.registrar_whatsapp_outbox()
api.build_kitchen_whatsapp_message()
api.update_kitchen_status(..., p_notify boolean DEFAULT true)
```

Worker:

```text
whatsapp_outbox_worker.py
```

## Flow

```text
Kitchen UI clicks LISTO
        ↓
api.update_kitchen_status()
        ↓
api.whatsapp_outbox row created as PENDING
        ↓
whatsapp_outbox_worker.py reads PENDING row
        ↓
worker sends message to OpenClaw outbound endpoint
        ↓
row becomes SENT
```

## Messages created

EN_PREPARACION:

```text
Tu pedido PED-000001 ya está en preparación 👨‍🍳
Te avisaremos cuando esté listo.
```

LISTO:

```text
Tu pedido PED-000001 ya está listo ✅
Gracias por esperar.
```

ENTREGADO:

```text
Tu pedido PED-000001 fue marcado como entregado ✅
Gracias por tu compra.
```

ANULADO:

```text
Tu pedido PED-000001 fue anulado.
Si necesitas ayuda, responde este mensaje.
```

## Install SQL

```bash
mkdir -p ~/postgrest-local/replau_kitchen_whatsapp_notifications
cp add_kitchen_whatsapp_notifications.sql whatsapp_outbox_worker.py requirements.txt .env.example replau-whatsapp-outbox-worker.service README.md test_whatsapp_outbox.sh \
  ~/postgrest-local/replau_kitchen_whatsapp_notifications/

cd ~/postgrest-local/replau_kitchen_whatsapp_notifications

sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi < add_kitchen_whatsapp_notifications.sql
sudo -u postgres psql -d localapi -c "NOTIFY pgrst, 'reload schema';"
sudo systemctl restart postgrest
```

## Install worker

```bash
sudo mkdir -p /opt/replau_whatsapp_outbox_worker
sudo chown -R "$USER:$USER" /opt/replau_whatsapp_outbox_worker

cp whatsapp_outbox_worker.py requirements.txt .env.example replau-whatsapp-outbox-worker.service \
  /opt/replau_whatsapp_outbox_worker/

cd /opt/replau_whatsapp_outbox_worker

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x whatsapp_outbox_worker.py
```

## Create env file

```bash
sudo cp .env.example /etc/replau-whatsapp-outbox-worker.env
sudo nano /etc/replau-whatsapp-outbox-worker.env
```

Start with:

```ini
WHATSAPP_DRY_RUN=true
```

This prints messages but does not send or mark SENT.

Secure it:

```bash
sudo chmod 600 /etc/replau-whatsapp-outbox-worker.env
sudo chown root:root /etc/replau-whatsapp-outbox-worker.env
```

## Test manually

Create a notification by clicking a Kitchen UI status button, or call:

```bash
curl -X POST "http://localhost:3000/rpc/update_kitchen_status" \
  -H "Content-Type: application/json" \
  -d '{
    "p_pedido_id": 1,
    "p_kitchen_status": "LISTO",
    "p_kitchen_notes": "Pedido listo para entrega",
    "p_notify": true
  }' | jq
```

Check outbox:

```bash
curl "http://localhost:3000/v_whatsapp_outbox?order=id.desc&limit=10" | jq
```

Run worker once:

```bash
sudo bash -c '
set -a
source /etc/replau-whatsapp-outbox-worker.env
set +a
cd /opt/replau_whatsapp_outbox_worker
WORKER_MODE=once /opt/replau_whatsapp_outbox_worker/.venv/bin/python /opt/replau_whatsapp_outbox_worker/whatsapp_outbox_worker.py
'
```

## Install as service

```bash
sudo cp /opt/replau_whatsapp_outbox_worker/replau-whatsapp-outbox-worker.service \
  /etc/systemd/system/replau-whatsapp-outbox-worker.service

sudo systemctl daemon-reload
sudo systemctl enable replau-whatsapp-outbox-worker
sudo systemctl start replau-whatsapp-outbox-worker
sudo systemctl status replau-whatsapp-outbox-worker --no-pager
```

Logs:

```bash
journalctl -u replau-whatsapp-outbox-worker -f
```

## Real OpenClaw sending

When OpenClaw exposes a send-message endpoint, edit:

```bash
sudo nano /etc/replau-whatsapp-outbox-worker.env
```

Set:

```ini
WHATSAPP_DRY_RUN=false
OPENCLAW_SEND_URL=http://127.0.0.1:18789/hooks/whatsapp-send
OPENCLAW_HOOK_TOKEN=RESTRICTED
```

Then restart:

```bash
sudo systemctl restart replau-whatsapp-outbox-worker
```

## Important

This package prepares the backend and worker. Real sending requires an OpenClaw outbound endpoint capable of sending WhatsApp messages.
