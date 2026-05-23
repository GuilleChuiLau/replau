#!/usr/bin/env bash
set -euo pipefail

echo "Adapter health:"
curl http://127.0.0.1:8792/health | jq

echo
echo "Adapter dry-run send test:"
curl -X POST "http://127.0.0.1:8792/send/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Token: RESTRICTED" \
  -d '{
    "whatsapp_number": "51999999999",
    "message_text": "Test dry-run desde Replau adapter",
    "event_type": "CUSTOM",
    "dry_run": true
  }' | jq
