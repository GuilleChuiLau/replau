#!/usr/bin/env python3
from pathlib import Path
import unittest


SOURCE = Path(__file__).with_name("replau_health_dashboard.py").read_text()


class WhatsAppPolicyVisibilityTests(unittest.TestCase):
    def test_ops_exposes_policy_without_contact_numbers(self) -> None:
        for marker in (
            'policy_rows=pg("/whatsapp_outbound_policy?',
            'policy_events=pg("/whatsapp_policy_events?',
            'policy["opted_out_count"]',
            'policy["recent_decision_counts"]',
            '"whatsapp_policy":policy',
            '"whatsapp_error_classification":error_classification',
            'policy["acknowledged_historical_error_ids"]',
            "WhatsApp Outbound Safety Policy",
            '@app.post("/api/whatsapp-emergency-pause")',
            '"p_state":"PAUSED"',
            "Type PAUSE to confirm",
        ):
            self.assertIn(marker, SOURCE)

    def test_tripped_circuit_is_critical(self) -> None:
        self.assertIn(
            'if policy.get("state")=="TRIPPED": crit.append(',
            SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
