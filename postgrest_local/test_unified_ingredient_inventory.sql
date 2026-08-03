\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
 warehouse_id integer;supplier_id integer;product_id integer;v_ingredient_id integer;v_recipe_id integer;result jsonb;stock_qty numeric;cost_row record;
BEGIN
 SELECT id INTO warehouse_id FROM api.almacenes WHERE active ORDER BY id LIMIT 1;
 SELECT id INTO supplier_id FROM api.proveedores WHERE active ORDER BY id LIMIT 1;
 SELECT id INTO product_id FROM api.productos WHERE active ORDER BY id LIMIT 1;
 IF warehouse_id IS NULL OR product_id IS NULL THEN RAISE EXCEPTION 'Contract test needs active warehouse and product';END IF;
 INSERT INTO api.ingredientes_costeo(nombre,sku,proveedor_id,costo_kg,moneda,stock_minimo,active)
 VALUES('CONTRACT INGREDIENT','CONTRACT-ING',supplier_id,10,'PEN',2,true) RETURNING id INTO v_ingredient_id;
 result:=api.post_ingredient_receipt(v_ingredient_id,warehouse_id,10,20,'PEN',supplier_id,'contract-receiver','Rollback supplier receipt','LOT-CONTRACT',current_date+30,'CONTRACT-RECEIPT');
 IF (result->>'stock_kg')::numeric<>10 THEN RAISE EXCEPTION 'Ingredient receipt failed: %',result;END IF;
 IF (SELECT costo_kg FROM api.ingredientes_costeo WHERE id=v_ingredient_id)<>20 THEN RAISE EXCEPTION 'Weighted cost update failed';END IF;
 PERFORM api.post_ingredient_waste(v_ingredient_id,warehouse_id,1,'contract-kitchen','SPOILAGE','Rollback waste test');
 PERFORM api.post_ingredient_movement(v_ingredient_id,warehouse_id,'ADJUST_NEGATIVE',1,'contract-manager','Rollback negative correction','COUNT_VARIANCE',NULL,'PEN',NULL,NULL,NULL,'CONTRACT_TEST',NULL,NULL,NULL);
 PERFORM api.post_ingredient_movement(v_ingredient_id,warehouse_id,'ADJUST_POSITIVE',0.5,'contract-manager','Rollback positive correction','COUNT_VARIANCE',NULL,'PEN',NULL,NULL,NULL,'CONTRACT_TEST',NULL,NULL,NULL);
 stock_qty:=api.ingredient_stock_quantity(warehouse_id,v_ingredient_id);
 IF stock_qty<>8.5 THEN RAISE EXCEPTION 'Expected ingredient stock 8.5, got %',stock_qty;END IF;
 UPDATE api.ingredientes_costeo SET inventory_enforced=true WHERE id=v_ingredient_id;
 BEGIN
  PERFORM api.post_ingredient_waste(v_ingredient_id,warehouse_id,9,'contract-kitchen','DAMAGE','Expected insufficient stock test');
  RAISE EXCEPTION 'Expected ingredient negative-stock protection';
 EXCEPTION WHEN OTHERS THEN
  IF SQLERRM='Expected ingredient negative-stock protection' THEN RAISE;END IF;
 END;
 INSERT INTO api.recetas_costeo(producto_id,nombre,rendimiento_unidades) VALUES(product_id,'CONTRACT RECIPE',2) RETURNING id INTO v_recipe_id;
 INSERT INTO api.receta_ingredientes_costeo(receta_id,ingrediente_id,cantidad_g) VALUES(v_recipe_id,v_ingredient_id,500);
 SELECT * INTO cost_row FROM api.v_receta_costos v WHERE v.receta_id=v_recipe_id;
 IF cost_row.costo_total<>10 OR cost_row.costo_por_unidad<>5 THEN RAISE EXCEPTION 'Recipe cost contract failed: total %, unit %',cost_row.costo_total,cost_row.costo_por_unidad;END IF;
 IF (SELECT count(*) FROM api.ingredient_stock_movements m WHERE m.ingredient_id=v_ingredient_id)<>4 THEN RAISE EXCEPTION 'Expected four immutable ingredient movements';END IF;
END $$;

ROLLBACK;
\echo 'Unified ingredient inventory contract test passed; all ingredients, recipes, lots, and movements rolled back.'
