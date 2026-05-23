BEGIN;

CREATE SCHEMA IF NOT EXISTS api;

ALTER TABLE api.pedidos ADD COLUMN IF NOT EXISTS kitchen_status text;
ALTER TABLE api.pedidos ADD COLUMN IF NOT EXISTS kitchen_started_at timestamptz;
ALTER TABLE api.pedidos ADD COLUMN IF NOT EXISTS kitchen_ready_at timestamptz;
ALTER TABLE api.pedidos ADD COLUMN IF NOT EXISTS kitchen_notes text;

UPDATE api.pedidos
SET kitchen_status = COALESCE(kitchen_status, 'NUEVO')
WHERE kitchen_status IS NULL;

ALTER TABLE api.pedidos ALTER COLUMN kitchen_status SET DEFAULT 'NUEVO';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pedidos_kitchen_status_check'
    ) THEN
        ALTER TABLE api.pedidos
        ADD CONSTRAINT pedidos_kitchen_status_check
        CHECK (kitchen_status IN ('NUEVO','EN_PREPARACION','LISTO','ENTREGADO','ANULADO'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pedidos_kitchen_status ON api.pedidos(kitchen_status);
CREATE INDEX IF NOT EXISTS idx_pedidos_created_at ON api.pedidos(created_at);

CREATE OR REPLACE FUNCTION api.update_kitchen_status(
    p_pedido_id integer,
    p_kitchen_status text,
    p_kitchen_notes text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_new_status text;
    v_order record;
BEGIN
    v_new_status := upper(trim(COALESCE(p_kitchen_status, '')));

    IF v_new_status NOT IN ('NUEVO','EN_PREPARACION','LISTO','ENTREGADO','ANULADO') THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Invalid kitchen status');
    END IF;

    SELECT id, pedido_num, estado, kitchen_status
    INTO v_order
    FROM api.pedidos
    WHERE id = p_pedido_id;

    IF v_order.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Pedido not found');
    END IF;

    UPDATE api.pedidos
    SET
        kitchen_status = v_new_status,
        kitchen_started_at = CASE
            WHEN v_new_status = 'EN_PREPARACION' AND kitchen_started_at IS NULL THEN now()
            ELSE kitchen_started_at
        END,
        kitchen_ready_at = CASE
            WHEN v_new_status = 'LISTO' THEN now()
            WHEN v_new_status IN ('NUEVO','EN_PREPARACION') THEN NULL
            ELSE kitchen_ready_at
        END,
        kitchen_notes = COALESCE(p_kitchen_notes, kitchen_notes),
        estado = CASE
            WHEN v_new_status = 'EN_PREPARACION' AND estado = 'CONFIRMADO' THEN 'EN_PREPARACION'
            WHEN v_new_status = 'ENTREGADO' THEN 'ENTREGADO'
            WHEN v_new_status = 'ANULADO' THEN 'ANULADO'
            ELSE estado
        END,
        updated_at = now()
    WHERE id = p_pedido_id;

    RETURN jsonb_build_object(
        'ok', true,
        'pedido_id', p_pedido_id,
        'pedido_num', v_order.pedido_num,
        'kitchen_status', v_new_status
    );
END;
$$;

CREATE OR REPLACE VIEW api.v_kitchen_orders AS
WITH base AS (
    SELECT
        p.id,
        p.pedido_num,
        p.cliente_id,
        c.nombre AS cliente_nombre,
        c.whatsapp_number,
        p.estado,
        COALESCE(p.kitchen_status, 'NUEVO') AS kitchen_status,
        p.total,
        p.created_at,
        p.updated_at,
        p.kitchen_started_at,
        p.kitchen_ready_at,
        p.kitchen_notes,
        COALESCE(pd.direccion_confirmada, pd.direccion_detectada) AS direccion,
        p.metodo_pago,
        EXTRACT(EPOCH FROM (now() - p.created_at)) / 60.0 AS queue_minutes_raw
    FROM api.pedidos p
    LEFT JOIN api.clientes_whatsapp c ON c.id = p.cliente_id
    LEFT JOIN LATERAL (
        SELECT *
        FROM api.pedido_direcciones d
        WHERE d.pedido_id = p.id
        ORDER BY d.id DESC
        LIMIT 1
    ) pd ON true
    WHERE p.canal = 'WHATSAPP'
      AND (
            (
                COALESCE(p.kitchen_status, 'NUEVO') = 'NUEVO'
                AND p.estado = 'CONFIRMADO'
                AND p.created_at >= now() - interval '8 hours'
            )
            OR (
                COALESCE(p.kitchen_status, 'NUEVO') IN ('EN_PREPARACION', 'LISTO')
                AND p.estado IN ('CONFIRMADO', 'EN_PREPARACION')
            )
          )
)
SELECT
    id,
    pedido_num,
    cliente_id,
    cliente_nombre,
    whatsapp_number,
    estado,
    kitchen_status,
    total,
    created_at,
    updated_at,
    kitchen_started_at,
    kitchen_ready_at,
    kitchen_notes,
    direccion,
    metodo_pago,
    round(queue_minutes_raw::numeric, 1) AS queue_minutes,
    CASE
        WHEN queue_minutes_raw > 30 THEN 'RED'
        WHEN queue_minutes_raw > 20 THEN 'YELLOW'
        ELSE 'GREEN'
    END AS queue_color
FROM base
ORDER BY created_at ASC;

CREATE OR REPLACE VIEW api.v_kitchen_order_items AS
SELECT
    pi.id,
    pi.pedido_id,
    p.pedido_num,
    pi.producto_id,
    pr.cdg_prod,
    COALESCE(pi.producto_texto, pr.nombre) AS producto_nombre,
    pi.cantidad,
    pi.unidad,
    pi.precio_unitario,
    pi.total_linea,
    p.created_at,
    p.kitchen_status,
    p.estado
FROM api.pedido_items pi
JOIN api.pedidos p ON p.id = pi.pedido_id
LEFT JOIN api.productos pr ON pr.id = pi.producto_id
WHERE p.canal = 'WHATSAPP';

GRANT SELECT ON api.v_kitchen_orders TO web_anon;
GRANT SELECT ON api.v_kitchen_order_items TO web_anon;
GRANT EXECUTE ON FUNCTION api.update_kitchen_status(integer, text, text) TO web_anon;

NOTIFY pgrst, 'reload schema';

COMMIT;
