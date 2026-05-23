# Replau Secure Logistics Viewer

This upgrade adds signed logistics order URLs.

Instead of sending logistics a raw PostgREST URL like:

```text
http://localhost:3000/v_pedidos_logistica?id=eq.1
```

the system now returns:

```text
http://127.0.0.1:8790/order/PED-000001?token=SECURE_TOKEN
```

The viewer validates the token before showing the order.

## Files

- `add_secure_order_urls.sql`
- `logistics_viewer.py`
- `requirements.txt`
- `.env.example`
- `replau-logistics-viewer.service`

## 1. Install database upgrade

```bash
cd ~/postgrest-local/replau_secure_logistics_viewer

sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi < add_secure_order_urls.sql
sudo -u postgres psql -d localapi -c "NOTIFY pgrst, 'reload schema';"
```

Restart PostgREST if you run it as a service:

```bash
sudo systemctl restart postgrest
```

If PostgREST is manual, stop and start it again.

## 2. Install viewer

```bash
sudo mkdir -p /opt/replau_logistics_viewer
sudo chown -R "$USER:$USER" /opt/replau_logistics_viewer

cp logistics_viewer.py requirements.txt .env.example replau-logistics-viewer.service /opt/replau_logistics_viewer/

cd /opt/replau_logistics_viewer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x logistics_viewer.py
```

## 3. Create environment file

```bash
sudo cp .env.example /etc/replau-logistics-viewer.env
sudo nano /etc/replau-logistics-viewer.env
sudo chmod 600 /etc/replau-logistics-viewer.env
sudo chown root:root /etc/replau-logistics-viewer.env
```

Local config:

```ini
POSTGREST_BASE_URL=http://127.0.0.1:3000
VIEWER_HOST=127.0.0.1
VIEWER_PORT=8790
REQUEST_TIMEOUT=30
```

## 4. Run manually

```bash
sudo bash -c '
set -a
source /etc/replau-logistics-viewer.env
set +a
cd /opt/replau_logistics_viewer
/opt/replau_logistics_viewer/.venv/bin/python /opt/replau_logistics_viewer/logistics_viewer.py
'
```

Test in another terminal:

```bash
curl http://127.0.0.1:8790/health | jq
```

## 5. Install as service

```bash
sudo cp /opt/replau_logistics_viewer/replau-logistics-viewer.service /etc/systemd/system/replau-logistics-viewer.service

sudo systemctl daemon-reload
sudo systemctl enable replau-logistics-viewer
sudo systemctl start replau-logistics-viewer

sudo systemctl status replau-logistics-viewer --no-pager
```

Logs:

```bash
journalctl -u replau-logistics-viewer -f
```

## 6. Update bridge environment

Edit:

```bash
sudo nano /etc/replau-openclaw-whatsapp-bridge.env
```

Set:

```ini
PUBLIC_ORDER_BASE_URL=http://127.0.0.1:8790
```

Restart bridge:

```bash
sudo systemctl restart replau-openclaw-whatsapp-bridge
```

## 7. Test new signed URL

Run a full WhatsApp bridge test again.

Then check latest order:

```bash
curl "http://localhost:3000/v_pedidos_logistica?order=id.desc&limit=1" | jq
```

The `order_url` should now look like:

```text
http://127.0.0.1:8790/order/PED-000004?token=...
```

Open it in Windows browser or test:

```bash
curl "PASTE_THE_ORDER_URL_HERE"
```

## 8. Generate token for an existing order

If you want to create a signed URL for an old order:

```bash
curl -X POST "http://localhost:3000/rpc/ensure_pedido_public_token"   -H "Content-Type: application/json"   -d '{
    "p_pedido_id": 1,
    "p_public_base_url": "http://127.0.0.1:8790",
    "p_expires_hours": 720
  }' | jq
```

## 9. Security note

This is safer than raw PostgREST URLs, but before real production:

- Use HTTPS
- Use a real domain
- Do not expose PostgREST directly to the internet
- Shorten token expiration if needed
- Add authentication for logistics staff if required
