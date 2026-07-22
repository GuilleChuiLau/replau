-- Privacy retention for the consent-based WhatsApp conversation request queue.

BEGIN;

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
    v_deleted integer := 0;
BEGIN
    IF p_active_redact_days NOT BETWEEN 7 AND 3650 THEN
        RAISE EXCEPTION 'active redact days must be between 7 and 3650';
    END IF;
    IF p_closed_redact_days NOT BETWEEN 1 AND 3650 THEN
        RAISE EXCEPTION 'closed redact days must be between 1 and 3650';
    END IF;
    IF p_delete_days NOT BETWEEN 30 AND 3650 OR p_delete_days <= p_closed_redact_days THEN
        RAISE EXCEPTION 'delete days must be between 30 and 3650 and exceed closed redact days';
    END IF;

    UPDATE api.whatsapp_conversation_requests
    SET sender_name = NULL,
        first_message_text = NULL,
        last_message_text = NULL,
        first_provider_message_id = NULL,
        last_provider_message_id = NULL,
        updated_at = p_now
    WHERE status IN ('AUTO_STARTED', 'IN_PROGRESS')
      AND last_inbound_at < p_now - make_interval(days => p_active_redact_days)
      AND (sender_name IS NOT NULL OR first_message_text IS NOT NULL OR last_message_text IS NOT NULL
           OR first_provider_message_id IS NOT NULL OR last_provider_message_id IS NOT NULL);
    GET DIAGNOSTICS v_active_redacted = ROW_COUNT;

    UPDATE api.whatsapp_conversation_requests
    SET sender_name = NULL,
        first_message_text = NULL,
        last_message_text = NULL,
        first_provider_message_id = NULL,
        last_provider_message_id = NULL,
        updated_at = p_now
    WHERE status IN ('CLOSED', 'BLOCKED')
      AND GREATEST(last_inbound_at, status_updated_at) < p_now - make_interval(days => p_closed_redact_days)
      AND (sender_name IS NOT NULL OR first_message_text IS NOT NULL OR last_message_text IS NOT NULL
           OR first_provider_message_id IS NOT NULL OR last_provider_message_id IS NOT NULL);
    GET DIAGNOSTICS v_closed_redacted = ROW_COUNT;

    DELETE FROM api.whatsapp_conversation_requests
    WHERE status IN ('CLOSED', 'BLOCKED')
      AND GREATEST(last_inbound_at, status_updated_at) < p_now - make_interval(days => p_delete_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    RETURN jsonb_build_object(
        'ok', true,
        'active_redacted', v_active_redacted,
        'closed_redacted', v_closed_redacted,
        'deleted', v_deleted,
        'applied_at', p_now
    );
END;
$$;

COMMENT ON FUNCTION api.apply_whatsapp_conversation_request_retention(integer, integer, integer, timestamptz) IS
'Redacts stale WhatsApp request content and deletes old closed/blocked queue rows using bounded retention periods.';

NOTIFY pgrst, 'reload schema';

COMMIT;
