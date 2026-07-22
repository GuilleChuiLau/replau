-- Operational staff inbox for user-initiated WhatsApp conversation requests.

BEGIN;

ALTER TABLE api.whatsapp_conversation_requests
    ADD COLUMN IF NOT EXISTS is_unread boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS priority text NOT NULL DEFAULT 'NORMAL',
    ADD COLUMN IF NOT EXISTS assigned_to text,
    ADD COLUMN IF NOT EXISTS assigned_at timestamptz,
    ADD COLUMN IF NOT EXISTS first_staff_action_at timestamptz,
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz,
    ADD COLUMN IF NOT EXISTS sla_due_at timestamptz,
    ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'api.whatsapp_conversation_requests'::regclass
          AND conname = 'whatsapp_request_priority_check'
    ) THEN
        ALTER TABLE api.whatsapp_conversation_requests
            ADD CONSTRAINT whatsapp_request_priority_check
            CHECK (priority IN ('NORMAL', 'HIGH', 'URGENT'));
    END IF;
END $$;

UPDATE api.whatsapp_conversation_requests
SET is_unread = status IN ('AUTO_STARTED', 'IN_PROGRESS'),
    resolved_at = CASE WHEN status IN ('CLOSED', 'BLOCKED') THEN status_updated_at END,
    sla_due_at = COALESCE(sla_due_at, first_inbound_at + interval '15 minutes');

CREATE INDEX IF NOT EXISTS idx_whatsapp_requests_inbox
ON api.whatsapp_conversation_requests(status, is_unread, priority, last_inbound_at DESC);

CREATE TABLE IF NOT EXISTS api.whatsapp_request_notes (
    id bigserial PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES api.whatsapp_conversation_requests(id) ON DELETE CASCADE,
    note_text text NOT NULL CHECK (length(trim(note_text)) BETWEEN 1 AND 2000),
    author text NOT NULL CHECK (length(trim(author)) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_request_notes_request
ON api.whatsapp_request_notes(request_id, created_at DESC);

CREATE TABLE IF NOT EXISTS api.whatsapp_request_events (
    id bigserial PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES api.whatsapp_conversation_requests(id) ON DELETE CASCADE,
    event_type text NOT NULL,
    actor text NOT NULL,
    from_status text,
    to_status text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_request_events_request
ON api.whatsapp_request_events(request_id, created_at DESC);

CREATE OR REPLACE FUNCTION api.update_whatsapp_request_inbox(
    p_request_id bigint,
    p_action text,
    p_actor text,
    p_assigned_to text DEFAULT NULL,
    p_priority text DEFAULT NULL,
    p_note text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_row api.whatsapp_conversation_requests%ROWTYPE;
    v_before_status text;
    v_action text := upper(trim(COALESCE(p_action, '')));
    v_actor text := trim(COALESCE(p_actor, ''));
    v_assigned_to text := NULLIF(trim(COALESCE(p_assigned_to, '')), '');
    v_priority text := NULLIF(upper(trim(COALESCE(p_priority, ''))), '');
    v_note text := NULLIF(trim(COALESCE(p_note, '')), '');
    v_now timestamptz := now();
BEGIN
    IF v_action NOT IN ('TAKE', 'ASSIGN', 'PRIORITY', 'NOTE', 'CLOSE', 'BLOCK', 'REOPEN', 'MARK_READ', 'MARK_UNREAD') THEN
        RAISE EXCEPTION 'Unsupported inbox action';
    END IF;
    IF length(v_actor) NOT BETWEEN 1 AND 80 OR v_actor !~ '^[[:alnum:] ._@-]+$' THEN
        RAISE EXCEPTION 'Invalid actor';
    END IF;
    IF v_assigned_to IS NOT NULL AND (length(v_assigned_to) > 80 OR v_assigned_to !~ '^[[:alnum:] ._@-]+$') THEN
        RAISE EXCEPTION 'Invalid assignee';
    END IF;
    IF v_priority IS NOT NULL AND v_priority NOT IN ('NORMAL', 'HIGH', 'URGENT') THEN
        RAISE EXCEPTION 'Invalid priority';
    END IF;
    IF v_note IS NOT NULL AND length(v_note) > 2000 THEN
        RAISE EXCEPTION 'Note is too long';
    END IF;
    IF v_action = 'ASSIGN' AND v_assigned_to IS NULL THEN RAISE EXCEPTION 'Assignee is required'; END IF;
    IF v_action = 'PRIORITY' AND v_priority IS NULL THEN RAISE EXCEPTION 'Priority is required'; END IF;
    IF v_action = 'NOTE' AND v_note IS NULL THEN RAISE EXCEPTION 'Note is required'; END IF;

    SELECT * INTO v_row
    FROM api.whatsapp_conversation_requests
    WHERE id = p_request_id
    FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Conversation request not found'; END IF;
    v_before_status := v_row.status;

    UPDATE api.whatsapp_conversation_requests
    SET status = CASE
            WHEN v_action IN ('TAKE', 'ASSIGN') THEN 'IN_PROGRESS'
            WHEN v_action = 'CLOSE' THEN 'CLOSED'
            WHEN v_action = 'BLOCK' THEN 'BLOCKED'
            WHEN v_action = 'REOPEN' THEN 'AUTO_STARTED'
            ELSE status END,
        is_unread = CASE
            WHEN v_action IN ('TAKE', 'ASSIGN', 'CLOSE', 'BLOCK', 'MARK_READ') THEN false
            WHEN v_action IN ('REOPEN', 'MARK_UNREAD') THEN true
            ELSE is_unread END,
        assigned_to = CASE
            WHEN v_action = 'TAKE' THEN v_actor
            WHEN v_action = 'ASSIGN' THEN v_assigned_to
            ELSE assigned_to END,
        assigned_at = CASE WHEN v_action IN ('TAKE', 'ASSIGN') THEN v_now ELSE assigned_at END,
        priority = CASE WHEN v_action = 'PRIORITY' THEN v_priority ELSE priority END,
        first_staff_action_at = COALESCE(first_staff_action_at, v_now),
        resolved_at = CASE
            WHEN v_action IN ('CLOSE', 'BLOCK') THEN v_now
            WHEN v_action = 'REOPEN' THEN NULL
            ELSE resolved_at END,
        sla_due_at = CASE WHEN v_action = 'REOPEN' THEN v_now + interval '15 minutes' ELSE sla_due_at END,
        status_updated_at = CASE WHEN v_action IN ('TAKE', 'ASSIGN', 'CLOSE', 'BLOCK', 'REOPEN') THEN v_now ELSE status_updated_at END,
        status_updated_by = CASE WHEN v_action IN ('TAKE', 'ASSIGN', 'CLOSE', 'BLOCK', 'REOPEN') THEN v_actor ELSE status_updated_by END,
        updated_at = v_now,
        version = version + 1
    WHERE id = p_request_id
    RETURNING * INTO v_row;

    IF v_note IS NOT NULL THEN
        INSERT INTO api.whatsapp_request_notes(request_id, note_text, author, created_at)
        VALUES (p_request_id, v_note, v_actor, v_now);
    END IF;

    INSERT INTO api.whatsapp_request_events(request_id, event_type, actor, from_status, to_status, details, created_at)
    VALUES (
        p_request_id, v_action, v_actor, v_before_status, v_row.status,
        jsonb_strip_nulls(jsonb_build_object('assigned_to', v_assigned_to, 'priority', v_priority, 'note_added', v_note IS NOT NULL)),
        v_now
    );

    RETURN jsonb_build_object('ok', true, 'request', to_jsonb(v_row));
END;
$$;

CREATE OR REPLACE VIEW api.v_whatsapp_request_inbox AS
SELECT
    r.*,
    CASE WHEN r.status IN ('AUTO_STARTED', 'IN_PROGRESS')
         THEN GREATEST(0, floor(extract(epoch FROM (now() - r.last_inbound_at)) / 60))::integer END AS wait_minutes,
    CASE WHEN r.first_staff_action_at IS NOT NULL
         THEN GREATEST(0, floor(extract(epoch FROM (r.first_staff_action_at - r.first_inbound_at))))::integer END AS response_seconds,
    COALESCE(n.note_count, 0) AS note_count,
    n.latest_note,
    n.latest_note_author,
    n.latest_note_at,
    o.order_id,
    o.pedido_num,
    o.order_status,
    o.order_total,
    o.order_created_at
FROM api.whatsapp_conversation_requests r
LEFT JOIN LATERAL (
    SELECT count(*)::integer AS note_count,
           (array_agg(note_text ORDER BY created_at DESC, id DESC))[1] AS latest_note,
           (array_agg(author ORDER BY created_at DESC, id DESC))[1] AS latest_note_author,
           max(created_at) AS latest_note_at
    FROM api.whatsapp_request_notes
    WHERE request_id = r.id
) n ON true
LEFT JOIN LATERAL (
    SELECT p.id AS order_id, p.pedido_num, p.estado AS order_status,
           p.total AS order_total, p.created_at AS order_created_at
    FROM api.v_pedidos_logistica p
    WHERE p.whatsapp_number = r.customer_address
    ORDER BY p.id DESC
    LIMIT 1
) o ON true;

CREATE OR REPLACE FUNCTION api.register_whatsapp_conversation_request(
    p_channel_kind text,
    p_channel_id text,
    p_account_id text,
    p_customer_address text,
    p_sender_name text DEFAULT NULL,
    p_message_text text DEFAULT NULL,
    p_provider_message_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_row api.whatsapp_conversation_requests%ROWTYPE;
    v_is_new boolean := false;
    v_channel_kind text := lower(trim(COALESCE(p_channel_kind, 'whatsapp')));
    v_channel_id text := trim(COALESCE(p_channel_id, ''));
    v_customer_address text := trim(COALESCE(p_customer_address, ''));
BEGIN
    IF v_channel_kind <> 'whatsapp' THEN RAISE EXCEPTION 'Unsupported channel kind'; END IF;
    IF v_channel_id = '' OR length(v_channel_id) > 120 THEN RAISE EXCEPTION 'Invalid channel id'; END IF;
    IF v_customer_address !~ '^[0-9]{8,20}$' THEN RAISE EXCEPTION 'Invalid WhatsApp customer address'; END IF;

    INSERT INTO api.whatsapp_conversation_requests (
        channel_kind, channel_id, account_id, customer_address, sender_name,
        first_message_text, last_message_text, first_provider_message_id,
        last_provider_message_id, status_updated_by, is_unread, sla_due_at
    ) VALUES (
        v_channel_kind, v_channel_id, NULLIF(trim(p_account_id), ''), v_customer_address,
        NULLIF(left(trim(p_sender_name), 160), ''), left(p_message_text, 2000),
        left(p_message_text, 2000), NULLIF(left(trim(p_provider_message_id), 240), ''),
        NULLIF(left(trim(p_provider_message_id), 240), ''), 'whatsapp-inbound', true, now() + interval '15 minutes'
    )
    ON CONFLICT (channel_kind, channel_id, customer_address) DO NOTHING
    RETURNING * INTO v_row;

    IF FOUND THEN
        v_is_new := true;
    ELSE
        UPDATE api.whatsapp_conversation_requests
        SET account_id = COALESCE(NULLIF(trim(p_account_id), ''), account_id),
            sender_name = COALESCE(NULLIF(left(trim(p_sender_name), 160), ''), sender_name),
            last_message_text = left(p_message_text, 2000),
            last_provider_message_id = COALESCE(NULLIF(left(trim(p_provider_message_id), 240), ''), last_provider_message_id),
            inbound_count = CASE WHEN NULLIF(trim(p_provider_message_id), '') IS NOT NULL
                                  AND last_provider_message_id = trim(p_provider_message_id)
                                 THEN inbound_count ELSE inbound_count + 1 END,
            last_inbound_at = now(), updated_at = now(), is_unread = true,
            status = CASE WHEN status = 'CLOSED' THEN 'AUTO_STARTED' ELSE status END,
            resolved_at = CASE WHEN status = 'CLOSED' THEN NULL ELSE resolved_at END,
            sla_due_at = CASE WHEN status = 'CLOSED' THEN now() + interval '15 minutes' ELSE sla_due_at END,
            status_updated_at = CASE WHEN status = 'CLOSED' THEN now() ELSE status_updated_at END,
            status_updated_by = CASE WHEN status = 'CLOSED' THEN 'whatsapp-inbound' ELSE status_updated_by END,
            version = version + 1
        WHERE channel_kind = v_channel_kind AND channel_id = v_channel_id AND customer_address = v_customer_address
        RETURNING * INTO v_row;
    END IF;

    RETURN jsonb_build_object('ok', true, 'request_id', v_row.id, 'is_new', v_is_new,
                              'status', v_row.status, 'inbound_count', v_row.inbound_count);
END;
$$;

CREATE OR REPLACE FUNCTION api.apply_whatsapp_conversation_request_retention(
    p_active_redact_days integer DEFAULT 30,
    p_closed_redact_days integer DEFAULT 7,
    p_delete_days integer DEFAULT 90,
    p_now timestamptz DEFAULT now()
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_active_redacted integer := 0;
    v_closed_redacted integer := 0;
    v_notes_deleted integer := 0;
    v_deleted integer := 0;
BEGIN
    IF p_active_redact_days NOT BETWEEN 7 AND 3650 THEN RAISE EXCEPTION 'active redact days must be between 7 and 3650'; END IF;
    IF p_closed_redact_days NOT BETWEEN 1 AND 3650 THEN RAISE EXCEPTION 'closed redact days must be between 1 and 3650'; END IF;
    IF p_delete_days NOT BETWEEN 30 AND 3650 OR p_delete_days <= p_closed_redact_days THEN
        RAISE EXCEPTION 'delete days must be between 30 and 3650 and exceed closed redact days';
    END IF;

    DELETE FROM api.whatsapp_request_notes n
    USING api.whatsapp_conversation_requests r
    WHERE n.request_id = r.id AND (
        (r.status IN ('AUTO_STARTED', 'IN_PROGRESS') AND r.last_inbound_at < p_now - make_interval(days => p_active_redact_days))
        OR (r.status IN ('CLOSED', 'BLOCKED') AND GREATEST(r.last_inbound_at, r.status_updated_at) < p_now - make_interval(days => p_closed_redact_days))
    );
    GET DIAGNOSTICS v_notes_deleted = ROW_COUNT;

    UPDATE api.whatsapp_conversation_requests
    SET sender_name = NULL, first_message_text = NULL, last_message_text = NULL,
        first_provider_message_id = NULL, last_provider_message_id = NULL, updated_at = p_now
    WHERE status IN ('AUTO_STARTED', 'IN_PROGRESS')
      AND last_inbound_at < p_now - make_interval(days => p_active_redact_days)
      AND (sender_name IS NOT NULL OR first_message_text IS NOT NULL OR last_message_text IS NOT NULL
           OR first_provider_message_id IS NOT NULL OR last_provider_message_id IS NOT NULL);
    GET DIAGNOSTICS v_active_redacted = ROW_COUNT;

    UPDATE api.whatsapp_conversation_requests
    SET sender_name = NULL, first_message_text = NULL, last_message_text = NULL,
        first_provider_message_id = NULL, last_provider_message_id = NULL, updated_at = p_now
    WHERE status IN ('CLOSED', 'BLOCKED')
      AND GREATEST(last_inbound_at, status_updated_at) < p_now - make_interval(days => p_closed_redact_days)
      AND (sender_name IS NOT NULL OR first_message_text IS NOT NULL OR last_message_text IS NOT NULL
           OR first_provider_message_id IS NOT NULL OR last_provider_message_id IS NOT NULL);
    GET DIAGNOSTICS v_closed_redacted = ROW_COUNT;

    DELETE FROM api.whatsapp_conversation_requests
    WHERE status IN ('CLOSED', 'BLOCKED')
      AND GREATEST(last_inbound_at, status_updated_at) < p_now - make_interval(days => p_delete_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    RETURN jsonb_build_object('ok', true, 'active_redacted', v_active_redacted,
        'closed_redacted', v_closed_redacted, 'notes_deleted', v_notes_deleted,
        'deleted', v_deleted, 'applied_at', p_now);
END;
$$;

GRANT SELECT ON api.v_whatsapp_request_inbox, api.whatsapp_request_notes, api.whatsapp_request_events TO web_anon;
GRANT EXECUTE ON FUNCTION api.update_whatsapp_request_inbox(bigint, text, text, text, text, text) TO web_anon;

NOTIFY pgrst, 'reload schema';

COMMIT;
