BEGIN;

DO $$
DECLARE
    v_outbox_id integer;
    v_number text := '51999999001';
    v_result jsonb;
BEGIN
    INSERT INTO api.whatsapp_outbox(whatsapp_number,message_text,event_type,status,idempotency_key)
    VALUES(v_number,'Rollback-only policy fixture','CUSTOM','PENDING','policy-contract-fixture-0001')
    RETURNING id INTO v_outbox_id;

    v_result := api.evaluate_whatsapp_outbound_policy(v_outbox_id,false);
    IF v_result->>'decision' <> 'PAUSED' OR COALESCE((v_result->>'allowed')::boolean,true) THEN
        RAISE EXCEPTION 'Initial policy was not safely paused: %',v_result;
    END IF;

    PERFORM api.record_whatsapp_policy_inbound(v_number,'START');
    PERFORM api.set_whatsapp_outbound_policy_state('ACTIVE','Rollback-only contract activation','contract-test');
    v_result := api.evaluate_whatsapp_outbound_policy(v_outbox_id,false);
    IF NOT COALESCE((v_result->>'allowed')::boolean,false) THEN
        RAISE EXCEPTION 'Active session was not allowed: %',v_result;
    END IF;

    PERFORM api.record_whatsapp_policy_inbound(v_number,'STOP');
    v_result := api.evaluate_whatsapp_outbound_policy(v_outbox_id,false);
    IF v_result->>'decision' <> 'OPTED_OUT' THEN
        RAISE EXCEPTION 'Opt-out was not enforced: %',v_result;
    END IF;

    PERFORM api.record_whatsapp_delivery_result(v_outbox_id,false,'synthetic failure 1');
    PERFORM api.record_whatsapp_delivery_result(v_outbox_id,false,'synthetic failure 2');
    v_result := api.record_whatsapp_delivery_result(v_outbox_id,false,'synthetic failure 3');
    IF v_result->>'state' <> 'TRIPPED' THEN
        RAISE EXCEPTION 'Circuit breaker did not trip: %',v_result;
    END IF;
END;
$$;

ROLLBACK;
