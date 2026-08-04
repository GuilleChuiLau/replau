-- Draft pilot ingredient and recipe data for Replau.
--
-- Safety properties:
--   * recipes are inserted inactive, so scanner picking cannot consume them;
--   * ingredient inventory enforcement remains disabled;
--   * no opening stock or stock movement is created;
--   * reruns add missing rows but do not overwrite operator corrections.
--
-- Costs and quantities are planning estimates. Confirm them against actual
-- supplier invoices, portion weights, and kitchen preparation before activation.

BEGIN;
SET LOCAL ROLE web_anon;

INSERT INTO api.ingredientes_costeo
    (nombre, sku, costo_kg, moneda, stock_minimo, inventory_enforced, active)
VALUES
    ('PAN DE HAMBURGUESA',       'ING-PAN-HAMB',       12.5000, 'PEN', 0, false, true),
    ('CARNE DE RES PARA HAMBURGUESA', 'ING-CARNE-RES', 30.0000, 'PEN', 0, false, true),
    ('LECHUGA',                  'ING-LECHUGA',         8.0000, 'PEN', 0, false, true),
    ('TOMATE',                   'ING-TOMATE',          6.0000, 'PEN', 0, false, true),
    ('CEBOLLA',                  'ING-CEBOLLA',         5.0000, 'PEN', 0, false, true),
    ('SALSA DE LA CASA',         'ING-SALSA-CASA',     18.0000, 'PEN', 0, false, true),
    ('QUESO PARA HAMBURGUESA',   'ING-QUESO-HAMB',     32.0000, 'PEN', 0, false, true),
    ('PAPA PREFRITA CONGELADA',  'ING-PAPA-PREFRITA',   8.5000, 'PEN', 0, false, true),
    ('ACEITE DE FRITURA ABSORBIDO', 'ING-ACEITE-FRIT',  9.0000, 'PEN', 0, false, true),
    ('SAL',                      'ING-SAL',              2.0000, 'PEN', 0, false, true),
    ('POLLO PARA TIRAS',         'ING-POLLO-TIRAS',     20.0000, 'PEN', 0, false, true),
    ('EMPANIZADO',               'ING-EMPANIZADO',      10.0000, 'PEN', 0, false, true),
    ('ALITAS DE POLLO',          'ING-ALITAS-POLLO',    16.0000, 'PEN', 0, false, true),
    ('SALSA PICANTE PARA ALITAS','ING-SALSA-PICANTE',  16.0000, 'PEN', 0, false, true)
ON CONFLICT (nombre) DO NOTHING;

INSERT INTO api.recetas_costeo
    (producto_id, nombre, rendimiento_unidades, active)
SELECT p.id, seed.recipe_name, 1, false
FROM (
    VALUES
        ('BURGER_SINGLE',        'BORRADOR PILOTO - HAMBURGUESA SIMPLE'),
        ('BURGER_SINGLE_CHEESE', 'BORRADOR PILOTO - HAMBURGUESA SIMPLE CON QUESO'),
        ('FRIES_SMALL',          'BORRADOR PILOTO - PAPAS FRITAS PEQUENAS'),
        ('CHICKEN_STRIPS_3',     'BORRADOR PILOTO - TIRAS DE POLLO X3'),
        ('WINGS_6',              'BORRADOR PILOTO - ALITAS FRITAS PICANTES X 6')
) AS seed(product_code, recipe_name)
JOIN api.productos p ON p.cdg_prod = seed.product_code
WHERE NOT EXISTS (
    SELECT 1
    FROM api.recetas_costeo r
    WHERE r.producto_id = p.id
      AND r.nombre = seed.recipe_name
);

WITH recipe_lines(product_code, ingredient_sku, quantity_g) AS (
    VALUES
        ('BURGER_SINGLE',        'ING-PAN-HAMB',        75.000),
        ('BURGER_SINGLE',        'ING-CARNE-RES',      100.000),
        ('BURGER_SINGLE',        'ING-LECHUGA',         15.000),
        ('BURGER_SINGLE',        'ING-TOMATE',          25.000),
        ('BURGER_SINGLE',        'ING-CEBOLLA',         10.000),
        ('BURGER_SINGLE',        'ING-SALSA-CASA',      20.000),
        ('BURGER_SINGLE',        'ING-SAL',              1.000),

        ('BURGER_SINGLE_CHEESE', 'ING-PAN-HAMB',        75.000),
        ('BURGER_SINGLE_CHEESE', 'ING-CARNE-RES',      100.000),
        ('BURGER_SINGLE_CHEESE', 'ING-LECHUGA',         15.000),
        ('BURGER_SINGLE_CHEESE', 'ING-TOMATE',          25.000),
        ('BURGER_SINGLE_CHEESE', 'ING-CEBOLLA',         10.000),
        ('BURGER_SINGLE_CHEESE', 'ING-SALSA-CASA',      20.000),
        ('BURGER_SINGLE_CHEESE', 'ING-QUESO-HAMB',      20.000),
        ('BURGER_SINGLE_CHEESE', 'ING-SAL',              1.000),

        ('FRIES_SMALL',          'ING-PAPA-PREFRITA',  150.000),
        ('FRIES_SMALL',          'ING-ACEITE-FRIT',      12.000),
        ('FRIES_SMALL',          'ING-SAL',               2.000),

        ('CHICKEN_STRIPS_3',     'ING-POLLO-TIRAS',     180.000),
        ('CHICKEN_STRIPS_3',     'ING-EMPANIZADO',       30.000),
        ('CHICKEN_STRIPS_3',     'ING-ACEITE-FRIT',      15.000),
        ('CHICKEN_STRIPS_3',     'ING-SAL',               2.000),

        ('WINGS_6',              'ING-ALITAS-POLLO',    360.000),
        ('WINGS_6',              'ING-SALSA-PICANTE',    30.000),
        ('WINGS_6',              'ING-ACEITE-FRIT',      18.000),
        ('WINGS_6',              'ING-SAL',               2.000)
)
INSERT INTO api.receta_ingredientes_costeo
    (receta_id, ingrediente_id, cantidad_g)
SELECT r.id, i.id, recipe_lines.quantity_g
FROM recipe_lines
JOIN api.productos p ON p.cdg_prod = recipe_lines.product_code
JOIN api.recetas_costeo r
  ON r.producto_id = p.id
 AND r.nombre = 'BORRADOR PILOTO - ' || p.nombre
JOIN api.ingredientes_costeo i ON i.sku = recipe_lines.ingredient_sku
ON CONFLICT (receta_id, ingrediente_id) DO NOTHING;

DO $$
DECLARE
    ingredient_count integer;
    recipe_count integer;
    line_count integer;
BEGIN
    SELECT count(*) INTO ingredient_count
    FROM api.ingredientes_costeo
    WHERE sku IN (
        'ING-PAN-HAMB','ING-CARNE-RES','ING-LECHUGA','ING-TOMATE',
        'ING-CEBOLLA','ING-SALSA-CASA','ING-QUESO-HAMB',
        'ING-PAPA-PREFRITA','ING-ACEITE-FRIT','ING-SAL',
        'ING-POLLO-TIRAS','ING-EMPANIZADO','ING-ALITAS-POLLO',
        'ING-SALSA-PICANTE'
    );

    SELECT count(*) INTO recipe_count
    FROM api.recetas_costeo r
    JOIN api.productos p ON p.id = r.producto_id
    WHERE p.cdg_prod IN (
        'BURGER_SINGLE','BURGER_SINGLE_CHEESE','FRIES_SMALL',
        'CHICKEN_STRIPS_3','WINGS_6'
    )
      AND r.nombre = 'BORRADOR PILOTO - ' || p.nombre;

    SELECT count(*) INTO line_count
    FROM api.receta_ingredientes_costeo ri
    JOIN api.recetas_costeo r ON r.id = ri.receta_id
    JOIN api.productos p ON p.id = r.producto_id
    WHERE p.cdg_prod IN (
        'BURGER_SINGLE','BURGER_SINGLE_CHEESE','FRIES_SMALL',
        'CHICKEN_STRIPS_3','WINGS_6'
    )
      AND r.nombre = 'BORRADOR PILOTO - ' || p.nombre;

    IF ingredient_count <> 14 OR recipe_count <> 5 OR line_count <> 26 THEN
        RAISE EXCEPTION
            'Pilot seed incomplete: ingredients %, recipes %, lines %',
            ingredient_count, recipe_count, line_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM api.recetas_costeo r
        JOIN api.productos p ON p.id = r.producto_id
        WHERE p.cdg_prod IN (
            'BURGER_SINGLE','BURGER_SINGLE_CHEESE','FRIES_SMALL',
            'CHICKEN_STRIPS_3','WINGS_6'
        )
          AND r.nombre = 'BORRADOR PILOTO - ' || p.nombre
          AND r.active
    ) THEN
        RAISE EXCEPTION 'Pilot recipes must remain inactive';
    END IF;

    IF EXISTS (
        SELECT 1 FROM api.ingredientes_costeo
        WHERE sku IN (
            'ING-PAN-HAMB','ING-CARNE-RES','ING-LECHUGA','ING-TOMATE',
            'ING-CEBOLLA','ING-SALSA-CASA','ING-QUESO-HAMB',
            'ING-PAPA-PREFRITA','ING-ACEITE-FRIT','ING-SAL',
            'ING-POLLO-TIRAS','ING-EMPANIZADO','ING-ALITAS-POLLO',
            'ING-SALSA-PICANTE'
        )
          AND inventory_enforced
    ) THEN
        RAISE EXCEPTION 'Pilot ingredients must not enable enforcement';
    END IF;
END $$;

COMMIT;

SET ROLE web_anon;

SELECT
    c.cdg_prod,
    c.producto_nombre,
    c.receta_nombre,
    c.ingredientes_count,
    c.costo_por_unidad,
    pp.precio AS precio_venta,
    round((pp.precio - c.costo_por_unidad)::numeric, 2) AS margen_bruto_estimado,
    round((100 * (pp.precio - c.costo_por_unidad) / NULLIF(pp.precio, 0))::numeric, 1)
        AS margen_bruto_pct_estimado
FROM api.v_receta_costos c
LEFT JOIN LATERAL (
    SELECT precio
    FROM api.producto_precios
    WHERE producto_id = c.producto_id
      AND active
      AND valid_from <= current_date
      AND (valid_to IS NULL OR valid_to >= current_date)
    ORDER BY valid_from DESC, id DESC
    LIMIT 1
) pp ON true
WHERE c.receta_nombre LIKE 'BORRADOR PILOTO - %'
ORDER BY c.cdg_prod;
