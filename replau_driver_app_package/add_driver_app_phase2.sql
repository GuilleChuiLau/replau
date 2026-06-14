BEGIN;

CREATE SCHEMA IF NOT EXISTS api;

ALTER TABLE api.order_pickup_points
ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE api.delivery_offer_candidates
ADD COLUMN IF NOT EXISTS accepted_assignment_id integer REFERENCES api.delivery_asignaciones(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_order_pickup_points_pickup
ON api.order_pickup_points(pickup_point_id);

CREATE INDEX IF NOT EXISTS idx_delivery_offer_candidates_pedido_status
ON api.delivery_offer_candidates(pedido_id, status);

CREATE OR REPLACE VIEW api.v_order_pickup_points AS
SELECT
    opp.pedido_id,
    p.pedido_num,
    p.estado AS pedido_estado,
    opp.pickup_point_id,
    pp.codigo AS pickup_codigo,
    pp.nombre AS pickup_nombre,
    pp.direccion AS pickup_direccion,
    pp.latitude AS pickup_latitude,
    pp.longitude AS pickup_longitude,
    pp.service_radius_km,
    opp.assigned_at,
    opp.updated_at
FROM api.order_pickup_points opp
JOIN api.pedidos p ON p.id = opp.pedido_id
JOIN api.pickup_points pp ON pp.id = opp.pickup_point_id;

CREATE OR REPLACE VIEW api.v_delivery_offer_candidates AS
SELECT
    c.id,
    c.batch_id,
    c.pedido_id,
    p.pedido_num,
    p.estado AS pedido_estado,
    b.pickup_point_id,
    pp.codigo AS pickup_codigo,
    pp.nombre AS pickup_nombre,
    pp.direccion AS pickup_direccion,
    pp.latitude AS pickup_latitude,
    pp.longitude AS pickup_longitude,
    c.driver_account_id,
    da.phone AS driver_phone,
    da.legal_name AS driver_name,
    c.repartidor_id,
    r.codigo AS repartidor_codigo,
    r.nombre AS repartidor_nombre,
    c.distance_km,
    c.eta_seconds,
    c.score,
    c.status,
    c.offered_at,
    c.viewed_at,
    c.responded_at,
    c.expires_at,
    c.accepted_assignment_id,
    b.status AS batch_status,
    b.created_at AS batch_created_at
FROM api.delivery_offer_candidates c
JOIN api.delivery_offer_batches b ON b.id = c.batch_id
JOIN api.pedidos p ON p.id = c.pedido_id
LEFT JOIN api.pickup_points pp ON pp.id = b.pickup_point_id
JOIN api.driver_accounts da ON da.id = c.driver_account_id
LEFT JOIN api.repartidores r ON r.id = c.repartidor_id;

CREATE OR REPLACE FUNCTION api.driver_set_order_pickup_point(
    p_pedido_id integer,
    p_pickup_point_id integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pickup record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM api.pedidos WHERE id = p_pedido_id) THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Pedido not found');
    END IF;

    SELECT * INTO v_pickup
    FROM api.pickup_points
    WHERE id = p_pickup_point_id
      AND activo = true;

    IF v_pickup.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Active pickup point not found');
    END IF;

    INSERT INTO api.order_pickup_points(pedido_id, pickup_point_id, assigned_at, updated_at)
    VALUES (p_pedido_id, p_pickup_point_id, now(), now())
    ON CONFLICT (pedido_id) DO UPDATE SET
        pickup_point_id = EXCLUDED.pickup_point_id,
        updated_at = now();

    RETURN jsonb_build_object(
        'ok', true,
        'pedido_id', p_pedido_id,
        'pickup_point_id', p_pickup_point_id,
        'pickup_codigo', v_pickup.codigo
    );
END;
$$;

CREATE OR REPLACE FUNCTION api.driver_resolve_pickup_point(
    p_pedido_id integer,
    p_pickup_point_id integer DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pickup_id integer;
BEGIN
    IF p_pickup_point_id IS NOT NULL THEN
        SELECT id INTO v_pickup_id
        FROM api.pickup_points
        WHERE id = p_pickup_point_id
          AND activo = true;
        IF v_pickup_id IS NOT NULL THEN
            PERFORM api.driver_set_order_pickup_point(p_pedido_id, v_pickup_id);
            RETURN v_pickup_id;
        END IF;
        RETURN NULL;
    END IF;

    SELECT pickup_point_id INTO v_pickup_id
    FROM api.order_pickup_points
    WHERE pedido_id = p_pedido_id;

    IF v_pickup_id IS NOT NULL THEN
        RETURN v_pickup_id;
    END IF;

    SELECT id INTO v_pickup_id
    FROM api.pickup_points
    WHERE activo = true
    ORDER BY id
    LIMIT 1;

    IF v_pickup_id IS NOT NULL THEN
        PERFORM api.driver_set_order_pickup_point(p_pedido_id, v_pickup_id);
    END IF;

    RETURN v_pickup_id;
END;
$$;

CREATE OR REPLACE FUNCTION api.driver_create_nearby_offer_batch(
    p_pedido_id integer,
    p_pickup_point_id integer DEFAULT NULL,
    p_radius_km numeric DEFAULT NULL,
    p_max_candidates integer DEFAULT 5,
    p_offer_ttl_seconds integer DEFAULT 300
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_pickup_id integer;
    v_pickup record;
    v_radius numeric(8,3);
    v_max_candidates integer := LEAST(GREATEST(COALESCE(p_max_candidates, 5), 1), 25);
    v_ttl integer := LEAST(GREATEST(COALESCE(p_offer_ttl_seconds, 300), 30), 1800);
    v_batch_id integer;
    v_candidate_count integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM api.pedidos WHERE id = p_pedido_id) THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Pedido not found');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM api.delivery_asignaciones
        WHERE pedido_id = p_pedido_id
          AND status IN ('ACCEPTED','ASSIGNED','COMPLETED')
    ) THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Pedido already assigned');
    END IF;

    v_pickup_id := api.driver_resolve_pickup_point(p_pedido_id, p_pickup_point_id);
    IF v_pickup_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'No active pickup point configured');
    END IF;

    SELECT * INTO v_pickup
    FROM api.pickup_points
    WHERE id = v_pickup_id
      AND activo = true;

    v_radius := COALESCE(p_radius_km, v_pickup.service_radius_km, 8.05);

    UPDATE api.delivery_offer_batches
    SET status = 'CANCELLED',
        updated_at = now()
    WHERE pedido_id = p_pedido_id
      AND status = 'OPEN';

    UPDATE api.delivery_offer_candidates
    SET status = 'CANCELLED',
        responded_at = now()
    WHERE pedido_id = p_pedido_id
      AND status IN ('OFFERED','VIEWED');

    INSERT INTO api.delivery_offer_batches(
        pedido_id,
        pickup_point_id,
        status,
        radius_km,
        max_candidates,
        expires_at
    )
    VALUES (
        p_pedido_id,
        v_pickup_id,
        'OPEN',
        v_radius,
        v_max_candidates,
        now() + make_interval(secs => v_ttl)
    )
    RETURNING id INTO v_batch_id;

    WITH candidates AS (
        SELECT
            a.id AS driver_account_id,
            a.repartidor_id,
            api.driver_distance_km(v_pickup.latitude, v_pickup.longitude, loc.latitude, loc.longitude) AS distance_km,
            loc.captured_at
        FROM api.driver_accounts a
        JOIN api.repartidores r ON r.id = a.repartidor_id
        JOIN api.v_driver_latest_locations loc ON loc.driver_account_id = a.id
        WHERE a.status IN ('APPROVED','ACTIVE')
          AND r.activo = true
          AND loc.captured_at > now() - interval '30 minutes'
          AND EXISTS (
              SELECT 1
              FROM api.driver_online_sessions s
              WHERE s.driver_account_id = a.id
                AND s.status = 'ONLINE'
                AND s.last_seen_at > now() - interval '30 minutes'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM api.delivery_asignaciones existing
              WHERE existing.repartidor_id = a.repartidor_id
                AND existing.status IN ('OFFERED','ACCEPTED','ASSIGNED')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM api.delivery_offer_candidates old
              WHERE old.pedido_id = p_pedido_id
                AND old.driver_account_id = a.id
                AND old.status IN ('ACCEPTED')
          )
    ),
    ranked AS (
        SELECT
            driver_account_id,
            repartidor_id,
            round(distance_km::numeric, 3) AS distance_km,
            GREATEST(60, CEIL((distance_km / 25.0) * 3600)::integer) AS eta_seconds,
            round((distance_km + EXTRACT(EPOCH FROM (now() - captured_at)) / 3600.0)::numeric, 4) AS score
        FROM candidates
        WHERE distance_km <= v_radius
        ORDER BY distance_km ASC, captured_at DESC, driver_account_id ASC
        LIMIT v_max_candidates
    )
    INSERT INTO api.delivery_offer_candidates(
        batch_id,
        pedido_id,
        driver_account_id,
        repartidor_id,
        distance_km,
        eta_seconds,
        score,
        status,
        expires_at
    )
    SELECT
        v_batch_id,
        p_pedido_id,
        driver_account_id,
        repartidor_id,
        distance_km,
        eta_seconds,
        score,
        'OFFERED',
        now() + make_interval(secs => v_ttl)
    FROM ranked;

    GET DIAGNOSTICS v_candidate_count = ROW_COUNT;

    IF v_candidate_count = 0 THEN
        UPDATE api.delivery_offer_batches
        SET status = 'EXPIRED',
            updated_at = now()
        WHERE id = v_batch_id;
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'batch_id', v_batch_id,
        'pedido_id', p_pedido_id,
        'pickup_point_id', v_pickup_id,
        'pickup_codigo', v_pickup.codigo,
        'radius_km', v_radius,
        'candidate_count', v_candidate_count
    );
END;
$$;

CREATE OR REPLACE FUNCTION api.driver_accept_nearby_offer(
    p_driver_account_id integer,
    p_candidate_id integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_candidate record;
    v_batch record;
    v_driver record;
    v_fee numeric(14,2) := api.delivery_driver_fee();
    v_assignment_id integer;
BEGIN
    SELECT * INTO v_driver
    FROM api.driver_accounts
    WHERE id = p_driver_account_id
      AND status IN ('APPROVED','ACTIVE')
      AND repartidor_id IS NOT NULL
    FOR UPDATE;

    IF v_driver.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Driver is not approved');
    END IF;

    SELECT * INTO v_candidate
    FROM api.delivery_offer_candidates
    WHERE id = p_candidate_id
      AND driver_account_id = p_driver_account_id
    FOR UPDATE;

    IF v_candidate.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Offer not found');
    END IF;

    SELECT * INTO v_batch
    FROM api.delivery_offer_batches
    WHERE id = v_candidate.batch_id
    FOR UPDATE;

    IF v_batch.status <> 'OPEN' THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Offer batch is not open', 'batch_status', v_batch.status);
    END IF;

    IF v_candidate.status NOT IN ('OFFERED','VIEWED') OR v_candidate.expires_at <= now() OR v_batch.expires_at <= now() THEN
        UPDATE api.delivery_offer_candidates
        SET status = 'EXPIRED',
            responded_at = now()
        WHERE id = v_candidate.id
          AND status IN ('OFFERED','VIEWED');
        UPDATE api.delivery_offer_batches
        SET status = 'EXPIRED',
            updated_at = now()
        WHERE id = v_batch.id
          AND status = 'OPEN'
          AND NOT EXISTS (
              SELECT 1 FROM api.delivery_offer_candidates
              WHERE batch_id = v_batch.id
                AND status IN ('OFFERED','VIEWED')
                AND expires_at > now()
          );
        RETURN jsonb_build_object('ok', false, 'error', 'Offer expired');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM api.delivery_asignaciones
        WHERE pedido_id = v_candidate.pedido_id
          AND status IN ('ACCEPTED','ASSIGNED','COMPLETED')
    ) THEN
        UPDATE api.delivery_offer_candidates
        SET status = CASE WHEN id = v_candidate.id THEN 'LOST' ELSE status END,
            responded_at = COALESCE(responded_at, now())
        WHERE batch_id = v_candidate.batch_id;
        UPDATE api.delivery_offer_batches
        SET status = 'ASSIGNED',
            updated_at = now()
        WHERE id = v_candidate.batch_id;
        RETURN jsonb_build_object('ok', false, 'error', 'Pedido already assigned');
    END IF;

    INSERT INTO api.delivery_asignaciones(
        pedido_id,
        repartidor_id,
        status,
        fee,
        responded_at,
        assigned_at,
        notes
    )
    VALUES (
        v_candidate.pedido_id,
        v_driver.repartidor_id,
        'ASSIGNED',
        v_fee,
        now(),
        now(),
        'Assigned by nearby driver app offer candidate ' || v_candidate.id::text
    )
    RETURNING id INTO v_assignment_id;

    UPDATE api.delivery_offer_candidates
    SET status = 'ACCEPTED',
        responded_at = now(),
        accepted_assignment_id = v_assignment_id
    WHERE id = v_candidate.id;

    UPDATE api.delivery_offer_candidates
    SET status = 'LOST',
        responded_at = COALESCE(responded_at, now())
    WHERE batch_id = v_candidate.batch_id
      AND id <> v_candidate.id
      AND status IN ('OFFERED','VIEWED');

    UPDATE api.delivery_offer_batches
    SET status = 'ASSIGNED',
        assigned_assignment_id = v_assignment_id,
        updated_at = now()
    WHERE id = v_candidate.batch_id;

    RETURN jsonb_build_object(
        'ok', true,
        'assignment_id', v_assignment_id,
        'candidate_id', v_candidate.id,
        'pedido_id', v_candidate.pedido_id,
        'repartidor_id', v_driver.repartidor_id,
        'fee', v_fee
    );
END;
$$;

CREATE OR REPLACE FUNCTION api.driver_decline_nearby_offer(
    p_driver_account_id integer,
    p_candidate_id integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_candidate record;
BEGIN
    SELECT * INTO v_candidate
    FROM api.delivery_offer_candidates
    WHERE id = p_candidate_id
      AND driver_account_id = p_driver_account_id
    FOR UPDATE;

    IF v_candidate.id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Offer not found');
    END IF;

    IF v_candidate.status NOT IN ('OFFERED','VIEWED') THEN
        RETURN jsonb_build_object('ok', false, 'error', 'Offer is not open', 'status', v_candidate.status);
    END IF;

    UPDATE api.delivery_offer_candidates
    SET status = 'DECLINED',
        responded_at = now()
    WHERE id = v_candidate.id;

    UPDATE api.delivery_offer_batches
    SET status = 'EXPIRED',
        updated_at = now()
    WHERE id = v_candidate.batch_id
      AND status = 'OPEN'
      AND NOT EXISTS (
          SELECT 1
          FROM api.delivery_offer_candidates
          WHERE batch_id = v_candidate.batch_id
            AND status IN ('OFFERED','VIEWED')
            AND expires_at > now()
      );

    RETURN jsonb_build_object('ok', true, 'candidate_id', v_candidate.id, 'status', 'DECLINED');
END;
$$;

GRANT SELECT, INSERT, UPDATE ON
    api.pickup_points,
    api.order_pickup_points,
    api.delivery_offer_batches,
    api.delivery_offer_candidates
TO web_anon;

GRANT SELECT ON
    api.v_order_pickup_points,
    api.v_delivery_offer_candidates
TO web_anon;

GRANT USAGE, SELECT, UPDATE
ON ALL SEQUENCES IN SCHEMA api
TO web_anon;

GRANT EXECUTE ON FUNCTION api.driver_set_order_pickup_point(integer, integer) TO web_anon;
GRANT EXECUTE ON FUNCTION api.driver_resolve_pickup_point(integer, integer) TO web_anon;
GRANT EXECUTE ON FUNCTION api.driver_create_nearby_offer_batch(integer, integer, numeric, integer, integer) TO web_anon;
GRANT EXECUTE ON FUNCTION api.driver_accept_nearby_offer(integer, integer) TO web_anon;
GRANT EXECUTE ON FUNCTION api.driver_decline_nearby_offer(integer, integer) TO web_anon;

NOTIFY pgrst, 'reload schema';

COMMIT;
