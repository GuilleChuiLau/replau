BEGIN;
CREATE TABLE IF NOT EXISTS api.whatsapp_sla_alerts(
 id bigserial PRIMARY KEY,
 request_id bigint NOT NULL REFERENCES api.whatsapp_conversation_requests(id) ON DELETE CASCADE,
 level text NOT NULL CHECK(level IN('WARNING','URGENT')),
 status text NOT NULL DEFAULT 'ACTIVE' CHECK(status IN('ACTIVE','CLEARED')),
 first_alerted_at timestamptz NOT NULL DEFAULT now(),
 last_notified_at timestamptz NOT NULL DEFAULT now(),
 repeat_count integer NOT NULL DEFAULT 0 CHECK(repeat_count>=0),
 cleared_at timestamptz,
 clear_reason text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_sla_active_request ON api.whatsapp_sla_alerts(request_id) WHERE status='ACTIVE';
CREATE INDEX IF NOT EXISTS idx_whatsapp_sla_active_level ON api.whatsapp_sla_alerts(status,level,last_notified_at);

CREATE OR REPLACE VIEW api.v_whatsapp_sla_alerts AS
SELECT a.*,r.sender_name,r.assigned_to,r.priority,r.status AS request_status,r.is_unread,
 GREATEST(0,floor(extract(epoch FROM(now()-r.last_inbound_at))/60))::integer AS wait_minutes,
 r.last_inbound_at,r.sla_due_at
FROM api.whatsapp_sla_alerts a JOIN api.whatsapp_conversation_requests r ON r.id=a.request_id;

CREATE OR REPLACE FUNCTION api.evaluate_whatsapp_sla_alerts(
 p_warning_minutes integer DEFAULT 10,p_urgent_minutes integer DEFAULT 15,
 p_cooldown_minutes integer DEFAULT 30,p_quiet boolean DEFAULT false,p_now timestamptz DEFAULT now())
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE rec record; existing api.whatsapp_sla_alerts%ROWTYPE; wanted text; notices jsonb:='[]'::jsonb; created_count integer:=0; escalated_count integer:=0; repeated_count integer:=0; cleared_count integer:=0;
BEGIN
 IF p_warning_minutes NOT BETWEEN 1 AND 120 THEN RAISE EXCEPTION 'warning minutes out of range'; END IF;
 IF p_urgent_minutes NOT BETWEEN p_warning_minutes+1 AND 240 THEN RAISE EXCEPTION 'urgent minutes out of range'; END IF;
 IF p_cooldown_minutes NOT BETWEEN 5 AND 1440 THEN RAISE EXCEPTION 'cooldown minutes out of range'; END IF;

 UPDATE api.whatsapp_sla_alerts a SET status='CLEARED',cleared_at=p_now,
  clear_reason=CASE WHEN r.status NOT IN('AUTO_STARTED','IN_PROGRESS') THEN 'REQUEST_CLOSED' ELSE 'STAFF_RESPONDED' END,
  updated_at=p_now
 FROM api.whatsapp_conversation_requests r
 WHERE a.request_id=r.id AND a.status='ACTIVE' AND (r.status NOT IN('AUTO_STARTED','IN_PROGRESS') OR r.is_unread=false);
 GET DIAGNOSTICS cleared_count=ROW_COUNT;

 IF NOT p_quiet THEN
  FOR rec IN SELECT r.id,r.sender_name,r.assigned_to,
    floor(extract(epoch FROM(p_now-r.last_inbound_at))/60)::integer AS wait_minutes
   FROM api.whatsapp_conversation_requests r
   WHERE r.status IN('AUTO_STARTED','IN_PROGRESS') AND r.is_unread=true
     AND r.last_inbound_at<=p_now-make_interval(mins=>p_warning_minutes)
   ORDER BY r.last_inbound_at ASC
  LOOP
   wanted:=CASE WHEN rec.wait_minutes>=p_urgent_minutes THEN 'URGENT' ELSE 'WARNING' END;
   SELECT * INTO existing FROM api.whatsapp_sla_alerts WHERE request_id=rec.id AND status='ACTIVE' FOR UPDATE;
   IF NOT FOUND THEN
    INSERT INTO api.whatsapp_sla_alerts(request_id,level,status,first_alerted_at,last_notified_at)
    VALUES(rec.id,wanted,'ACTIVE',p_now,p_now);
    created_count:=created_count+1;
    notices:=notices||jsonb_build_array(jsonb_build_object('request_id',rec.id,'level',wanted,'wait_minutes',rec.wait_minutes,'sender_name',rec.sender_name,'assigned_to',rec.assigned_to,'kind','NEW'));
    INSERT INTO api.whatsapp_request_events(request_id,event_type,actor,details,created_at) VALUES(rec.id,'SLA_'||wanted,'sla-monitor',jsonb_build_object('wait_minutes',rec.wait_minutes),p_now);
   ELSIF existing.level='WARNING' AND wanted='URGENT' THEN
    UPDATE api.whatsapp_sla_alerts SET level='URGENT',last_notified_at=p_now,updated_at=p_now WHERE id=existing.id;
    escalated_count:=escalated_count+1;
    notices:=notices||jsonb_build_array(jsonb_build_object('request_id',rec.id,'level','URGENT','wait_minutes',rec.wait_minutes,'sender_name',rec.sender_name,'assigned_to',rec.assigned_to,'kind','ESCALATED'));
    INSERT INTO api.whatsapp_request_events(request_id,event_type,actor,details,created_at) VALUES(rec.id,'SLA_URGENT','sla-monitor',jsonb_build_object('wait_minutes',rec.wait_minutes,'escalated',true),p_now);
   ELSIF existing.last_notified_at<=p_now-make_interval(mins=>p_cooldown_minutes) THEN
    UPDATE api.whatsapp_sla_alerts SET last_notified_at=p_now,repeat_count=repeat_count+1,updated_at=p_now WHERE id=existing.id;
    repeated_count:=repeated_count+1;
    notices:=notices||jsonb_build_array(jsonb_build_object('request_id',rec.id,'level',existing.level,'wait_minutes',rec.wait_minutes,'sender_name',rec.sender_name,'assigned_to',rec.assigned_to,'kind','REPEAT'));
   END IF;
  END LOOP;
 END IF;
 RETURN jsonb_build_object('ok',true,'quiet',p_quiet,'created',created_count,'escalated',escalated_count,'repeated',repeated_count,'cleared',cleared_count,'notifications',notices,'evaluated_at',p_now);
END $$;
GRANT SELECT ON api.v_whatsapp_sla_alerts TO web_anon;
GRANT EXECUTE ON FUNCTION api.evaluate_whatsapp_sla_alerts(integer,integer,integer,boolean,timestamptz) TO web_anon;
NOTIFY pgrst,'reload schema';
COMMIT;
