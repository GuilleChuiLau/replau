#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name("logistics_viewer.py").read_text()
MIGRATION = Path(__file__).parents[1].joinpath("postgrest_local/add_integrated_delivery_operations.sql").read_text()
ATOMIC_ASSIGNMENT = Path(__file__).parents[1].joinpath("postgrest_local/add_atomic_delivery_assignment.sql").read_text()
ATOMIC_ASSIGNMENT_TEST = Path(__file__).parents[1].joinpath("postgrest_local/test_atomic_delivery_assignment.sql").read_text()
LOAD_MATCHING = Path(__file__).parents[1].joinpath("postgrest_local/add_load_aware_delivery_matching.sql").read_text()
LOAD_MATCHING_TEST = Path(__file__).parents[1].joinpath("postgrest_local/test_load_aware_delivery_matching.sql").read_text()


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

    def test_picking_can_handoff_directly_to_delivery_matching(self) -> None:
        for marker in (
            'action="/ops/picking/handoff-delivery"',
            'Listo + asignar',
            '@app.post("/ops/picking/handoff-delivery")',
            'def picking_handoff_delivery(',
            '"p_estado": "DESPACHADO"',
            'return delivery_assign_driver(pedido_num=pedido_num, repartidor_id=repartidor_id)',
        ):
            self.assertIn(marker, SOURCE)

    def test_delivery_matching_supports_checkbox_assignment_with_form_fallback(self) -> None:
        for marker in (
            'class="matching-checkbox"',
            'class="button good assign-selected-button"',
            'document.querySelectorAll(".matching-checkbox:checked")',
            'Asignar seleccionados',
            'fetch("/ops/delivery/assign-driver-batch"',
            '¿Asignar ${{selected.length}} pedido(s) a ${{driverName}}?',
            'action="/ops/delivery/assign-driver"',
        ):
            self.assertIn(marker, SOURCE)

    def test_batch_assignment_is_bounded_and_reports_partial_results(self) -> None:
        block = SOURCE.split('def delivery_assign_driver_batch', 1)[1].split(
            '@app.post("/ops/picking/handoff-delivery")', 1
        )[0]
        for marker in (
            'list(dict.fromkeys(requested))',
            'if len(unique) > 25',
            'delivery_assign_driver(pedido_num=pedido_num, repartidor_id=repartidor_id)',
            '"assigned": assigned',
            '"failed": failed',
            'status_code=200 if not failed else 207',
        ):
            self.assertIn(marker, block)

    def test_direct_assignment_uses_atomic_database_rpc(self) -> None:
        block = SOURCE.split('def delivery_assign_driver(', 1)[1].split(
            '@app.post("/ops/delivery/assign-driver-batch"', 1
        )[0]
        for marker in (
            '"assign_delivery_driver_atomic"',
            '"p_actor": "logistics-ui"',
            '"p_idempotency_key": idempotency_key',
            '"p_expected_active_assignment_id": expected_assignment_id',
            '"idempotency_key": f"{idempotency_key}:driver-notice"',
        ):
            self.assertIn(marker, block)
        self.assertNotIn('pg_patch(f"/delivery_asignaciones', block)
        self.assertNotIn('pg_post(\n        "/delivery_asignaciones"', block)

    def test_atomic_assignment_locks_and_audits_the_replacement(self) -> None:
        for marker in (
            "CREATE OR REPLACE FUNCTION api.assign_delivery_driver_atomic",
            "FOR UPDATE OF p",
            "FROM api.repartidores",
            "FOR UPDATE;",
            "Payment is not cleared for dispatch",
            "previous_assignment_ids",
            "Assignment conflict: order already has an active assignment",
            "Assignment conflict: active assignment changed",
            "'DELIVERY_ATOMIC_ASSIGNMENT'",
            "idempotency_key",
            "version = version + 1",
            "GRANT EXECUTE ON FUNCTION api.assign_delivery_driver_atomic",
        ):
            self.assertIn(marker, ATOMIC_ASSIGNMENT)

    def test_atomic_assignment_contract_is_rollback_only_and_idempotent(self) -> None:
        for marker in (
            "BEGIN;",
            "ROLLBACK;",
            "api.assign_delivery_driver_atomic(",
            "Production may correctly have no open orders",
            "SET estado = 'DESPACHADO'",
            "Atomic assignment left multiple active rows",
            "Atomic assignment audit event is missing",
            "Idempotent replay failed",
        ):
            self.assertIn(marker, ATOMIC_ASSIGNMENT_TEST)

    def test_automatic_matching_is_load_aware_and_bounded(self) -> None:
        for marker in (
            "max_active_deliveries_per_driver",
            "FOR UPDATE OF p",
            "FOR UPDATE OF r SKIP LOCKED",
            "score.active_load < v_max_active",
            "score.active_load ASC",
            "score.pending_offers ASC",
            "score.last_assignment_at ASC NULLS FIRST",
            "'DELIVERY_LOAD_AWARE_OFFER'",
            "'selection_reason'",
            "'Payment is not cleared for dispatch'",
        ):
            self.assertIn(marker, LOAD_MATCHING)

    def test_load_aware_matching_contract_is_rollback_only(self) -> None:
        for marker in (
            "BEGIN;",
            "ROLLBACK;",
            "SET estado = 'DESPACHADO'",
            "api.ofrecer_delivery_a_siguiente_repartidor",
            "Load-aware matching did not explain its selection",
            "Load-aware offer audit event is missing",
        ):
            self.assertIn(marker, LOAD_MATCHING_TEST)

    def test_delivery_station_explains_automatic_match_selection(self) -> None:
        for marker in (
            'match_driver: str = Query("")',
            'match_load: str = Query("")',
            'data["automatic_match_notice"] = notice',
            '"match_driver": data.get("repartidor_nombre")',
            '"match_reason": data.get("selection_reason", "")',
            'class="match-notice"',
        ):
            self.assertIn(marker, SOURCE)

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
