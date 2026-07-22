-- Phase 3: account-scoped conversation state and message persistence.
-- Additive and reversible: legacy tables remain intact for rollback/reporting.

BEGIN;

CREATE TABLE IF NOT EXISTS api.whatsapp_conversation_states (
    id bigserial PRIMARY KEY,
    channel_kind text NOT NULL DEFAULT 'whatsapp',
    channel_id text NOT NULL,
    account_id text,
    customer_address text NOT NULL,
    cliente_id integer REFERENCES api.clientes_whatsapp(id) ON DELETE SET NULL,
    estado text NOT NULL DEFAULT 'NEW' CHECK (estado IN (
        'NEW','ASKING_NAME_AND_ITEMS','WAITING_PAYMENT_AND_LOCATION',
        'WAITING_ADDRESS_CONFIRMATION','CONFIRMED','CANCELLED','ERROR'
    )),
    pedido_id integer REFERENCES api.pedidos(id) ON DELETE SET NULL,
    pedido_borrador jsonb,
    last_inbound_text text,
    last_outbound_text text,
    last_message_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (channel_kind, channel_id, customer_address)
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversation_states_recent
ON api.whatsapp_conversation_states(channel_kind, channel_id, last_message_at DESC);

INSERT INTO api.whatsapp_conversation_states (
    channel_kind, channel_id, account_id, customer_address, cliente_id, estado,
    pedido_id, pedido_borrador, last_inbound_text, last_outbound_text,
    last_message_at, created_at, updated_at
)
SELECT
    COALESCE(NULLIF(channel_kind, ''), 'whatsapp'),
    COALESCE(NULLIF(channel_id, ''), 'replau-main'),
    account_id,
    COALESCE(NULLIF(customer_address, ''), whatsapp_number),
    cliente_id, estado, pedido_id, pedido_borrador, last_inbound_text,
    last_outbound_text, last_message_at, created_at, updated_at
FROM api.whatsapp_conversaciones
ON CONFLICT (channel_kind, channel_id, customer_address) DO NOTHING;

CREATE OR REPLACE FUNCTION api.registrar_whatsapp_mensaje_canal(
    p_channel_kind text,
    p_channel_id text,
    p_account_id text,
    p_customer_address text,
    p_direction text,
    p_message_type text DEFAULT 'text',
    p_message_text text DEFAULT NULL,
    p_latitude numeric DEFAULT NULL,
    p_longitude numeric DEFAULT NULL,
    p_raw_payload jsonb DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_kind text := lower(trim(COALESCE(p_channel_kind, 'whatsapp')));
    v_channel text := trim(COALESCE(p_channel_id, ''));
    v_customer text := trim(COALESCE(p_customer_address, ''));
    v_direction text := upper(trim(COALESCE(p_direction, '')));
    v_message_id integer;
BEGIN
    IF v_kind <> 'whatsapp' OR v_channel = '' OR length(v_channel) > 120 THEN
        RAISE EXCEPTION 'Invalid channel identity';
    END IF;
    IF v_customer !~ '^[0-9]{8,20}$' THEN RAISE EXCEPTION 'Invalid customer address'; END IF;
    IF v_direction NOT IN ('INBOUND','OUTBOUND') THEN RAISE EXCEPTION 'Invalid direction'; END IF;

    INSERT INTO api.whatsapp_mensajes (
        whatsapp_number, channel_kind, channel_id, account_id, customer_address,
        direction, message_type, message_text, latitude, longitude, raw_payload
    ) VALUES (
        v_customer, v_kind, v_channel, NULLIF(trim(p_account_id), ''), v_customer,
        v_direction, p_message_type, p_message_text, p_latitude, p_longitude, p_raw_payload
    ) RETURNING id INTO v_message_id;

    INSERT INTO api.whatsapp_conversation_states (
        channel_kind, channel_id, account_id, customer_address, estado,
        last_inbound_text, last_outbound_text, last_message_at
    ) VALUES (
        v_kind, v_channel, NULLIF(trim(p_account_id), ''), v_customer,
        CASE WHEN v_direction='INBOUND' THEN 'ASKING_NAME_AND_ITEMS' ELSE 'NEW' END,
        CASE WHEN v_direction='INBOUND' THEN p_message_text END,
        CASE WHEN v_direction='OUTBOUND' THEN p_message_text END,
        now()
    )
    ON CONFLICT (channel_kind, channel_id, customer_address) DO UPDATE SET
        account_id=COALESCE(EXCLUDED.account_id,api.whatsapp_conversation_states.account_id),
        last_inbound_text=CASE WHEN v_direction='INBOUND' THEN p_message_text ELSE api.whatsapp_conversation_states.last_inbound_text END,
        last_outbound_text=CASE WHEN v_direction='OUTBOUND' THEN p_message_text ELSE api.whatsapp_conversation_states.last_outbound_text END,
        last_message_at=now(), updated_at=now();

    RETURN jsonb_build_object('ok',true,'message_id',v_message_id,'channel_id',v_channel);
END;
$$;

NOTIFY pgrst, 'reload schema';
COMMIT;
