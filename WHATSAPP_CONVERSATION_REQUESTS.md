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

## Privacy retention

`add_whatsapp_request_retention.sql` installs a transactional cleanup function.
The daily `replau-conversation-retention.timer` applies these defaults after the
02:30 database backup:

- Open requests inactive for 30 days lose sender name, message text, and provider message IDs.
- Closed or blocked requests lose those fields after 7 days.
- Closed or blocked request rows are deleted after 90 days.

Channel identity, customer address, status, counts, and timestamps remain only
while the row is operationally retained. The cleanup never deletes active rows.
The three windows can be overridden in `ops.env` with
`WHATSAPP_REQUEST_ACTIVE_REDACT_DAYS`, `WHATSAPP_REQUEST_CLOSED_REDACT_DAYS`,
and `WHATSAPP_REQUEST_DELETE_DAYS`; both the runner and database function reject
unsafe values.

## Staff inbox

Migration `add_whatsapp_staff_inbox.sql` upgrades the private queue into an
operational inbox at `http://127.0.0.1:8793/conversation-requests`.

The inbox provides:

- unread/read state and 15-minute waiting/SLA indicators;
- `NORMAL`, `HIGH`, and `URGENT` priority;
- staff assignment and take/resolve/block/reopen actions;
- append-only internal notes and an append-only lifecycle audit timeline;
- latest linked-order context when the customer has an order;
- search and status/priority/ownership/read-state filters;
- open, unread, overdue, urgent, new-today, resolved-today, and average
  first-response metrics.

Every write is validated by `api.update_whatsapp_request_inbox`; the dashboard
does not patch lifecycle fields directly. New customer messages mark a request
unread, and a message after closure reopens it with a fresh SLA. Internal notes
are private and are deleted by the existing privacy-retention job when the
corresponding request content reaches its redaction window.
# Staff replies

Staff can reply from the private Ops inbox. A reply is inserted transactionally into the existing WhatsApp outbox and delivered by the existing worker and OpenClaw adapter. Each submission carries a unique idempotency key, is limited to ten replies per conversation in five minutes, and creates an audit event. Blocked conversations cannot receive replies.

Apply and contract-test the additive migration:

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi \
  -f postgrest_local/add_whatsapp_staff_replies.sql \
  -f postgrest_local/test_whatsapp_staff_replies.sql
```

The contract test always rolls back and never sends a WhatsApp message. Delivery status is available from each request's **Outbound delivery history** link.
