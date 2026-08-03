\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
 warehouse_id integer;
 product_id integer;
 barcode_value text;
 factor numeric;
 created jsonb;
 changed jsonb;
 session_id bigint;
 current_qty numeric;
 posted_qty numeric;
 movement_count integer;
BEGIN
 SELECT id INTO warehouse_id FROM api.almacenes WHERE active ORDER BY id LIMIT 1;
 SELECT b.producto_id,b.barcode,b.unit_factor INTO product_id,barcode_value,factor
 FROM api.product_barcodes b JOIN api.productos p ON p.id=b.producto_id
 WHERE b.active AND p.active ORDER BY b.id LIMIT 1;
 IF warehouse_id IS NULL OR product_id IS NULL THEN RAISE EXCEPTION 'Contract test needs an active warehouse and barcode';END IF;

 current_qty:=api.inventory_product_quantity(warehouse_id,product_id);
 created:=api.create_inventory_count(warehouse_id,'CYCLE','contract-counter','Rollback-only contract test');
 session_id:=(created->>'session_id')::bigint;
 changed:=api.scan_inventory_count(session_id,barcode_value,1,'contract-counter');
 IF (changed->>'actual_quantity')::numeric<>factor THEN RAISE EXCEPTION 'Scanner factor was not applied: %',changed;END IF;
 changed:=api.submit_inventory_count(session_id,'contract-counter','Physical count complete',1);

 BEGIN
  PERFORM api.approve_inventory_count(session_id,'contract-counter','Reviewed physical count',(changed->>'version')::integer);
  RAISE EXCEPTION 'Expected independent-approver protection';
 EXCEPTION WHEN OTHERS THEN
  IF SQLERRM='Expected independent-approver protection' THEN RAISE;END IF;
 END;

 changed:=api.approve_inventory_count(session_id,'contract-manager','Reviewed physical count',(changed->>'version')::integer);
 IF changed->>'status'<>'POSTED' THEN RAISE EXCEPTION 'Expected posted count: %',changed;END IF;
 posted_qty:=api.inventory_product_quantity(warehouse_id,product_id);
 IF posted_qty<>factor THEN RAISE EXCEPTION 'Expected reconciled quantity %, got %',factor,posted_qty;END IF;
 SELECT count(*) INTO movement_count FROM api.movimientos_stock WHERE doc_tipo='INVENTORY_COUNT' AND doc_id=session_id;
 IF movement_count<>(changed->>'movement_count')::integer THEN RAISE EXCEPTION 'Movement count mismatch';END IF;

 created:=api.create_inventory_count(warehouse_id,'FULL','contract-counter','Completeness contract test');
 session_id:=(created->>'session_id')::bigint;
 PERFORM api.set_inventory_count_quantity(session_id,product_id,posted_qty,'contract-counter');
 BEGIN
  PERFORM api.submit_inventory_count(session_id,'contract-counter','Incomplete full count',1);
  RAISE EXCEPTION 'Expected full-count completeness protection';
 EXCEPTION WHEN OTHERS THEN
  IF SQLERRM='Expected full-count completeness protection' THEN RAISE;END IF;
 END;
 PERFORM api.void_inventory_count(session_id,'contract-manager','Incomplete test count');

 created:=api.create_inventory_count(warehouse_id,'CYCLE','contract-counter','Stale snapshot contract test');
 session_id:=(created->>'session_id')::bigint;
 PERFORM api.set_inventory_count_quantity(session_id,product_id,posted_qty,'contract-counter');
 changed:=api.submit_inventory_count(session_id,'contract-counter','Stale snapshot check',1);
 INSERT INTO api.movimientos_stock(movimiento_tipo,almacen_destino_id,producto_id,cantidad,doc_tipo,doc_id,referencia,observacion)
 VALUES('AJUSTE_POSITIVO',warehouse_id,product_id,1,'CONTRACT_TEST',session_id,'ROLLBACK-ONLY','Stale snapshot contract test');
 BEGIN
  PERFORM api.approve_inventory_count(session_id,'contract-manager','Expected stale rejection',(changed->>'version')::integer);
  RAISE EXCEPTION 'Expected stale-snapshot protection';
 EXCEPTION WHEN OTHERS THEN
  IF SQLERRM='Expected stale-snapshot protection' THEN RAISE;END IF;
 END;
 PERFORM api.void_inventory_count(session_id,'contract-manager','Stale snapshot test');
END $$;

ROLLBACK;
\echo 'Inventory counting contract test passed; all test data and movements rolled back.'
