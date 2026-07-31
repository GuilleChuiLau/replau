#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("kitchen_ui", HERE / "kitchen_ui.py")
kitchen = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(kitchen)


class KitchenV4Tests(unittest.TestCase):
    def test_operational_lanes(self):
        self.assertEqual(kitchen.order_lane({"payment_ready": False}), "PROBLEMAS")
        self.assertEqual(kitchen.order_lane({"payment_ready": True, "queue_color": "RED"}), "PROBLEMAS")
        self.assertEqual(
            kitchen.order_lane({"payment_ready": True, "queue_color": "GREEN", "kitchen_status": "LISTO"}),
            "LISTOS",
        )

    def test_warning_detection(self):
        order = {"observacion": "ALERGIA: sin maní", "items": [], "kitchen_notes": ""}
        self.assertIn("⚠ ALERGIA", kitchen.order_warnings(order))
        self.assertIn("⚠ SIN / RESTRICCIÓN", kitchen.order_warnings(order))

    def test_migration_contract(self):
        sql = (HERE / "add_kitchen_v4.sql").read_text()
        for marker in (
            "kitchen_version",
            "kitchen_order_events",
            "kitchen_order_events is append-only",
            "p_expected_version",
            "STALE_KITCHEN_VERSION",
            "PAYMENT_NOT_READY",
            "acknowledge_kitchen_order",
        ):
            self.assertIn(marker, sql)

    def test_ui_uses_versioned_rpcs_and_sse(self):
        source = (HERE / "kitchen_ui.py").read_text()
        self.assertIn('"transition_kitchen_order"', source)
        self.assertIn('"acknowledge_kitchen_order"', source)
        self.assertIn('@app.get("/api/events")', source)
        self.assertNotIn("save_cleared_kitchen_order_ids", source)


if __name__ == "__main__":
    unittest.main()
