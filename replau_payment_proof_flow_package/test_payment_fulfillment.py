#!/usr/bin/env python3
"""Static contract tests for the PostgreSQL payment-fulfillment migration.

The hosted CI runner has no Replau database. These checks protect the state
machine contract there; the installer still uses psql ON_ERROR_STOP for the
authoritative database validation before deployment.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SQL = (Path(__file__).with_name("add_payment_fulfillment.sql")).read_text()
UI = (Path(__file__).with_name("replau_payment_proof_review.py")).read_text()


def transition_pairs() -> set[tuple[str, str]]:
    block = SQL.split("CREATE OR REPLACE FUNCTION api.payment_transition_allowed", 1)[1]
    block = block.split("CREATE OR REPLACE FUNCTION api.transition_payment_fulfillment", 1)[0]
    return set(re.findall(r"\('([A-Z_]+)','([A-Z_]+)'\)", block))


class PaymentFulfillmentMigrationTests(unittest.TestCase):
    def test_core_objects_are_additive_and_audited(self) -> None:
        for marker in (
            "CREATE TABLE IF NOT EXISTS api.payment_fulfillments",
            "CREATE TABLE IF NOT EXISTS api.payment_fulfillment_events",
            "CREATE OR REPLACE FUNCTION api.transition_payment_fulfillment",
            "p_expected_version integer DEFAULT NULL",
            "FOR UPDATE",
            "Invalid payment fulfillment transition",
            "Refund exceeds received amount",
            "payment_fulfillment_events is append-only",
            "CREATE TRIGGER audit_payment_proof_fulfillment_event",
        ):
            self.assertIn(marker, SQL)

    def test_expected_happy_paths_exist(self) -> None:
        pairs = transition_pairs()
        proof_path = [
            ("PAYMENT_REQUESTED", "PROOF_REQUIRED"),
            ("PROOF_REQUIRED", "UNDER_REVIEW"),
            ("UNDER_REVIEW", "VERIFIED"),
            ("VERIFIED", "RELEASED"),
            ("RELEASED", "RECONCILED"),
            ("RECONCILED", "SETTLED"),
        ]
        cod_path = [
            ("COD_DUE", "COD_COLLECTED"),
            ("COD_COLLECTED", "RECONCILED"),
        ]
        self.assertTrue(set(proof_path).issubset(pairs))
        self.assertTrue(set(cod_path).issubset(pairs))

    def test_dangerous_shortcuts_do_not_exist(self) -> None:
        pairs = transition_pairs()
        for forbidden in (
            ("PAYMENT_REQUESTED", "SETTLED"),
            ("PROOF_REQUIRED", "VERIFIED"),
            ("UNDER_REVIEW", "SETTLED"),
            ("COD_DUE", "SETTLED"),
            ("REFUNDED", "VERIFIED"),
        ):
            self.assertNotIn(forbidden, pairs)

    def test_migration_is_idempotent_at_object_boundary(self) -> None:
        self.assertIn("ON CONFLICT (pedido_id) DO NOTHING", SQL)
        self.assertIn("DROP TRIGGER IF EXISTS sync_payment_fulfillment_from_order", SQL)
        self.assertIn("CREATE OR REPLACE VIEW api.v_payment_fulfillments", SQL)

    def test_cashier_ui_uses_controlled_versioned_transitions(self) -> None:
        for marker in (
            'FULFILLMENT_ACTIONS = {',
            '@app.get("/fulfillment/{pedido_id}"',
            '@app.post("/fulfillment/{pedido_id}/transition")',
            '"p_expected_version": expected_version',
            '"p_source": "cashier_ui"',
            'Immutable audit timeline',
        ):
            self.assertIn(marker, UI)


if __name__ == "__main__":
    unittest.main()
