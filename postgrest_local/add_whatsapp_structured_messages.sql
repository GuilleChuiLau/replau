BEGIN;

CREATE OR REPLACE FUNCTION api.whatsapp_message_payload_is_valid(p_payload jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    v_type text;
    v_button jsonb;
    v_button_ids text[] := ARRAY[]::text[];
BEGIN
    IF p_payload IS NULL THEN
        RETURN true;
    END IF;
    IF jsonb_typeof(p_payload) <> 'object' OR octet_length(p_payload::text) > 32768 THEN
        RETURN false;
    END IF;
    IF p_payload ->> 'schema_version' <> '1' THEN
        RETURN false;
    END IF;

    v_type := p_payload ->> 'type';
    IF v_type NOT IN ('text', 'interactive_buttons', 'template') THEN
        RETURN false;
    END IF;
    IF length(trim(COALESCE(p_payload ->> 'fallback_text', ''))) NOT BETWEEN 1 AND 4096 THEN
        RETURN false;
    END IF;

    IF v_type = 'text' THEN
        RETURN true;
    END IF;

    IF v_type = 'interactive_buttons' THEN
        IF jsonb_typeof(p_payload -> 'interactive') <> 'object'
           OR length(trim(COALESCE(p_payload #>> '{interactive,body}', ''))) NOT BETWEEN 1 AND 1024 THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(p_payload #> '{interactive,buttons}') <> 'array' THEN
            RETURN false;
        END IF;
        IF jsonb_array_length(p_payload #> '{interactive,buttons}') NOT BETWEEN 1 AND 3 THEN
            RETURN false;
        END IF;
        FOR v_button IN SELECT value FROM jsonb_array_elements(p_payload #> '{interactive,buttons}')
        LOOP
            IF jsonb_typeof(v_button) <> 'object'
               OR length(trim(COALESCE(v_button ->> 'id', ''))) NOT BETWEEN 1 AND 256
               OR length(trim(COALESCE(v_button ->> 'title', ''))) NOT BETWEEN 1 AND 20
               OR (v_button ->> 'id') = ANY(v_button_ids) THEN
                RETURN false;
            END IF;
            v_button_ids := array_append(v_button_ids, v_button ->> 'id');
        END LOOP;
        RETURN true;
    END IF;

    IF jsonb_typeof(p_payload -> 'template') <> 'object'
       OR COALESCE(p_payload #>> '{template,name}', '') !~ '^[a-z0-9_]{1,512}$'
       OR COALESCE(p_payload #>> '{template,language}', '') !~ '^[A-Za-z]{2,3}([_-][A-Za-z0-9]{2,8})?$'
       OR upper(COALESCE(p_payload #>> '{template,category}', '')) NOT IN ('UTILITY', 'MARKETING', 'AUTHENTICATION') THEN
        RETURN false;
    END IF;
    IF p_payload #> '{template,components}' IS NOT NULL THEN
        IF jsonb_typeof(p_payload #> '{template,components}') <> 'array' THEN
            RETURN false;
        END IF;
        IF jsonb_array_length(p_payload #> '{template,components}') > 10 THEN
            RETURN false;
        END IF;
    END IF;
    RETURN true;
END;
$$;

ALTER TABLE api.whatsapp_outbox
    ADD COLUMN IF NOT EXISTS message_payload jsonb;

ALTER TABLE api.whatsapp_outbox
    DROP CONSTRAINT IF EXISTS whatsapp_outbox_message_payload_check;
ALTER TABLE api.whatsapp_outbox
    ADD CONSTRAINT whatsapp_outbox_message_payload_check
    CHECK (api.whatsapp_message_payload_is_valid(message_payload)) NOT VALID;
ALTER TABLE api.whatsapp_outbox
    VALIDATE CONSTRAINT whatsapp_outbox_message_payload_check;

COMMENT ON COLUMN api.whatsapp_outbox.message_payload IS
    'Optional provider-neutral WhatsApp payload. message_text remains the mandatory safe fallback.';

CREATE OR REPLACE FUNCTION api.registrar_whatsapp_outbox_structured(
    p_pedido_id integer,
    p_event_type text,
    p_message_text text,
    p_message_payload jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_result jsonb;
    v_outbox_id integer;
BEGIN
    IF NOT api.whatsapp_message_payload_is_valid(p_message_payload) THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Invalid WhatsApp message_payload');
    END IF;
    IF trim(COALESCE(p_message_payload ->> 'fallback_text', '')) <> trim(COALESCE(p_message_text, '')) THEN
        RETURN jsonb_build_object('ok', false, 'error', 'message_text must equal message_payload.fallback_text');
    END IF;

    v_result := api.registrar_whatsapp_outbox(p_pedido_id, p_event_type, p_message_text);
    IF NOT COALESCE((v_result ->> 'ok')::boolean, false) THEN
        RETURN v_result;
    END IF;

    v_outbox_id := (v_result ->> 'outbox_id')::integer;
    UPDATE api.whatsapp_outbox
    SET message_payload = p_message_payload
    WHERE id = v_outbox_id;

    RETURN v_result || jsonb_build_object(
        'message_type', p_message_payload ->> 'type',
        'schema_version', (p_message_payload ->> 'schema_version')::integer
    );
END;
$$;

GRANT EXECUTE ON FUNCTION api.registrar_whatsapp_outbox_structured(integer, text, text, jsonb) TO web_anon;

NOTIFY pgrst, 'reload schema';

COMMIT;
