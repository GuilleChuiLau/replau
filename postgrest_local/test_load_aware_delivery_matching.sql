BEGIN;

DO $$
DECLARE
    v_order record;
    v_driver record;
    v_result jsonb;
BEGIN
    SELECT p.id, p.pedido_num
    INTO v_order
    FROM api.pedidos p
    JOIN api.payment_fulfillments pf ON pf.pedido_id = p.id
    WHERE (
        (upper(COALESCE(p.metodo_pago, '')) = 'CONTRA_ENTREGA' AND pf.status IN ('COD_DUE', 'COD_COLLECTED', 'RECONCILED', 'SETTLED'))
        OR
        (upper(COALESCE(p.metodo_pago, '')) <> 'CONTRA_ENTREGA' AND pf.status IN ('RELEASED', 'RECONCILED', 'SETTLED'))
    )
      AND upper(COALESCE(p.observacion, '')) NOT LIKE '%MODALIDAD: RECOJO%'
      AND upper(COALESCE(p.observacion, '')) NOT LIKE '%MODALIDAD: PICKUP%'
    ORDER BY p.id DESC
    LIMIT 1;
    IF v_order.id IS NULL THEN
        RAISE EXCEPTION 'A payment-cleared delivery order fixture is required';
    END IF;

    SELECT id INTO v_driver
    FROM api.repartidores
    WHERE activo = true
    ORDER BY id
    LIMIT 1;
    IF v_driver.id IS NULL THEN
        RAISE EXCEPTION 'An active driver fixture is required';
    END IF;

    UPDATE api.pedidos SET estado = 'DESPACHADO' WHERE id = v_order.id;
    UPDATE api.delivery_asignaciones
    SET status = 'CANCELLED', updated_at = now()
    WHERE pedido_id = v_order.id
      AND status IN ('OFFERED', 'ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED');
    INSERT INTO api.delivery_config(key, value)
    VALUES ('max_active_deliveries_per_driver', '20')
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

    v_result := api.ofrecer_delivery_a_siguiente_repartidor(v_order.id);
    IF NOT COALESCE((v_result->>'ok')::boolean, false) THEN
        RAISE EXCEPTION 'Load-aware matching failed: %', v_result;
    END IF;
    IF v_result->>'selection_reason' IS NULL
       OR v_result->>'active_load_before_offer' IS NULL
       OR v_result->>'max_active_deliveries_per_driver' IS NULL THEN
        RAISE EXCEPTION 'Load-aware matching did not explain its selection: %', v_result;
    END IF;
    IF NOT EXISTS(
        SELECT 1
        FROM api.delivery_operation_events
        WHERE assignment_id = (v_result->>'assignment_id')::integer
          AND event_type = 'DELIVERY_LOAD_AWARE_OFFER'
    ) THEN
        RAISE EXCEPTION 'Load-aware offer audit event is missing';
    END IF;
END;
$$;

ROLLBACK;
