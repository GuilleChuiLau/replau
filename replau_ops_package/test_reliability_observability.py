#!/usr/bin/env python3
from __future__ import annotations

import unittest
import json
from pathlib import Path
from unittest.mock import patch
from starlette.requests import Request

import replau_health_dashboard as dashboard
import replau_whatsapp_watchdog as watchdog


DASHBOARD_SOURCE = Path(__file__).with_name("replau_health_dashboard.py").read_text()


class WhatsAppIncidentTests(unittest.TestCase):
    def test_duplicate_disconnect_log_lines_are_one_incident(self) -> None:
        events = [
            {"kind": "disconnected", "at": "2026-07-20T22:46:29-05:00", "message": "watchdog timeout"},
            {"kind": "disconnected", "at": "2026-07-20T22:46:29-05:00", "message": "recovering status 499"},
            {"kind": "connected", "at": "2026-07-20T22:46:32-05:00", "message": "Listening"},
        ]
        incidents = watchdog.disconnect_incidents(events)
        self.assertEqual(1, len(incidents))
        self.assertEqual(2, incidents[0]["log_event_count"])
        self.assertEqual(3, incidents[0]["duration_seconds"])

    def test_separate_recoveries_remain_separate_incidents(self) -> None:
        events = [
            {"kind": "disconnected", "at": "2026-07-20T20:00:00Z", "message": "first"},
            {"kind": "connected", "at": "2026-07-20T20:00:04Z", "message": "up"},
            {"kind": "disconnected", "at": "2026-07-20T21:00:00Z", "message": "second"},
            {"kind": "connected", "at": "2026-07-20T21:00:06Z", "message": "up"},
        ]
        incidents = watchdog.disconnect_incidents(events)
        self.assertEqual([4, 6], [item["duration_seconds"] for item in incidents])


class BackupVisibilityTests(unittest.TestCase):
    def test_dashboard_has_systemd_backup_fallback_contract(self) -> None:
        for marker in (
            'BACKUP_SERVICE=os.environ.get("BACKUP_SERVICE"',
            '["systemctl","show",BACKUP_SERVICE',
            'values.get("Result")=="success"',
            'values.get("ExecMainStatus")=="0"',
            '"path_visibility":"restricted"',
            '["journalctl","--unit",BACKUP_SERVICE',
            '"Backup complete"',
        ):
            self.assertIn(marker, DASHBOARD_SOURCE)

    def test_previous_boot_backup_is_recovered_from_journal(self) -> None:
        event=json.dumps({"MESSAGE":"[2026-07-22] Backup complete","__REALTIME_TIMESTAMP":"1784743727000000"})
        responses=[
            {"ok":False,"stdout":"","stderr":"restricted"},
            {"ok":True,"stdout":event+"\n","stderr":""},
        ]
        with patch.object(dashboard.Path,"exists",return_value=False), patch.object(dashboard,"cmd",side_effect=responses):
            result=dashboard.latest_backup()
        self.assertTrue(result["ok"])
        self.assertEqual("journal",result["source"])
        self.assertEqual("restricted",result["path_visibility"])

    def test_disabled_email_channel_does_not_create_queue_warning(self) -> None:
        self.assertIn('EMAIL_NOTIFICATIONS_ENABLED=os.environ.get("EMAIL_NOTIFICATIONS_ENABLED","false")', DASHBOARD_SOURCE)
        self.assertIn('if EMAIL_NOTIFICATIONS_ENABLED and emails["data"]', DASHBOARD_SOURCE)


class ConversationRequestQueueTests(unittest.TestCase):
    def test_dashboard_keeps_request_queue_private_and_state_bounded(self) -> None:
        for marker in (
            '@app.get("/conversation-requests"',
            'auth(req,x_ops_token)',
            '"AUTO_STARTED","IN_PROGRESS","CLOSED","BLOCKED"',
            'Never use it for cold outreach.',
            'update_whatsapp_request_inbox',
            'INBOX_ACTIONS',
            'Internal note',
            'Audit timeline',
            'enqueue_whatsapp_staff_reply',
            'Reply on WhatsApp',
            'Outbound delivery history',
            'idempotency_key',
            'active_sla_alerts',
            'SLA:</strong>',
        ):
            self.assertIn(marker, DASHBOARD_SOURCE)

    def test_inbox_filters_are_combined_without_server_side_injection(self) -> None:
        rows=[
            {"id":1,"status":"AUTO_STARTED","priority":"URGENT","assigned_to":None,"is_unread":True,"sender_name":"Ana","pedido_num":"PED-1"},
            {"id":2,"status":"CLOSED","priority":"NORMAL","assigned_to":"Memo","is_unread":False,"sender_name":"Luis","pedido_num":"PED-2"},
        ]
        with patch.object(dashboard,"pg",return_value={"ok":True,"data":rows}):
            result=dashboard.conversation_requests("AUTO_STARTED","URGENT","unassigned","true","ana")
        self.assertEqual([1],[row["id"] for row in result["data"]])

    def test_inbox_metrics_surface_waiting_and_response_time(self) -> None:
        metrics=dashboard.conversation_inbox_metrics([
            {"status":"AUTO_STARTED","is_unread":True,"wait_minutes":20,"priority":"URGENT","response_seconds":120},
            {"status":"IN_PROGRESS","is_unread":False,"wait_minutes":2,"priority":"NORMAL","response_seconds":240},
        ])
        self.assertEqual(2,metrics["open"])
        self.assertEqual(1,metrics["unread"])
        self.assertEqual(1,metrics["waiting"])
        self.assertEqual(0,metrics["warning"])
        self.assertEqual(180,metrics["avg_response_seconds"])

    def test_staff_inbox_renders_operational_context(self) -> None:
        row={
            "id":1,"status":"AUTO_STARTED","priority":"URGENT","assigned_to":None,
            "is_unread":True,"sender_name":"Ana","customer_address":"51999999999",
            "inbound_count":2,"wait_minutes":20,"last_message_text":"menu",
            "first_inbound_at":"2026-07-22T10:00:00-05:00","last_inbound_at":"2026-07-22T10:01:00-05:00",
            "sla_due_at":"2026-07-22T10:15:00-05:00","response_seconds":None,
            "pedido_num":"PED-1","order_status":"CONFIRMADO","order_total":42,
            "note_count":1,"latest_note":"Call customer","latest_note_author":"Memo",
        }
        request=Request({"type":"http","method":"GET","path":"/conversation-requests","query_string":b"","headers":[]})
        with patch.object(dashboard,"conversation_requests",return_value={"ok":True,"data":[row]}), patch.object(dashboard,"canned_replies",return_value={"ok":True,"data":[{"code":"menu","label":"Menú","message_text":"Nuestro menú"}]}), patch.object(dashboard,"active_sla_alerts",return_value={"ok":True,"data":[{"level":"URGENT","sender_name":"Ana","wait_minutes":20,"assigned_to":None}]}):
            response=dashboard.conversation_requests_page(request)
        body=response.body.decode()
        for marker in ("WhatsApp Staff Inbox","PED-1","Call customer","Waiting 15m+","MARK_READ","Reply on WhatsApp","Nuestro menú","Outbound delivery history","URGENT SLA:"):
            self.assertIn(marker,body)


if __name__ == "__main__":
    unittest.main()
