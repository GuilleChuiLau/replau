#!/usr/bin/env bash
set -euo pipefail

echo "Viewer health:"
curl http://127.0.0.1:8790/health | jq

echo
echo "Generate signed URL for pedido_id=1:"
curl -X POST "http://localhost:3000/rpc/ensure_pedido_public_token" \
  -H "Content-Type: application/json" \
  -d '{
    "p_pedido_id": 1,
    "p_public_base_url": "http://127.0.0.1:8790",
    "p_expires_hours": 720
  }' | jq
