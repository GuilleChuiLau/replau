# Replau Payment Proof Flow

This package adds Yape/Plin payment proof support to the Replau WhatsApp order system.

## OCR-assisted review

The review page can run local RapidOCR against a saved image and display the
amount, recipient, operation/reference number, date/time, OCR confidence,
duplicate-operation warnings, and comparison with the order total. Set
`PAYMENT_EXPECTED_RECIPIENTS` in the service environment to a comma-separated
list of valid recipient names or identifying fragments to enable recipient
matching.

The OCR pipeline runs the original image plus contrast-enhanced and adaptive
threshold passes when OpenCV is available. It merges the strongest text result
from each pass, recognizes common Yape, Plin, BCP, BBVA, Interbank, and
Scotiabank receipt wording, and reports confidence separately for provider,
amount, recipient, operation number, timestamp, and success wording. Cashier
alerts include stable reason codes and severity so an operator can distinguish
an amount mismatch from a low-confidence extraction or an unknown provider.

Each image also receives a local perceptual difference hash. A proof whose hash
is within `PAYMENT_PERCEPTUAL_HASH_DISTANCE` bits of another proof is flagged
for manual review even if compression or a minor image change produced a
different SHA-256 digest. The conservative default distance is `2`.

Receipt timestamps are parsed in `PAYMENT_TIMEZONE` (default `America/Lima`).
Proofs older than `PAYMENT_RECEIPT_MAX_AGE_HOURS` (default `72`) or more than
`PAYMENT_RECEIPT_FUTURE_TOLERANCE_MINUTES` (default `10`) in the future receive
high-severity review reasons. These checks remain advisory and never approve or
reject a payment automatically.

## Immutable OCR decision audit

Apply `add_payment_ocr_audit.sql` to store versioned OCR snapshots in
PostgreSQL. Snapshots contain hashes, extracted fields, per-field confidence,
checks, reason codes, and the advisory recommendation, but deliberately exclude
raw OCR lines and field evidence to minimize duplicated personal data.

Cashier decisions use `revisar_comprobante_pago_auditado` and must reference a
snapshot belonging to the same proof. The decision and snapshot link is
append-only; database triggers reject changes or deletion of OCR snapshots and
review events. Opening a proof reuses the snapshot for the same file/cache
version, while **Actualizar análisis OCR** creates a new version.

OCR is advisory only. It must not automatically approve a payment because a
screenshot can be edited or reused and does not prove settlement. Keep manual
review enabled unless the transaction is reconciled with an authoritative bank
or payment-provider source.

## What it adds

- `api.pedido_payment_proofs` table
- `payment_status` fields on `api.pedidos`
- `api.v_payment_proofs_logistica` review view
- RPC to mark an order as proof-required
- RPC to register a WhatsApp image/document as payment proof
- RPC to verify/reject a proof
- Review UI on `http://127.0.0.1:8795`
- Customer WhatsApp notifications when proof is verified/rejected
- One durable payment-fulfillment record per order
- Strict, version-checked transitions for release, COD collection, reconciliation,
  settlement, cancellation, and refunds
- Append-only payment fulfillment event history for audit and reporting

## Payment fulfillment states

`add_payment_fulfillment.sql` keeps the existing proof workflow compatible while
adding the financial lifecycle:

```text
PAYMENT_REQUESTED / COD_DUE
  -> PROOF_REQUIRED -> UNDER_REVIEW -> VERIFIED -> RELEASED
  -> COD_COLLECTED
  -> RECONCILED -> SETTLED
```

Rejected proofs can return to proof collection. Verified, collected,
reconciled, or settled payments can enter `REFUND_PENDING`, followed by a
partial or full refund. Every accepted transition records the actor, source,
note, amount, and previous/new status. Callers may pass `p_expected_version` to
prevent two cashiers from acting on stale state.

OCR remains advisory. `VERIFIED` still requires a cashier decision unless a
future authoritative provider reconciliation integration supplies the decision.

The cashier workspace links each proof to its fulfillment ledger. The ledger
page shows expected, received, and refunded amounts; the immutable event
timeline; and only the next transitions allowed by the current state. Every
submission includes the displayed version, so a stale cashier screen cannot
overwrite a newer decision.

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
