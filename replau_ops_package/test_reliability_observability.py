#!/usr/bin/env python3
from __future__ import annotations

import unittest
import json
from pathlib import Path
from unittest.mock import patch

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
            'This is not a cold-outreach list.',
            'consent_basis',
        ):
            self.assertIn(marker, DASHBOARD_SOURCE)


if __name__ == "__main__":
    unittest.main()
