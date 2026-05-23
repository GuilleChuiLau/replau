# Replau Google Reverse Geocoding Package

This package adds Google reverse geocoding support to your OpenClaw WhatsApp bridge.

It converts a WhatsApp location like:

```text
Ubicación enviada: -12.119938, -76.99172
```

into a readable address using Google Maps Geocoding API.

## Files

```text
google_reverse_geocode.py
test_reverse_geocode.py
bridge_google_geocode_integration_snippet.py
install_reverse_geocode_helper.sh
find_bridge_location_points.sh
.env.google.example
README.md
```

## 1. Add API key to bridge environment

```bash
sudo nano /etc/replau-openclaw-whatsapp-bridge.env
```

Add:

```ini
GOOGLE_MAPS_API_KEY=YOUR_REAL_GOOGLE_MAPS_API_KEY
GOOGLE_GEOCODE_LANGUAGE=es
GOOGLE_GEOCODE_REGION=pe
GOOGLE_GEOCODE_TIMEOUT=15
```

Secure:

```bash
sudo chmod 600 /etc/replau-openclaw-whatsapp-bridge.env
sudo chown root:root /etc/replau-openclaw-whatsapp-bridge.env
```

## 2. Install helper into the bridge folder

From the unzipped package folder:

```bash
chmod +x install_reverse_geocode_helper.sh
./install_reverse_geocode_helper.sh
```

This copies the helper into:

```text
/opt/replau_openclaw_whatsapp_bridge
```

## 3. Test the API key and reverse geocode

```bash
sudo bash -c '
set -a
source /etc/replau-openclaw-whatsapp-bridge.env
set +a
cd /opt/replau_openclaw_whatsapp_bridge
./test_reverse_geocode.py -12.119938 -76.99172
'
```

Expected:

```text
"ok": true
"formatted_address": "..."
```

If you get `REQUEST_DENIED`, check Google Cloud: billing enabled, Geocoding API enabled, and API key restrictions allow Geocoding API.

## 4. Find bridge integration point

```bash
chmod +x find_bridge_location_points.sh
./find_bridge_location_points.sh
```

## 5. Add helper import to bridge.py

```bash
sudo nano /opt/replau_openclaw_whatsapp_bridge/bridge.py
```

Near the top, add:

```python
from google_reverse_geocode import format_detected_address_for_whatsapp
```

## 6. In the location-handling part, call the helper

After your bridge extracts latitude and longitude:

```python
geo = format_detected_address_for_whatsapp(latitude, longitude)

detected_address = geo["detected_address"]
maps_url = geo["maps_url"]

reply_text = geo["message_text"]
next_state = "WAITING_ADDRESS_CONFIRMATION"
```

Save these fields into your conversation draft/state:

```python
pedido_borrador["latitude"] = latitude
pedido_borrador["longitude"] = longitude
pedido_borrador["detected_address"] = detected_address
pedido_borrador["maps_url"] = maps_url
```

## 7. Restart bridge

```bash
sudo systemctl restart replau-openclaw-whatsapp-bridge
sudo systemctl status replau-openclaw-whatsapp-bridge --no-pager
```

## Important

This package does not automatically patch `bridge.py`, because your bridge has custom state logic. If you paste your current `bridge.py`, I can make the exact patch.
