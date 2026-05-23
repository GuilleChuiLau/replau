#!/usr/bin/env bash
set -euo pipefail

echo "Latest order:"
curl "http://localhost:3000/v_pedidos_logistica?select=id,pedido_num,estado,total,order_url&order=id.desc&limit=1" | jq

echo
echo "Reservation summary:"
curl "http://localhost:3000/v_pedidos_reserva_resumen?order=pedido_id.desc&limit=5" | jq

echo
echo "Reservations:"
curl "http://localhost:3000/v_stock_reservas?order=id.desc&limit=10" | jq

echo
echo "Available stock:"
curl "http://localhost:3000/v_stock_disponible_productos" | jq
