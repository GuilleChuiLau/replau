BEGIN;

DO $$
DECLARE
    v_order_id integer;
    v_message text;
BEGIN
    SELECT id INTO v_order_id
    FROM api.v_pedidos_logistica
    ORDER BY id DESC
    LIMIT 1;
    IF v_order_id IS NULL THEN
        RAISE EXCEPTION 'No order is available for the driver WhatsApp message test';
    END IF;
    v_message := api.driver_trip_message(v_order_id, 7.00, 'TEST');
    IF v_message NOT LIKE '%Pago carrera: S/ 7.00%' OR v_message NOT LIKE '%RECOJO%' OR v_message NOT LIKE '%ENTREGA AL CLIENTE%' THEN
        RAISE EXCEPTION 'Incomplete driver trip message: %', v_message;
    END IF;
END;
$$;

ROLLBACK;
