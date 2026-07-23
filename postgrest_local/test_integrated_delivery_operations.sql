BEGIN;
DO $$ DECLARE aid integer; pid integer; result jsonb;
BEGIN
 SELECT a.id,a.pedido_id INTO aid,pid FROM api.delivery_asignaciones a JOIN api.payment_fulfillments pf ON pf.pedido_id=a.pedido_id WHERE a.status='COMPLETED' LIMIT 1;
 IF aid IS NULL THEN RAISE EXCEPTION 'A completed assignment fixture is required'; END IF;
 UPDATE api.delivery_asignaciones SET status='ASSIGNED',picked_up_at=NULL,en_route_at=NULL,arrived_at=NULL,completed_at=NULL WHERE id=aid;
 result:=api.update_delivery_operation(aid,'PICKUP','contract-test',NULL,NULL,NULL,'delivery-contract-pickup-0001'); IF result->>'status'<>'PICKED_UP' THEN RAISE EXCEPTION 'Pickup failed: %',result; END IF;
 result:=api.update_delivery_operation(aid,'EN_ROUTE','contract-test',NULL,NULL,NULL,'delivery-contract-route-00001'); IF result->>'status'<>'EN_ROUTE' THEN RAISE EXCEPTION 'Route failed: %',result; END IF;
 result:=api.update_delivery_operation(aid,'ARRIVE','contract-test',NULL,NULL,NULL,'delivery-contract-arrive-0001'); IF result->>'status'<>'ARRIVED' THEN RAISE EXCEPTION 'Arrival failed: %',result; END IF;
 result:=api.update_delivery_operation(aid,'FAIL','contract-test','Synthetic failure',NULL,NULL,'delivery-contract-fail-000001'); IF result->>'status'<>'FAILED' THEN RAISE EXCEPTION 'Failure failed: %',result; END IF;
 result:=api.update_delivery_operation(aid,'REOPEN','contract-test',NULL,NULL,NULL,'delivery-contract-reopen-0001'); IF result->>'status'<>'ASSIGNED' THEN RAISE EXCEPTION 'Reopen failed: %',result; END IF;
 IF (SELECT count(*) FROM api.delivery_operation_events WHERE assignment_id=aid)<5 THEN RAISE EXCEPTION 'Missing audit events'; END IF;
END $$;
ROLLBACK;
