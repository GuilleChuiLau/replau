#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name("logistics_viewer.py").read_text()


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


if __name__ == "__main__":
    unittest.main()
