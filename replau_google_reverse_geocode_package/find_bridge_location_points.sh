#!/usr/bin/env bash
set -euo pipefail
BRIDGE_FILE="${1:-/opt/replau_openclaw_whatsapp_bridge/bridge.py}"
if [ ! -f "$BRIDGE_FILE" ]; then echo "bridge.py not found at: $BRIDGE_FILE"; exit 1; fi
echo "Searching likely location-handling integration points in: $BRIDGE_FILE"
grep -nEi "lat|latitude|lng|lon|longitude|location|ubicaci|direccion|address|WAITING_ADDRESS|confirmar_pedido" "$BRIDGE_FILE" || true
echo
echo "Open it with: sudo nano $BRIDGE_FILE"
