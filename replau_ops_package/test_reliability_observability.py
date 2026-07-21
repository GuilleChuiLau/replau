#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

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
        ):
            self.assertIn(marker, DASHBOARD_SOURCE)

    def test_disabled_email_channel_does_not_create_queue_warning(self) -> None:
        self.assertIn('EMAIL_NOTIFICATIONS_ENABLED=os.environ.get("EMAIL_NOTIFICATIONS_ENABLED","false")', DASHBOARD_SOURCE)
        self.assertIn('if EMAIL_NOTIFICATIONS_ENABLED and emails["data"]', DASHBOARD_SOURCE)


if __name__ == "__main__":
    unittest.main()
