BEGIN;

CREATE TABLE IF NOT EXISTS api.payment_proof_ocr_analyses (
    id bigserial PRIMARY KEY,
    proof_id integer NOT NULL REFERENCES api.pedido_payment_proofs(id) ON DELETE RESTRICT,
    analysis_version integer NOT NULL,
    source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
    perceptual_hash text CHECK(perceptual_hash IS NULL OR perceptual_hash ~ '^[a-f0-9]{16}$'),
    cache_version integer NOT NULL,
    engine text NOT NULL,
    ocr_passes jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(ocr_passes) = 'array'),
    ocr_confidence numeric(6,5) NOT NULL CHECK(ocr_confidence BETWEEN 0 AND 1),
    fields jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(fields) = 'object'),
    field_confidence jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(field_confidence) = 'object'),
    checks jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(checks) = 'object'),
    review_reasons jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(review_reasons) = 'array'),
    recommendation text NOT NULL CHECK(recommendation IN ('MANUAL_REVIEW', 'REVIEW_OR_REJECT')),
    advisory_only boolean NOT NULL CHECK(advisory_only = true),
    analyzed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(proof_id, analysis_version)
);

CREATE INDEX IF NOT EXISTS idx_payment_ocr_analyses_proof
ON api.payment_proof_ocr_analyses(proof_id, analysis_version DESC);
CREATE INDEX IF NOT EXISTS idx_payment_ocr_analyses_sha
ON api.payment_proof_ocr_analyses(source_sha256);

CREATE TABLE IF NOT EXISTS api.payment_proof_review_events (
    id bigserial PRIMARY KEY,
    proof_id integer NOT NULL REFERENCES api.pedido_payment_proofs(id) ON DELETE RESTRICT,
    ocr_analysis_id bigint NOT NULL REFERENCES api.payment_proof_ocr_analyses(id) ON DELETE RESTRICT,
    decision text NOT NULL CHECK(decision IN ('VERIFIED', 'REJECTED', 'CANCELLED')),
    reviewed_by text NOT NULL,
    review_notes text,
    customer_notification_requested boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payment_review_events_proof
ON api.payment_proof_review_events(proof_id, created_at DESC);

ALTER TABLE api.pedido_payment_proofs
ADD COLUMN IF NOT EXISTS reviewed_ocr_analysis_id bigint
REFERENCES api.payment_proof_ocr_analyses(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION api.reject_payment_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Payment OCR and review audit records are immutable';
END;
$$;

DROP TRIGGER IF EXISTS reject_payment_ocr_analysis_mutation ON api.payment_proof_ocr_analyses;
CREATE TRIGGER reject_payment_ocr_analysis_mutation
BEFORE UPDATE OR DELETE ON api.payment_proof_ocr_analyses
FOR EACH ROW EXECUTE FUNCTION api.reject_payment_audit_mutation();

DROP TRIGGER IF EXISTS reject_payment_review_event_mutation ON api.payment_proof_review_events;
CREATE TRIGGER reject_payment_review_event_mutation
BEFORE UPDATE OR DELETE ON api.payment_proof_review_events
FOR EACH ROW EXECUTE FUNCTION api.reject_payment_audit_mutation();

CREATE OR REPLACE FUNCTION api.record_payment_proof_ocr_analysis(
    p_proof_id integer,
    p_analysis jsonb,
    p_force_new boolean DEFAULT false
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_proof api.pedido_payment_proofs%ROWTYPE;
    v_existing api.payment_proof_ocr_analyses%ROWTYPE;
    v_created api.payment_proof_ocr_analyses%ROWTYPE;
    v_version integer;
    v_sha text := lower(trim(COALESCE(p_analysis->>'sha256', '')));
    v_cache_version integer;
BEGIN
    SELECT * INTO v_proof
    FROM api.pedido_payment_proofs
    WHERE id = p_proof_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Payment proof not found';
    END IF;
    IF COALESCE((p_analysis->>'advisory_only')::boolean, false) IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'OCR analysis must be advisory only';
    END IF;
    IF v_sha !~ '^[a-f0-9]{64}$' THEN
        RAISE EXCEPTION 'Invalid OCR source SHA-256';
    END IF;
    v_cache_version := COALESCE((p_analysis->>'cache_version')::integer, 0);
    IF v_cache_version < 1 THEN
        RAISE EXCEPTION 'Invalid OCR cache version';
    END IF;

    IF NOT p_force_new THEN
        SELECT * INTO v_existing
        FROM api.payment_proof_ocr_analyses
        WHERE proof_id = p_proof_id
          AND source_sha256 = v_sha
          AND cache_version = v_cache_version
        ORDER BY analysis_version DESC
        LIMIT 1;
        IF FOUND THEN
            RETURN jsonb_build_object(
                'ok', true,
                'created', false,
                'analysis_id', v_existing.id,
                'analysis_version', v_existing.analysis_version
            );
        END IF;
    END IF;

    SELECT COALESCE(max(analysis_version), 0) + 1
    INTO v_version
    FROM api.payment_proof_ocr_analyses
    WHERE proof_id = p_proof_id;

    INSERT INTO api.payment_proof_ocr_analyses(
        proof_id,
        analysis_version,
        source_sha256,
        perceptual_hash,
        cache_version,
        engine,
        ocr_passes,
        ocr_confidence,
        fields,
        field_confidence,
        checks,
        review_reasons,
        recommendation,
        advisory_only,
        analyzed_at
    )
    VALUES(
        p_proof_id,
        v_version,
        v_sha,
        NULLIF(lower(trim(COALESCE(p_analysis->>'perceptual_hash', ''))), ''),
        v_cache_version,
        trim(COALESCE(p_analysis->>'engine', 'unknown')),
        COALESCE(p_analysis->'ocr_passes', '[]'::jsonb),
        COALESCE((p_analysis->>'ocr_confidence')::numeric, 0),
        COALESCE(p_analysis->'fields', '{}'::jsonb),
        COALESCE(p_analysis->'field_confidence', '{}'::jsonb),
        COALESCE(p_analysis->'checks', '{}'::jsonb),
        COALESCE(p_analysis->'review_reasons', '[]'::jsonb),
        COALESCE(NULLIF(p_analysis->>'recommendation', ''), 'REVIEW_OR_REJECT'),
        true,
        COALESCE((p_analysis->>'analyzed_at')::timestamptz, now())
    )
    RETURNING * INTO v_created;

    RETURN jsonb_build_object(
        'ok', true,
        'created', true,
        'analysis_id', v_created.id,
        'analysis_version', v_created.analysis_version
    );
END;
$$;

CREATE OR REPLACE FUNCTION api.revisar_comprobante_pago_auditado(
    p_proof_id integer,
    p_ocr_analysis_id bigint,
    p_status text,
    p_verified_by text DEFAULT 'logistica',
    p_notes text DEFAULT NULL,
    p_notify boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, public
AS $$
DECLARE
    v_analysis api.payment_proof_ocr_analyses%ROWTYPE;
    v_result jsonb;
    v_event_id bigint;
BEGIN
    SELECT * INTO v_analysis
    FROM api.payment_proof_ocr_analyses
    WHERE id = p_ocr_analysis_id
      AND proof_id = p_proof_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'OCR analysis does not belong to this payment proof';
    END IF;
    IF p_status NOT IN ('VERIFIED', 'REJECTED', 'CANCELLED') THEN
        RAISE EXCEPTION 'Invalid proof status';
    END IF;
    IF length(trim(COALESCE(p_verified_by, ''))) NOT BETWEEN 1 AND 80 THEN
        RAISE EXCEPTION 'Invalid reviewer';
    END IF;

    v_result := api.revisar_comprobante_pago(
        p_proof_id,
        p_status,
        p_verified_by,
        p_notes,
        p_notify
    );

    INSERT INTO api.payment_proof_review_events(
        proof_id,
        ocr_analysis_id,
        decision,
        reviewed_by,
        review_notes,
        customer_notification_requested
    )
    VALUES(
        p_proof_id,
        p_ocr_analysis_id,
        p_status,
        trim(p_verified_by),
        p_notes,
        p_notify
    )
    RETURNING id INTO v_event_id;

    UPDATE api.pedido_payment_proofs
    SET reviewed_ocr_analysis_id = p_ocr_analysis_id
    WHERE id = p_proof_id;

    RETURN v_result || jsonb_build_object(
        'ocr_analysis_id', p_ocr_analysis_id,
        'ocr_analysis_version', v_analysis.analysis_version,
        'review_event_id', v_event_id
    );
END;
$$;

CREATE OR REPLACE VIEW api.v_payment_proof_ocr_analyses AS
SELECT
    a.id,
    a.proof_id,
    a.analysis_version,
    a.source_sha256,
    a.perceptual_hash,
    a.cache_version,
    a.engine,
    a.ocr_passes,
    a.ocr_confidence,
    a.fields,
    a.field_confidence,
    a.checks,
    a.review_reasons,
    a.recommendation,
    a.advisory_only,
    a.analyzed_at,
    a.created_at,
    EXISTS(
        SELECT 1 FROM api.payment_proof_review_events e
        WHERE e.ocr_analysis_id = a.id
    ) AS used_for_decision
FROM api.payment_proof_ocr_analyses a;

GRANT SELECT ON api.payment_proof_ocr_analyses, api.payment_proof_review_events,
    api.v_payment_proof_ocr_analyses TO web_anon;
GRANT EXECUTE ON FUNCTION api.record_payment_proof_ocr_analysis(integer, jsonb, boolean) TO web_anon;
GRANT EXECUTE ON FUNCTION api.revisar_comprobante_pago_auditado(integer, bigint, text, text, text, boolean) TO web_anon;

NOTIFY pgrst, 'reload schema';
COMMIT;
