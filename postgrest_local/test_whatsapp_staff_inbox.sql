\set ON_ERROR_STOP on

BEGIN;

INSERT INTO api.whatsapp_conversation_requests (
    channel_id, customer_address, sender_name, first_message_text, last_message_text,
    status, first_inbound_at, last_inbound_at, status_updated_at, created_at, updated_at
) VALUES (
    'staff-inbox-contract', '51000000011', 'Contract Customer', 'hola', 'menu',
    'AUTO_STARTED', now() - interval '20 minutes', now() - interval '20 minutes', now(), now(), now()
);

SELECT api.update_whatsapp_request_inbox(
    (SELECT id FROM api.whatsapp_conversation_requests WHERE channel_id = 'staff-inbox-contract'),
    'TAKE', 'contract-operator', NULL, NULL, 'Customer asked about delivery'
);
SELECT api.update_whatsapp_request_inbox(
    (SELECT id FROM api.whatsapp_conversation_requests WHERE channel_id = 'staff-inbox-contract'),
    'PRIORITY', 'contract-operator', NULL, 'URGENT', NULL
);
SELECT api.update_whatsapp_request_inbox(
    (SELECT id FROM api.whatsapp_conversation_requests WHERE channel_id = 'staff-inbox-contract'),
    'CLOSE', 'contract-operator', NULL, NULL, NULL
);

DO $$
DECLARE
    v_row api.whatsapp_conversation_requests%ROWTYPE;
    v_note_count integer;
    v_event_count integer;
BEGIN
    SELECT * INTO v_row FROM api.whatsapp_conversation_requests WHERE channel_id = 'staff-inbox-contract';
    IF v_row.status <> 'CLOSED' OR v_row.is_unread OR v_row.assigned_to <> 'contract-operator'
       OR v_row.priority <> 'URGENT' OR v_row.first_staff_action_at IS NULL OR v_row.resolved_at IS NULL
       OR v_row.version <> 4 THEN
        RAISE EXCEPTION 'staff inbox lifecycle state is invalid';
    END IF;
    SELECT count(*) INTO v_note_count FROM api.whatsapp_request_notes WHERE request_id = v_row.id;
    SELECT count(*) INTO v_event_count FROM api.whatsapp_request_events WHERE request_id = v_row.id;
    IF v_note_count <> 1 OR v_event_count <> 3 THEN RAISE EXCEPTION 'staff inbox audit records are invalid'; END IF;
    IF NOT EXISTS (SELECT 1 FROM api.v_whatsapp_request_inbox WHERE id = v_row.id AND note_count = 1) THEN
        RAISE EXCEPTION 'staff inbox view did not expose the request';
    END IF;
END $$;

ROLLBACK;
