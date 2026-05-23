BEGIN;

CREATE OR REPLACE FUNCTION api.delivery_cancel_assignment(
    p_assignment_id integer,
    p_notes text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_assignment record;
BEGIN
    SELECT * INTO v_assignment
    FROM api.delivery_asignaciones
    WHERE id = p_assignment_id
    FOR UPDATE;

    IF v_assignment.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Assignment not found');
    END IF;

    IF v_assignment.status IN ('COMPLETED','CANCELLED') THEN
        RETURN jsonb_build_object('ok', true, 'assignment_id', v_assignment.id, 'status', v_assignment.status, 'unchanged', true);
    END IF;

    UPDATE api.delivery_asignaciones
    SET status = 'CANCELLED',
        notes = NULLIF(trim(COALESCE(p_notes, '')), ''),
        updated_at = now()
    WHERE id = v_assignment.id;

    RETURN jsonb_build_object('ok', true, 'assignment_id', v_assignment.id, 'status', 'CANCELLED');
END;
$$;

CREATE OR REPLACE FUNCTION api.delivery_complete_latest_assignment(p_pedido_id integer)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_assignment record;
BEGIN
    SELECT * INTO v_assignment
    FROM api.delivery_asignaciones
    WHERE pedido_id = p_pedido_id
      AND status IN ('OFFERED','ACCEPTED','ASSIGNED')
    ORDER BY assigned_at DESC NULLS LAST, offered_at DESC, created_at DESC
    LIMIT 1
    FOR UPDATE;

    IF v_assignment.id IS NULL THEN
        RETURN jsonb_build_object('ok', true, 'handled', false, 'reason', 'No active assignment');
    END IF;

    UPDATE api.delivery_asignaciones
    SET status = 'COMPLETED',
        completed_at = now(),
        updated_at = now()
    WHERE id = v_assignment.id;

    RETURN jsonb_build_object('ok', true, 'handled', true, 'assignment_id', v_assignment.id, 'status', 'COMPLETED');
END;
$$;

CREATE OR REPLACE FUNCTION api.delivery_offer_next_by_pedido_num(p_pedido_num text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pedido_id integer;
BEGIN
    SELECT id INTO v_pedido_id
    FROM api.pedidos
    WHERE pedido_num = trim(p_pedido_num)
    LIMIT 1;

    IF v_pedido_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Pedido not found');
    END IF;

    RETURN api.ofrecer_delivery_a_siguiente_repartidor(v_pedido_id);
END;
$$;

CREATE OR REPLACE FUNCTION api.delivery_set_driver_active(
    p_repartidor_id integer,
    p_activo boolean
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_driver record;
BEGIN
    UPDATE api.repartidores
    SET activo = p_activo,
        updated_at = now()
    WHERE id = p_repartidor_id
    RETURNING * INTO v_driver;

    IF v_driver.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Driver not found');
    END IF;

    RETURN jsonb_build_object('ok', true, 'repartidor_id', v_driver.id, 'activo', v_driver.activo);
END;
$$;

GRANT EXECUTE ON FUNCTION api.delivery_cancel_assignment(integer, text) TO web_anon;
GRANT EXECUTE ON FUNCTION api.delivery_complete_latest_assignment(integer) TO web_anon;
GRANT EXECUTE ON FUNCTION api.delivery_offer_next_by_pedido_num(text) TO web_anon;
GRANT EXECUTE ON FUNCTION api.delivery_set_driver_active(integer, boolean) TO web_anon;

COMMIT;
