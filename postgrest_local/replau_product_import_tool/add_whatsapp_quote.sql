BEGIN;

-- =========================================================
-- WHATSAPP QUOTE ENDPOINT FOR POSTGREST
--
-- Adds:
--   api.cotizar_pedido_whatsapp(...)
--   api.buscar_productos_whatsapp(...)
--
-- Main endpoint:
--   POST http://localhost:3000/rpc/cotizar_pedido_whatsapp
--
-- This endpoint does NOT create an order.
-- It only validates, matches products, calculates prices, and returns
-- a WhatsApp-ready quote/confirmation message.
-- =========================================================

CREATE SCHEMA IF NOT EXISTS api;

-- ---------------------------------------------------------
-- Helper: normalize text for simple product matching.
-- ---------------------------------------------------------

CREATE OR REPLACE FUNCTION api.simple_norm(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT upper(regexp_replace(trim(coalesce(p_text, '')), '\s+', ' ', 'g'));
$$;


-- ---------------------------------------------------------
-- Function: search product catalog for OpenClaw suggestions.
-- Useful when the agent is not sure how to map customer text.
-- ---------------------------------------------------------

CREATE OR REPLACE FUNCTION api.buscar_productos_whatsapp(
    p_search text,
    p_limit integer DEFAULT 10
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_search text;
    v_limit integer;
    v_results jsonb;
BEGIN
    v_search := api.simple_norm(p_search);
    v_limit := LEAST(GREATEST(COALESCE(p_limit, 10), 1), 50);

    IF v_search = '' THEN
        RAISE EXCEPTION 'p_search is required';
    END IF;

    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'producto_id', x.producto_id,
                'cdg_prod', x.cdg_prod,
                'nombre', x.nombre,
                'tipo_producto', x.tipo_producto,
                'unidad_medida', x.unidad_medida,
                'pack_default', x.pack_default,
                'envase_default', x.envase_default,
                'unidad_precio', x.unidad_precio,
                'precio', x.precio,
                'moneda', x.moneda,
                'match_score', x.match_score
            )
            ORDER BY x.match_score DESC, x.nombre
        ),
        '[]'::jsonb
    )
    INTO v_results
    FROM (
        SELECT
            p.id AS producto_id,
            p.cdg_prod,
            p.nombre,
            p.tipo_producto,
            p.unidad_medida,
            p.pack_default,
            p.envase_default,
            pp.unidad AS unidad_precio,
            pp.precio,
            pp.moneda,
            CASE
                WHEN api.simple_norm(p.cdg_prod) = v_search THEN 100
                WHEN api.simple_norm(p.nombre) = v_search THEN 95
                WHEN api.simple_norm(p.nombre) LIKE '%' || v_search || '%' THEN 80
                WHEN api.simple_norm(p.cdg_prod) LIKE '%' || v_search || '%' THEN 70
                ELSE 10
            END AS match_score
        FROM api.productos p
        LEFT JOIN LATERAL (
            SELECT pp.unidad, pp.precio, pp.moneda
            FROM api.producto_precios pp
            WHERE pp.producto_id = p.id
              AND pp.active = true
              AND pp.valid_from <= current_date
              AND (pp.valid_to IS NULL OR pp.valid_to >= current_date)
            ORDER BY pp.valid_from DESC, pp.id DESC
            LIMIT 1
        ) pp ON true
        WHERE p.active = true
          AND (
              api.simple_norm(p.cdg_prod) = v_search
              OR api.simple_norm(p.nombre) = v_search
              OR api.simple_norm(p.nombre) LIKE '%' || v_search || '%'
              OR api.simple_norm(p.cdg_prod) LIKE '%' || v_search || '%'
          )
        ORDER BY match_score DESC, p.nombre
        LIMIT v_limit
    ) x;

    RETURN jsonb_build_object(
        'ok', true,
        'search', p_search,
        'results_count', jsonb_array_length(v_results),
        'results', v_results
    );
END;
$$;


-- ---------------------------------------------------------
-- Function: quote WhatsApp order.
--
-- Input example:
-- {
--   "p_customer_name": "Juan Perez",
--   "p_items": [
--     {"producto_texto": "PIMIENTA MOLIDA", "cantidad": 2, "unidad": "BOLSA"},
--     {"producto_texto": "CANELA MOLIDA", "cantidad": 1, "unidad": "POTE"}
--   ],
--   "p_delivery": 0
-- }
-- ---------------------------------------------------------

CREATE OR REPLACE FUNCTION api.cotizar_pedido_whatsapp(
    p_customer_name text,
    p_items jsonb,
    p_delivery numeric DEFAULT 0
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_item jsonb;
    v_index integer := 0;

    v_customer_name text;
    v_delivery numeric(14,2);

    v_producto_id integer;
    v_cdg_prod text;
    v_producto_nombre text;
    v_producto_texto text;
    v_producto_search text;

    v_cantidad numeric(14,3);
    v_unidad text;
    v_precio_unitario numeric(14,2);
    v_total_linea numeric(14,2);

    v_subtotal numeric(14,2) := 0;
    v_total numeric(14,2) := 0;

    v_items_ok jsonb := '[]'::jsonb;
    v_errors jsonb := '[]'::jsonb;
    v_quote_lines text := '';
    v_whatsapp_text text;
BEGIN
    v_customer_name := NULLIF(trim(COALESCE(p_customer_name, '')), '');
    v_delivery := COALESCE(p_delivery, 0);

    IF v_customer_name IS NULL THEN
        RAISE EXCEPTION 'p_customer_name is required';
    END IF;

    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'p_items must be a non-empty JSON array';
    END IF;

    IF v_delivery < 0 THEN
        RAISE EXCEPTION 'p_delivery cannot be negative';
    END IF;


    FOR v_item IN
        SELECT value
        FROM jsonb_array_elements(p_items)
    LOOP
        v_index := v_index + 1;

        BEGIN
            v_producto_id := NULL;
            v_cdg_prod := NULL;
            v_producto_nombre := NULL;
            v_producto_texto := NULL;
            v_producto_search := NULL;
            v_cantidad := NULL;
            v_unidad := NULL;
            v_precio_unitario := NULL;
            v_total_linea := NULL;

            v_producto_texto := COALESCE(
                NULLIF(trim(v_item ->> 'producto_texto'), ''),
                NULLIF(trim(v_item ->> 'product'), ''),
                NULLIF(trim(v_item ->> 'nombre'), ''),
                NULLIF(trim(v_item ->> 'cdg_prod'), '')
            );

            IF v_producto_texto IS NULL THEN
                RAISE EXCEPTION 'Item % is missing producto_texto/product/nombre/cdg_prod', v_index;
            END IF;

            v_producto_search := api.simple_norm(v_producto_texto);

            v_cantidad := NULLIF(trim(COALESCE(v_item ->> 'cantidad', '')), '')::numeric;

            IF v_cantidad IS NULL OR v_cantidad <= 0 THEN
                RAISE EXCEPTION 'Item % has invalid cantidad', v_index;
            END IF;

            v_unidad := upper(NULLIF(trim(COALESCE(v_item ->> 'unidad', v_item ->> 'unit', '')), ''));

            IF NULLIF(trim(COALESCE(v_item ->> 'producto_id', '')), '') IS NOT NULL THEN
                v_producto_id := (v_item ->> 'producto_id')::integer;

                SELECT p.id, p.cdg_prod, p.nombre
                INTO v_producto_id, v_cdg_prod, v_producto_nombre
                FROM api.productos p
                WHERE p.id = v_producto_id
                  AND p.active = true;

                IF v_producto_id IS NULL THEN
                    RAISE EXCEPTION 'Item % producto_id not found or inactive', v_index;
                END IF;
            ELSE
                SELECT p.id, p.cdg_prod, p.nombre
                INTO v_producto_id, v_cdg_prod, v_producto_nombre
                FROM api.productos p
                WHERE p.active = true
                  AND (
                      api.simple_norm(p.cdg_prod) = v_producto_search
                      OR api.simple_norm(p.nombre) = v_producto_search
                      OR api.simple_norm(p.nombre) LIKE '%' || v_producto_search || '%'
                  )
                ORDER BY
                    CASE
                        WHEN api.simple_norm(p.cdg_prod) = v_producto_search THEN 0
                        WHEN api.simple_norm(p.nombre) = v_producto_search THEN 1
                        WHEN api.simple_norm(p.nombre) LIKE '%' || v_producto_search || '%' THEN 2
                        ELSE 3
                    END,
                    p.id
                LIMIT 1;

                IF v_producto_id IS NULL THEN
                    RAISE EXCEPTION 'Item % product not found: %', v_index, v_producto_texto;
                END IF;
            END IF;

            -- Use default product pack if customer did not provide unit.
            IF v_unidad IS NULL THEN
                SELECT upper(COALESCE(p.pack_default, p.unidad_medida))
                INTO v_unidad
                FROM api.productos p
                WHERE p.id = v_producto_id;
            END IF;

            SELECT pp.precio
            INTO v_precio_unitario
            FROM api.producto_precios pp
            WHERE pp.producto_id = v_producto_id
              AND pp.active = true
              AND pp.moneda = 'PEN'
              AND upper(pp.unidad) = upper(v_unidad)
              AND pp.valid_from <= current_date
              AND (pp.valid_to IS NULL OR pp.valid_to >= current_date)
            ORDER BY pp.valid_from DESC, pp.id DESC
            LIMIT 1;

            IF v_precio_unitario IS NULL THEN
                RAISE EXCEPTION 'Item % has no active price for product % and unit %', v_index, v_cdg_prod, v_unidad;
            END IF;

            v_total_linea := round((v_cantidad * v_precio_unitario)::numeric, 2);
            v_subtotal := v_subtotal + v_total_linea;

            v_items_ok := v_items_ok || jsonb_build_array(
                jsonb_build_object(
                    'linea', v_index,
                    'producto_id', v_producto_id,
                    'cdg_prod', v_cdg_prod,
                    'producto_texto_cliente', v_producto_texto,
                    'producto_nombre', v_producto_nombre,
                    'cantidad', v_cantidad,
                    'unidad', v_unidad,
                    'precio_unitario', v_precio_unitario,
                    'total_linea', v_total_linea
                )
            );

            v_quote_lines := v_quote_lines ||
                v_index::text || '. ' ||
                v_producto_nombre || ' x ' ||
                v_cantidad::text || ' ' ||
                COALESCE(v_unidad, '') ||
                ' — S/ ' || to_char(v_total_linea, 'FM999999990.00') ||
                E'\n';

        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors || jsonb_build_array(
                jsonb_build_object(
                    'linea', v_index,
                    'producto_texto', COALESCE(v_producto_texto, ''),
                    'error', SQLERRM,
                    'raw_item', v_item
                )
            );
        END;
    END LOOP;

    v_total := v_subtotal + v_delivery;

    IF jsonb_array_length(v_errors) = 0 THEN
        v_whatsapp_text :=
            'Gracias, ' || v_customer_name || '.' || E'\n\n' ||
            'Confirmo tu pedido:' || E'\n\n' ||
            v_quote_lines || E'\n' ||
            'Subtotal: S/ ' || to_char(v_subtotal, 'FM999999990.00') || E'\n' ||
            'Delivery: S/ ' || to_char(v_delivery, 'FM999999990.00') || E'\n' ||
            'Total: S/ ' || to_char(v_total, 'FM999999990.00') || E'\n\n' ||
            'Opciones de pago:' || E'\n' ||
            '1. Yape' || E'\n' ||
            '2. Plin' || E'\n' ||
            '3. Transferencia' || E'\n' ||
            '4. Contra entrega' || E'\n\n' ||
            'Por favor indica tu forma de pago y envíame tu ubicación.';
    ELSE
        v_whatsapp_text :=
            'Gracias, ' || v_customer_name || '.' || E'\n\n' ||
            'Necesito confirmar algunos productos antes de calcular el total.' || E'\n' ||
            'Por favor revisa los productos indicados o envía el pedido nuevamente.';
    END IF;

    RETURN jsonb_build_object(
        'ok', jsonb_array_length(v_errors) = 0,
        'customer_name', v_customer_name,
        'items_count', jsonb_array_length(p_items),
        'items_ok_count', jsonb_array_length(v_items_ok),
        'errors_count', jsonb_array_length(v_errors),
        'items', v_items_ok,
        'errors', v_errors,
        'subtotal', v_subtotal,
        'delivery', v_delivery,
        'total', v_total,
        'payment_options', jsonb_build_array('YAPE', 'PLIN', 'TRANSFERENCIA', 'CONTRA_ENTREGA'),
        'whatsapp_quote_text', v_whatsapp_text
    );
END;
$$;


GRANT EXECUTE ON FUNCTION api.simple_norm(text) TO web_anon;
GRANT EXECUTE ON FUNCTION api.buscar_productos_whatsapp(text, integer) TO web_anon;
GRANT EXECUTE ON FUNCTION api.cotizar_pedido_whatsapp(text, jsonb, numeric) TO web_anon;

GRANT SELECT
ON api.productos,
   api.producto_precios,
   api.producto_packs
TO web_anon;

NOTIFY pgrst, 'reload schema';

COMMIT;
