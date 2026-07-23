BEGIN;
ALTER TABLE api.delivery_asignaciones DROP CONSTRAINT IF EXISTS delivery_asignaciones_status_check;
ALTER TABLE api.delivery_asignaciones ADD CONSTRAINT delivery_asignaciones_status_check CHECK(status IN('OFFERED','ACCEPTED','REJECTED','EXPIRED','ASSIGNED','PICKED_UP','EN_ROUTE','ARRIVED','COMPLETED','FAILED','CANCELLED'));
ALTER TABLE api.delivery_asignaciones
 ADD COLUMN IF NOT EXISTS priority text NOT NULL DEFAULT 'NORMAL',
 ADD COLUMN IF NOT EXISTS promised_at timestamptz,
 ADD COLUMN IF NOT EXISTS picked_up_at timestamptz,
 ADD COLUMN IF NOT EXISTS en_route_at timestamptz,
 ADD COLUMN IF NOT EXISTS arrived_at timestamptz,
 ADD COLUMN IF NOT EXISTS failed_at timestamptz,
 ADD COLUMN IF NOT EXISTS failure_reason text,
 ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='api.delivery_asignaciones'::regclass AND conname='delivery_assignment_priority_check') THEN ALTER TABLE api.delivery_asignaciones ADD CONSTRAINT delivery_assignment_priority_check CHECK(priority IN('NORMAL','HIGH','URGENT')); END IF; END $$;

CREATE TABLE IF NOT EXISTS api.delivery_operation_events(
 id bigserial PRIMARY KEY,assignment_id integer NOT NULL REFERENCES api.delivery_asignaciones(id) ON DELETE CASCADE,
 pedido_id integer NOT NULL REFERENCES api.pedidos(id) ON DELETE CASCADE,event_type text NOT NULL,actor text NOT NULL,
 from_status text,to_status text,details jsonb NOT NULL DEFAULT '{}'::jsonb,idempotency_key text UNIQUE,created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_delivery_operation_events_assignment ON api.delivery_operation_events(assignment_id,created_at DESC);
CREATE TABLE IF NOT EXISTS api.delivery_incidents(
 id bigserial PRIMARY KEY,assignment_id integer NOT NULL REFERENCES api.delivery_asignaciones(id) ON DELETE CASCADE,
 pedido_id integer NOT NULL REFERENCES api.pedidos(id) ON DELETE CASCADE,status text NOT NULL DEFAULT 'OPEN' CHECK(status IN('OPEN','RESOLVED')),
 reason text NOT NULL,opened_by text NOT NULL,opened_at timestamptz NOT NULL DEFAULT now(),resolved_by text,resolved_at timestamptz,notes text);
CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_open_incident ON api.delivery_incidents(assignment_id) WHERE status='OPEN';

ALTER TABLE api.whatsapp_outbox DROP CONSTRAINT IF EXISTS whatsapp_outbox_event_type_check;
ALTER TABLE api.whatsapp_outbox ADD CONSTRAINT whatsapp_outbox_event_type_check CHECK(event_type IN(
 'KITCHEN_EN_PREPARACION','KITCHEN_LISTO','KITCHEN_ENTREGADO','KITCHEN_ANULADO','CUSTOM','STAFF_REPLY',
 'DELIVERY_ASSIGNED','DELIVERY_PICKED_UP','DELIVERY_EN_ROUTE','DELIVERY_ARRIVED','DELIVERY_DELIVERED','DELIVERY_FAILED'));

CREATE OR REPLACE FUNCTION api.queue_delivery_assigned_customer_update() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE ord record; outbox_key text;
BEGIN
 IF NEW.status='ASSIGNED' AND (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
  SELECT p.pedido_num,p.cliente_id,c.whatsapp_number,r.nombre AS driver_name INTO ord
  FROM api.pedidos p JOIN api.clientes_whatsapp c ON c.id=p.cliente_id LEFT JOIN api.repartidores r ON r.id=NEW.repartidor_id WHERE p.id=NEW.pedido_id;
  IF ord.whatsapp_number IS NOT NULL THEN outbox_key:='delivery:'||NEW.id||':assigned';
   INSERT INTO api.whatsapp_outbox(pedido_id,cliente_id,whatsapp_number,message_text,event_type,status,idempotency_key)
   VALUES(NEW.pedido_id,ord.cliente_id,ord.whatsapp_number,'Asignamos un repartidor a tu pedido '||ord.pedido_num||' 🛵'||CASE WHEN ord.driver_name IS NOT NULL THEN E'\n\nRepartidor: '||ord.driver_name ELSE '' END,'DELIVERY_ASSIGNED','PENDING',outbox_key)
   ON CONFLICT(idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING;
  END IF;
  INSERT INTO api.delivery_operation_events(assignment_id,pedido_id,event_type,actor,from_status,to_status,details,idempotency_key)
  VALUES(NEW.id,NEW.pedido_id,'DELIVERY_ASSIGNED','dispatch',CASE WHEN TG_OP='UPDATE' THEN OLD.status ELSE NULL END,'ASSIGNED',jsonb_build_object('repartidor_id',NEW.repartidor_id),'delivery-assigned-'||NEW.id)
  ON CONFLICT(idempotency_key) DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS queue_delivery_assigned_customer_update ON api.delivery_asignaciones;
CREATE TRIGGER queue_delivery_assigned_customer_update AFTER INSERT OR UPDATE OF status ON api.delivery_asignaciones FOR EACH ROW EXECUTE FUNCTION api.queue_delivery_assigned_customer_update();

CREATE OR REPLACE FUNCTION api.update_delivery_operation(p_assignment_id integer,p_action text,p_actor text,p_reason text DEFAULT NULL,p_priority text DEFAULT NULL,p_promised_at timestamptz DEFAULT NULL,p_idempotency_key text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE a api.delivery_asignaciones%ROWTYPE; action text:=upper(trim(COALESCE(p_action,''))); actor text:=trim(COALESCE(p_actor,'')); reason text:=NULLIF(trim(COALESCE(p_reason,'')),''); pri text:=NULLIF(upper(trim(COALESCE(p_priority,''))),''); ikey text:=trim(COALESCE(p_idempotency_key,'')); target text; now_at timestamptz:=now(); ord record; msg text; event_name text; outbox_key text; request_id bigint;
BEGIN
 IF action NOT IN('PICKUP','EN_ROUTE','ARRIVE','DELIVER','FAIL','REOPEN','SET_PRIORITY','SET_PROMISE') THEN RAISE EXCEPTION 'Unsupported delivery action'; END IF;
 IF length(actor) NOT BETWEEN 1 AND 80 OR actor !~ '^[[:alnum:] ._@-]+$' THEN RAISE EXCEPTION 'Invalid actor'; END IF;
 IF length(ikey) NOT BETWEEN 16 AND 120 OR ikey !~ '^[A-Za-z0-9._:-]+$' THEN RAISE EXCEPTION 'Invalid idempotency key'; END IF;
 IF EXISTS(SELECT 1 FROM api.delivery_operation_events WHERE idempotency_key=ikey) THEN RETURN jsonb_build_object('ok',true,'duplicate',true); END IF;
 IF action='FAIL' AND (reason IS NULL OR length(reason)>500) THEN RAISE EXCEPTION 'Failure reason is required'; END IF;
 IF action='SET_PRIORITY' AND pri NOT IN('NORMAL','HIGH','URGENT') THEN RAISE EXCEPTION 'Invalid priority'; END IF;
 IF action='SET_PROMISE' AND p_promised_at IS NULL THEN RAISE EXCEPTION 'Promised time is required'; END IF;
 SELECT * INTO a FROM api.delivery_asignaciones WHERE id=p_assignment_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Assignment not found'; END IF;
 SELECT p.id,p.pedido_num,p.cliente_id,p.metodo_pago,p.estado,p.order_url,c.whatsapp_number,pf.status AS payment_status
 INTO ord FROM api.pedidos p JOIN api.clientes_whatsapp c ON c.id=p.cliente_id LEFT JOIN api.payment_fulfillments pf ON pf.pedido_id=p.id WHERE p.id=a.pedido_id;
 IF action='PICKUP' AND a.status NOT IN('ASSIGNED','ACCEPTED') THEN RAISE EXCEPTION 'Pickup is not allowed from %',a.status; END IF;
 IF action='EN_ROUTE' AND a.status NOT IN('ASSIGNED','ACCEPTED','PICKED_UP') THEN RAISE EXCEPTION 'En route is not allowed from %',a.status; END IF;
 IF action='ARRIVE' AND a.status<>'EN_ROUTE' THEN RAISE EXCEPTION 'Arrival is not allowed from %',a.status; END IF;
 IF action='DELIVER' AND a.status NOT IN('PICKED_UP','EN_ROUTE','ARRIVED') THEN RAISE EXCEPTION 'Delivery is not allowed from %',a.status; END IF;
 IF action='DELIVER' AND ((upper(ord.metodo_pago)='CONTRA_ENTREGA' AND COALESCE(ord.payment_status,'') NOT IN('COD_COLLECTED','RECONCILED','SETTLED')) OR (upper(ord.metodo_pago)<>'CONTRA_ENTREGA' AND COALESCE(ord.payment_status,'') NOT IN('RELEASED','RECONCILED','SETTLED'))) THEN RAISE EXCEPTION 'Payment is not cleared for delivery completion'; END IF;
 IF action='FAIL' AND a.status NOT IN('OFFERED','ACCEPTED','ASSIGNED','PICKED_UP','EN_ROUTE','ARRIVED') THEN RAISE EXCEPTION 'Failure is not allowed from %',a.status; END IF;
 IF action='REOPEN' AND a.status NOT IN('FAILED','CANCELLED') THEN RAISE EXCEPTION 'Reopen is not allowed from %',a.status; END IF;
 target:=CASE action WHEN 'PICKUP' THEN 'PICKED_UP' WHEN 'EN_ROUTE' THEN 'EN_ROUTE' WHEN 'ARRIVE' THEN 'ARRIVED' WHEN 'DELIVER' THEN 'COMPLETED' WHEN 'FAIL' THEN 'FAILED' WHEN 'REOPEN' THEN 'ASSIGNED' ELSE a.status END;
 UPDATE api.delivery_asignaciones SET status=target,priority=CASE WHEN action='SET_PRIORITY' THEN pri ELSE priority END,promised_at=CASE WHEN action='SET_PROMISE' THEN p_promised_at ELSE promised_at END,
  picked_up_at=CASE WHEN action='PICKUP' THEN now_at WHEN action='EN_ROUTE' THEN COALESCE(picked_up_at,now_at) ELSE picked_up_at END,en_route_at=CASE WHEN action='EN_ROUTE' THEN now_at ELSE en_route_at END,arrived_at=CASE WHEN action='ARRIVE' THEN now_at ELSE arrived_at END,
  completed_at=CASE WHEN action='DELIVER' THEN now_at WHEN action='REOPEN' THEN NULL ELSE completed_at END,failed_at=CASE WHEN action='FAIL' THEN now_at WHEN action='REOPEN' THEN NULL ELSE failed_at END,
  failure_reason=CASE WHEN action='FAIL' THEN reason WHEN action='REOPEN' THEN NULL ELSE failure_reason END,updated_at=now_at,version=version+1 WHERE id=a.id;
 IF action='EN_ROUTE' THEN UPDATE api.pedidos SET estado='DESPACHADO',updated_at=now_at WHERE id=a.pedido_id; END IF;
 IF action='DELIVER' THEN UPDATE api.pedidos SET estado='ENTREGADO',updated_at=now_at WHERE id=a.pedido_id; END IF;
 IF action='FAIL' THEN
  INSERT INTO api.delivery_incidents(assignment_id,pedido_id,reason,opened_by) VALUES(a.id,a.pedido_id,reason,actor) ON CONFLICT DO NOTHING;
  SELECT id INTO request_id FROM api.whatsapp_conversation_requests WHERE customer_address=ord.whatsapp_number ORDER BY last_inbound_at DESC LIMIT 1;
  IF request_id IS NOT NULL THEN UPDATE api.whatsapp_conversation_requests SET priority='URGENT',is_unread=true,status=CASE WHEN status='CLOSED' THEN 'AUTO_STARTED' ELSE status END,resolved_at=NULL,updated_at=now_at,version=version+1 WHERE id=request_id;
   INSERT INTO api.whatsapp_request_events(request_id,event_type,actor,details,created_at) VALUES(request_id,'DELIVERY_EXCEPTION',actor,jsonb_build_object('pedido_id',a.pedido_id,'assignment_id',a.id,'reason',reason),now_at); END IF;
 END IF;
 IF action='REOPEN' THEN UPDATE api.delivery_incidents SET status='RESOLVED',resolved_by=actor,resolved_at=now_at WHERE assignment_id=a.id AND status='OPEN'; END IF;
 event_name:='DELIVERY_'||action;
 INSERT INTO api.delivery_operation_events(assignment_id,pedido_id,event_type,actor,from_status,to_status,details,idempotency_key,created_at)
 VALUES(a.id,a.pedido_id,event_name,actor,a.status,target,jsonb_strip_nulls(jsonb_build_object('reason',reason,'priority',pri,'promised_at',p_promised_at)),ikey,now_at);
 msg:=CASE action
  WHEN 'PICKUP' THEN 'Tu pedido '||ord.pedido_num||' fue recogido y pronto saldrá a ruta 🛵'
  WHEN 'EN_ROUTE' THEN 'Tu pedido '||ord.pedido_num||' está en camino 🛵'||CASE WHEN ord.order_url IS NOT NULL THEN E'\n\nTracking: '||replace(ord.order_url,'/order/','/track/') ELSE '' END
  WHEN 'ARRIVE' THEN 'El repartidor de tu pedido '||ord.pedido_num||' está llegando 📍'
  WHEN 'DELIVER' THEN 'Tu pedido '||ord.pedido_num||' fue entregado ✅ Gracias por tu compra.'
  WHEN 'FAIL' THEN 'Tuvimos un inconveniente con la entrega de tu pedido '||ord.pedido_num||'. Nuestro equipo ya está revisándolo.' ELSE NULL END;
 IF msg IS NOT NULL AND ord.whatsapp_number IS NOT NULL THEN outbox_key:='delivery:'||a.id||':'||lower(action)||':'||ikey;
  INSERT INTO api.whatsapp_outbox(pedido_id,cliente_id,whatsapp_number,message_text,event_type,status,idempotency_key)
  VALUES(a.pedido_id,ord.cliente_id,ord.whatsapp_number,msg,CASE action WHEN 'PICKUP' THEN 'DELIVERY_PICKED_UP' WHEN 'EN_ROUTE' THEN 'DELIVERY_EN_ROUTE' WHEN 'ARRIVE' THEN 'DELIVERY_ARRIVED' WHEN 'DELIVER' THEN 'DELIVERY_DELIVERED' ELSE 'DELIVERY_FAILED' END,'PENDING',outbox_key) ON CONFLICT(idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING;
 END IF;
 RETURN jsonb_build_object('ok',true,'duplicate',false,'assignment_id',a.id,'from_status',a.status,'status',target,'request_escalated',request_id IS NOT NULL);
END $$;

CREATE OR REPLACE VIEW api.v_delivery_operations AS
SELECT a.id AS assignment_id,a.pedido_id,p.pedido_num,p.estado AS pedido_estado,p.metodo_pago,p.total,c.nombre AS cliente_nombre,c.whatsapp_number,
 p.direccion_confirmada,p.direccion_detectada,p.maps_url,a.repartidor_id,r.codigo AS repartidor_codigo,r.nombre AS repartidor_nombre,a.status,a.priority,a.promised_at,
 a.offered_at,a.assigned_at,a.picked_up_at,a.en_route_at,a.arrived_at,a.completed_at,a.failed_at,a.failure_reason,a.driver_latitude,a.driver_longitude,a.driver_location_at,a.version,
 GREATEST(0,floor(extract(epoch FROM(now()-COALESCE(a.assigned_at,a.offered_at))))/60)::integer AS operation_minutes,
 CASE WHEN a.status IN('OFFERED') AND now()-a.offered_at>=interval '10 minutes' THEN 'URGENT'
      WHEN a.status IN('ACCEPTED','ASSIGNED') AND now()-COALESCE(a.assigned_at,a.responded_at,a.offered_at)>=interval '15 minutes' THEN 'URGENT'
      WHEN a.status IN('PICKED_UP','EN_ROUTE','ARRIVED') AND a.promised_at IS NOT NULL AND now()>a.promised_at THEN 'URGENT'
      WHEN a.status IN('OFFERED') AND now()-a.offered_at>=interval '5 minutes' THEN 'WARNING'
      WHEN a.status IN('ACCEPTED','ASSIGNED') AND now()-COALESCE(a.assigned_at,a.responded_at,a.offered_at)>=interval '10 minutes' THEN 'WARNING' ELSE 'OK' END AS sla_level,
 i.id AS incident_id,i.reason AS incident_reason,i.opened_at AS incident_opened_at,
 (SELECT wr.id FROM api.whatsapp_conversation_requests wr WHERE wr.customer_address=c.whatsapp_number ORDER BY wr.last_inbound_at DESC LIMIT 1) AS conversation_request_id
FROM api.delivery_asignaciones a JOIN api.pedidos p ON p.id=a.pedido_id JOIN api.clientes_whatsapp c ON c.id=p.cliente_id LEFT JOIN api.repartidores r ON r.id=a.repartidor_id LEFT JOIN api.delivery_incidents i ON i.assignment_id=a.id AND i.status='OPEN';

CREATE OR REPLACE VIEW api.v_delivery_asignaciones AS
SELECT a.id,a.pedido_id,p.pedido_num,p.estado AS pedido_estado,a.repartidor_id,r.codigo AS repartidor_codigo,r.nombre AS repartidor_nombre,r.whatsapp_number AS repartidor_whatsapp,
 a.status,a.fee,a.offered_at,a.responded_at,a.assigned_at,a.rejected_at,a.completed_at,a.driver_latitude,a.driver_longitude,a.driver_location_at,a.response_text,a.notes,a.created_at,a.updated_at,
 a.priority,a.promised_at,a.picked_up_at,a.en_route_at,a.arrived_at,a.failed_at,a.failure_reason,a.version,
 CASE WHEN a.status='OFFERED' AND now()-a.offered_at>=interval '10 minutes' THEN 'URGENT'
      WHEN a.status IN('ACCEPTED','ASSIGNED') AND now()-COALESCE(a.assigned_at,a.responded_at,a.offered_at)>=interval '15 minutes' THEN 'URGENT'
      WHEN a.status IN('PICKED_UP','EN_ROUTE','ARRIVED') AND a.promised_at IS NOT NULL AND now()>a.promised_at THEN 'URGENT'
      WHEN a.status='OFFERED' AND now()-a.offered_at>=interval '5 minutes' THEN 'WARNING'
      WHEN a.status IN('ACCEPTED','ASSIGNED') AND now()-COALESCE(a.assigned_at,a.responded_at,a.offered_at)>=interval '10 minutes' THEN 'WARNING' ELSE 'OK' END AS sla_level
FROM api.delivery_asignaciones a JOIN api.pedidos p ON p.id=a.pedido_id LEFT JOIN api.repartidores r ON r.id=a.repartidor_id;

GRANT SELECT ON api.delivery_operation_events,api.delivery_incidents,api.v_delivery_operations,api.v_delivery_asignaciones TO web_anon;
GRANT EXECUTE ON FUNCTION api.update_delivery_operation(integer,text,text,text,text,timestamptz,text) TO web_anon;
NOTIFY pgrst,'reload schema';
COMMIT;
