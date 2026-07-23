BEGIN;

CREATE OR REPLACE FUNCTION api.assign_default_bigboy_pickup()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pickup_id integer;
BEGIN
    SELECT id INTO v_pickup_id
    FROM api.pickup_points
    WHERE codigo = 'BIGBOY_EL_POLO'
      AND activo = true
    LIMIT 1;

    IF v_pickup_id IS NOT NULL THEN
        INSERT INTO api.order_pickup_points(pedido_id, pickup_point_id)
        VALUES (NEW.id, v_pickup_id)
        ON CONFLICT (pedido_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_assign_default_bigboy_pickup ON api.pedidos;
CREATE TRIGGER trg_assign_default_bigboy_pickup
AFTER INSERT ON api.pedidos
FOR EACH ROW
EXECUTE FUNCTION api.assign_default_bigboy_pickup();

INSERT INTO api.order_pickup_points(pedido_id, pickup_point_id)
SELECT p.id, pp.id
FROM api.pedidos p
CROSS JOIN api.pickup_points pp
WHERE pp.codigo = 'BIGBOY_EL_POLO'
  AND pp.activo = true
  AND p.estado IN ('CONFIRMADO', 'EN_PREPARACION', 'DESPACHADO')
ON CONFLICT (pedido_id) DO NOTHING;

COMMIT;
