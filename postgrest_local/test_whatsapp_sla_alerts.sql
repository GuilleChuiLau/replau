BEGIN;
DO $$ DECLARE rid bigint; a jsonb; b jsonb; c jsonb;
BEGIN
 INSERT INTO api.whatsapp_conversation_requests(channel_id,customer_address,status_updated_by,last_inbound_at)
 VALUES('sla-contract','51999999999','contract-test',now()-interval '12 minutes') RETURNING id INTO rid;
 a:=api.evaluate_whatsapp_sla_alerts(10,15,30,false,now());
 IF (a->>'created')::integer<1 OR (SELECT level FROM api.whatsapp_sla_alerts WHERE request_id=rid AND status='ACTIVE')<>'WARNING' THEN RAISE EXCEPTION 'Warning failed: %',a; END IF;
 b:=api.evaluate_whatsapp_sla_alerts(10,15,30,false,now()+interval '4 minutes');
 IF (b->>'escalated')::integer<1 OR (SELECT level FROM api.whatsapp_sla_alerts WHERE request_id=rid AND status='ACTIVE')<>'URGENT' THEN RAISE EXCEPTION 'Urgent escalation failed: %',b; END IF;
 UPDATE api.whatsapp_conversation_requests SET is_unread=false WHERE id=rid;
 c:=api.evaluate_whatsapp_sla_alerts(10,15,30,false,now()+interval '5 minutes');
 IF (c->>'cleared')::integer<1 OR EXISTS(SELECT 1 FROM api.whatsapp_sla_alerts WHERE request_id=rid AND status='ACTIVE') THEN RAISE EXCEPTION 'Clear failed: %',c; END IF;
END $$;
ROLLBACK;
