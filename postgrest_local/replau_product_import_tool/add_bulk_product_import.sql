BEGIN;

-- =========================================================
-- BULK PRODUCT IMPORT FOR POSTGREST
-- Creates:
--   api.bool_from_text()
--   api.bulk_upsert_productos(jsonb)
--
-- Endpoint after reload:
--   POST http://localhost:3000/rpc/bulk_upsert_productos
-- =========================================================

CREATE SCHEMA IF NOT EXISTS api;

CREATE OR REPLACE FUNCTION api.bool_from_text(
    p_value text,
    p_default boolean DEFAULT true
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    v text;
BEGIN
    IF p_value IS NULL OR trim(p_value) = '' THEN
        RETURN p_default;
    END IF;

    v := lower(trim(p_value));

    IF v IN ('true', 't', '1', 'yes', 'y', 'si', 'sí', 's') THEN
        RETURN true;
    ELSIF v IN ('false', 'f', '0', 'no', 'n') THEN
        RETURN false;
    END IF;

    RAISE EXCEPTION 'Invalid boolean value: %', p_value;
END;
$$;


CREATE OR REPLACE FUNCTION api.bulk_upsert_productos(
    p_items jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_item jsonb;
    v_index integer := 0;

    v_producto_id integer;

    v_cdg_prod text;
    v_nombre text;
    v_tipo_producto text;
    v_unidad_medida text;
    v_pack_default text;
    v_envase_default text;
    v_stock_minimo numeric(14,3);
    v_controla_lote boolean;
    v_controla_vencimiento boolean;
    v_active boolean;

    v_pack_name text;
    v_pack_factor numeric(14,3);
    v_pack_is_default boolean;

    v_precio_unidad text;
    v_precio numeric(14,2);
    v_moneda char(3);
    v_valid_from date;
    v_valid_to date;

    v_products_count integer := 0;
    v_packs_count integer := 0;
    v_prices_count integer := 0;
    v_errors jsonb := '[]'::jsonb;
BEGIN
    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' THEN
        RAISE EXCEPTION 'p_items must be a JSON array';
    END IF;

    FOR v_item IN
        SELECT value
        FROM jsonb_array_elements(p_items)
    LOOP
        v_index := v_index + 1;

        BEGIN
            -- ----------------------------
            -- Required product fields
            -- ----------------------------
            v_cdg_prod := upper(trim(COALESCE(v_item ->> 'cdg_prod', '')));
            v_nombre := trim(COALESCE(v_item ->> 'nombre', ''));
            v_tipo_producto := upper(trim(COALESCE(v_item ->> 'tipo_producto', '')));

            IF v_cdg_prod = '' THEN
                RAISE EXCEPTION 'cdg_prod is required';
            END IF;

            IF v_nombre = '' THEN
                RAISE EXCEPTION 'nombre is required';
            END IF;

            IF v_tipo_producto = '' THEN
                RAISE EXCEPTION 'tipo_producto is required';
            END IF;

            IF v_tipo_producto NOT IN ('GRANELES', 'CONDIMENTOS', 'INSUMOS', 'TERMINADO', 'OTRO') THEN
                RAISE EXCEPTION 'tipo_producto "%" is invalid', v_tipo_producto;
            END IF;

            -- ----------------------------
            -- Optional product fields
            -- ----------------------------
            v_unidad_medida := upper(trim(COALESCE(NULLIF(v_item ->> 'unidad_medida', ''), 'UND')));
            v_pack_default := NULLIF(trim(COALESCE(v_item ->> 'pack_default', '')), '');
            v_envase_default := NULLIF(trim(COALESCE(v_item ->> 'envase_default', '')), '');

            v_stock_minimo := COALESCE(NULLIF(trim(COALESCE(v_item ->> 'stock_minimo', '')), '')::numeric, 0);
            IF v_stock_minimo < 0 THEN
                RAISE EXCEPTION 'stock_minimo cannot be negative';
            END IF;

            v_controla_lote := api.bool_from_text(v_item ->> 'controla_lote', true);
            v_controla_vencimiento := api.bool_from_text(v_item ->> 'controla_vencimiento', true);
            v_active := api.bool_from_text(v_item ->> 'active', true);

            -- ----------------------------
            -- Upsert product
            -- ----------------------------
            INSERT INTO api.productos (
                cdg_prod,
                nombre,
                tipo_producto,
                unidad_medida,
                pack_default,
                envase_default,
                stock_minimo,
                controla_lote,
                controla_vencimiento,
                active
            )
            VALUES (
                v_cdg_prod,
                v_nombre,
                v_tipo_producto,
                v_unidad_medida,
                v_pack_default,
                v_envase_default,
                v_stock_minimo,
                v_controla_lote,
                v_controla_vencimiento,
                v_active
            )
            ON CONFLICT (cdg_prod)
            DO UPDATE SET
                nombre = EXCLUDED.nombre,
                tipo_producto = EXCLUDED.tipo_producto,
                unidad_medida = EXCLUDED.unidad_medida,
                pack_default = EXCLUDED.pack_default,
                envase_default = EXCLUDED.envase_default,
                stock_minimo = EXCLUDED.stock_minimo,
                controla_lote = EXCLUDED.controla_lote,
                controla_vencimiento = EXCLUDED.controla_vencimiento,
                active = EXCLUDED.active,
                updated_at = now()
            RETURNING id INTO v_producto_id;

            v_products_count := v_products_count + 1;

            -- ----------------------------
            -- Optional pack upsert
            -- ----------------------------
            v_pack_name := upper(trim(COALESCE(
                NULLIF(v_item ->> 'pack_name', ''),
                NULLIF(v_item ->> 'pack_default', ''),
                ''
            )));

            IF v_pack_name <> '' THEN
                v_pack_factor := COALESCE(NULLIF(trim(COALESCE(v_item ->> 'pack_factor', '')), '')::numeric, 1);
                IF v_pack_factor <= 0 THEN
                    RAISE EXCEPTION 'pack_factor must be greater than zero';
                END IF;

                v_pack_is_default := api.bool_from_text(v_item ->> 'pack_is_default', true);

                INSERT INTO api.producto_packs (
                    producto_id,
                    pack_name,
                    factor,
                    is_default,
                    active
                )
                VALUES (
                    v_producto_id,
                    v_pack_name,
                    v_pack_factor,
                    v_pack_is_default,
                    v_active
                )
                ON CONFLICT (producto_id, pack_name)
                DO UPDATE SET
                    factor = EXCLUDED.factor,
                    is_default = EXCLUDED.is_default,
                    active = EXCLUDED.active,
                    updated_at = now();

                v_packs_count := v_packs_count + 1;
            END IF;

            -- ----------------------------
            -- Optional price upsert
            -- ----------------------------
            IF NULLIF(trim(COALESCE(v_item ->> 'precio', '')), '') IS NOT NULL THEN
                v_precio := (v_item ->> 'precio')::numeric;
                IF v_precio < 0 THEN
                    RAISE EXCEPTION 'precio cannot be negative';
                END IF;

                v_precio_unidad := upper(trim(COALESCE(
                    NULLIF(v_item ->> 'precio_unidad', ''),
                    NULLIF(v_item ->> 'unidad_precio', ''),
                    NULLIF(v_item ->> 'unidad', ''),
                    v_pack_name,
                    v_unidad_medida
                )));

                IF v_precio_unidad = '' THEN
                    RAISE EXCEPTION 'precio_unidad is required when precio is provided';
                END IF;

                v_moneda := upper(trim(COALESCE(NULLIF(v_item ->> 'moneda', ''), 'PEN')))::char(3);
                IF v_moneda NOT IN ('PEN', 'USD', 'EUR') THEN
                    RAISE EXCEPTION 'moneda "%" is invalid', v_moneda;
                END IF;

                v_valid_from := COALESCE(NULLIF(trim(COALESCE(v_item ->> 'valid_from', '')), '')::date, current_date);
                v_valid_to := NULLIF(trim(COALESCE(v_item ->> 'valid_to', '')), '')::date;

                INSERT INTO api.producto_precios (
                    producto_id,
                    unidad,
                    precio,
                    moneda,
                    active,
                    valid_from,
                    valid_to
                )
                VALUES (
                    v_producto_id,
                    v_precio_unidad,
                    v_precio,
                    v_moneda,
                    v_active,
                    v_valid_from,
                    v_valid_to
                )
                ON CONFLICT (producto_id, unidad, moneda, valid_from)
                DO UPDATE SET
                    precio = EXCLUDED.precio,
                    active = EXCLUDED.active,
                    valid_to = EXCLUDED.valid_to,
                    updated_at = now();

                v_prices_count := v_prices_count + 1;
            END IF;

        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors || jsonb_build_array(
                jsonb_build_object(
                    'row', v_index,
                    'cdg_prod', COALESCE(v_item ->> 'cdg_prod', ''),
                    'error', SQLERRM,
                    'item', v_item
                )
            );
        END;
    END LOOP;

    RETURN jsonb_build_object(
        'ok', jsonb_array_length(v_errors) = 0,
        'rows_received', jsonb_array_length(p_items),
        'products_processed', v_products_count,
        'packs_processed', v_packs_count,
        'prices_processed', v_prices_count,
        'errors_count', jsonb_array_length(v_errors),
        'errors', v_errors
    );
END;
$$;


GRANT EXECUTE ON FUNCTION api.bool_from_text(text, boolean) TO web_anon;
GRANT EXECUTE ON FUNCTION api.bulk_upsert_productos(jsonb) TO web_anon;

GRANT SELECT, INSERT, UPDATE, DELETE
ON api.productos,
   api.producto_packs,
   api.producto_precios
TO web_anon;

GRANT USAGE, SELECT, UPDATE
ON ALL SEQUENCES IN SCHEMA api
TO web_anon;

NOTIFY pgrst, 'reload schema';

COMMIT;
