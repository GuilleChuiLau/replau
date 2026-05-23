# Replau Integration Smoke Test

Script:

```bash
/home/guill/codex/replau_integration_smoke_test.py --timeout 90
```

What it checks:

1. PostgREST root exposes expected tables/RPCs.
2. Health endpoints respond for:
   - inbound WhatsApp bridge `:8789`
   - logistics viewer `:8790`
   - kitchen UI `:8791`
   - OpenClaw WhatsApp send adapter `:8792`
3. Hamburger menu product exists.
4. Quote RPC matches burger/fries/onion-rings items.
5. Inbound bridge conversation flow:
   - customer order text
   - payment method
   - WhatsApp location
   - address confirmation
   - special request prompt/comment
   - order creation
6. DB side effects:
   - `pedidos`
   - special request saved in `pedidos.observacion`
   - `pedido_items`
   - `stock_reservas`
   - `email_logistica_log`
7. Kitchen UI sees the order and renders detail page.
8. Kitchen status RPC updates to `EN_PREPARACION` with live notification disabled for smoke-test safety.
9. Logistics public order API/HTML accepts secure token.
10. Logistics Picking page renders the status form with the correct absolute action.
11. Send adapter executes OpenClaw WhatsApp send in `dry_run=true` mode.
12. Cleanup neutralizes generated outbound queue rows:
    - smoke WhatsApp outbox rows are marked `CANCELLED`
    - smoke email log rows are marked `SENT` with cleanup note immediately after order creation

Safety notes:

- The script creates real test orders named `Smoke Burger <id>`.
- It does **not** intentionally send real WhatsApp messages: kitchen notification is disabled, Picking/Delivery status mutation is not posted, and the send-adapter call uses `dry_run=true`.
- It neutralizes generated pending email rows immediately after order creation to prevent the live email worker from sending smoke-test logistics emails.
- If the script is interrupted before cleanup, inspect:

```bash
curl 'http://127.0.0.1:3000/whatsapp_outbox?status=eq.PENDING' | jq
curl 'http://127.0.0.1:3000/email_logistica_log?status=eq.PENDING' | jq
```

Last known passing run:

- Created order: `PED-000048`
- Total: `S/ 44.0`
- Result: all checks passed, including special request persistence, safe kitchen status update, Picking page rendering, send-adapter dry-run, and outbound queue neutralization.
