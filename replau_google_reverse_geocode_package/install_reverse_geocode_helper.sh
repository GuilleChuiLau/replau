#!/usr/bin/env bash
set -euo pipefail
BRIDGE_DIR="${BRIDGE_DIR:-/opt/replau_openclaw_whatsapp_bridge}"
ENV_FILE="${ENV_FILE:-/etc/replau-openclaw-whatsapp-bridge.env}"
echo "Installing Google reverse geocode helper into: $BRIDGE_DIR"
if [ ! -d "$BRIDGE_DIR" ]; then echo "ERROR: bridge directory not found: $BRIDGE_DIR"; exit 1; fi
cp google_reverse_geocode.py "$BRIDGE_DIR/"
cp test_reverse_geocode.py "$BRIDGE_DIR/"
chmod +x "$BRIDGE_DIR/test_reverse_geocode.py"
echo "Checking env file: $ENV_FILE"
if sudo test -f "$ENV_FILE"; then
  if sudo grep -q '^GOOGLE_MAPS_API_KEY=' "$ENV_FILE"; then echo "GOOGLE_MAPS_API_KEY found in bridge env file."; else echo "GOOGLE_MAPS_API_KEY not found. Add it to $ENV_FILE"; fi
else echo "WARNING: env file not found: $ENV_FILE"; fi
cat <<EOF

Next test:
sudo bash -c '
set -a
source $ENV_FILE
set +a
cd $BRIDGE_DIR
./test_reverse_geocode.py -12.119938 -76.99172
'
EOF
