BEGIN;

-- =========================================================
-- SECURE LOGISTICS ORDER VIEWER UPGRADE
--
-- Adds signed public order tokens and replaces
-- api.confirmar_pedido_whatsapp(...) so it returns a secure
-- human-friendly logistics viewer URL instead of raw PostgREST JSON.
--
-- New viewer URL format:
--   http://127.0.0.1:8790/order/PED-000001?token=...
--
-- Run as postgres:
-- sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi < add_secure_order_urls.sql
-- =========================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS api;

ALTER TABLE api.pedidos
ADD COLUMN IF NOT EXISTS public_token text;

ALTER TABLE api.pedidos
ADD COLUMN IF NOT EXISTS public_token_expires_at timestamptz;

ALTER TABLE api.pedidos
ADD COLUMN IF NOT EXISTS public_token_created_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pedidos_public_token_unique
ON api.pedidos(public_token)
WHERE public_token IS NOT NULL;


-- =========================================================
-- Helper: URL-safe random token
-- =========================================================

CREATE OR REPLACE FUNCTION api.make_public_token(
    p_bytes integer DEFAULT 32
)
RETURNS text
LANGUAGE sql
VOLATILE
AS $$
    SELECT rtrim(
        translate(
            encode(gen_random_bytes(GREATEST(16, LEAST(COALESCE(p_bytes, 32), 64))), 'base64'),
            '+/',
            '-_'
        ),
        '='
    );
$$;


-- =========================================================
-- Create or refresh a public token for an order
-- =========================================================

CREATE OR REPLACE FUNCTION api.ensure_pedido_public_token(
    p_pedido_id integer,
    p_public_base_url text DEFAULT 'http://127.0.0.1:8790',
    p_expires_hours integer DEFAULT 720
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pedido_num text;
    v_token text;
    v_expires_at timestamptz;
    v_order_url text;
    v_existing_token text;
    v_existing_expires_at timestamptz;
    v_try integer := 0;
BEGIN
    IF p_pedido_id IS NULL THEN
        RAISE EXCEPTION 'p_pedido_id is required';
    END IF;

    SELECT pedido_num, public_token, public_token_expires_at
    INTO v_pedido_num, v_existing_token, v_existing_expires_at
    FROM api.pedidos
    WHERE id = p_pedido_id;

    IF v_pedido_num IS NULL THEN
        RAISE EXCEPTION 'Pedido id % not found', p_pedido_id;
    END IF;

    IF v_existing_token IS NOT NULL
       AND v_existing_expires_at IS NOT NULL
       AND v_existing_expires_at > now()
    THEN
        v_token := v_existing_token;
        v_expires_at := v_existing_expires_at;
    ELSE
        v_expires_at := now() + make_interval(hours => COALESCE(p_expires_hours, 720));

        LOOP
            v_try := v_try + 1;
            v_token := api.make_public_token(32);

            BEGIN
                UPDATE api.pedidos
                SET
                    public_token = v_token,
                    public_token_expires_at = v_expires_at,
                    public_token_created_at = now(),
                    updated_at = now()
                WHERE id = p_pedido_id;

                EXIT;
            EXCEPTION WHEN unique_violation THEN
                IF v_try > 10 THEN
                    RAISE EXCEPTION 'Could not generate unique public token';
                END IF;
            END;
        END LOOP;
    END IF;

    v_order_url :=
        rtrim(COALESCE(NULLIF(p_public_base_url, ''), 'http://127.0.0.1:8790'), '/') ||
        '/order/' ||
        v_pedido_num ||
        '?token=' ||
        v_token;

    UPDATE api.pedidos
    SET
        order_url = v_order_url,
        items_url = v_order_url || '#items',
        updated_at = now()
    WHERE id = p_pedido_id;

    RETURN jsonb_build_object(
        'ok', true,
        'pedido_id', p_pedido_id,
        'pedido_num', v_pedido_num,
        'public_token', v_token,
        'public_token_expires_at', v_expires_at,
        'order_url', v_order_url,
        'items_url', v_order_url || '#items'
    );
END;
$$;


-- =========================================================
-- Validate a public order token and return order + items
-- Used by the logistics viewer service.
-- =========================================================

CREATE OR REPLACE FUNCTION api.obtener_pedido_publico(
    p_pedido_num text,
    p_token text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pedido_id integer;
    v_order jsonb;
    v_items jsonb;
BEGIN
    IF p_pedido_num IS NULL OR trim(p_pedido_num) = '' THEN
        RETURN jsonb_build_object('ok', false, 'error', 'pedido_num is required');
    END IF;

    IF p_token IS NULL OR trim(p_token) = '' THEN
        RETURN jsonb_build_object('ok', false, 'error', 'token is required');
    END IF;

    SELECT p.id
    INTO v_pedido_id
    FROM api.pedidos p
    WHERE p.pedido_num = trim(p_pedido_num)
      AND p.public_token = trim(p_token)
      AND p.public_token_expires_at IS NOT NULL
      AND p.public_token_expires_at > now();

    IF v_pedido_id IS NULL THEN
        RETURN jsonb_build_object(
            'ok', false,
            'error', 'Invalid or expired order link'
        );
    END IF;

    SELECT to_jsonb(o)
    INTO v_order
    FROM api.v_pedidos_logistica o
    WHERE o.id = v_pedido_id;

    SELECT COALESCE(jsonb_agg(to_jsonb(i) ORDER BY i.id), '[]'::jsonb)
    INTO v_items
    FROM api.v_pedido_items_logistica i
    WHERE i.pedido_id = v_pedido_id;

    RETURN jsonb_build_object(
        'ok', true,
        'order', v_order,
        'items', v_items
    );
END;
$$;


-- =========================================================
-- Optional: logistics can update status using the signed token.
-- Useful later for buttons in the viewer.
-- =========================================================

CREATE OR REPLACE FUNCTION api.actualizar_estado_pedido_publico(
    p_pedido_num text,
    p_token text,
    p_estado text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pedido_id integer;
    v_estado text;
BEGIN
    v_estado := upper(trim(COALESCE(p_estado, '')));

    IF v_estado NOT IN ('CONFIRMADO', 'EN_PREPARACION', 'DESPACHADO', 'ENTREGADO', 'ANULADO') THEN
        RETURN jsonb_build_object(
            'ok', false,
            'error', 'Invalid status',
            'allowed', jsonb_build_array('CONFIRMADO', 'EN_PREPARACION', 'DESPACHADO', 'ENTREGADO', 'ANULADO')
        );
    END IF;

    SELECT p.id
    INTO v_pedido_id
    FROM api.pedidos p
    WHERE p.pedido_num = trim(p_pedido_num)
      AND p.public_token = trim(p_token)
      AND p.public_token_expires_at IS NOT NULL
      AND p.public_token_expires_at > now();

    IF v_pedido_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Invalid or expired order link');
    END IF;

    UPDATE api.pedidos
    SET estado = v_estado,
        updated_at = now()
    WHERE id = v_pedido_id;

    IF v_estado = 'DESPACHADO' THEN
        BEGIN
            PERFORM api.ofrecer_delivery_a_siguiente_repartidor(v_pedido_id);
        EXCEPTION WHEN undefined_function OR undefined_table THEN
            -- Driver dispatch module is optional; ignore until installed.
            NULL;
        END;
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'pedido_id', v_pedido_id,
        'pedido_num', p_pedido_num,
        'estado', v_estado
    );
END;
$$;


-- =========================================================
-- Replace confirmar_pedido_whatsapp so it generates secure viewer URL.
-- Signature is unchanged, so the existing bridge keeps working.
-- Important: p_base_url is now the logistics viewer base URL,
-- e.g. http://127.0.0.1:8790
-- =========================================================

CREATE OR REPLACE FUNCTION api.confirmar_pedido_whatsapp(
    p_whatsapp_number text,
    p_customer_name text,
    p_payment_method text,
    p_latitude numeric,
    p_longitude numeric,
    p_detected_address text,
    p_confirmed_address text,
    p_items jsonb,
    p_base_url text DEFAULT 'http://127.0.0.1:8790',
    p_delivery numeric DEFAULT 0,
    p_observacion text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_cliente_id integer;
    v_pedido_id integer;
    v_pedido_num text;

    v_payment_method text;
    v_item jsonb;
    v_index integer := 0;

    v_producto_id integer;
    v_producto_texto text;
    v_cantidad numeric(14,3);
    v_unidad text;
    v_precio_unitario numeric(14,2);
    v_total_linea numeric(14,2);

    v_subtotal numeric(14,2) := 0;
    v_delivery numeric(14,2) := COALESCE(p_delivery, 0);
    v_total numeric(14,2);

    v_order_url text;
    v_items_url text;
    v_maps_url text;
    v_token_result jsonb;

    v_email_subject text;
    v_email_body text;
    v_items_text text := '';

    v_email_log_id integer;
BEGIN
    IF p_whatsapp_number IS NULL OR trim(p_whatsapp_number) = '' THEN
        RAISE EXCEPTION 'whatsapp_number is required';
    END IF;

    IF p_customer_name IS NULL OR trim(p_customer_name) = '' THEN
        RAISE EXCEPTION 'customer_name is required';
    END IF;

    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'items must be a non-empty JSON array';
    END IF;

    IF v_delivery < 0 THEN
        RAISE EXCEPTION 'delivery cannot be negative';
    END IF;

    v_payment_method := upper(replace(trim(p_payment_method), ' ', '_'));

    IF v_payment_method NOT IN ('YAPE', 'PLIN', 'TRANSFERENCIA', 'CONTRA_ENTREGA') THEN
        RAISE EXCEPTION 'Invalid payment_method: %. Use YAPE, PLIN, TRANSFERENCIA, or CONTRA_ENTREGA.', p_payment_method;
    END IF;

    INSERT INTO api.clientes_whatsapp (
        whatsapp_number,
        nombre,
        last_order_at
    )
    VALUES (
        trim(p_whatsapp_number),
        trim(p_customer_name),
        now()
    )
    ON CONFLICT (whatsapp_number)
    DO UPDATE SET
        nombre = EXCLUDED.nombre,
        last_order_at = now(),
        updated_at = now()
    RETURNING id INTO v_cliente_id;

    v_pedido_num := 'PED-' || lpad(nextval('api.pedido_num_seq')::text, 6, '0');

    INSERT INTO api.pedidos (
        pedido_num,
        cliente_id,
        canal,
        estado,
        metodo_pago,
        moneda,
        subtotal,
        delivery,
        total,
        observacion
    )
    VALUES (
        v_pedido_num,
        v_cliente_id,
        'WHATSAPP',
        'CONFIRMADO',
        v_payment_method,
        'PEN',
        0,
        v_delivery,
        0,
        p_observacion
    )
    RETURNING id INTO v_pedido_id;

    FOR v_item IN
        SELECT value
        FROM jsonb_array_elements(p_items)
    LOOP
        v_index := v_index + 1;

        v_producto_id := NULL;
        v_producto_texto := NULL;
        v_cantidad := NULL;
        v_unidad := NULL;
        v_precio_unitario := NULL;
        v_total_linea := NULL;

        v_producto_texto := COALESCE(
            NULLIF(trim(v_item ->> 'producto_texto'), ''),
            NULLIF(trim(v_item ->> 'product'), ''),
            NULLIF(trim(v_item ->> 'nombre'), '')
        );

        IF v_producto_texto IS NULL THEN
            RAISE EXCEPTION 'Item % is missing producto_texto/product/nombre', v_index;
        END IF;

        v_cantidad := NULLIF(trim(v_item ->> 'cantidad'), '')::numeric;

        IF v_cantidad IS NULL OR v_cantidad <= 0 THEN
            RAISE EXCEPTION 'Item % has invalid cantidad', v_index;
        END IF;

        v_unidad := upper(NULLIF(trim(COALESCE(v_item ->> 'unidad', v_item ->> 'unit')), ''));

        IF NULLIF(trim(v_item ->> 'producto_id'), '') IS NOT NULL THEN
            v_producto_id := (v_item ->> 'producto_id')::integer;
        END IF;

        IF v_producto_id IS NULL THEN
            SELECT p.id
            INTO v_producto_id
            FROM api.productos p
            WHERE upper(p.cdg_prod) = upper(v_producto_texto)
               OR upper(p.nombre) = upper(v_producto_texto)
            ORDER BY p.id
            LIMIT 1;
        END IF;

        IF NULLIF(trim(v_item ->> 'precio_unitario'), '') IS NOT NULL THEN
            v_precio_unitario := (v_item ->> 'precio_unitario')::numeric;
        END IF;

        IF v_precio_unitario IS NULL AND v_producto_id IS NOT NULL THEN
            SELECT pp.precio
            INTO v_precio_unitario
            FROM api.producto_precios pp
            WHERE pp.producto_id = v_producto_id
              AND pp.active = true
              AND pp.moneda = 'PEN'
              AND (v_unidad IS NULL OR upper(pp.unidad) = upper(v_unidad))
              AND pp.valid_from <= current_date
              AND (pp.valid_to IS NULL OR pp.valid_to >= current_date)
            ORDER BY
                CASE
                    WHEN v_unidad IS NOT NULL AND upper(pp.unidad) = upper(v_unidad) THEN 0
                    ELSE 1
                END,
                pp.valid_from DESC,
                pp.id DESC
            LIMIT 1;
        END IF;

        v_precio_unitario := COALESCE(v_precio_unitario, 0);

        IF v_precio_unitario < 0 THEN
            RAISE EXCEPTION 'Item % has invalid precio_unitario', v_index;
        END IF;

        v_total_linea := round((v_cantidad * v_precio_unitario)::numeric, 2);

        INSERT INTO api.pedido_items (
            pedido_id,
            producto_id,
            producto_texto,
            cantidad,
            unidad,
            precio_unitario,
            total_linea,
            raw_item
        )
        VALUES (
            v_pedido_id,
            v_producto_id,
            v_producto_texto,
            v_cantidad,
            v_unidad,
            v_precio_unitario,
            v_total_linea,
            v_item
        );

        v_subtotal := v_subtotal + v_total_linea;

        v_items_text := v_items_text ||
            v_index::text || '. ' ||
            v_producto_texto || ' x ' ||
            v_cantidad::text || ' ' ||
            COALESCE(v_unidad, '') ||
            ' — S/ ' || to_char(v_total_linea, 'FM999999990.00') ||
            E'\n';
    END LOOP;

    INSERT INTO api.pedido_direcciones (
        pedido_id,
        latitud,
        longitud,
        direccion_detectada,
        direccion_confirmada,
        confirmed
    )
    VALUES (
        v_pedido_id,
        p_latitude,
        p_longitude,
        p_detected_address,
        p_confirmed_address,
        true
    );

    v_total := v_subtotal + v_delivery;

    IF p_latitude IS NOT NULL AND p_longitude IS NOT NULL THEN
        v_maps_url := 'https://www.google.com/maps?q=' ||
            p_latitude::text ||
            ',' ||
            p_longitude::text;
    END IF;

    UPDATE api.pedidos
    SET
        subtotal = v_subtotal,
        delivery = v_delivery,
        total = v_total,
        maps_url = v_maps_url,
        updated_at = now()
    WHERE id = v_pedido_id;

    v_token_result := api.ensure_pedido_public_token(
        v_pedido_id,
        COALESCE(NULLIF(p_base_url, ''), 'http://127.0.0.1:8790'),
        720
    );

    v_order_url := v_token_result ->> 'order_url';
    v_items_url := v_token_result ->> 'items_url';

    UPDATE api.clientes_whatsapp
    SET
        last_order_at = now(),
        last_order_id = v_pedido_id,
        last_order_num = v_pedido_num,
        last_order_total = v_total,
        last_payment_method = v_payment_method,
        last_latitude = p_latitude,
        last_longitude = p_longitude,
        last_maps_url = v_maps_url,
        last_written_address = CASE
            WHEN p_confirmed_address IS NOT NULL AND position(E'\nReferencia del mapa:' in p_confirmed_address) > 0
                THEN split_part(p_confirmed_address, E'\nReferencia del mapa:', 1)
            ELSE p_confirmed_address
        END,
        last_detected_address = p_detected_address,
        last_confirmed_address = p_confirmed_address,
        last_order_snapshot = jsonb_build_object(
            'pedido_id', v_pedido_id,
            'pedido_num', v_pedido_num,
            'total', v_total,
            'payment_method', v_payment_method,
            'items', p_items,
            'order_url', v_order_url,
            'maps_url', v_maps_url
        ),
        updated_at = now()
    WHERE id = v_cliente_id;

    INSERT INTO api.whatsapp_conversaciones (
        whatsapp_number,
        cliente_id,
        estado,
        pedido_id,
        pedido_borrador,
        last_message_at
    )
    VALUES (
        trim(p_whatsapp_number),
        v_cliente_id,
        'CONFIRMED',
        v_pedido_id,
        jsonb_build_object(
            'pedido_id', v_pedido_id,
            'pedido_num', v_pedido_num,
            'total', v_total,
            'items', p_items,
            'order_url', v_order_url
        ),
        now()
    )
    ON CONFLICT (whatsapp_number)
    DO UPDATE SET
        cliente_id = EXCLUDED.cliente_id,
        estado = 'CONFIRMED',
        pedido_id = EXCLUDED.pedido_id,
        pedido_borrador = EXCLUDED.pedido_borrador,
        last_message_at = now(),
        updated_at = now();

    v_email_subject := 'Nuevo pedido WhatsApp ' || v_pedido_num;

    v_email_body :=
        'Nuevo pedido confirmado por WhatsApp.' || E'\n\n' ||
        'Pedido: ' || v_pedido_num || E'\n' ||
        'Cliente: ' || trim(p_customer_name) || E'\n' ||
        'WhatsApp: ' || trim(p_whatsapp_number) || E'\n' ||
        'Pago: ' || v_payment_method || E'\n' ||
        'Subtotal: S/ ' || to_char(v_subtotal, 'FM999999990.00') || E'\n' ||
        'Delivery: S/ ' || to_char(v_delivery, 'FM999999990.00') || E'\n' ||
        'Total: S/ ' || to_char(v_total, 'FM999999990.00') || E'\n\n' ||
        'Items:' || E'\n' ||
        v_items_text || E'\n' ||
        'Dirección confirmada:' || E'\n' ||
        COALESCE(p_confirmed_address, p_detected_address, '(sin dirección)') || E'\n\n' ||
        'Ver pedido:' || E'\n' ||
        v_order_url || E'\n\n' ||
        'Ubicación:' || E'\n' ||
        COALESCE(v_maps_url, '(sin ubicación)') || E'\n';

    INSERT INTO api.email_logistica_log (
        pedido_id,
        recipient,
        subject,
        body,
        status
    )
    VALUES (
        v_pedido_id,
        'logistica@replau.com',
        v_email_subject,
        v_email_body,
        'PENDING'
    )
    RETURNING id INTO v_email_log_id;

    RETURN jsonb_build_object(
        'ok', true,
        'pedido_id', v_pedido_id,
        'pedido_num', v_pedido_num,
        'cliente_id', v_cliente_id,
        'subtotal', v_subtotal,
        'delivery', v_delivery,
        'total', v_total,
        'payment_method', v_payment_method,
        'order_url', v_order_url,
        'items_url', v_items_url,
        'maps_url', v_maps_url,
        'public_token_expires_at', v_token_result ->> 'public_token_expires_at',
        'email_log_id', v_email_log_id,
        'email_to', 'logistica@replau.com',
        'email_status', 'PENDING',
        'whatsapp_confirmation_text',
            'Pedido confirmado ✅' || E'\n\n' ||
            'Pedido: ' || v_pedido_num || E'\n' ||
            'Cliente: ' || trim(p_customer_name) || E'\n' ||
            'Total: S/ ' || to_char(v_total, 'FM999999990.00') || E'\n' ||
            'Pago: ' || v_payment_method || E'\n\n' ||
            'Dirección:' || E'\n' ||
            COALESCE(p_confirmed_address, p_detected_address, '(sin dirección)') || E'\n\n' ||
            'Logística puede abrir el pedido aquí:' || E'\n' ||
            '[Abrir pedido ' || v_pedido_num || '](' || v_order_url || ')'
    );
END;
$$;


GRANT EXECUTE ON FUNCTION api.make_public_token(integer) TO web_anon;

GRANT EXECUTE ON FUNCTION api.ensure_pedido_public_token(integer, text, integer) TO web_anon;

GRANT EXECUTE ON FUNCTION api.obtener_pedido_publico(text, text) TO web_anon;

GRANT EXECUTE ON FUNCTION api.actualizar_estado_pedido_publico(text, text, text) TO web_anon;

GRANT EXECUTE ON FUNCTION api.confirmar_pedido_whatsapp(
    text,
    text,
    text,
    numeric,
    numeric,
    text,
    text,
    jsonb,
    text,
    numeric,
    text
) TO web_anon;

NOTIFY pgrst, 'reload schema';

COMMIT;
