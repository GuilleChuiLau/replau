#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import whatsapp_outbox_worker as worker


ROOT = Path(__file__).parents[1]
MIGRATION = (ROOT / "postgrest_local/add_whatsapp_outbound_policy.sql").read_text()
CONTRACT = (ROOT / "postgrest_local/test_whatsapp_outbound_policy.sql").read_text()
BRIDGE = (ROOT / "replau_openclaw_whatsapp_bridge/bridge.py").read_text()


class WhatsAppOutboundPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {
            "id": 10,
            "pedido_id": 5,
            "whatsapp_number": "51999999999",
            "message_text": "Transactional update",
            "event_type": "DELIVERY_EN_ROUTE",
            "attempts": 0,
            "created_at": "2026-07-26T10:00:00Z",
        }

    def test_paused_policy_never_calls_sender(self) -> None:
        with (
            patch.object(worker, "evaluate_policy", return_value={
                "allowed": False, "decision": "PAUSED", "reason": "Restriction pause", "retry_after_seconds": 900,
            }),
            patch.object(worker, "patch_outbox") as patch_row,
            patch.object(worker, "send_whatsapp") as send,
        ):
            worker.process_one(self.row)
        send.assert_not_called()
        payload = patch_row.call_args.args[1]
        self.assertEqual(payload["status"], "PENDING")
        self.assertEqual(payload["policy_decision"], "PAUSED")
        self.assertIn("not_before", payload)

    def test_opt_out_cancels_without_delivery(self) -> None:
        with (
            patch.object(worker, "evaluate_policy", return_value={
                "allowed": False, "decision": "OPTED_OUT", "reason": "Recipient opted out", "retry_after_seconds": 0,
            }),
            patch.object(worker, "patch_outbox") as patch_row,
            patch.object(worker, "send_whatsapp") as send,
        ):
            worker.process_one(self.row)
        send.assert_not_called()
        self.assertEqual(patch_row.call_args.args[1]["status"], "CANCELLED")

    def test_coalescing_keeps_only_latest_delivery_update(self) -> None:
        older = dict(self.row, id=9, event_type="DELIVERY_PICKED_UP", created_at="2026-07-26T09:59:00Z")
        with patch.object(worker, "pg_rpc", return_value={"ok": True}) as rpc:
            retained = worker.coalesce_rows([older, self.row])
        self.assertEqual([row["id"] for row in retained], [10])
        self.assertEqual(rpc.call_args.args[0], "record_whatsapp_coalesced")
        self.assertEqual(rpc.call_args.args[1]["p_cancelled_outbox_id"], 9)

    def test_migration_defaults_to_safe_pause_and_enforces_windows(self) -> None:
        for marker in (
            "state text NOT NULL DEFAULT 'PAUSED'",
            "recipient_hourly_limit",
            "session_hours",
            "failure_trip_threshold",
            "No active customer-initiated service window",
            "Automatic trip after consecutive delivery failures",
            "record_whatsapp_policy_inbound",
            "record_whatsapp_coalesced",
        ):
            self.assertIn(marker, MIGRATION)

    def test_contract_is_rollback_only_and_covers_opt_out_and_trip(self) -> None:
        for marker in (
            "BEGIN;",
            "ROLLBACK;",
            "Initial policy was not safely paused",
            "'START'",
            "'STOP'",
            "Circuit breaker did not trip",
        ):
            self.assertIn(marker, CONTRACT)

    def test_bridge_records_inbound_policy_state(self) -> None:
        self.assertIn("def record_whatsapp_policy_inbound(", BRIDGE)
        self.assertIn("record_whatsapp_policy_inbound(identity, inbound.message_text)", BRIDGE)


if __name__ == "__main__":
    unittest.main()
