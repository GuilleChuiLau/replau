# Replau Channel Boundaries

Goal: make Replau safe to run behind more than one WhatsApp channel/account without turning the bridge into a knot of channel-specific hacks.

## Current state

The live solution works, but it is single-channel by design:

- Customer identity is keyed by `whatsapp_number` only.
- Conversation identity is keyed by `whatsapp_number` only.
- Message log stores `whatsapp_number` only.
- Bridge route is `/webhook/whatsapp` and assumes one Replau WhatsApp inbox.
- Send adapter targets one OpenClaw channel/account via env.

That is fine for today. It becomes risky when opening multiple WhatsApp channels because the same customer number on two channels would share state, memories, drafts, payment receipts, and order history.

## Clean boundaries

### 1. Transport boundary

Owns provider-specific webhook shapes and outbound send mechanics.

Examples:

- OpenClaw WhatsApp webhook payload
- Future WhatsApp account/channel payloads
- Media URL/base64 extraction
- Reply delivery metadata

It should output a normalized inbound message:

```json
{
  "channel_kind": "whatsapp",
  "channel_id": "replau-main",
  "account_id": "optional-provider-account",
  "customer_address": "51999999999",
  "message_type": "text|location|image|document",
  "message_text": "...",
  "latitude": -12.0,
  "longitude": -77.0,
  "media": { "url": "...", "mime_type": "..." },
  "raw_payload": {}
}
```

Rule: transport code may know OpenClaw/WhatsApp details; ordering code should not.

### 2. Conversation boundary

Owns state machine and draft state.

Conversation key should become:

```text
(channel_kind, channel_id, customer_address)
```

Not just `whatsapp_number`.

Allowed states should stay intentionally small:

- `NEW`
- `ASKING_NAME_AND_ITEMS`
- `WAITING_PAYMENT_AND_LOCATION`
- `WAITING_ADDRESS_CONFIRMATION`
- `CONFIRMED`
- `CANCELLED`
- `ERROR`

Micro-steps like special request should live inside `pedido_borrador` flags, not as new DB states unless the DB constraint is migrated first.

### 3. Ordering boundary

Owns business logic:

- menu parsing
- quote-first flow
- payment/location requirements
- Yape proof rules
- special request/comments
- order confirmation payload

It should not care which WhatsApp account received the message.

### 4. Catalog boundary

Owns products, aliases, prices, combos, sauce rules.

Current hotwings canonical products:

- `ALITAS FRITAS PICANTES X 6` — S/14 — 1 sauce
- `ALITAS FRITAS PICANTES X 12` — S/24 — 2 sauces
- `ALITAS FRITAS PICANTES X 24` — S/42 — 4 sauces

Sauces:

- BBQ
- Honey Mustard
- Buffalo
- Blue Cheese
- Ranch

Aliases should map into canonical product names before quote RPC.

### 5. Persistence boundary

Owns PostgREST/DB details.

Bridge handlers should call repository-like helpers, not manually build URLs everywhere long-term.

Target helpers:

- `log_message(identity, direction, ...)`
- `get_conversation(identity)`
- `patch_conversation(identity, payload)`
- `get_customer(identity)`
- `quote_order(customer_name, items, delivery)`
- `confirm_order(identity, draft)`

### 6. Restaurant/tenant boundary

Before adding another restaurant or brand, add restaurant identity explicitly.

For multiple WhatsApp channels for the same restaurant, `channel_id` is enough.
For multiple restaurants, add `tenant_id` / `restaurant_id` and scope catalog/orders/customers by it.

Do not overload WhatsApp account as restaurant identity.

## Staged rollout plan

### Phase 0 — now: keep live stable

- Keep live single-channel behavior.
- Do not introduce unsupported DB states.
- Keep source and deploy bridge copies identical.
- Keep seed catalog aligned with live DB names.

### Phase 1 — non-breaking channel metadata

Add nullable/default metadata columns while preserving old unique keys:

- `channel_kind text default 'whatsapp'`
- `channel_id text default 'replau-main'`
- `account_id text null`
- `customer_address text generated/filled from whatsapp_number initially`

This lets us start logging channel identity without changing behavior.

### Phase 2 — code seam

Update bridge internals to pass an identity object:

```python
ConversationIdentity(
    channel_kind='whatsapp',
    channel_id='replau-main',
    customer_address=inbound.whatsapp_number,
)
```

Existing DB helper can still collapse that to `whatsapp_number` until schema is ready.

### Phase 3 — composite keys

After code reads/writes channel identity:

- create unique index on `(channel_kind, channel_id, customer_address)`
- update registrar/confirm/customer RPCs to accept channel fields
- stop relying on `whatsapp_number UNIQUE`

### Phase 4 — add second WhatsApp channel

Only after Phase 3:

- configure second OpenClaw WhatsApp account/channel
- deploy second bridge env or route with `CHANNEL_ID`
- run no-mutate QA and a controlled E2E test

## Sharp edges to avoid

- Do not add a state to Python unless DB `whatsapp_estado_check` accepts it.
- Do not key conversations only by phone number when multiple channels exist.
- Do not let future product imports revert canonical names.
- Do not duplicate payment receipt paths across channel/customer identity without scoping filenames.
- Do not make outbound send logic call OpenClaw from business logic; keep it in adapter/notification layer.

## Current verified gates

- Bridge health: `http://127.0.0.1:8789/health`
- PostgREST health: `http://127.0.0.1:3000/`
- No-mutate web QA: `/home/guill/codex/replau_web_qa.py --timeout 60 --no-mutate`
- Targeted parser/state tests can be run by importing `/opt/replau_openclaw_whatsapp_bridge/bridge.py` in its venv.
