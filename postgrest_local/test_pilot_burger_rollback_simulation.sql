\set ON_ERROR_STOP on

-- Exercises the real scanner-picking and recipe-consumption path with the
-- generated BURGER_SINGLE calibration data. Every mutation is rolled back.
SET ROLE web_anon;

DO $$
DECLARE
    v_recipe_id integer;
    v_line_count integer;
    v_mismatch_count integer;
BEGIN
    SELECT r.id INTO v_recipe_id
    FROM api.recetas_costeo r
    JOIN api.productos p ON p.id = r.producto_id
    WHERE p.cdg_prod = 'BURGER_SINGLE'
      AND r.nombre = 'BORRADOR PILOTO - HAMBURGUESA SIMPLE';

    IF v_recipe_id IS NULL THEN
        RAISE EXCEPTION 'Inactive BURGER_SINGLE pilot recipe is missing';
    END IF;
    IF (SELECT active FROM api.recetas_costeo WHERE id = v_recipe_id) THEN
        RAISE EXCEPTION 'Pilot recipe must be inactive before rollback simulation';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM api.ingredientes_costeo
        WHERE sku IN (
            'ING-PAN-HAMB','ING-CARNE-RES','ING-LECHUGA','ING-TOMATE',
            'ING-CEBOLLA','ING-SALSA-CASA','ING-SAL'
        )
          AND inventory_enforced
    ) THEN
        RAISE EXCEPTION 'Pilot ingredient enforcement must be off before simulation';
    END IF;

    SELECT count(*) INTO v_line_count
    FROM api.receta_ingredientes_costeo
    WHERE receta_id = v_recipe_id;

    WITH expected(sku, quantity_g, cost_per_kg) AS (
        VALUES
            ('ING-PAN-HAMB',    75.000::numeric, 12.5000::numeric),
            ('ING-CARNE-RES',  100.000::numeric, 30.0000::numeric),
            ('ING-LECHUGA',     15.000::numeric,  8.0000::numeric),
            ('ING-TOMATE',      25.000::numeric,  6.0000::numeric),
            ('ING-CEBOLLA',     10.000::numeric,  5.0000::numeric),
            ('ING-SALSA-CASA',  20.000::numeric, 18.0000::numeric),
            ('ING-SAL',          1.000::numeric,  2.0000::numeric)
    )
    SELECT count(*) INTO v_mismatch_count
    FROM expected e
    LEFT JOIN api.ingredientes_costeo i ON i.sku = e.sku AND i.active
    LEFT JOIN api.receta_ingredientes_costeo ri
      ON ri.receta_id = v_recipe_id
     AND ri.ingrediente_id = i.id
    WHERE i.id IS NULL
       OR i.costo_kg <> e.cost_per_kg
       OR ri.id IS NULL
       OR ri.cantidad_g <> e.quantity_g;

    IF v_line_count <> 7 OR v_mismatch_count <> 0 THEN
        RAISE EXCEPTION
            'Pilot calibration preflight failed: lines %, mismatches %',
            v_line_count, v_mismatch_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM api.product_barcodes
        WHERE barcode = 'BURGER_SINGLE'
          AND producto_id = (SELECT id FROM api.productos WHERE cdg_prod = 'BURGER_SINGLE')
          AND active
          AND unit_factor = 1
    ) THEN
        RAISE EXCEPTION 'Active BURGER_SINGLE unit barcode is missing';
    END IF;
END $$;

BEGIN;

DO $$
DECLARE
    v_warehouse_id integer;
    v_product_id integer;
    v_recipe_id integer;
    v_customer_id integer;
    v_order_id integer;
    v_order_item_id integer;
    v_public_token text := 'CONTRACT-BURGER-ROLLBACK-TOKEN';
    v_scan_one jsonb;
    v_scan_two jsonb;
    v_complete jsonb;
    v_retry jsonb;
    v_receipt jsonb;
    v_theoretical_cost numeric;
    v_remaining_units numeric;
    v_expected_remaining numeric;
    v_actual_remaining numeric;
    v_calibration record;
BEGIN
    v_warehouse_id := api.get_default_almacen_id();
    SELECT id INTO v_product_id
    FROM api.productos
    WHERE cdg_prod = 'BURGER_SINGLE' AND active;
    SELECT id INTO v_recipe_id
    FROM api.recetas_costeo
    WHERE producto_id = v_product_id
      AND nombre = 'BORRADOR PILOTO - HAMBURGUESA SIMPLE';

    IF v_warehouse_id IS NULL OR v_product_id IS NULL OR v_recipe_id IS NULL THEN
        RAISE EXCEPTION 'Simulation requires the default warehouse, product, and pilot recipe';
    END IF;

    UPDATE api.ingredientes_costeo
    SET inventory_enforced = true
    WHERE sku IN (
        'ING-PAN-HAMB','ING-CARNE-RES','ING-LECHUGA','ING-TOMATE',
        'ING-CEBOLLA','ING-SALSA-CASA','ING-SAL'
    );
    IF NOT FOUND THEN RAISE EXCEPTION 'Pilot ingredients were not found'; END IF;

    UPDATE api.recetas_costeo SET active = true WHERE id = v_recipe_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Pilot recipe was not activated in transaction'; END IF;

    FOR v_calibration IN
        SELECT *
        FROM (VALUES
            ('ING-PAN-HAMB',    1.500::numeric, 12.5000::numeric, 'EST-PAN-20260803',     DATE '2026-08-06', 75.000::numeric),
            ('ING-CARNE-RES',   5.000::numeric, 30.0000::numeric, 'EST-CARNE-20260803',   DATE '2026-08-05',100.000::numeric),
            ('ING-LECHUGA',     1.000::numeric,  8.0000::numeric, 'EST-LECHUGA-20260803', DATE '2026-08-05', 15.000::numeric),
            ('ING-TOMATE',      2.000::numeric,  6.0000::numeric, 'EST-TOMATE-20260803',  DATE '2026-08-07', 25.000::numeric),
            ('ING-CEBOLLA',     2.000::numeric,  5.0000::numeric, 'EST-CEBOLLA-20260803', DATE '2026-08-17', 10.000::numeric),
            ('ING-SALSA-CASA',  1.000::numeric, 18.0000::numeric, 'EST-SALSA-20260803',   DATE '2026-08-10', 20.000::numeric),
            ('ING-SAL',         1.000::numeric,  2.0000::numeric, 'EST-SAL-20260803',     DATE '2028-08-03',  1.000::numeric)
        ) AS c(sku, opening_kg, cost_per_kg, lot_code, expires_on, portion_g)
    LOOP
        SELECT api.post_ingredient_receipt(
            i.id,
            v_warehouse_id,
            v_calibration.opening_kg,
            v_calibration.cost_per_kg,
            'PEN',
            NULL,
            'contract-burger-calibration',
            'Rollback generated burger calibration receipt',
            v_calibration.lot_code,
            v_calibration.expires_on,
            'GENERATED-ESTIMATE-NOT-INVOICE'
        ) INTO v_receipt
        FROM api.ingredientes_costeo i
        WHERE i.sku = v_calibration.sku;

        IF v_receipt IS NULL
           OR NOT (v_receipt->>'ok')::boolean
           OR (v_receipt->>'stock_kg')::numeric <> v_calibration.opening_kg THEN
            RAISE EXCEPTION 'Generated receipt failed for %: %', v_calibration.sku, v_receipt;
        END IF;
    END LOOP;

    INSERT INTO api.clientes_whatsapp(whatsapp_number, nombre)
    VALUES('+51999000002-contract-burger','Contract Burger Rollback')
    RETURNING id INTO v_customer_id;

    INSERT INTO api.pedidos(
        pedido_num, cliente_id, canal, estado, metodo_pago, moneda,
        subtotal, delivery, total, observacion, public_token,
        public_token_created_at, public_token_expires_at
    ) VALUES(
        'CONTRACT-BURGER-ROLLBACK', v_customer_id, 'MANUAL', 'CONFIRMADO',
        'CONTRA_ENTREGA', 'PEN', 24, 0, 24,
        'Rollback-only generated two-burger scanner simulation',
        v_public_token, now(), now() + interval '1 hour'
    ) RETURNING id INTO v_order_id;

    INSERT INTO api.pedido_items(
        pedido_id, producto_id, producto_texto, cantidad, unidad,
        precio_unitario, total_linea
    ) VALUES(
        v_order_id, v_product_id, 'HAMBURGUESA SIMPLE', 2, 'UNIDAD', 12, 24
    ) RETURNING id INTO v_order_item_id;

    v_scan_one := api.scan_picking_barcode(
        'CONTRACT-BURGER-ROLLBACK', v_public_token,
        'BURGER_SINGLE', 'contract-burger-picker'
    );
    IF NOT (v_scan_one->>'ok')::boolean
       OR (v_scan_one->>'complete')::boolean
       OR (v_scan_one->>'scanned_quantity')::numeric <> 1 THEN
        RAISE EXCEPTION 'First burger scan failed: %', v_scan_one;
    END IF;

    v_scan_two := api.scan_picking_barcode(
        'CONTRACT-BURGER-ROLLBACK', v_public_token,
        'BURGER_SINGLE', 'contract-burger-picker'
    );
    IF NOT (v_scan_two->>'ok')::boolean
       OR NOT (v_scan_two->>'complete')::boolean
       OR (v_scan_two->>'scanned_quantity')::numeric <> 2 THEN
        RAISE EXCEPTION 'Second burger scan failed: %', v_scan_two;
    END IF;

    v_complete := api.complete_scanner_picking(
        'CONTRACT-BURGER-ROLLBACK', v_public_token,
        'contract-burger-picker'
    );
    IF NOT (v_complete->>'ok')::boolean
       OR v_complete->>'estado' <> 'DESPACHADO'
       OR (v_complete->'ingredients'->>'movement_count')::integer <> 7
       OR (v_complete->'ingredients'->>'total_quantity_kg')::numeric <> 0.492
       OR (v_complete->'ingredients'->>'skipped_line_count')::integer <> 0 THEN
        RAISE EXCEPTION 'Scanner completion/ingredient result failed: %', v_complete;
    END IF;

    SELECT theoretical_cost INTO v_theoretical_cost
    FROM api.v_order_ingredient_consumption
    WHERE pedido_id = v_order_id;
    IF v_theoretical_cost <> 9.2390 THEN
        RAISE EXCEPTION 'Expected S/9.2390 theoretical cost, got %', v_theoretical_cost;
    END IF;

    IF (SELECT estado FROM api.pedidos WHERE id = v_order_id) <> 'DESPACHADO' THEN
        RAISE EXCEPTION 'Synthetic order did not reach DESPACHADO';
    END IF;
    IF (SELECT status FROM api.picking_sessions WHERE pedido_id = v_order_id) <> 'COMPLETED' THEN
        RAISE EXCEPTION 'Synthetic picking session did not complete';
    END IF;
    IF (SELECT count(*) FROM api.picking_scan_events e
        JOIN api.picking_sessions s ON s.id = e.session_id
        WHERE s.pedido_id = v_order_id AND e.result = 'ACCEPTED') <> 2 THEN
        RAISE EXCEPTION 'Expected exactly two accepted scanner events';
    END IF;
    IF (SELECT count(*) FROM api.ingredient_stock_movements
        WHERE doc_type = 'ORDER_RECIPE_CONSUMPTION' AND doc_id = v_order_id) <> 7 THEN
        RAISE EXCEPTION 'Expected seven ingredient consumption movements';
    END IF;

    FOR v_calibration IN
        SELECT *
        FROM (VALUES
            ('ING-PAN-HAMB',    1.500::numeric, 75.000::numeric),
            ('ING-CARNE-RES',   5.000::numeric,100.000::numeric),
            ('ING-LECHUGA',     1.000::numeric, 15.000::numeric),
            ('ING-TOMATE',      2.000::numeric, 25.000::numeric),
            ('ING-CEBOLLA',     2.000::numeric, 10.000::numeric),
            ('ING-SALSA-CASA',  1.000::numeric, 20.000::numeric),
            ('ING-SAL',         1.000::numeric,  1.000::numeric)
        ) AS c(sku, opening_kg, portion_g)
    LOOP
        v_expected_remaining := v_calibration.opening_kg - (2 * v_calibration.portion_g / 1000);
        SELECT api.ingredient_stock_quantity(v_warehouse_id, i.id)
        INTO v_actual_remaining
        FROM api.ingredientes_costeo i
        WHERE i.sku = v_calibration.sku;
        IF v_actual_remaining <> v_expected_remaining THEN
            RAISE EXCEPTION
                'Remaining stock mismatch for %: expected %, got %',
                v_calibration.sku, v_expected_remaining, v_actual_remaining;
        END IF;
    END LOOP;

    SELECT floor(min(
        s.stock_kg / NULLIF(ri.cantidad_g / 1000.0, 0)
    )) INTO v_remaining_units
    FROM api.receta_ingredientes_costeo ri
    JOIN api.ingredientes_costeo i ON i.id = ri.ingrediente_id
    JOIN api.v_ingredient_stock s
      ON s.ingredient_id = i.id
     AND s.warehouse_id = v_warehouse_id
    WHERE ri.receta_id = v_recipe_id;
    IF v_remaining_units <> 18 THEN
        RAISE EXCEPTION 'Expected capacity for 18 more burgers, got %', v_remaining_units;
    END IF;

    v_retry := api.consume_order_ingredients(
        v_order_id, v_warehouse_id, 'contract-burger-retry',
        (SELECT id FROM api.picking_sessions WHERE pedido_id = v_order_id)
    );
    IF NOT (v_retry->>'already_posted')::boolean
       OR (v_retry->>'movement_count')::integer <> 7
       OR (SELECT count(*) FROM api.ingredient_stock_movements
           WHERE doc_type = 'ORDER_RECIPE_CONSUMPTION' AND doc_id = v_order_id) <> 7 THEN
        RAISE EXCEPTION 'Ingredient-consumption retry was not idempotent: %', v_retry;
    END IF;

    RAISE NOTICE
        'Simulation passed: 2 burgers, 7 ingredients, 0.492 kg, S/9.2390 cost, 18 burgers remaining';
END $$;

ROLLBACK;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM api.pedidos WHERE pedido_num = 'CONTRACT-BURGER-ROLLBACK') THEN
        RAISE EXCEPTION 'Rollback retained the synthetic order';
    END IF;
    IF EXISTS (
        SELECT 1 FROM api.clientes_whatsapp
        WHERE whatsapp_number = '+51999000002-contract-burger'
    ) THEN
        RAISE EXCEPTION 'Rollback retained the synthetic customer';
    END IF;
    IF EXISTS (
        SELECT 1 FROM api.ingredient_lots
        WHERE lot_code LIKE 'EST-%-20260803'
    ) THEN
        RAISE EXCEPTION 'Rollback retained generated lot placeholders';
    END IF;
    IF EXISTS (
        SELECT 1 FROM api.ingredient_stock_movements
        WHERE reference = 'GENERATED-ESTIMATE-NOT-INVOICE'
           OR doc_type = 'ORDER_RECIPE_CONSUMPTION'
              AND reference = 'CONTRACT-BURGER-ROLLBACK'
    ) THEN
        RAISE EXCEPTION 'Rollback retained generated ingredient movements';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM api.recetas_costeo r
        JOIN api.productos p ON p.id = r.producto_id
        WHERE p.cdg_prod = 'BURGER_SINGLE'
          AND r.nombre = 'BORRADOR PILOTO - HAMBURGUESA SIMPLE'
          AND r.active
    ) THEN
        RAISE EXCEPTION 'Rollback left the pilot recipe active';
    END IF;
    IF EXISTS (
        SELECT 1 FROM api.ingredientes_costeo
        WHERE sku IN (
            'ING-PAN-HAMB','ING-CARNE-RES','ING-LECHUGA','ING-TOMATE',
            'ING-CEBOLLA','ING-SALSA-CASA','ING-SAL'
        )
          AND inventory_enforced
    ) THEN
        RAISE EXCEPTION 'Rollback left pilot ingredient enforcement enabled';
    END IF;
END $$;

\echo 'Generated BURGER_SINGLE rollback simulation passed; synthetic customer, order, scans, lots, stock, consumption, activation, and enforcement were all rolled back.'
