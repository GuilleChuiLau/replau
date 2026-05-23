BEGIN;

-- Re-apply only the public status update function so DESPACHADO triggers the
-- optional driver dispatch module now that it is installed.
CREATE OR REPLACE FUNCTION api.actualizar_estado_pedido_publico(
    p_pedido_num text,
    p_token text,
    p_estado text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pedido_id integer;
    v_estado text;
BEGIN
    v_estado := upper(trim(COALESCE(p_estado, '')));

    IF v_estado NOT IN ('CONFIRMADO', 'EN_PREPARACION', 'DESPACHADO', 'ENTREGADO', 'ANULADO') THEN
        RETURN jsonb_build_object(
            'ok', false,
            'error', 'Invalid status',
            'allowed', jsonb_build_array('CONFIRMADO', 'EN_PREPARACION', 'DESPACHADO', 'ENTREGADO', 'ANULADO')
        );
    END IF;

    SELECT p.id
    INTO v_pedido_id
    FROM api.pedidos p
    WHERE p.pedido_num = trim(p_pedido_num)
      AND p.public_token = trim(p_token)
      AND p.public_token_expires_at IS NOT NULL
      AND p.public_token_expires_at > now();

    IF v_pedido_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Invalid or expired order link');
    END IF;

    UPDATE api.pedidos
    SET estado = v_estado,
        updated_at = now()
    WHERE id = v_pedido_id;

    IF v_estado = 'DESPACHADO' THEN
        BEGIN
            PERFORM api.ofrecer_delivery_a_siguiente_repartidor(v_pedido_id);
        EXCEPTION WHEN undefined_function OR undefined_table THEN
            -- Driver dispatch module is optional; ignore until installed.
            NULL;
        END;
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'pedido_id', v_pedido_id,
        'pedido_num', p_pedido_num,
        'estado', v_estado
    );
END;
$$;

GRANT EXECUTE ON FUNCTION api.actualizar_estado_pedido_publico(text, text, text) TO web_anon;

COMMIT;
