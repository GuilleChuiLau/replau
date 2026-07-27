BEGIN;

DO $$
DECLARE
    v_proof_id integer;
    v_analysis jsonb;
    v_duplicate jsonb;
    v_review jsonb;
    v_analysis_id bigint;
    v_update_blocked boolean := false;
BEGIN
    SELECT id INTO v_proof_id
    FROM api.pedido_payment_proofs
    ORDER BY id DESC
    LIMIT 1;
    IF v_proof_id IS NULL THEN
        RAISE EXCEPTION 'A payment proof fixture is required';
    END IF;

    v_analysis := api.record_payment_proof_ocr_analysis(
        v_proof_id,
        jsonb_build_object(
            'sha256', repeat('a', 64),
            'perceptual_hash', repeat('b', 16),
            'cache_version', 999,
            'engine', 'contract-test',
            'ocr_passes', jsonb_build_array('fixture'),
            'ocr_confidence', 0.91,
            'fields', jsonb_build_object('provider', 'TEST', 'amount', 10),
            'field_confidence', jsonb_build_object('amount', 0.91),
            'checks', jsonb_build_object('amount_match', true),
            'review_reasons', '[]'::jsonb,
            'recommendation', 'MANUAL_REVIEW',
            'advisory_only', true,
            'analyzed_at', now()
        ),
        false
    );
    v_analysis_id := (v_analysis->>'analysis_id')::bigint;
    IF NOT COALESCE((v_analysis->>'created')::boolean, false) THEN
        RAISE EXCEPTION 'OCR audit snapshot was not created: %', v_analysis;
    END IF;

    v_duplicate := api.record_payment_proof_ocr_analysis(
        v_proof_id,
        jsonb_build_object(
            'sha256', repeat('a', 64),
            'cache_version', 999,
            'engine', 'contract-test',
            'ocr_confidence', 0.91,
            'recommendation', 'MANUAL_REVIEW',
            'advisory_only', true
        ),
        false
    );
    IF COALESCE((v_duplicate->>'created')::boolean, true)
       OR v_duplicate->>'analysis_id' <> v_analysis->>'analysis_id' THEN
        RAISE EXCEPTION 'OCR snapshot deduplication failed: %', v_duplicate;
    END IF;

    v_review := api.revisar_comprobante_pago_auditado(
        v_proof_id,
        v_analysis_id,
        'CANCELLED',
        'contract-test',
        'Rollback-only OCR audit contract',
        false
    );
    IF v_review->>'review_event_id' IS NULL
       OR v_review->>'ocr_analysis_id' <> v_analysis_id::text THEN
        RAISE EXCEPTION 'Audited review linkage failed: %', v_review;
    END IF;

    BEGIN
        UPDATE api.payment_proof_ocr_analyses
        SET recommendation = 'REVIEW_OR_REJECT'
        WHERE id = v_analysis_id;
    EXCEPTION WHEN OTHERS THEN
        v_update_blocked := true;
    END;
    IF NOT v_update_blocked THEN
        RAISE EXCEPTION 'OCR audit snapshot was mutable';
    END IF;
END;
$$;

ROLLBACK;
