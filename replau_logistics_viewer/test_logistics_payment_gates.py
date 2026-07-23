#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name("logistics_viewer.py").read_text()
MIGRATION = Path(__file__).parents[1].joinpath("postgrest_local/add_integrated_delivery_operations.sql").read_text()


class LogisticsPaymentGateContractTests(unittest.TestCase):
    def test_dispatch_and_completion_have_separate_payment_gates(self) -> None:
        for marker in (
            'PREPAID_RELEASED_STATES = {"RELEASED", "RECONCILED", "SETTLED"}',
            'COD_COLLECTED_STATES = {"COD_COLLECTED", "RECONCILED", "SETTLED"}',
            'def payment_dispatch_allowed(',
            'def payment_delivery_completion_allowed(',
            'target_status == "DESPACHADO" and not payment_dispatch_allowed(order)',
            'target_status == "ENTREGADO" and not payment_delivery_completion_allowed(order)',
        ):
            self.assertIn(marker, SOURCE)

    def test_driver_assignment_is_payment_gated(self) -> None:
        offer_block = SOURCE.split('def delivery_offer_next', 1)[1].split('def delivery_assign_driver', 1)[0]
        assign_block = SOURCE.split('def delivery_assign_driver', 1)[1].split('@app.post("/ops/delivery/collect-cod")', 1)[0]
        self.assertIn('if not payment_dispatch_allowed(order)', offer_block)
        self.assertIn('if not payment_dispatch_allowed(order)', assign_block)

    def test_cod_collection_is_versioned_and_amount_checked(self) -> None:
        block = SOURCE.split('def delivery_collect_cod', 1)[1].split('@app.post("/ops/delivery/assignment-cancel")', 1)[0]
        for marker in (
            'p_to_status": "COD_COLLECTED"',
            '"p_expected_version": expected_version',
            'El monto cobrado no coincide con el total esperado',
            '"p_source": "logistics_ui"',
        ):
            self.assertIn(marker, block)

    def test_exception_first_workspace_is_present(self) -> None:
        for marker in (
            'elif view == "exceptions"',
            'def logistics_exception_reasons(',
            '"Pago sin liberar"',
            '"Dirección sin confirmar"',
            '"Despachado sin repartidor"',
            'Excepciones operativas',
        ):
            self.assertIn(marker, SOURCE)

    def test_integrated_delivery_lifecycle_is_used(self) -> None:
        for marker in (
            'def delivery_operation_actions_html(',
            '@app.post("/ops/delivery/transition")',
            '"PICKUP","EN_ROUTE","ARRIVE","DELIVER","FAIL","REOPEN"',
            'payment_delivery_completion_allowed(order)',
            'update_delivery_operation',
            'Motivo obligatorio',
            '"failed": "Problemas"',
            'def ops_inbox_url(',
            'Abrir inbox',
            "get('sla_level')",
        ):
            self.assertIn(marker,SOURCE)

    def test_delivery_migration_keeps_payment_audit_outbox_and_incident_contracts(self) -> None:
        for marker in (
            "Payment is not cleared for delivery completion",
            "api.delivery_operation_events",
            "api.delivery_incidents",
            "DELIVERY_EXCEPTION",
            "api.whatsapp_outbox",
            "idempotency_key",
            "api.v_delivery_operations",
            "sla_level",
        ):
            self.assertIn(marker,MIGRATION)


if __name__ == "__main__":
    unittest.main()
