\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
  supplier_id integer;
  warehouse_id integer;
  product_id integer;
  barcode_value text;
  factor numeric;
  other_barcode text;
  created jsonb;
  changed jsonb;
  order_id integer;
  session_id bigint;
BEGIN
  SELECT id INTO supplier_id FROM api.proveedores WHERE active ORDER BY id LIMIT 1;
  SELECT id INTO warehouse_id FROM api.almacenes WHERE active ORDER BY id LIMIT 1;
  SELECT b.producto_id,b.barcode,b.unit_factor
    INTO product_id,barcode_value,factor
  FROM api.product_barcodes b
  JOIN api.productos p ON p.id=b.producto_id
  WHERE b.active AND p.active
  ORDER BY b.id LIMIT 1;

  IF supplier_id IS NULL OR warehouse_id IS NULL OR product_id IS NULL THEN
    RAISE EXCEPTION 'Contract test needs one active supplier, warehouse, product, and barcode';
  END IF;

  created:=api.create_purchase_order(supplier_id,current_date+7,'PEN','contract-test','Rolled back contract test');
  order_id:=(created->>'order_id')::integer;
  changed:=api.add_purchase_order_line(order_id,product_id,factor*2,10,0,0,'contract-test');
  changed:=api.transition_purchase_order(order_id,'APPROVE','contract-test',(changed->>'version')::integer,NULL);
  changed:=api.transition_purchase_order(order_id,'SEND','contract-test',(changed->>'version')::integer,NULL);

  BEGIN
    PERFORM api.transition_purchase_order(order_id,'CANCEL','contract-test',1,'stale version check');
    RAISE EXCEPTION 'Expected stale-version protection';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM='Expected stale-version protection' THEN RAISE; END IF;
  END;

  created:=api.create_po_inventory_receiving(order_id,warehouse_id,'contract-test','Rolled back contract test');
  session_id:=(created->>'session_id')::bigint;
  changed:=api.scan_inventory_receiving(session_id,barcode_value,1,'contract-test',NULL,NULL);
  IF NOT COALESCE((changed->>'ok')::boolean,false) OR (changed->>'purchase_order_line_id') IS NULL THEN
    RAISE EXCEPTION 'Expected accepted, PO-matched scan: %',changed;
  END IF;

  BEGIN
    PERFORM api.scan_inventory_receiving(session_id,barcode_value,2,'contract-test',NULL,NULL);
    RAISE EXCEPTION 'Expected over-receipt protection';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM='Expected over-receipt protection' THEN RAISE; END IF;
  END;

  SELECT barcode INTO other_barcode
  FROM api.product_barcodes
  WHERE active AND producto_id<>product_id
  ORDER BY id LIMIT 1;
  IF other_barcode IS NOT NULL THEN
    BEGIN
      PERFORM api.scan_inventory_receiving(session_id,other_barcode,1,'contract-test',NULL,NULL);
      RAISE EXCEPTION 'Expected wrong-product protection';
    EXCEPTION WHEN OTHERS THEN
      IF SQLERRM='Expected wrong-product protection' THEN RAISE; END IF;
    END;
  END IF;

  changed:=api.void_inventory_receiving(session_id,'contract-test','Contract test cleanup');
  IF changed->>'status'<>'VOIDED' THEN RAISE EXCEPTION 'Expected voided session: %',changed; END IF;
END $$;

ROLLBACK;

\echo 'Procurement contract test passed; all test data rolled back.'
