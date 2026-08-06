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
STRUCTURED_MIGRATION = (ROOT / "postgrest_local/add_whatsapp_structured_messages.sql").read_text()
STRUCTURED_CONTRACT = (ROOT / "postgrest_local/test_whatsapp_structured_messages.sql").read_text()


class WhatsAppOutboundPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {
            "id": 10,
            "pedido_id": 5,
            "whatsapp_number": "51999999999",
            "message_text": "Transactional update",
            "message_payload": None,
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

    def test_structured_payload_is_forwarded_to_adapter(self) -> None:
        payload = {
            "schema_version": 1,
            "type": "interactive_buttons",
            "fallback_text": "1) Ver menú 2) Ver mi pedido 3) Hablar con alguien",
            "interactive": {
                "body": "¿Qué deseas hacer?",
                "buttons": [
                    {"id": "replau.view_menu", "title": "Ver menú"},
                    {"id": "replau.view_order", "title": "Ver mi pedido"},
                    {"id": "replau.human_help", "title": "Hablar con alguien"},
                ],
            },
        }
        row = dict(self.row, message_text=payload["fallback_text"], message_payload=payload)
        with patch.object(worker, "WHATSAPP_DRY_RUN", False), patch.object(
            worker, "OPENCLAW_SEND_URL", "http://adapter.invalid/send/whatsapp"
        ), patch.object(worker.requests, "post") as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"ok": True}
            worker.send_whatsapp(row)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["message_text"], payload["fallback_text"])
        self.assertEqual(sent["message_payload"], payload)

    def test_pre_migration_schema_falls_back_without_retrying_delivery(self) -> None:
        missing = worker.requests.HTTPError("column missing")
        missing.response = worker.requests.Response()
        missing.response.status_code = 400
        missing.response._content = b'{"message":"column whatsapp_outbox.message_payload does not exist"}'
        old_schema_response = worker.requests.Response()
        old_schema_response.status_code = 200
        old_schema_response._content = b'[]'
        with patch.object(worker, "_MESSAGE_PAYLOAD_COLUMN_AVAILABLE", None), patch.object(
            worker, "postgrest_request", side_effect=[missing, old_schema_response]
        ) as request:
            self.assertEqual(worker.get_pending(), [])
        self.assertEqual(request.call_count, 2)
        self.assertIn("message_payload", request.call_args_list[0].args[1])
        self.assertNotIn("message_payload", request.call_args_list[1].args[1])

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

    def test_structured_message_sql_is_versioned_validated_and_rollback_tested(self) -> None:
        for marker in (
            "whatsapp_message_payload_is_valid",
            "interactive_buttons",
            "message_text must equal message_payload.fallback_text",
            "VALIDATE CONSTRAINT whatsapp_outbox_message_payload_check",
        ):
            self.assertIn(marker, STRUCTURED_MIGRATION)
        self.assertIn("BEGIN;", STRUCTURED_CONTRACT)
        self.assertIn("ROLLBACK;", STRUCTURED_CONTRACT)
        self.assertIn("Structured payload was not persisted", STRUCTURED_CONTRACT)


if __name__ == "__main__":
    unittest.main()
