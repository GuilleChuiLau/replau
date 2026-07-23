BEGIN;
ALTER TABLE api.whatsapp_outbox
 ADD COLUMN IF NOT EXISTS conversation_request_id bigint REFERENCES api.whatsapp_conversation_requests(id) ON DELETE SET NULL,
 ADD COLUMN IF NOT EXISTS staff_actor text,
 ADD COLUMN IF NOT EXISTS idempotency_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_outbox_idempotency_key ON api.whatsapp_outbox(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_whatsapp_outbox_conversation_request ON api.whatsapp_outbox(conversation_request_id,created_at DESC);
ALTER TABLE api.whatsapp_outbox DROP CONSTRAINT IF EXISTS whatsapp_outbox_event_type_check;
ALTER TABLE api.whatsapp_outbox ADD CONSTRAINT whatsapp_outbox_event_type_check CHECK (event_type IN ('KITCHEN_EN_PREPARACION','KITCHEN_LISTO','KITCHEN_ENTREGADO','KITCHEN_ANULADO','CUSTOM','STAFF_REPLY'));

CREATE TABLE IF NOT EXISTS api.whatsapp_canned_replies(
 id bigserial PRIMARY KEY, code text NOT NULL UNIQUE CHECK(code ~ '^[a-z0-9_-]{2,40}$'),
 label text NOT NULL CHECK(length(trim(label)) BETWEEN 2 AND 80),
 message_text text NOT NULL CHECK(length(trim(message_text)) BETWEEN 1 AND 2000),
 active boolean NOT NULL DEFAULT true, sort_order integer NOT NULL DEFAULT 100,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
INSERT INTO api.whatsapp_canned_replies(code,label,message_text,sort_order) VALUES
 ('welcome','Saludo','¡Hola! Gracias por escribirnos. ¿En qué podemos ayudarte?',10),
 ('menu','Menú','Con gusto. Puedes revisar nuestro menú y hacer tu pedido aquí: https://orders.replau.com',20),
 ('hours','Horario','Gracias por escribirnos. Enseguida confirmamos nuestro horario de atención para hoy.',30),
 ('delivery','Entrega','Sí realizamos entregas. Envíanos tu ubicación o dirección para confirmar cobertura y costo.',40),
 ('payment','Pago','Aceptamos los medios de pago disponibles al finalizar tu pedido. Si ya pagaste, envíanos el comprobante para revisarlo.',50),
 ('order_status','Estado del pedido','Estamos revisando el estado de tu pedido y te confirmamos en un momento.',60)
ON CONFLICT(code) DO NOTHING;

CREATE OR REPLACE FUNCTION api.enqueue_whatsapp_staff_reply(p_request_id bigint,p_actor text,p_message_text text,p_idempotency_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE v_request api.whatsapp_conversation_requests%ROWTYPE; v_actor text:=trim(COALESCE(p_actor,'')); v_message text:=trim(COALESCE(p_message_text,'')); v_key text:=trim(COALESCE(p_idempotency_key,'')); v_outbox_id integer; v_existing integer; v_now timestamptz:=now();
BEGIN
 IF length(v_actor) NOT BETWEEN 1 AND 80 OR v_actor !~ '^[[:alnum:] ._@-]+$' THEN RAISE EXCEPTION 'Invalid actor'; END IF;
 IF length(v_message) NOT BETWEEN 1 AND 2000 THEN RAISE EXCEPTION 'Message must be between 1 and 2000 characters'; END IF;
 IF length(v_key) NOT BETWEEN 16 AND 120 OR v_key !~ '^[A-Za-z0-9._:-]+$' THEN RAISE EXCEPTION 'Invalid idempotency key'; END IF;
 SELECT id INTO v_existing FROM api.whatsapp_outbox WHERE idempotency_key=v_key;
 IF FOUND THEN RETURN jsonb_build_object('ok',true,'duplicate',true,'outbox_id',v_existing); END IF;
 SELECT * INTO v_request FROM api.whatsapp_conversation_requests WHERE id=p_request_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Conversation request not found'; END IF;
 IF v_request.consent_basis<>'USER_INITIATED' THEN RAISE EXCEPTION 'Conversation lacks user-initiated consent'; END IF;
 IF v_request.status='BLOCKED' THEN RAISE EXCEPTION 'Blocked conversations cannot receive replies'; END IF;
 IF v_request.customer_address !~ '^[0-9]{8,20}$' THEN RAISE EXCEPTION 'Invalid WhatsApp customer address'; END IF;
 IF (SELECT count(*) FROM api.whatsapp_outbox WHERE conversation_request_id=p_request_id AND event_type='STAFF_REPLY' AND created_at>=v_now-interval '5 minutes')>=10 THEN RAISE EXCEPTION 'Reply rate limit exceeded'; END IF;
 INSERT INTO api.whatsapp_outbox(whatsapp_number,message_text,event_type,status,conversation_request_id,staff_actor,idempotency_key)
 VALUES(v_request.customer_address,v_message,'STAFF_REPLY','PENDING',p_request_id,v_actor,v_key) RETURNING id INTO v_outbox_id;
 UPDATE api.whatsapp_conversation_requests SET status='IN_PROGRESS',is_unread=false,assigned_to=COALESCE(assigned_to,v_actor),assigned_at=COALESCE(assigned_at,v_now),first_staff_action_at=COALESCE(first_staff_action_at,v_now),resolved_at=NULL,status_updated_at=v_now,status_updated_by=v_actor,updated_at=v_now,version=version+1 WHERE id=p_request_id;
 INSERT INTO api.whatsapp_request_events(request_id,event_type,actor,from_status,to_status,details,created_at)
 VALUES(p_request_id,'REPLY_QUEUED',v_actor,v_request.status,'IN_PROGRESS',jsonb_build_object('outbox_id',v_outbox_id,'idempotency_key',v_key),v_now);
 RETURN jsonb_build_object('ok',true,'duplicate',false,'outbox_id',v_outbox_id,'status','PENDING');
EXCEPTION WHEN unique_violation THEN SELECT id INTO v_existing FROM api.whatsapp_outbox WHERE idempotency_key=v_key; RETURN jsonb_build_object('ok',true,'duplicate',true,'outbox_id',v_existing);
END $$;

CREATE OR REPLACE VIEW api.v_whatsapp_request_replies AS
SELECT o.id,o.conversation_request_id,o.message_text,o.staff_actor,o.status,o.attempts,o.created_at,o.sent_at,o.error_message
FROM api.whatsapp_outbox o WHERE o.event_type='STAFF_REPLY';
GRANT SELECT ON api.whatsapp_canned_replies,api.v_whatsapp_request_replies TO web_anon;
GRANT EXECUTE ON FUNCTION api.enqueue_whatsapp_staff_reply(bigint,text,text,text) TO web_anon;
NOTIFY pgrst,'reload schema';
COMMIT;
