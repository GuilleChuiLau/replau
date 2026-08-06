# WhatsApp structured messages

## Outcome

Replau supports a versioned, provider-neutral outbound message contract while
preserving plain text as a mandatory fallback. The existing OpenClaw transport
remains the default. Meta Cloud API support is present but disabled until its
credentials, approved templates, and account review are complete.

## Message contract

Every structured message contains:

```json
{
  "schema_version": 1,
  "type": "interactive_buttons",
  "fallback_text": "Text that remains usable on a text-only transport",
  "interactive": {
    "body": "What the native interactive message displays",
    "buttons": [{"id": "replau.view_menu", "title": "Ver menú"}]
  }
}
```

Supported types are `text`, `interactive_buttons`, and `template`. The database
limits interactive messages to three unique buttons and templates to the
`UTILITY`, `MARKETING`, or `AUTHENTICATION` categories. `message_text` must equal
`fallback_text`, so switching transports cannot create a blank customer message.

## Transport behavior

- `WHATSAPP_TRANSPORT=openclaw` sends `fallback_text`. It reports
  `delivery_mode=fallback_text` when a richer message was requested.
- `WHATSAPP_TRANSPORT=meta_cloud_api` sends text, reply buttons, and approved
  templates natively. With `reply_to_message_id`, it marks the inbound message
  read and starts the typing indicator before replying.
- Dry-run Meta requests return the exact request body without a network call.

Changing transports does not bypass the existing database outbound policy,
opt-outs, service window, rate limits, coalescing, retries, or circuit breaker.

## Interactive inbound actions

- `replau.view_menu` → `menu`
- `replau.view_order` → `estado de mi pedido`
- `replau.human_help` → `hablar con alguien`

Unknown provider reply IDs use their visible title as the fallback. Channel and
account identity survive normalization, including direct Meta webhook shapes.

## Database migration

The application database role intentionally cannot alter schema. Apply and test
the additive migration with the local PostgreSQL owner:

```bash
sudo -u postgres psql -d localapi -v ON_ERROR_STOP=1 \
  -f /home/guill/.openclaw/workspace/replau/postgrest_local/add_whatsapp_structured_messages.sql

sudo -u postgres psql -d localapi -v ON_ERROR_STOP=1 \
  -f /home/guill/.openclaw/workspace/replau/postgrest_local/test_whatsapp_structured_messages.sql
```

The worker detects a pre-migration schema and stays in text-only compatibility
mode, so a service restart before migration is safe.

## Utility-template drafts

Validate the three Spanish transactional drafts without external changes:

```bash
python3 replau_whatsapp_templates/provision_templates.py
```

Creation is guarded by `--apply` plus `META_TEMPLATE_APPLY_CONFIRM=YES` and must
not be used while account status or template wording remains unresolved.

## Activation checklist

1. Apply and run the rollback-only database contract above.
2. Confirm Meta Business verification, phone ownership, account quality, and
   utility-template approval.
3. Put `META_PHONE_NUMBER_ID` and `META_ACCESS_TOKEN` only in the protected
   adapter environment file.
4. Test the adapter with `dry_run=true` and confirm native request bodies.
5. Set `WHATSAPP_TRANSPORT=meta_cloud_api`, restart only the adapter, and run a
   controlled customer-initiated canary.
6. Keep the existing outbound policy limits active and monitor failures.

Rollback is `WHATSAPP_TRANSPORT=openclaw` followed by an adapter restart.
