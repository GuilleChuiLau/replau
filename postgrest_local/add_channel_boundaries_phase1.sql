-- Replau channel boundaries Phase 1: non-breaking metadata only.
--
-- Purpose:
--   Prepare WhatsApp conversation/message/customer tables for multiple WhatsApp
--   channels/accounts without changing the live single-channel behavior yet.
--
-- Safe characteristics:
--   - Adds columns with defaults/nullability.
--   - Keeps existing whatsapp_number unique constraints and RPC signatures intact.
--   - Does NOT switch lookup keys yet.
--
-- Do not apply Phase 3 composite key changes until bridge code reads/writes
-- channel identity everywhere.

BEGIN;

ALTER TABLE api.clientes_whatsapp
    ADD COLUMN IF NOT EXISTS channel_kind text NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS channel_id text NOT NULL DEFAULT 'replau-main',
    ADD COLUMN IF NOT EXISTS account_id text,
    ADD COLUMN IF NOT EXISTS customer_address text;

UPDATE api.clientes_whatsapp
SET customer_address = whatsapp_number
WHERE customer_address IS NULL OR trim(customer_address) = '';

ALTER TABLE api.clientes_whatsapp
    ALTER COLUMN customer_address SET DEFAULT '';

ALTER TABLE api.whatsapp_conversaciones
    ADD COLUMN IF NOT EXISTS channel_kind text NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS channel_id text NOT NULL DEFAULT 'replau-main',
    ADD COLUMN IF NOT EXISTS account_id text,
    ADD COLUMN IF NOT EXISTS customer_address text;

UPDATE api.whatsapp_conversaciones
SET customer_address = whatsapp_number
WHERE customer_address IS NULL OR trim(customer_address) = '';

ALTER TABLE api.whatsapp_conversaciones
    ALTER COLUMN customer_address SET DEFAULT '';

ALTER TABLE api.whatsapp_mensajes
    ADD COLUMN IF NOT EXISTS channel_kind text NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS channel_id text NOT NULL DEFAULT 'replau-main',
    ADD COLUMN IF NOT EXISTS account_id text,
    ADD COLUMN IF NOT EXISTS customer_address text;

UPDATE api.whatsapp_mensajes
SET customer_address = whatsapp_number
WHERE customer_address IS NULL OR trim(customer_address) = '';

ALTER TABLE api.whatsapp_mensajes
    ALTER COLUMN customer_address SET DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_clientes_whatsapp_channel_customer
ON api.clientes_whatsapp(channel_kind, channel_id, customer_address);

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversaciones_channel_customer
ON api.whatsapp_conversaciones(channel_kind, channel_id, customer_address);

CREATE INDEX IF NOT EXISTS idx_whatsapp_mensajes_channel_customer
ON api.whatsapp_mensajes(channel_kind, channel_id, customer_address, created_at DESC);

COMMENT ON COLUMN api.clientes_whatsapp.channel_id IS 'Logical inbound channel/account namespace. Phase 1 default: replau-main.';
COMMENT ON COLUMN api.whatsapp_conversaciones.channel_id IS 'Logical inbound channel/account namespace. Phase 1 default: replau-main.';
COMMENT ON COLUMN api.whatsapp_mensajes.channel_id IS 'Logical inbound channel/account namespace. Phase 1 default: replau-main.';

COMMIT;
