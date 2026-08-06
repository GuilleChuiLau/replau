# Replau WhatsApp send adapter

The adapter exposes `POST /send/whatsapp` and keeps transport details outside
ordering and outbox code.

`WHATSAPP_TRANSPORT=openclaw` is the safe default. It accepts structured
messages but sends their mandatory text fallback through the existing OpenClaw
CLI. `WHATSAPP_TRANSPORT=meta_cloud_api` enables native reply buttons, approved
templates, read receipts, and typing indicators after protected Meta settings
are configured.

The health response reports the active transport and capabilities without
revealing credentials. See `../WHATSAPP_STRUCTURED_MESSAGES.md` for migration,
testing, activation, and rollback.
