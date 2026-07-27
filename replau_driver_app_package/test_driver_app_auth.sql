BEGIN;

DO $$
DECLARE
    v_account_id integer;
    v_result jsonb;
BEGIN
    SELECT id INTO v_account_id FROM api.driver_accounts ORDER BY id LIMIT 1;
    IF v_account_id IS NULL THEN
        RAISE EXCEPTION 'Driver auth contract test requires one driver account';
    END IF;

    INSERT INTO api.driver_auth_credentials(driver_account_id, pin_salt, pin_hash)
    VALUES (v_account_id, repeat('a', 32), repeat('b', 64))
    ON CONFLICT (driver_account_id) DO UPDATE
    SET pin_salt = EXCLUDED.pin_salt,
        pin_hash = EXCLUDED.pin_hash,
        failed_attempts = 0,
        locked_until = NULL;

    v_result := api.driver_record_auth_attempt(v_account_id, false, 'test-ip', 'contract-test');
    IF v_result->>'error' <> 'INVALID_PIN' THEN
        RAISE EXCEPTION 'Expected INVALID_PIN, got %', v_result;
    END IF;

    v_result := api.driver_record_auth_attempt(v_account_id, true, 'test-ip', 'contract-test');
    IF COALESCE((v_result->>'ok')::boolean, false) IS NOT TRUE THEN
        RAISE EXCEPTION 'Expected successful reset, got %', v_result;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM api.driver_auth_events
        WHERE driver_account_id = v_account_id AND event_type = 'LOGIN_SUCCEEDED'
    ) THEN
        RAISE EXCEPTION 'Successful login audit event missing';
    END IF;
END;
$$;

ROLLBACK;
