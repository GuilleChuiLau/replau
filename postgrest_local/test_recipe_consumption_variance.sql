\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
 v_warehouse_id integer;
 v_product_id integer;
 v_ingredient_id integer;
 v_recipe_id integer;
 v_customer_id integer;
 v_order_id integer;
 v_order_item_id integer;
 v_factor numeric;
 v_expected_kg numeric;
 v_result jsonb;
 v_second_result jsonb;
 v_variance record;
BEGIN
 SELECT id INTO v_warehouse_id FROM api.almacenes WHERE active ORDER BY id LIMIT 1;
 SELECT p.id INTO v_product_id FROM api.productos p
 WHERE p.active
   AND NOT EXISTS(SELECT 1 FROM api.recetas_costeo r WHERE r.producto_id=p.id AND r.active)
   AND NOT EXISTS(SELECT 1 FROM api.inventory_stock_controls c WHERE c.product_id=p.id AND c.enforcement_enabled)
 ORDER BY p.id LIMIT 1;
 SELECT cliente_id INTO v_customer_id FROM api.pedidos ORDER BY id LIMIT 1;
 IF v_warehouse_id IS NULL OR v_product_id IS NULL OR v_customer_id IS NULL THEN
  RAISE EXCEPTION 'Contract test needs an active warehouse, unconfigured product, and existing customer';
 END IF;

 v_factor:=api.get_producto_factor(v_product_id,NULL);
 v_expected_kg:=round((4*v_factor*500/1000.0/2)::numeric,3);

 INSERT INTO api.ingredientes_costeo(nombre,sku,costo_kg,moneda,inventory_enforced)
 VALUES('CONTRACT RECIPE CONSUMPTION','CONTRACT-RECIPE-CONSUME',12,'PEN',true)
 RETURNING id INTO v_ingredient_id;
 PERFORM api.post_ingredient_receipt(v_ingredient_id,v_warehouse_id,greatest(v_expected_kg+1,10),12,'PEN',NULL,'contract-receiver','Rollback recipe stock receipt',NULL,NULL,'CONTRACT-RECIPE-STOCK');
 INSERT INTO api.recetas_costeo(producto_id,nombre,rendimiento_unidades)
 VALUES(v_product_id,'CONTRACT DISPATCH RECIPE',2) RETURNING id INTO v_recipe_id;
 INSERT INTO api.receta_ingredientes_costeo(receta_id,ingrediente_id,cantidad_g)
 VALUES(v_recipe_id,v_ingredient_id,500);

 INSERT INTO api.pedidos(pedido_num,cliente_id,canal,estado,metodo_pago,moneda)
 VALUES('CONTRACT-RECIPE-CONSUME',v_customer_id,'MANUAL','CONFIRMADO','CONTRA_ENTREGA','PEN')
 RETURNING id INTO v_order_id;
 INSERT INTO api.pedido_items(pedido_id,producto_id,producto_texto,cantidad,unidad,precio_unitario,total_linea)
 SELECT v_order_id,p.id,p.nombre,4,NULL,0,0 FROM api.productos p WHERE p.id=v_product_id
 RETURNING id INTO v_order_item_id;

 v_result:=api.consume_order_ingredients(v_order_id,v_warehouse_id,'contract-kitchen',NULL);
 IF (v_result->>'movement_count')::integer<>1 OR (v_result->>'total_quantity_kg')::numeric<>v_expected_kg THEN
  RAISE EXCEPTION 'Recipe consumption result failed: % expected kg %',v_result,v_expected_kg;
 END IF;
 IF (SELECT count(*) FROM api.ingredient_stock_movements m WHERE m.doc_type='ORDER_RECIPE_CONSUMPTION' AND m.doc_id=v_order_id)<>1 THEN
  RAISE EXCEPTION 'Expected one recipe consumption movement';
 END IF;
 v_second_result:=api.consume_order_ingredients(v_order_id,v_warehouse_id,'contract-kitchen',NULL);
 IF NOT (v_second_result->>'already_posted')::boolean THEN RAISE EXCEPTION 'Recipe consumption was not idempotent';END IF;
 IF (SELECT count(*) FROM api.ingredient_stock_movements m WHERE m.doc_type='ORDER_RECIPE_CONSUMPTION' AND m.doc_id=v_order_id)<>1 THEN
  RAISE EXCEPTION 'Idempotent retry duplicated consumption';
 END IF;

 PERFORM api.post_ingredient_waste(v_ingredient_id,v_warehouse_id,0.1,'contract-kitchen','PREP_LOSS','Rollback variance waste');
 SELECT * INTO v_variance FROM api.v_ingredient_usage_variance_daily v
 WHERE v.usage_date=timezone('America/Lima',now())::date AND v.warehouse_id=v_warehouse_id AND v.ingredient_id=v_ingredient_id;
 IF v_variance.theoretical_consumption_kg<>v_expected_kg OR v_variance.waste_variance_kg<>0.1 OR v_variance.actual_usage_kg<>v_expected_kg+0.1 THEN
  RAISE EXCEPTION 'Ingredient variance report failed: theoretical %, waste %, actual %',v_variance.theoretical_consumption_kg,v_variance.waste_variance_kg,v_variance.actual_usage_kg;
 END IF;
 IF (SELECT skipped_line_count FROM api.ingredient_consumption_batches b WHERE b.pedido_id=v_order_id)<>0 THEN
  RAISE EXCEPTION 'Configured recipe line was incorrectly marked skipped';
 END IF;
END $$;

ROLLBACK;
\echo 'Recipe consumption and variance contract test passed; all orders, recipes, ingredients, batches, and movements rolled back.'
