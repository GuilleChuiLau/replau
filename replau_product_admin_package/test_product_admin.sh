#!/usr/bin/env bash
set -euo pipefail
echo "Service:"
sudo systemctl status replau-product-admin --no-pager || true
echo
echo "Health:"
curl http://127.0.0.1:8794/health | jq
echo
echo "Products sample:"
curl "http://localhost:3000/productos?limit=5" | jq
echo
echo "Prices sample:"
curl "http://localhost:3000/producto_precios?limit=5" | jq
