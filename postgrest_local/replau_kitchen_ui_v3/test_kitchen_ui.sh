#!/usr/bin/env bash
set -euo pipefail
echo "Kitchen UI health:"
curl http://127.0.0.1:8791/health | jq
echo
echo "Kitchen orders:"
curl http://127.0.0.1:8791/api/orders | jq
echo
echo "Kitchen orders direct from PostgREST:"
curl http://localhost:3000/v_kitchen_orders | jq
