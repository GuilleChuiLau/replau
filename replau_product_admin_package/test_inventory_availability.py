#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
SQL=(ROOT/"postgrest_local/add_inventory_availability.sql").read_text()
CONTRACT=(ROOT/"postgrest_local/test_inventory_availability.sql").read_text()
SOURCE=(ROOT/"replau_product_admin_package/replau_product_admin.py").read_text()

class InventoryAvailabilityContractTests(unittest.TestCase):
    def test_enforcement_requires_recent_physical_count(self):
        self.assertIn("A posted physical count from the last 30 days is required before enforcement",SQL)
        self.assertIn("api.configure_inventory_stock_control",SQL)

    def test_reservation_uses_locked_available_to_promise(self):
        block=SQL.split("FUNCTION api.assert_inventory_availability",1)[1].split("FUNCTION api.configure_inventory_stock_control",1)[0]
        self.assertIn("pg_advisory_xact_lock",block)
        self.assertIn("Insufficient available stock",block)
        reserve=SQL.split("FUNCTION api.reservar_stock_pedido_item",1)[1].split("FUNCTION api.expire_stale_stock_reservations",1)[0]
        self.assertIn("api.assert_inventory_availability",reserve)
        self.assertIn("reactivated",reserve)

    def test_expiry_is_safe_and_dry_run_by_default(self):
        expiry=SQL.split("FUNCTION api.expire_stale_stock_reservations",1)[1].split("CREATE OR REPLACE VIEW",1)[0]
        self.assertIn("p_dry_run boolean DEFAULT true",SQL)
        self.assertIn("p.estado='CONFIRMADO'",expiry)
        self.assertIn("PAID_VERIFIED",expiry)
        self.assertIn("api.picking_sessions",expiry)

    def test_contract_covers_order_block_expiry_and_reactivation(self):
        for marker in ("Expected order-confirmation stock protection","Dry-run expiry contract failed","Expired reservation was not safely revalidated","ROLLBACK;"):
            self.assertIn(marker,CONTRACT)

    def test_admin_requires_explicit_activation_and_expiry_confirmation(self):
        for marker in ('@app.get("/inventory-controls"','Block insufficient orders','Preview expiry','Release eligible reservations','requires a posted physical count'):
            self.assertIn(marker,SOURCE)

if __name__=="__main__":unittest.main()
