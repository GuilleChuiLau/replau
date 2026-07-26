BEGIN;

DO $$
DECLARE
    v_order record;
    v_driver record;
    v_result jsonb;
    v_duplicate jsonb;
    v_expected_assignment_id integer;
    v_key text := 'atomic-assignment-contract-0001';
BEGIN
    SELECT
        p.id,
        p.pedido_num
    INTO v_order
    FROM api.pedidos p
    JOIN api.payment_fulfillments pf ON pf.pedido_id = p.id
    WHERE upper(COALESCE(p.estado, '')) NOT IN ('ENTREGADO', 'ANULADO', 'CANCELLED')
      AND (
          (
              upper(COALESCE(p.metodo_pago, '')) = 'CONTRA_ENTREGA'
              AND pf.status IN ('COD_DUE', 'COD_COLLECTED', 'RECONCILED', 'SETTLED')
          )
          OR (
              upper(COALESCE(p.metodo_pago, '')) <> 'CONTRA_ENTREGA'
              AND pf.status IN ('RELEASED', 'RECONCILED', 'SETTLED')
          )
      )
      AND upper(COALESCE(p.observacion, '')) NOT LIKE '%MODALIDAD: RECOJO%'
      AND upper(COALESCE(p.observacion, '')) NOT LIKE '%MODALIDAD: PICKUP%'
    ORDER BY p.id DESC
    LIMIT 1;
    IF v_order.id IS NULL THEN
        RAISE EXCEPTION 'An assignable delivery order fixture is required';
    END IF;

    SELECT id, codigo INTO v_driver
    FROM api.repartidores
    WHERE activo = true
    ORDER BY id
    LIMIT 1;
    IF v_driver.id IS NULL THEN
        RAISE EXCEPTION 'An active driver fixture is required';
    END IF;

    SELECT id INTO v_expected_assignment_id
    FROM api.delivery_asignaciones
    WHERE pedido_id = v_order.id
      AND status IN ('OFFERED', 'ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED', 'FAILED')
    ORDER BY id DESC
    LIMIT 1;

    v_result := api.assign_delivery_driver_atomic(
        v_order.pedido_num,
        v_driver.id,
        'contract-test',
        v_key,
        v_expected_assignment_id
    );
    IF NOT COALESCE((v_result->>'ok')::boolean, false)
       OR COALESCE((v_result->>'duplicate')::boolean, true) THEN
        RAISE EXCEPTION 'Atomic assignment failed: %', v_result;
    END IF;
    IF (
        SELECT count(*)
        FROM api.delivery_asignaciones
        WHERE pedido_id = v_order.id
          AND status IN ('OFFERED', 'ACCEPTED', 'ASSIGNED', 'PICKED_UP', 'EN_ROUTE', 'ARRIVED', 'FAILED')
    ) <> 1 THEN
        RAISE EXCEPTION 'Atomic assignment left multiple active rows';
    END IF;
    IF NOT EXISTS(
        SELECT 1
        FROM api.delivery_operation_events
        WHERE idempotency_key = v_key
          AND event_type = 'DELIVERY_ATOMIC_ASSIGNMENT'
          AND assignment_id = (v_result->>'assignment_id')::integer
    ) THEN
        RAISE EXCEPTION 'Atomic assignment audit event is missing';
    END IF;

    v_duplicate := api.assign_delivery_driver_atomic(
        v_order.pedido_num,
        v_driver.id,
        'contract-test',
        v_key,
        v_expected_assignment_id
    );
    IF NOT COALESCE((v_duplicate->>'duplicate')::boolean, false)
       OR v_duplicate->>'assignment_id' <> v_result->>'assignment_id' THEN
        RAISE EXCEPTION 'Idempotent replay failed: %', v_duplicate;
    END IF;
END;
$$;

ROLLBACK;
