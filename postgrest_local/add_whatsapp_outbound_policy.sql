BEGIN;

CREATE TABLE IF NOT EXISTS api.whatsapp_outbound_policy (
    id boolean PRIMARY KEY DEFAULT true CHECK(id),
    state text NOT NULL DEFAULT 'PAUSED' CHECK(state IN ('ACTIVE','PAUSED','TRIPPED')),
    state_reason text NOT NULL DEFAULT 'Initial safe deployment pause',
    hourly_limit integer NOT NULL DEFAULT 40 CHECK(hourly_limit BETWEEN 1 AND 1000),
    daily_limit integer NOT NULL DEFAULT 150 CHECK(daily_limit BETWEEN 1 AND 10000),
    recipient_hourly_limit integer NOT NULL DEFAULT 6 CHECK(recipient_hourly_limit BETWEEN 1 AND 100),
    session_hours integer NOT NULL DEFAULT 24 CHECK(session_hours BETWEEN 1 AND 72),
    coalesce_seconds integer NOT NULL DEFAULT 120 CHECK(coalesce_seconds BETWEEN 0 AND 3600),
    failure_trip_threshold integer NOT NULL DEFAULT 3 CHECK(failure_trip_threshold BETWEEN 1 AND 20),
    consecutive_failures integer NOT NULL DEFAULT 0,
    tripped_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL DEFAULT 'migration'
);

INSERT INTO api.whatsapp_outbound_policy(id)
VALUES(true)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS api.whatsapp_contact_policy (
    whatsapp_number text PRIMARY KEY,
    consent_status text NOT NULL DEFAULT 'UNKNOWN'
        CHECK(consent_status IN ('UNKNOWN','OPTED_IN','OPTED_OUT')),
    last_inbound_at timestamptz,
    opted_in_at timestamptz,
    opted_out_at timestamptz,
    opt_source text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO api.whatsapp_contact_policy(whatsapp_number, last_inbound_at)
SELECT
    regexp_replace(whatsapp_number, '\D', '', 'g'),
    max(created_at)
FROM api.whatsapp_mensajes
WHERE direction = 'INBOUND'
  AND regexp_replace(whatsapp_number, '\D', '', 'g') <> ''
GROUP BY regexp_replace(whatsapp_number, '\D', '', 'g')
ON CONFLICT(whatsapp_number) DO UPDATE
SET last_inbound_at = greatest(
    api.whatsapp_contact_policy.last_inbound_at,
    EXCLUDED.last_inbound_at
);

CREATE TABLE IF NOT EXISTS api.whatsapp_policy_events (
    id bigserial PRIMARY KEY,
    outbox_id integer REFERENCES api.whatsapp_outbox(id) ON DELETE SET NULL,
    recipient_suffix text,
    event_type text,
    decision text NOT NULL CHECK(decision IN (
        'ALLOW','PAUSED','CIRCUIT_TRIPPED','OPTED_OUT','SESSION_EXPIRED',
        'ACCOUNT_HOURLY_LIMIT','ACCOUNT_DAILY_LIMIT','RECIPIENT_HOURLY_LIMIT',
        'DRY_RUN','SEND_SUCCEEDED','SEND_FAILED','COALESCED'
    )),
    reason text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_policy_events_created
ON api.whatsapp_policy_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_whatsapp_policy_events_outbox
ON api.whatsapp_policy_events(outbox_id, created_at DESC);

ALTER TABLE api.whatsapp_outbox
    ADD COLUMN IF NOT EXISTS policy_decision text,
    ADD COLUMN IF NOT EXISTS policy_reason text,
    ADD COLUMN IF NOT EXISTS policy_evaluated_at timestamptz,
    ADD COLUMN IF NOT EXISTS not_before timestamptz;

CREATE INDEX IF NOT EXISTS idx_whatsapp_outbox_policy_pending
ON api.whatsapp_outbox(status, not_before, created_at);

CREATE OR REPLACE FUNCTION api.record_whatsapp_policy_inbound(
    p_whatsapp_number text,
    p_message_text text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_number text := regexp_replace(COALESCE(p_whatsapp_number, ''), '\D', '', 'g');
    v_message text := upper(trim(COALESCE(p_message_text, '')));
    v_status text;
    v_now timestamptz := now();
BEGIN
    IF length(v_number) < 8 THEN
        RAISE EXCEPTION 'Invalid WhatsApp number';
    END IF;
    v_status := CASE
        WHEN v_message IN ('STOP','BAJA','SALIR','CANCELAR SUSCRIPCION','NO MENSAJES') THEN 'OPTED_OUT'
        WHEN v_message IN ('START','ALTA','INICIAR','ACEPTO MENSAJES') THEN 'OPTED_IN'
        ELSE NULL
    END;

    INSERT INTO api.whatsapp_contact_policy(
        whatsapp_number,
        consent_status,
        last_inbound_at,
        opted_in_at,
        opted_out_at,
        opt_source,
        updated_at
    )
    VALUES(
        v_number,
        COALESCE(v_status, 'UNKNOWN'),
        v_now,
        CASE WHEN v_status = 'OPTED_IN' THEN v_now END,
        CASE WHEN v_status = 'OPTED_OUT' THEN v_now END,
        CASE WHEN v_status IS NOT NULL THEN 'inbound_keyword' END,
        v_now
    )
    ON CONFLICT(whatsapp_number) DO UPDATE
    SET last_inbound_at = v_now,
        consent_status = COALESCE(v_status, api.whatsapp_contact_policy.consent_status),
        opted_in_at = CASE WHEN v_status = 'OPTED_IN' THEN v_now ELSE api.whatsapp_contact_policy.opted_in_at END,
        opted_out_at = CASE WHEN v_status = 'OPTED_OUT' THEN v_now ELSE api.whatsapp_contact_policy.opted_out_at END,
        opt_source = CASE WHEN v_status IS NOT NULL THEN 'inbound_keyword' ELSE api.whatsapp_contact_policy.opt_source END,
        updated_at = v_now;

    IF v_status IS DISTINCT FROM 'OPTED_OUT' THEN
        UPDATE api.whatsapp_outbox
        SET not_before = NULL,
            policy_decision = NULL,
            policy_reason = NULL
        WHERE regexp_replace(whatsapp_number, '\D', '', 'g') = v_number
          AND status = 'PENDING'
          AND policy_decision = 'SESSION_EXPIRED';
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'whatsapp_number', v_number,
        'consent_status', COALESCE(v_status, (
            SELECT consent_status FROM api.whatsapp_contact_policy WHERE whatsapp_number = v_number
        )),
        'last_inbound_at', v_now
    );
END;
$$;

CREATE OR REPLACE FUNCTION api.evaluate_whatsapp_outbound_policy(
    p_outbox_id integer,
    p_record boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_row api.whatsapp_outbox%ROWTYPE;
    v_policy api.whatsapp_outbound_policy%ROWTYPE;
    v_contact api.whatsapp_contact_policy%ROWTYPE;
    v_number text;
    v_decision text := 'ALLOW';
    v_reason text := 'Policy checks passed';
    v_retry integer := 0;
    v_hourly integer;
    v_daily integer;
    v_recipient_hourly integer;
BEGIN
    SELECT * INTO v_row FROM api.whatsapp_outbox WHERE id = p_outbox_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Outbox row not found'; END IF;
    SELECT * INTO v_policy FROM api.whatsapp_outbound_policy WHERE id = true;
    IF NOT FOUND THEN RAISE EXCEPTION 'WhatsApp outbound policy is not configured'; END IF;

    v_number := regexp_replace(COALESCE(v_row.whatsapp_number, ''), '\D', '', 'g');
    SELECT * INTO v_contact FROM api.whatsapp_contact_policy WHERE whatsapp_number = v_number;

    SELECT count(*) INTO v_hourly
    FROM api.whatsapp_outbox
    WHERE status = 'SENT' AND sent_at >= now() - interval '1 hour';
    SELECT count(*) INTO v_daily
    FROM api.whatsapp_outbox
    WHERE status = 'SENT' AND sent_at >= now() - interval '24 hours';
    SELECT count(*) INTO v_recipient_hourly
    FROM api.whatsapp_outbox
    WHERE status = 'SENT'
      AND sent_at >= now() - interval '1 hour'
      AND regexp_replace(whatsapp_number, '\D', '', 'g') = v_number;

    IF v_policy.state = 'PAUSED' THEN
        v_decision := 'PAUSED'; v_reason := v_policy.state_reason; v_retry := 900;
    ELSIF v_policy.state = 'TRIPPED' THEN
        v_decision := 'CIRCUIT_TRIPPED'; v_reason := v_policy.state_reason; v_retry := 1800;
    ELSIF v_contact.consent_status = 'OPTED_OUT' THEN
        v_decision := 'OPTED_OUT'; v_reason := 'Recipient opted out'; v_retry := 0;
    ELSIF v_contact.last_inbound_at IS NULL
       OR v_contact.last_inbound_at < now() - make_interval(hours => v_policy.session_hours) THEN
        v_decision := 'SESSION_EXPIRED'; v_reason := 'No active customer-initiated service window'; v_retry := 3600;
    ELSIF v_hourly >= v_policy.hourly_limit THEN
        v_decision := 'ACCOUNT_HOURLY_LIMIT'; v_reason := 'Account hourly limit reached'; v_retry := 900;
    ELSIF v_daily >= v_policy.daily_limit THEN
        v_decision := 'ACCOUNT_DAILY_LIMIT'; v_reason := 'Account daily limit reached'; v_retry := 3600;
    ELSIF v_recipient_hourly >= v_policy.recipient_hourly_limit THEN
        v_decision := 'RECIPIENT_HOURLY_LIMIT'; v_reason := 'Recipient hourly limit reached'; v_retry := 900;
    END IF;

    IF p_record THEN
        INSERT INTO api.whatsapp_policy_events(
            outbox_id, recipient_suffix, event_type, decision, reason, details
        )
        VALUES(
            v_row.id, right(v_number,4), v_row.event_type, v_decision, v_reason,
            jsonb_build_object(
                'account_hourly_count', v_hourly,
                'account_daily_count', v_daily,
                'recipient_hourly_count', v_recipient_hourly,
                'last_inbound_at', v_contact.last_inbound_at,
                'policy_state', v_policy.state
            )
        );
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'allowed', v_decision = 'ALLOW',
        'decision', v_decision,
        'reason', v_reason,
        'retry_after_seconds', v_retry,
        'policy_state', v_policy.state,
        'account_hourly_count', v_hourly,
        'account_daily_count', v_daily,
        'recipient_hourly_count', v_recipient_hourly
    );
END;
$$;

CREATE OR REPLACE FUNCTION api.record_whatsapp_delivery_result(
    p_outbox_id integer,
    p_succeeded boolean,
    p_error text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_policy api.whatsapp_outbound_policy%ROWTYPE;
    v_row api.whatsapp_outbox%ROWTYPE;
    v_failures integer;
BEGIN
    SELECT * INTO v_row FROM api.whatsapp_outbox WHERE id = p_outbox_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Outbox row not found'; END IF;
    SELECT * INTO v_policy FROM api.whatsapp_outbound_policy WHERE id = true FOR UPDATE;

    IF p_succeeded THEN
        UPDATE api.whatsapp_outbound_policy
        SET consecutive_failures = 0, updated_at = now(), updated_by = 'outbox-worker'
        WHERE id = true
        RETURNING * INTO v_policy;
        INSERT INTO api.whatsapp_policy_events(outbox_id,recipient_suffix,event_type,decision,reason)
        VALUES(v_row.id,right(regexp_replace(v_row.whatsapp_number,'\D','','g'),4),v_row.event_type,'SEND_SUCCEEDED','Outbound adapter confirmed delivery');
    ELSE
        v_failures := v_policy.consecutive_failures + 1;
        UPDATE api.whatsapp_outbound_policy
        SET consecutive_failures = v_failures,
            state = CASE WHEN v_failures >= failure_trip_threshold THEN 'TRIPPED' ELSE state END,
            state_reason = CASE
                WHEN v_failures >= failure_trip_threshold THEN 'Automatic trip after consecutive delivery failures'
                ELSE state_reason
            END,
            tripped_at = CASE WHEN v_failures >= failure_trip_threshold THEN now() ELSE tripped_at END,
            updated_at = now(),
            updated_by = 'outbox-worker'
        WHERE id = true
        RETURNING * INTO v_policy;
        INSERT INTO api.whatsapp_policy_events(outbox_id,recipient_suffix,event_type,decision,reason,details)
        VALUES(v_row.id,right(regexp_replace(v_row.whatsapp_number,'\D','','g'),4),v_row.event_type,'SEND_FAILED','Outbound adapter delivery failed',jsonb_build_object('error',left(COALESCE(p_error,''),500),'consecutive_failures',v_failures));
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'state', v_policy.state,
        'consecutive_failures', v_policy.consecutive_failures
    );
END;
$$;

CREATE OR REPLACE FUNCTION api.record_whatsapp_coalesced(
    p_cancelled_outbox_id integer,
    p_retained_outbox_id integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE v_old api.whatsapp_outbox%ROWTYPE; v_new api.whatsapp_outbox%ROWTYPE;
BEGIN
    SELECT * INTO v_old FROM api.whatsapp_outbox WHERE id=p_cancelled_outbox_id FOR UPDATE;
    SELECT * INTO v_new FROM api.whatsapp_outbox WHERE id=p_retained_outbox_id;
    IF v_old.id IS NULL OR v_new.id IS NULL THEN RAISE EXCEPTION 'Outbox row not found'; END IF;
    IF v_old.status <> 'PENDING' THEN RETURN jsonb_build_object('ok',true,'unchanged',true); END IF;
    IF regexp_replace(v_old.whatsapp_number,'\D','','g') <> regexp_replace(v_new.whatsapp_number,'\D','','g')
       OR v_old.pedido_id IS DISTINCT FROM v_new.pedido_id THEN
        RAISE EXCEPTION 'Coalesced rows must share recipient and order';
    END IF;
    UPDATE api.whatsapp_outbox
    SET status='CANCELLED',policy_decision='COALESCED',
        policy_reason='Superseded by outbox '||v_new.id,
        policy_evaluated_at=now(),error_message='Coalesced into newer delivery update'
    WHERE id=v_old.id;
    INSERT INTO api.whatsapp_policy_events(outbox_id,recipient_suffix,event_type,decision,reason,details)
    VALUES(v_old.id,right(regexp_replace(v_old.whatsapp_number,'\D','','g'),4),v_old.event_type,'COALESCED','Superseded by a newer delivery update',jsonb_build_object('retained_outbox_id',v_new.id));
    RETURN jsonb_build_object('ok',true,'cancelled_outbox_id',v_old.id,'retained_outbox_id',v_new.id);
END;
$$;

CREATE OR REPLACE FUNCTION api.set_whatsapp_outbound_policy_state(
    p_state text,
    p_reason text,
    p_actor text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE v_state text := upper(trim(COALESCE(p_state,''))); v_policy api.whatsapp_outbound_policy%ROWTYPE;
BEGIN
    IF v_state NOT IN ('ACTIVE','PAUSED') THEN RAISE EXCEPTION 'State must be ACTIVE or PAUSED'; END IF;
    IF length(trim(COALESCE(p_reason,''))) NOT BETWEEN 3 AND 300 THEN RAISE EXCEPTION 'Reason is required'; END IF;
    IF length(trim(COALESCE(p_actor,''))) NOT BETWEEN 1 AND 80 THEN RAISE EXCEPTION 'Actor is required'; END IF;
    UPDATE api.whatsapp_outbound_policy
    SET state=v_state,state_reason=trim(p_reason),consecutive_failures=0,
        tripped_at=CASE WHEN v_state='ACTIVE' THEN NULL ELSE tripped_at END,
        updated_at=now(),updated_by=trim(p_actor)
    WHERE id=true RETURNING * INTO v_policy;
    RETURN jsonb_build_object('ok',true,'state',v_policy.state,'reason',v_policy.state_reason,'updated_by',v_policy.updated_by);
END;
$$;

GRANT SELECT ON api.whatsapp_outbound_policy, api.whatsapp_contact_policy, api.whatsapp_policy_events TO web_anon;
GRANT EXECUTE ON FUNCTION api.record_whatsapp_policy_inbound(text,text) TO web_anon;
GRANT EXECUTE ON FUNCTION api.evaluate_whatsapp_outbound_policy(integer,boolean) TO web_anon;
GRANT EXECUTE ON FUNCTION api.record_whatsapp_delivery_result(integer,boolean,text) TO web_anon;
GRANT EXECUTE ON FUNCTION api.record_whatsapp_coalesced(integer,integer) TO web_anon;
GRANT EXECUTE ON FUNCTION api.set_whatsapp_outbound_policy_state(text,text,text) TO web_anon;

NOTIFY pgrst, 'reload schema';
COMMIT;
