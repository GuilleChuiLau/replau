\set ON_ERROR_STOP on

BEGIN;

INSERT INTO api.whatsapp_conversation_requests (
    channel_id, customer_address, sender_name, first_message_text, last_message_text,
    first_provider_message_id, last_provider_message_id, status,
    first_inbound_at, last_inbound_at, status_updated_at, created_at, updated_at
) VALUES
    ('retention-contract', '51000000001', 'Old active', 'first', 'last', 'first-1', 'last-1',
     'AUTO_STARTED', '1999-01-01', '1999-01-01', '1999-01-01', '1999-01-01', '1999-01-01'),
    ('retention-contract', '51000000002', 'Recent active', 'first', 'last', 'first-2', 'last-2',
     'IN_PROGRESS', '1999-12-20', '1999-12-20', '1999-12-20', '1999-12-20', '1999-12-20'),
    ('retention-contract', '51000000003', 'Closed redacted', 'first', 'last', 'first-3', 'last-3',
     'CLOSED', '1999-12-01', '1999-12-01', '1999-12-01', '1999-12-01', '1999-12-01'),
    ('retention-contract', '51000000004', 'Closed deleted', 'first', 'last', 'first-4', 'last-4',
     'BLOCKED', '1999-01-01', '1999-01-01', '1999-01-01', '1999-01-01', '1999-01-01');

SELECT api.apply_whatsapp_conversation_request_retention(30, 7, 90, '2000-01-01'::timestamptz);

DO $$
DECLARE
    v_old_active api.whatsapp_conversation_requests%ROWTYPE;
    v_recent_active api.whatsapp_conversation_requests%ROWTYPE;
    v_closed api.whatsapp_conversation_requests%ROWTYPE;
BEGIN
    SELECT * INTO v_old_active FROM api.whatsapp_conversation_requests
     WHERE channel_id = 'retention-contract' AND customer_address = '51000000001';
    IF NOT FOUND OR v_old_active.last_message_text IS NOT NULL OR v_old_active.sender_name IS NOT NULL THEN
        RAISE EXCEPTION 'stale active request was not safely redacted and retained';
    END IF;

    SELECT * INTO v_recent_active FROM api.whatsapp_conversation_requests
     WHERE channel_id = 'retention-contract' AND customer_address = '51000000002';
    IF NOT FOUND OR v_recent_active.last_message_text <> 'last' THEN
        RAISE EXCEPTION 'recent active request was unexpectedly changed';
    END IF;

    SELECT * INTO v_closed FROM api.whatsapp_conversation_requests
     WHERE channel_id = 'retention-contract' AND customer_address = '51000000003';
    IF NOT FOUND OR v_closed.last_provider_message_id IS NOT NULL OR v_closed.sender_name IS NOT NULL THEN
        RAISE EXCEPTION 'closed request was not redacted and retained';
    END IF;

    IF EXISTS (
        SELECT 1 FROM api.whatsapp_conversation_requests
         WHERE channel_id = 'retention-contract' AND customer_address = '51000000004'
    ) THEN
        RAISE EXCEPTION 'expired blocked request was not deleted';
    END IF;
END $$;

ROLLBACK;
