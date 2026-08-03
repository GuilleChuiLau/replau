\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
 warehouse_id integer;
 product_id integer;
 factor numeric;
 count_result jsonb;
 count_id bigint;
 version integer;
 customer_id integer;
 order_one integer;
 order_two integer;
 item_one integer;
 reservation_id integer;
 expiry_result jsonb;
BEGIN
 SELECT id INTO warehouse_id FROM api.almacenes WHERE active ORDER BY id LIMIT 1;
 SELECT p.id,api.get_producto_factor(p.id,NULL) INTO product_id,factor FROM api.productos p
 WHERE p.active AND NOT EXISTS(SELECT 1 FROM api.stock_reservas sr WHERE sr.producto_id=p.id AND sr.estado='RESERVADA') ORDER BY p.id LIMIT 1;
 IF warehouse_id IS NULL OR product_id IS NULL THEN RAISE EXCEPTION 'Contract test needs an unreserved active product and warehouse';END IF;

 count_result:=api.create_inventory_count(warehouse_id,'CYCLE','contract-counter','Availability baseline');count_id:=(count_result->>'session_id')::bigint;
 PERFORM api.set_inventory_count_quantity(count_id,product_id,10,'contract-counter');
 count_result:=api.submit_inventory_count(count_id,'contract-counter','Availability baseline',1);version:=(count_result->>'version')::integer;
 PERFORM api.approve_inventory_count(count_id,'contract-manager','Availability baseline',version);
 PERFORM api.configure_inventory_stock_control(warehouse_id,product_id,true,0,30,'contract-manager','Rollback-only enforcement test');
 PERFORM api.assert_inventory_availability(warehouse_id,product_id,10,NULL);
 BEGIN
  PERFORM api.assert_inventory_availability(warehouse_id,product_id,10.001,NULL);
  RAISE EXCEPTION 'Expected insufficient-stock protection';
 EXCEPTION WHEN OTHERS THEN
  IF SQLERRM='Expected insufficient-stock protection' THEN RAISE;END IF;
 END;

 INSERT INTO api.clientes_whatsapp(whatsapp_number,nombre) VALUES('+51999000001-contract','Contract Test') RETURNING id INTO customer_id;
 INSERT INTO api.pedidos(pedido_num,cliente_id,canal,estado,metodo_pago,subtotal,delivery,total,observacion)
 VALUES('CONTRACT-ATP-ONE',customer_id,'MANUAL','BORRADOR','CONTRA_ENTREGA',0,0,0,'Rollback only') RETURNING id INTO order_one;
 INSERT INTO api.pedido_items(pedido_id,producto_id,producto_texto,cantidad,unidad,precio_unitario,total_linea)
 VALUES(order_one,product_id,'Contract product',5/factor,NULL,0,0) RETURNING id INTO item_one;
 UPDATE api.pedidos SET estado='CONFIRMADO' WHERE id=order_one;
 SELECT id INTO reservation_id FROM api.stock_reservas WHERE pedido_item_id=item_one AND estado='RESERVADA';
 IF reservation_id IS NULL THEN RAISE EXCEPTION 'Expected enforced reservation';END IF;

 INSERT INTO api.pedidos(pedido_num,cliente_id,canal,estado,metodo_pago,subtotal,delivery,total,observacion)
 VALUES('CONTRACT-ATP-TWO',customer_id,'MANUAL','BORRADOR','CONTRA_ENTREGA',0,0,0,'Rollback only') RETURNING id INTO order_two;
 INSERT INTO api.pedido_items(pedido_id,producto_id,producto_texto,cantidad,unidad,precio_unitario,total_linea)
 VALUES(order_two,product_id,'Contract product',6/factor,NULL,0,0);
 BEGIN
  UPDATE api.pedidos SET estado='CONFIRMADO' WHERE id=order_two;
  RAISE EXCEPTION 'Expected order-confirmation stock protection';
 EXCEPTION WHEN OTHERS THEN
  IF SQLERRM='Expected order-confirmation stock protection' THEN RAISE;END IF;
 END;

 UPDATE api.stock_reservas SET expires_at=now()-interval '1 minute' WHERE id=reservation_id;
 expiry_result:=api.expire_stale_stock_reservations(now(),'contract-expiry',true);
 IF (expiry_result->>'candidate_count')::integer<1 OR (expiry_result->>'released_count')::integer<>0 THEN RAISE EXCEPTION 'Dry-run expiry contract failed: %',expiry_result;END IF;
 expiry_result:=api.expire_stale_stock_reservations(now(),'contract-expiry',false);
 IF (expiry_result->>'released_count')::integer<1 THEN RAISE EXCEPTION 'Expiry release contract failed: %',expiry_result;END IF;
 UPDATE api.pedidos SET estado='EN_PREPARACION' WHERE id=order_one;
 IF NOT EXISTS(SELECT 1 FROM api.stock_reservas WHERE id=reservation_id AND estado='RESERVADA' AND expired_at IS NULL) THEN RAISE EXCEPTION 'Expired reservation was not safely revalidated';END IF;
END $$;

ROLLBACK;
\echo 'Inventory availability contract test passed; all orders, reservations, counts, and movements rolled back.'
