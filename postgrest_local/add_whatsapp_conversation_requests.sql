-- Multi-account, consent-based WhatsApp conversation request queue.
-- A request is created only after the customer initiates a direct conversation.

BEGIN;

CREATE TABLE IF NOT EXISTS api.whatsapp_conversation_requests (
    id bigserial PRIMARY KEY,
    channel_kind text NOT NULL DEFAULT 'whatsapp',
    channel_id text NOT NULL,
    account_id text,
    customer_address text NOT NULL,
    sender_name text,
    first_message_text text,
    last_message_text text,
    first_provider_message_id text,
    last_provider_message_id text,
    inbound_count integer NOT NULL DEFAULT 1 CHECK (inbound_count > 0),
    status text NOT NULL DEFAULT 'AUTO_STARTED'
        CHECK (status IN ('AUTO_STARTED', 'IN_PROGRESS', 'CLOSED', 'BLOCKED')),
    consent_basis text NOT NULL DEFAULT 'USER_INITIATED'
        CHECK (consent_basis = 'USER_INITIATED'),
    first_inbound_at timestamptz NOT NULL DEFAULT now(),
    last_inbound_at timestamptz NOT NULL DEFAULT now(),
    status_updated_at timestamptz NOT NULL DEFAULT now(),
    status_updated_by text NOT NULL DEFAULT 'system',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (channel_kind, channel_id, customer_address)
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversation_requests_status
ON api.whatsapp_conversation_requests(status, last_inbound_at DESC);

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversation_requests_account
ON api.whatsapp_conversation_requests(channel_kind, channel_id, last_inbound_at DESC);

CREATE OR REPLACE FUNCTION api.register_whatsapp_conversation_request(
    p_channel_kind text,
    p_channel_id text,
    p_account_id text,
    p_customer_address text,
    p_sender_name text DEFAULT NULL,
    p_message_text text DEFAULT NULL,
    p_provider_message_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_row api.whatsapp_conversation_requests%ROWTYPE;
    v_is_new boolean := false;
    v_channel_kind text := lower(trim(COALESCE(p_channel_kind, 'whatsapp')));
    v_channel_id text := trim(COALESCE(p_channel_id, ''));
    v_customer_address text := trim(COALESCE(p_customer_address, ''));
BEGIN
    IF v_channel_kind <> 'whatsapp' THEN
        RAISE EXCEPTION 'Unsupported channel kind';
    END IF;
    IF v_channel_id = '' OR length(v_channel_id) > 120 THEN
        RAISE EXCEPTION 'Invalid channel id';
    END IF;
    IF v_customer_address !~ '^[0-9]{8,20}$' THEN
        RAISE EXCEPTION 'Invalid WhatsApp customer address';
    END IF;

    INSERT INTO api.whatsapp_conversation_requests (
        channel_kind, channel_id, account_id, customer_address, sender_name,
        first_message_text, last_message_text, first_provider_message_id,
        last_provider_message_id, status_updated_by
    ) VALUES (
        v_channel_kind, v_channel_id, NULLIF(trim(p_account_id), ''), v_customer_address,
        NULLIF(left(trim(p_sender_name), 160), ''), left(p_message_text, 2000),
        left(p_message_text, 2000), NULLIF(left(trim(p_provider_message_id), 240), ''),
        NULLIF(left(trim(p_provider_message_id), 240), ''), 'whatsapp-inbound'
    )
    ON CONFLICT (channel_kind, channel_id, customer_address) DO NOTHING
    RETURNING * INTO v_row;

    IF FOUND THEN
        v_is_new := true;
    ELSE
        UPDATE api.whatsapp_conversation_requests
        SET account_id = COALESCE(NULLIF(trim(p_account_id), ''), account_id),
            sender_name = COALESCE(NULLIF(left(trim(p_sender_name), 160), ''), sender_name),
            last_message_text = left(p_message_text, 2000),
            last_provider_message_id = COALESCE(NULLIF(left(trim(p_provider_message_id), 240), ''), last_provider_message_id),
            inbound_count = CASE
                WHEN NULLIF(trim(p_provider_message_id), '') IS NOT NULL
                     AND last_provider_message_id = trim(p_provider_message_id)
                    THEN inbound_count
                ELSE inbound_count + 1
            END,
            last_inbound_at = now(),
            updated_at = now(),
            status = CASE WHEN status = 'CLOSED' THEN 'AUTO_STARTED' ELSE status END,
            status_updated_at = CASE WHEN status = 'CLOSED' THEN now() ELSE status_updated_at END,
            status_updated_by = CASE WHEN status = 'CLOSED' THEN 'whatsapp-inbound' ELSE status_updated_by END
        WHERE channel_kind = v_channel_kind
          AND channel_id = v_channel_id
          AND customer_address = v_customer_address
        RETURNING * INTO v_row;
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'request_id', v_row.id,
        'is_new', v_is_new,
        'status', v_row.status,
        'inbound_count', v_row.inbound_count
    );
END;
$$;

COMMENT ON TABLE api.whatsapp_conversation_requests IS
'Private staff queue of direct, user-initiated WhatsApp conversation requests. Never use as a cold-outreach list.';

COMMIT;
