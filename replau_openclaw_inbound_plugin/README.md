# Replau WhatsApp Inbound Router

OpenClaw plugin that intercepts direct WhatsApp messages in the awaited
`before_dispatch` phase, forwards a normalized payload to the local Replau
ordering bridge, and returns only the bridge's deterministic restaurant reply.
It also registers `inbound_claim` for plugin-owned conversations.

This keeps customer chats inside the restaurant workflow and prevents public
senders from accessing the personal OpenClaw agent or its tools. The plugin
reads the existing bridge hook token from the private bridge environment file;
it never copies or logs that token.

The router supports text and best-effort inbound image/document extraction
from OpenClaw's private inbound media directory. Location messages remain
compatible because the enriched dispatch body contains coordinates and the
Replau bridge already extracts them.

Only direct WhatsApp conversations are handled. Groups, channels, parent
conversations, and threaded bindings are declined. Media is restricted to
JPG, PNG, WebP, or PDF files inside the canonical inbound directory and is
limited to 8 MB by default (`maxMediaBytes`).

Provider button/list replies are normalized into stable Replau action IDs and
forwarded to the bridge. Bridge responses may contain a versioned
`message_payload`; the plugin passes it to the send adapter together with the
inbound provider message ID so a future Meta Cloud API transport can use native
buttons plus read/typing behavior. Text fallback remains mandatory.
