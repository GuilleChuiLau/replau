BEGIN;

DO $$
DECLARE
    v_order_id integer;
    v_result jsonb;
    v_outbox_id integer;
    v_fallback text := 'Elige una opción: 1) Ver menú 2) Ver mi pedido 3) Hablar con alguien';
BEGIN
    SELECT id INTO v_order_id FROM api.pedidos ORDER BY id DESC LIMIT 1;
    IF v_order_id IS NULL THEN
        RAISE EXCEPTION 'Structured-message contract requires at least one order';
    END IF;

    IF NOT api.whatsapp_message_payload_is_valid(jsonb_build_object(
        'schema_version', 1,
        'type', 'interactive_buttons',
        'fallback_text', v_fallback,
        'interactive', jsonb_build_object(
            'body', '¿Qué deseas hacer?',
            'buttons', jsonb_build_array(
                jsonb_build_object('id', 'replau.view_menu', 'title', 'Ver menú'),
                jsonb_build_object('id', 'replau.view_order', 'title', 'Ver mi pedido'),
                jsonb_build_object('id', 'replau.human_help', 'title', 'Hablar con alguien')
            )
        )
    )) THEN
        RAISE EXCEPTION 'Valid interactive payload was rejected';
    END IF;

    IF api.whatsapp_message_payload_is_valid('{"schema_version":1,"type":"interactive_buttons","fallback_text":"x","interactive":{"body":"x","buttons":[]}}'::jsonb) THEN
        RAISE EXCEPTION 'Empty button array was accepted';
    END IF;

    v_result := api.registrar_whatsapp_outbox_structured(
        v_order_id,
        'CUSTOM',
        v_fallback,
        jsonb_build_object(
            'schema_version', 1,
            'type', 'interactive_buttons',
            'fallback_text', v_fallback,
            'interactive', jsonb_build_object(
                'body', '¿Qué deseas hacer?',
                'buttons', jsonb_build_array(
                    jsonb_build_object('id', 'replau.view_menu', 'title', 'Ver menú'),
                    jsonb_build_object('id', 'replau.view_order', 'title', 'Ver mi pedido'),
                    jsonb_build_object('id', 'replau.human_help', 'title', 'Hablar con alguien')
                )
            )
        )
    );
    IF NOT COALESCE((v_result ->> 'ok')::boolean, false)
       OR v_result ->> 'message_type' <> 'interactive_buttons' THEN
        RAISE EXCEPTION 'Structured outbox enqueue failed: %', v_result;
    END IF;
    v_outbox_id := (v_result ->> 'outbox_id')::integer;
    IF (SELECT message_payload ->> 'type' FROM api.whatsapp_outbox WHERE id = v_outbox_id) <> 'interactive_buttons' THEN
        RAISE EXCEPTION 'Structured payload was not persisted';
    END IF;

    v_result := api.registrar_whatsapp_outbox_structured(
        v_order_id,
        'CUSTOM',
        'different fallback',
        '{"schema_version":1,"type":"text","fallback_text":"expected fallback"}'::jsonb
    );
    IF COALESCE((v_result ->> 'ok')::boolean, false) THEN
        RAISE EXCEPTION 'Mismatched fallback text was accepted';
    END IF;
END;
$$;

ROLLBACK;
\echo 'WhatsApp structured-message contract test passed; all fixtures rolled back.'
