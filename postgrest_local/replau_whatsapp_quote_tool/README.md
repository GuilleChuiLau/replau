# Replau WhatsApp Quote Tool

This adds the quote step for the WhatsApp ordering flow.

It creates:

```text
POST /rpc/cotizar_pedido_whatsapp
POST /rpc/buscar_productos_whatsapp
```

## Why this is needed

Before calling:

```text
POST /rpc/confirmar_pedido_whatsapp
```

OpenClaw needs to quote the customer order:

1. Match customer text to products.
2. Find active price.
3. Calculate line totals.
4. Return the total.
5. Return a WhatsApp-ready confirmation message asking for payment method and location.

## Install

Copy the SQL file to WSL, then run:

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi < add_whatsapp_quote.sql
sudo systemctl restart postgrest
```

If you run PostgREST manually, stop it with `CTRL + C` and start it again.

## Test product search

```bash
curl -X POST "http://localhost:3000/rpc/buscar_productos_whatsapp" \
  -H "Content-Type: application/json" \
  -d '{
    "p_search": "pimienta",
    "p_limit": 10
  }' | jq
```

## Test quote endpoint

```bash
curl -X POST "http://localhost:3000/rpc/cotizar_pedido_whatsapp" \
  -H "Content-Type: application/json" \
  -d @test_quote_payload.json | jq
```

Expected result:

```text
ok = true
subtotal = 32.00
total = 32.00
whatsapp_quote_text = ready to send to customer
```

## Flow in OpenClaw

```text
1. Customer sends name and items.
2. OpenClaw parses items into JSON.
3. OpenClaw calls /rpc/cotizar_pedido_whatsapp.
4. OpenClaw sends whatsapp_quote_text.
5. Customer sends payment method and location.
6. Reverse geocode location.
7. Customer confirms address.
8. OpenClaw calls /rpc/confirmar_pedido_whatsapp.
```
