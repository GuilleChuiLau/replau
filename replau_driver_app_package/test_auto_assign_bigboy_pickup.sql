BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM api.pickup_points
        WHERE codigo = 'BIGBOY_EL_POLO'
          AND activo = true
          AND latitude = -12.10376420
          AND longitude = -76.97262310
    ) THEN
        RAISE EXCEPTION 'BIGBOY_EL_POLO pickup is missing or has incorrect coordinates';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'trg_assign_default_bigboy_pickup'
          AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION 'Default BigBoy pickup trigger is missing';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM api.pedidos p
        LEFT JOIN api.order_pickup_points opp ON opp.pedido_id = p.id
        WHERE p.estado IN ('CONFIRMADO', 'EN_PREPARACION', 'DESPACHADO')
          AND opp.pedido_id IS NULL
    ) THEN
        RAISE EXCEPTION 'An active delivery order has no pickup mapping';
    END IF;
END;
$$;

ROLLBACK;
