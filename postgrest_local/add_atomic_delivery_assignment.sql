BEGIN;

CREATE OR REPLACE FUNCTION api.assign_delivery_driver_atomic(
    p_pedido_num text,
    p_repartidor_id integer,
    p_actor text,
    p_idempotency_key text,
    p_expected_active_assignment_id integer DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_order record;
    v_driver api.repartidores%ROWTYPE;
    v_assignment api.delivery_asignaciones%ROWTYPE;
    v_existing_event api.delivery_operation_events%ROWTYPE;
    v_previous_ids integer[];
    v_actor text := trim(COALESCE(p_actor, ''));
    v_key text := trim(COALESCE(p_idempotency_key, ''));
    v_now timestamptz := now();
    v_fee numeric(14,2);
BEGIN
    IF length(v_actor) NOT BETWEEN 1 AND 80
       OR v_actor !~ '^[[:alnum:] ._@-]+$' THEN
        RAISE EXCEPTION 'Invalid actor';
    END IF;
    IF length(v_key) NOT BETWEEN 16 AND 120
       OR v_key !~ '^[A-Za-z0-9._:-]+$' THEN
        RAISE EXCEPTION 'Invalid idempotency key';
    END IF;

    SELECT * INTO v_existing_event
    FROM api.delivery_operation_events
    WHERE idempotency_key = v_key;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'ok', true,
            'duplicate', true,
            'assignment_id', v_existing_event.assignment_id
        );
    END IF;

    SELECT
        p.id,
        p.pedido_num,
        p.estado,
        p.metodo_pago,
        p.observacion,
        v.direccion_confirmada,
        v.direccion_detectada,
        pf.status AS payment_status
    INTO v_order
    FROM api.pedidos p
    JOIN api.v_pedidos_logistica v ON v.id = p.id
    LEFT JOIN api.payment_fulfillments pf ON pf.pedido_id = p.id
    WHERE p.pedido_num = trim(p_pedido_num)
    LIMIT 1
    FOR UPDATE OF p;

    IF v_order.id IS NULL THEN
        RAISE EXCEPTION 'Pedido not found';
    END IF;

    -- A concurrent request with the same key may have completed while this
    -- transaction waited for the order lock.
    SELECT * INTO v_existing_event
    FROM api.delivery_operation_events
    WHERE idempotency_key = v_key;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'ok', true,
            'duplicate', true,
            'assignment_id', v_existing_event.assignment_id
        );
    END IF;

    IF upper(COALESCE(v_order.estado, '')) IN ('ENTREGADO', 'ANULADO', 'CANCELLED') THEN
        RAISE EXCEPTION 'Order is not assignable from state %', v_order.estado;
    END IF;
    IF upper(COALESCE(v_order.observacion, '')) LIKE '%MODALIDAD: RECOJO%'
       OR upper(COALESCE(v_order.observacion, '')) LIKE '%MODALIDAD: PICKUP%'
       OR upper(COALESCE(v_order.direccion_confirmada, v_order.direccion_detectada, '')) LIKE 'RECOJO EN RESTAURANTE%' THEN
        RAISE EXCEPTION 'Pickup orders do not require a driver';
    END IF;
    IF (
        upper(COALESCE(v_order.metodo_pago, '')) = 'CONTRA_ENTREGA'
        AND COALESCE(v_order.payment_status, '') NOT IN ('COD_DUE', 'COD_COLLECTED', 'RECONCILED', 'SETTLED')
    ) OR (
        upper(COALESCE(v_order.metodo_pago, '')) <> 'CONTRA_ENTREGA'
        AND COALESCE(v_order.payment_status, '') NOT IN ('RELEASED', 'RECONCILED', 'SETTLED')
    ) THEN
        RAISE EXCEPTION 'Payment is not cleared for dispatch';
    END IF;

    SELECT * INTO v_driver
    FROM api.repartidores
    WHERE id = p_repartidor_id
    FOR UPDATE;
    IF v_driver.id IS NULL THEN
        RAISE EXCEPTION 'Driver not found';
    END IF;
    IF NOT v_driver.activo THEN
        RAISE EXCEPTION 'Driver is not active';
    END IF;

    SELECT COALESCE(array_agg(id ORDER BY id), ARRAY[]::integer[])
    INTO v_previous_ids
    FROM api.delivery_asignaciones
    WHERE pedido_id = v_order.id
      AND status IN ('OFFERED', 'ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED', 'FAILED');
    IF cardinality(v_previous_ids) > 0 AND p_expected_active_assignment_id IS NULL THEN
        RAISE EXCEPTION 'Assignment conflict: order already has an active assignment';
    END IF;
    IF p_expected_active_assignment_id IS NOT NULL
       AND NOT (p_expected_active_assignment_id = ANY(v_previous_ids)) THEN
        RAISE EXCEPTION 'Assignment conflict: active assignment changed';
    END IF;

    v_fee := api.delivery_driver_fee();
    INSERT INTO api.delivery_asignaciones(
        pedido_id,
        repartidor_id,
        status,
        fee,
        responded_at,
        assigned_at,
        response_text,
        notes
    )
    VALUES(
        v_order.id,
        v_driver.id,
        'ASSIGNED',
        v_fee,
        v_now,
        v_now,
        'ASIGNADO_DESDE_DISPATCH',
        'Asignado atomicamente desde Delivery Station'
    )
    RETURNING * INTO v_assignment;

    UPDATE api.delivery_asignaciones
    SET status = 'CANCELLED',
        notes = concat_ws(
            E'\n',
            NULLIF(trim(COALESCE(notes, '')), ''),
            'Cancelado por reasignacion atomica a ' || v_driver.codigo || ' desde Delivery Station'
        ),
        updated_at = v_now,
        version = version + 1
    WHERE pedido_id = v_order.id
      AND id <> v_assignment.id
      AND status IN ('OFFERED', 'ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED', 'FAILED');

    INSERT INTO api.delivery_operation_events(
        assignment_id,
        pedido_id,
        event_type,
        actor,
        from_status,
        to_status,
        details,
        idempotency_key,
        created_at
    )
    VALUES(
        v_assignment.id,
        v_order.id,
        'DELIVERY_ATOMIC_ASSIGNMENT',
        v_actor,
        NULL,
        'ASSIGNED',
        jsonb_build_object(
            'repartidor_id', v_driver.id,
            'repartidor_codigo', v_driver.codigo,
            'previous_assignment_ids', v_previous_ids
        ),
        v_key,
        v_now
    );

    RETURN jsonb_build_object(
        'ok', true,
        'duplicate', false,
        'assignment_id', v_assignment.id,
        'pedido_id', v_order.id,
        'pedido_num', v_order.pedido_num,
        'repartidor_id', v_driver.id,
        'repartidor_codigo', v_driver.codigo,
        'fee', v_fee,
        'cancelled_assignment_ids', v_previous_ids
    );
END;
$$;

GRANT EXECUTE ON FUNCTION api.assign_delivery_driver_atomic(text, integer, text, text, integer) TO web_anon;

NOTIFY pgrst, 'reload schema';
COMMIT;
