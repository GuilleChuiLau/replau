#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
SQL=(ROOT/"postgrest_local/add_inventory_counting.sql").read_text()
CONTRACT=(ROOT/"postgrest_local/test_inventory_counting.sql").read_text()
SOURCE=(ROOT/"replau_product_admin_package/replau_product_admin.py").read_text()

class InventoryCountingContractTests(unittest.TestCase):
    def test_count_is_versioned_audited_and_dual_controlled(self):
        for marker in ("api.inventory_count_sessions","api.inventory_count_lines","api.inventory_count_events","Stale inventory count version","Approver must be different from count operator"):
            self.assertIn(marker,SQL)

    def test_posting_uses_atomic_ledger_adjustments_and_stale_guard(self):
        approve=SQL.split("FUNCTION api.approve_inventory_count",1)[1].split("FUNCTION api.void_inventory_count",1)[0]
        for marker in ("AJUSTE_POSITIVO","AJUSTE_NEGATIVO","INVENTORY_COUNT","Stock changed after snapshot"):
            self.assertIn(marker,approve)

    def test_full_counts_require_completion_and_scans_apply_pack_factor(self):
        self.assertIn("Full count has % uncounted product(s)",SQL)
        scan=SQL.split("FUNCTION api.scan_inventory_count",1)[1].split("FUNCTION api.set_inventory_count_quantity",1)[0]
        self.assertIn("p_packages*bc.unit_factor",scan)

    def test_admin_workspace_exposes_safe_workflow(self):
        for marker in ('@app.get("/inventory-counts"','Scanner increment','Set exact physical quantity','Independent approval','Approve and post adjustments','Immutable audit history'):
            self.assertIn(marker,SOURCE)

    def test_database_contract_is_rollback_only(self):
        self.assertIn("BEGIN;",CONTRACT)
        self.assertIn("ROLLBACK;",CONTRACT)
        self.assertIn("Expected stale-snapshot protection",CONTRACT)

if __name__=="__main__":unittest.main()
