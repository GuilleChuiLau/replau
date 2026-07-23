BEGIN;
DO $$ DECLARE rid bigint; a jsonb; b jsonb;
BEGIN
 INSERT INTO api.whatsapp_conversation_requests(channel_id,customer_address,status_updated_by) VALUES('staff-reply-contract','51999999999','contract-test') RETURNING id INTO rid;
 a:=api.enqueue_whatsapp_staff_reply(rid,'contract-test','Safe synthetic reply','contract-test-key-00000001');
 b:=api.enqueue_whatsapp_staff_reply(rid,'contract-test','Safe synthetic reply','contract-test-key-00000001');
 IF NOT (a->>'ok')::boolean OR (a->>'duplicate')::boolean THEN RAISE EXCEPTION 'First enqueue failed: %',a; END IF;
 IF NOT (b->>'duplicate')::boolean THEN RAISE EXCEPTION 'Duplicate not suppressed: %',b; END IF;
 IF (SELECT count(*) FROM api.whatsapp_outbox WHERE conversation_request_id=rid)<>1 THEN RAISE EXCEPTION 'Wrong outbox count'; END IF;
 IF (SELECT status FROM api.whatsapp_conversation_requests WHERE id=rid)<>'IN_PROGRESS' THEN RAISE EXCEPTION 'Request not in progress'; END IF;
END $$;
ROLLBACK;
