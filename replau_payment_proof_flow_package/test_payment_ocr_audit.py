#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

from replau_payment_proof_review import ocr_audit_payload


PACKAGE = Path(__file__).parent
MIGRATION = (PACKAGE / "add_payment_ocr_audit.sql").read_text()
CONTRACT = (PACKAGE / "test_payment_ocr_audit.sql").read_text()
APP = (PACKAGE / "replau_payment_proof_review.py").read_text()


class PaymentOcrAuditTests(unittest.TestCase):
    def test_audit_payload_excludes_raw_ocr_text(self) -> None:
        payload = ocr_audit_payload({
            "sha256": "a" * 64,
            "fields": {"amount": 10},
            "lines": ["private raw OCR text"],
            "field_evidence": {"recipient": "private"},
            "advisory_only": True,
        })
        self.assertEqual(payload["fields"], {"amount": 10})
        self.assertNotIn("lines", payload)
        self.assertNotIn("field_evidence", payload)

    def test_schema_is_versioned_append_only_and_advisory(self) -> None:
        for marker in (
            "CREATE TABLE IF NOT EXISTS api.payment_proof_ocr_analyses",
            "UNIQUE(proof_id, analysis_version)",
            "advisory_only boolean NOT NULL CHECK(advisory_only = true)",
            "CREATE TABLE IF NOT EXISTS api.payment_proof_review_events",
            "OCR analysis does not belong to this payment proof",
            "reject_payment_audit_mutation",
            "reviewed_ocr_analysis_id",
        ):
            self.assertIn(marker, MIGRATION)

    def test_snapshot_and_review_rpcs_are_linked(self) -> None:
        for marker in (
            "api.record_payment_proof_ocr_analysis",
            "api.revisar_comprobante_pago_auditado",
            "ocr_analysis_id",
            "review_event_id",
            "v_payment_proof_ocr_analyses",
        ):
            self.assertIn(marker, MIGRATION)

    def test_contract_is_rollback_only_and_checks_immutability(self) -> None:
        for marker in (
            "BEGIN;",
            "ROLLBACK;",
            "OCR snapshot deduplication failed",
            "Audited review linkage failed",
            "OCR audit snapshot was mutable",
            "'CANCELLED'",
            "false",
        ):
            self.assertIn(marker, CONTRACT)

    def test_cashier_requires_persisted_snapshot_for_decision(self) -> None:
        for marker in (
            "persist_ocr_analysis(",
            "force=force_ocr",
            'name="ocr_analysis_id"',
            '"revisar_comprobante_pago_auditado"',
            '"p_ocr_analysis_id": ocr_analysis_id',
            "Decisión deshabilitada hasta que exista un snapshot OCR persistido.",
            "Historial OCR inmutable",
            "PAYMENT_RECEIPT_LEGACY_DIRS",
            "allowed_roots = (PAYMENT_RECEIPT_DIR, *PAYMENT_RECEIPT_LEGACY_DIRS)",
        ):
            self.assertIn(marker, APP)


if __name__ == "__main__":
    unittest.main()
