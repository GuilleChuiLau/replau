BEGIN;

INSERT INTO api.delivery_config(key, value)
VALUES ('max_active_deliveries_per_driver', '3')
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION api.ofrecer_delivery_a_siguiente_repartidor(p_pedido_id integer)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_order record;
    v_driver record;
    v_fee numeric(14,2) := api.delivery_driver_fee();
    v_max_active integer;
    v_assignment_id integer;
    v_message text;
BEGIN
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
    WHERE p.id = p_pedido_id
    FOR UPDATE OF p;

    IF v_order.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Pedido not found');
    END IF;
    IF upper(COALESCE(v_order.estado, '')) IN ('ENTREGADO', 'ANULADO', 'CANCELLED') THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Order is not assignable from its current state');
    END IF;
    IF upper(COALESCE(v_order.observacion, '')) LIKE '%MODALIDAD: RECOJO%'
       OR upper(COALESCE(v_order.observacion, '')) LIKE '%MODALIDAD: PICKUP%'
       OR upper(COALESCE(v_order.direccion_confirmada, v_order.direccion_detectada, '')) LIKE 'RECOJO EN RESTAURANTE%' THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Pickup orders do not require a driver');
    END IF;
    IF (
        upper(COALESCE(v_order.metodo_pago, '')) = 'CONTRA_ENTREGA'
        AND COALESCE(v_order.payment_status, '') NOT IN ('COD_DUE', 'COD_COLLECTED', 'RECONCILED', 'SETTLED')
    ) OR (
        upper(COALESCE(v_order.metodo_pago, '')) <> 'CONTRA_ENTREGA'
        AND COALESCE(v_order.payment_status, '') NOT IN ('RELEASED', 'RECONCILED', 'SETTLED')
    ) THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Payment is not cleared for dispatch');
    END IF;
    IF EXISTS(
        SELECT 1
        FROM api.delivery_asignaciones
        WHERE pedido_id = p_pedido_id
          AND status IN ('OFFERED', 'ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED')
    ) THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Order already has an active delivery assignment');
    END IF;

    SELECT greatest(1, least(20, COALESCE(NULLIF(value, '')::integer, 3)))
    INTO v_max_active
    FROM api.delivery_config
    WHERE key = 'max_active_deliveries_per_driver';
    v_max_active := COALESCE(v_max_active, 3);

    WITH driver_scores AS MATERIALIZED (
        SELECT
            r.id,
            count(a.id) FILTER (
                WHERE a.status IN ('ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED')
            )::integer AS active_load,
            count(a.id) FILTER (WHERE a.status = 'OFFERED')::integer AS pending_offers,
            max(COALESCE(a.assigned_at, a.responded_at, a.offered_at, a.created_at))
                FILTER (WHERE a.status IN ('ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED', 'COMPLETED'))
                AS last_assignment_at
        FROM api.repartidores r
        LEFT JOIN api.delivery_asignaciones a ON a.repartidor_id = r.id
        WHERE r.activo = true
          AND NOT EXISTS (
              SELECT 1
              FROM api.delivery_asignaciones previous
              WHERE previous.pedido_id = p_pedido_id
                AND previous.repartidor_id = r.id
                AND previous.status IN ('OFFERED', 'ACCEPTED', 'REJECTED', 'ASSIGNED')
          )
        GROUP BY r.id
    )
    SELECT
        r.*,
        score.active_load,
        score.pending_offers,
        score.last_assignment_at
    INTO v_driver
    FROM api.repartidores r
    JOIN driver_scores score ON score.id = r.id
    WHERE score.active_load < v_max_active
    ORDER BY
        score.active_load ASC,
        score.pending_offers ASC,
        score.last_assignment_at ASC NULLS FIRST,
        r.orden_turno ASC,
        r.id ASC
    LIMIT 1
    FOR UPDATE OF r SKIP LOCKED;

    IF v_driver.id IS NULL THEN
        RETURN jsonb_build_object(
            'ok', false,
            'error', 'No driver is available below the active workload limit',
            'max_active_deliveries_per_driver', v_max_active
        );
    END IF;

    v_message := api.delivery_offer_message(p_pedido_id, v_fee);
    INSERT INTO api.delivery_asignaciones(pedido_id, repartidor_id, status, fee)
    VALUES (p_pedido_id, v_driver.id, 'OFFERED', v_fee)
    RETURNING id INTO v_assignment_id;

    INSERT INTO api.delivery_operation_events(
        assignment_id,
        pedido_id,
        event_type,
        actor,
        from_status,
        to_status,
        details,
        idempotency_key
    )
    VALUES(
        v_assignment_id,
        p_pedido_id,
        'DELIVERY_LOAD_AWARE_OFFER',
        'automatic-matcher',
        NULL,
        'OFFERED',
        jsonb_build_object(
            'repartidor_id', v_driver.id,
            'active_load_before_offer', v_driver.active_load,
            'pending_offers_before_offer', v_driver.pending_offers,
            'last_assignment_at', v_driver.last_assignment_at,
            'max_active_deliveries_per_driver', v_max_active,
            'selection_rule', 'active_load,pending_offers,last_assignment_at,turn_order'
        ),
        'delivery-load-offer-' || v_assignment_id
    );

    INSERT INTO api.whatsapp_outbox(
        pedido_id,
        whatsapp_number,
        message_text,
        event_type,
        status,
        idempotency_key
    )
    VALUES(
        p_pedido_id,
        v_driver.whatsapp_number,
        v_message,
        'CUSTOM',
        'PENDING',
        'delivery-load-offer-driver-' || v_assignment_id
    );

    RETURN jsonb_build_object(
        'ok', true,
        'assignment_id', v_assignment_id,
        'pedido_id', p_pedido_id,
        'repartidor_id', v_driver.id,
        'repartidor_codigo', v_driver.codigo,
        'repartidor_nombre', v_driver.nombre,
        'fee', v_fee,
        'active_load_before_offer', v_driver.active_load,
        'pending_offers_before_offer', v_driver.pending_offers,
        'max_active_deliveries_per_driver', v_max_active,
        'selection_reason', 'Menor carga activa, menos ofertas pendientes y mayor tiempo sin asignacion'
    );
END;
$$;

GRANT EXECUTE ON FUNCTION api.ofrecer_delivery_a_siguiente_repartidor(integer) TO web_anon;

NOTIFY pgrst, 'reload schema';
COMMIT;
