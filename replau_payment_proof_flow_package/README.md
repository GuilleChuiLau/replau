# Replau Payment Proof Flow

This package adds Yape/Plin payment proof support to the Replau WhatsApp order system.

## What it adds

- `api.pedido_payment_proofs` table
- `payment_status` fields on `api.pedidos`
- `api.v_payment_proofs_logistica` review view
- RPC to mark an order as proof-required
- RPC to register a WhatsApp image/document as payment proof
- RPC to verify/reject a proof
- Review UI on `http://127.0.0.1:8795`
- Customer WhatsApp notifications when proof is verified/rejected

## Install

```bash
chmod +x install_payment_proof_flow.sh
./install_payment_proof_flow.sh
```

## Test service

```bash
sudo systemctl status replau-payment-proof-review --no-pager
curl http://127.0.0.1:8795/health | jq
```

## Test database flow

Find latest order:

```bash
curl "http://localhost:3000/v_pedidos_logistica?select=id,pedido_num,cliente_nombre,total&order=id.desc&limit=5" | jq
```

Run:

```bash
cd /opt/replau_payment_proof_review
./test_payment_proof_flow.sh 18 51998116843
```

Open review UI:

```text
http://127.0.0.1:8795
```

Verify/reject a proof. If notify is enabled, it creates a WhatsApp outbox row. Your existing outbox worker sends it.

## Bridge integration

Read:

```text
bridge_payment_proof_integration.md
```

You need to patch `/opt/replau_openclaw_whatsapp_bridge/bridge.py` to route WhatsApp image/document messages to:

```text
/rpc/registrar_comprobante_pago_whatsapp
```

## Ops Dashboard

After installation, add this service/port to Ops Dashboard:

```text
replau-payment-proof-review
port 8795
health: http://127.0.0.1:8795/health
```
