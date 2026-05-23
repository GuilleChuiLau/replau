#!/usr/bin/env bash
set -euo pipefail

PEDIDO_ID="${1:-}"
WHATSAPP_NUMBER="${2:-51998116843}"

if [ -z "$PEDIDO_ID" ]; then
  echo "Usage: ./test_payment_proof_flow.sh PEDIDO_ID [WHATSAPP_NUMBER]"
  echo "Find latest pedido_id with:"
  echo 'curl "http://localhost:3000/v_pedidos_logistica?select=id,pedido_num,cliente_nombre,total&order=id.desc&limit=5" | jq'
  exit 1
fi

echo "1) Mark order as proof-required"
curl -s -X POST "http://localhost:3000/rpc/marcar_pago_requiere_comprobante" \
  -H "Content-Type: application/json" \
  -d "{\"p_pedido_id\":$PEDIDO_ID,\"p_required\":true,\"p_notes\":\"Test payment proof required\"}" | jq

echo
echo "2) Register fake proof"
curl -s -X POST "http://localhost:3000/rpc/registrar_comprobante_pago_whatsapp" \
  -H "Content-Type: application/json" \
  -d "{\"p_whatsapp_number\":\"$WHATSAPP_NUMBER\",\"p_media_url\":\"https://example.com/test-proof.jpg\",\"p_caption\":\"Comprobante test Yape\",\"p_media_type\":\"image\",\"p_pedido_id\":$PEDIDO_ID}" | jq

echo
echo "3) Latest proofs"
curl -s "http://localhost:3000/v_payment_proofs_logistica?order=id.desc&limit=5" | jq

echo
echo "4) Review UI health"
curl -s "http://127.0.0.1:8795/health" | jq
