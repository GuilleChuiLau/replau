# Replau OpenClaw WhatsApp Bridge

This is the local bridge between OpenClaw/WhatsApp and your PostgREST backend.

It receives WhatsApp webhook messages, calls your PostgREST RPC endpoints, manages conversation state, and returns `reply_text` for OpenClaw to send back to the customer.

## Main route

```text
POST http://127.0.0.1:8789/webhook/whatsapp

The authenticated webhook applies per-customer, per-account flood controls before
database writes or order-state changes. Defaults allow 6 messages per 10 seconds,
12 per minute, and 4 repeats of identical content per 30 seconds. Override them
with `WHATSAPP_RATE_LIMIT_BURST`, `WHATSAPP_RATE_LIMIT_BURST_SECONDS`,
`WHATSAPP_RATE_LIMIT_MINUTE`, `WHATSAPP_RATE_LIMIT_REPEAT`, and
`WHATSAPP_RATE_LIMIT_REPEAT_SECONDS` in `bridge.env` when operational evidence
supports different thresholds. `WHATSAPP_RATE_LIMIT_MAX_SENDERS` bounds the
in-memory sender map at 10,000 entries by default.
```

## Health route

```text
GET http://127.0.0.1:8789/health
```

## Flow

```text
Customer sends name and items
    ↓
Bridge logs inbound message
    ↓
Bridge parses order
    ↓
Bridge calls /rpc/cotizar_pedido_whatsapp
    ↓
Bridge returns quote text
    ↓
Customer sends payment method
    ↓
Customer sends location
    ↓
Bridge reverse-geocodes location
    ↓
Customer confirms address
    ↓
Bridge calls /rpc/confirmar_pedido_whatsapp
    ↓
Bridge returns final confirmation with order URL
```

## Install

```bash
sudo mkdir -p /opt/replau-openclaw-whatsapp-bridge
sudo chown -R "$USER:$USER" /opt/replau-openclaw-whatsapp-bridge

cp bridge.py requirements.txt /opt/replau-openclaw-whatsapp-bridge/
cd /opt/replau-openclaw-whatsapp-bridge

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x bridge.py
```

## Create environment file

```bash
sudo cp .env.example /etc/replau-openclaw-whatsapp-bridge.env
sudo nano /etc/replau-openclaw-whatsapp-bridge.env
sudo chmod 600 /etc/replau-openclaw-whatsapp-bridge.env
sudo chown root:root /etc/replau-openclaw-whatsapp-bridge.env
```

For local testing:

```ini
POSTGREST_BASE_URL=http://127.0.0.1:3000
PUBLIC_ORDER_BASE_URL=http://localhost:3000
OPENCLAW_HOOK_TOKEN=RESTRICTED
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=8789
GEOCODER_PROVIDER=nominatim
```

If you do not want the bridge to call the internet for reverse geocoding:

```ini
GEOCODER_PROVIDER=none
```

## Run manually

```bash
cd /opt/replau-openclaw-whatsapp-bridge
source .venv/bin/activate

set -a
source /etc/replau-openclaw-whatsapp-bridge.env
set +a

python bridge.py
```

## Test health

In another terminal:

```bash
curl http://127.0.0.1:8789/health | jq
```

## Test full conversation

### 1. Customer sends name and items

```bash
curl -X POST "http://127.0.0.1:8789/webhook/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Token: RESTRICTED" \
  -d @test_order_message.json | jq
```

Expected: bridge returns a quote in `reply_text`.

### 2. Customer sends payment method

```bash
curl -X POST "http://127.0.0.1:8789/webhook/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Token: RESTRICTED" \
  -d @test_payment_message.json | jq
```

Expected: bridge asks for location.

### 3. Customer sends location

```bash
curl -X POST "http://127.0.0.1:8789/webhook/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Token: RESTRICTED" \
  -d @test_location_message.json | jq
```

Expected: bridge asks customer to confirm detected address.

### 4. Customer confirms address

```bash
curl -X POST "http://127.0.0.1:8789/webhook/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Token: RESTRICTED" \
  -d @test_confirm_address_message.json | jq
```

Expected: bridge calls `/rpc/confirmar_pedido_whatsapp` and returns the final order URL.

## Install as systemd service

Copy service file:

```bash
sudo cp replau-openclaw-whatsapp-bridge.service /etc/systemd/system/replau-openclaw-whatsapp-bridge.service
sudo systemctl daemon-reload
sudo systemctl enable replau-openclaw-whatsapp-bridge
sudo systemctl start replau-openclaw-whatsapp-bridge
sudo systemctl status replau-openclaw-whatsapp-bridge --no-pager
```

View logs:

```bash
journalctl -u replau-openclaw-whatsapp-bridge -f
```

## OpenClaw integration

Configure OpenClaw to POST inbound WhatsApp messages to:

```text
http://127.0.0.1:8789/webhook/whatsapp
```

Include header:

```text
X-Hook-Token: RESTRICTED
```

OpenClaw should read the response JSON field:

```text
reply_text
```

and send that text back to the WhatsApp customer.

## Normalized payload format

Text:

```json
{
  "whatsapp_number": "51999999999",
  "message_type": "text",
  "message_text": "Juan Perez\n2 bolsas de pimienta molida"
}
```

Location:

```json
{
  "whatsapp_number": "51999999999",
  "message_type": "location",
  "latitude": -12.046374,
  "longitude": -77.042793
}
```

The bridge also tries to extract common nested WhatsApp/OpenClaw payload formats.
