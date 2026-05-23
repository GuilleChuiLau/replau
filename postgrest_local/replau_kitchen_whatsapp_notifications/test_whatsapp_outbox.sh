#!/usr/bin/env bash
set -euo pipefail

echo "Latest Kitchen orders:"
curl "http://localhost:3000/v_kitchen_orders?order=id.desc&limit=5" | jq

echo
echo "Latest WhatsApp outbox rows:"
curl "http://localhost:3000/v_whatsapp_outbox?order=id.desc&limit=10" | jq

echo
echo "Worker dry-run once:"
sudo bash -c '
set -a
source /etc/replau-whatsapp-outbox-worker.env
set +a
cd /opt/replau_whatsapp_outbox_worker
WORKER_MODE=once /opt/replau_whatsapp_outbox_worker/.venv/bin/python /opt/replau_whatsapp_outbox_worker/whatsapp_outbox_worker.py
'
