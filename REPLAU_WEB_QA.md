# Replau Web QA Regression

Script:

```bash
/home/guill/codex/replau_web_qa.py --timeout 60
```

Purpose: crawl and exercise the web UI flows for Logistics and Kitchen.

Coverage:

- Logistics dashboard `/dashboard`
- Logistics blocked numbers page `/blocked`
- Public order page `/order/{pedido_num}?token=...`
- Picking page `/ops/picking/{pedido_num}?token=...`
- Delivery page `/ops/delivery/{pedido_num}?token=...`
- Kitchen board `/`
- Kitchen order detail `/order/{pedido_id}`
- All same-service links discovered in those pages
- Status forms and redirect behavior for:
  - Logistics public order status form
  - Logistics Picking form
  - Logistics Delivery form
  - Kitchen status form

The default run mutates statuses on existing test/recent orders. Use this for structure-only crawling:

```bash
/home/guill/codex/replau_web_qa.py --timeout 60 --no-mutate
```

Latest passing run:

- 21 checks OK
- 0 warnings
- 0 failures

Fixes made during QA:

1. Logistics blocked/unblock redirect hardened:
   - form action now `/blocked/unblock`
   - redirect now `/blocked?message=...`
2. Invalid-order dashboard link hardened to `/dashboard`.
3. Kitchen source copies synchronized with deployed behavior:
   - back link `/`
   - status form action `/order/{id}/status`
   - redirect `/order/{id}`
   - `p_notify=true` passed to kitchen status RPC

Related broader E2E test:

```bash
/home/guill/codex/replau_integration_smoke_test.py --timeout 90
```
