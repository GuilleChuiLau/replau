# WhatsApp Conversation Requests

## Purpose

Record direct, user-initiated WhatsApp conversations in a private operations
queue while the existing Replau restaurant flow replies to the customer.

This queue is not a cold-outreach list. A row exists only after the WhatsApp
user initiates the conversation.

## Identity and lifecycle

Requests are unique by:

```text
(channel_kind, channel_id, customer_address)
```

The inbound OpenClaw plugin derives a stable `channel_id` from `accountId` so
two WhatsApp accounts cannot collide in the request queue. Internal request
states are deliberately separate from order/conversation states:

- `AUTO_STARTED`: Replau started its restaurant reply flow.
- `IN_PROGRESS`: Staff are actively reviewing or assisting.
- `CLOSED`: No current staff action is required.
- `BLOCKED`: Staff intentionally excluded the request from follow-up.

Repeated inbound messages update `last_message_text`, `last_inbound_at`, and
`inbound_count`. Repeated delivery of the same provider message ID does not
increment the count.

## Components

- Migration: `postgrest_local/add_whatsapp_conversation_requests.sql`
- Inbound account namespace: `replau_openclaw_inbound_plugin/router-core.ts`
- Bridge registration: `replau_openclaw_whatsapp_bridge/bridge.py`
- Private staff UI: `http://127.0.0.1:8793/conversation-requests`
- Private JSON API: `http://127.0.0.1:8793/api/conversation-requests`

The bridge treats queue registration as best-effort. Queue downtime must never
prevent a customer from ordering.

## Controlled rollout

1. Back up PostgreSQL and verify the backup service succeeded.
2. Apply `add_whatsapp_conversation_requests.sql`, followed by
   `add_channel_boundaries_phase3.sql`, to `localapi` as the PostgreSQL
   administrator.
3. Deploy the updated bridge, ops dashboard, and inbound plugin source.
4. Restart only the bridge, ops dashboard, and OpenClaw gateway.
5. Send one controlled inbound message from the existing restaurant account.
6. Verify one queue row, an automatic restaurant reply, and no duplicate order.
7. Repeat the same provider event in a synthetic test and verify the count does
   not increase.

## Multi-account gate

Phase 3 is implemented and regression-tested in source. Do not enable a second
live WhatsApp account until both migrations and the updated bridge/plugin have
been deployed together and the controlled current-account test passes.
