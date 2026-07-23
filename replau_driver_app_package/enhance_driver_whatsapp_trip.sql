BEGIN;

CREATE OR REPLACE FUNCTION api.driver_trip_message(
    p_pedido_id integer,
    p_fee numeric DEFAULT NULL,
    p_title text DEFAULT 'Delivery 🛵'
)
RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path = api, public
AS $$
DECLARE
    v_order record;
    v_pickup record;
    v_pickup_map text;
    v_customer_map text;
    v_route text;
BEGIN
    SELECT * INTO v_order FROM api.v_pedidos_logistica WHERE id = p_pedido_id;
    IF v_order.id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT pp.* INTO v_pickup
    FROM api.order_pickup_points opp
    JOIN api.pickup_points pp ON pp.id = opp.pickup_point_id
    WHERE opp.pedido_id = p_pedido_id
    LIMIT 1;

    IF v_pickup.id IS NOT NULL THEN
        v_pickup_map := 'https://www.google.com/maps/search/?api=1&query=' ||
            v_pickup.latitude::text || ',' || v_pickup.longitude::text;
    END IF;
    v_customer_map := COALESCE(
        v_order.maps_url,
        CASE WHEN v_order.latitud IS NOT NULL AND v_order.longitud IS NOT NULL
            THEN 'https://www.google.com/maps/search/?api=1&query=' ||
                 v_order.latitud::text || ',' || v_order.longitud::text
        END
    );
    IF v_pickup.id IS NOT NULL AND v_order.latitud IS NOT NULL AND v_order.longitud IS NOT NULL THEN
        v_route := 'https://www.google.com/maps/dir/?api=1&origin=' ||
            v_pickup.latitude::text || ',' || v_pickup.longitude::text ||
            '&destination=' || v_order.latitud::text || ',' || v_order.longitud::text ||
            '&travelmode=driving';
    END IF;

    RETURN
        COALESCE(NULLIF(trim(p_title), ''), 'Delivery 🛵') || E'\n\n' ||
        'Pedido: ' || v_order.pedido_num || E'\n' ||
        CASE WHEN p_fee IS NOT NULL
            THEN 'Pago carrera: S/ ' || to_char(p_fee, 'FM999999990.00') || E'\n'
            ELSE ''
        END || E'\n' ||
        'RECOJO' || E'\n' ||
        COALESCE(v_pickup.nombre, v_pickup.codigo, 'Punto de recojo pendiente') || E'\n' ||
        COALESCE(v_pickup.direccion, '(dirección pendiente)') || E'\n' ||
        CASE WHEN v_pickup_map IS NOT NULL THEN v_pickup_map || E'\n' ELSE '' END ||
        E'\nENTREGA AL CLIENTE\n' ||
        COALESCE(v_order.direccion_confirmada, v_order.direccion_detectada, '(dirección pendiente)') || E'\n' ||
        CASE WHEN v_customer_map IS NOT NULL THEN v_customer_map || E'\n' ELSE '' END ||
        CASE WHEN v_route IS NOT NULL THEN E'\nRUTA COMPLETA\n' || v_route || E'\n' ELSE '' END;
END;
$$;

CREATE OR REPLACE FUNCTION api.delivery_offer_message(p_pedido_id integer, p_fee numeric)
RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path = api, public
AS $$
BEGIN
    RETURN api.driver_trip_message(p_pedido_id, p_fee, 'Nuevo delivery disponible 🛵') ||
        E'\nResponde ACEPTAR para tomarlo o NO para pasarlo.';
END;
$$;

CREATE OR REPLACE FUNCTION api.delivery_assignment_message(p_pedido_id integer)
RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path = api, public
AS $$
DECLARE
    v_fee numeric;
BEGIN
    SELECT fee INTO v_fee
    FROM api.delivery_asignaciones
    WHERE pedido_id = p_pedido_id
    ORDER BY assigned_at DESC NULLS LAST, created_at DESC
    LIMIT 1;
    RETURN api.driver_trip_message(p_pedido_id, v_fee, 'Pedido asignado ✅') ||
        E'\nComparte tu ubicación. Responde RECOGIDO al retirar, LLEGUÉ al llegar y ENTREGADO al finalizar.';
END;
$$;

GRANT EXECUTE ON FUNCTION api.driver_trip_message(integer, numeric, text) TO web_anon;
NOTIFY pgrst, 'reload schema';

COMMIT;
