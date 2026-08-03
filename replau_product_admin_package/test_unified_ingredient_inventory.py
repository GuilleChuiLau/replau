#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
SQL=(ROOT/"postgrest_local/add_unified_ingredient_inventory.sql").read_text()
CONTRACT=(ROOT/"postgrest_local/test_unified_ingredient_inventory.sql").read_text()
SOURCE=(ROOT/"replau_product_admin_package/replau_product_admin.py").read_text()

class UnifiedIngredientInventoryTests(unittest.TestCase):
    def test_ledger_covers_receipts_consumption_waste_and_adjustments(self):
        for marker in ("api.ingredient_stock_movements","'RECEIPT'","'CONSUMPTION'","'WASTE'","'ADJUST_POSITIVE'","'ADJUST_NEGATIVE'"):
            self.assertIn(marker,SQL)
        self.assertIn("Ingredient stock movements are append-only",SQL)
        self.assertIn("ingredientes_legacy_counters_zero",SQL)

    def test_movement_is_locked_audited_and_can_prevent_negative_stock(self):
        movement=SQL.split("FUNCTION api.post_ingredient_movement",1)[1].split("FUNCTION api.post_ingredient_receipt",1)[0]
        for marker in ("pg_advisory_xact_lock","Insufficient ingredient stock","p_reason","p_actor"):
            self.assertIn(marker,movement)

    def test_receipts_update_weighted_cost_and_lots(self):
        self.assertIn("api.ingredient_lots",SQL)
        self.assertIn("new_cost:=",SQL)
        self.assertIn("ON CONFLICT(ingredient_id,warehouse_id,lot_code)",SQL)

    def test_ui_uses_ledger_instead_of_counter_patches(self):
        self.assertIn('/v_ingredient_inventory_summary?active=eq.true',SOURCE)
        for marker in ('@app.get("/ingredient-ledger"','Receive ingredient','Record waste','Post audited adjustment','Movement history'):
            self.assertIn(marker,SOURCE)
        stock_route=SOURCE.split('def update_ingredient_stock',1)[1].split('@app.post("/costs/recipes")',1)[0]
        self.assertIn('post_ingredient_movement',stock_route)
        self.assertNotIn('pg_patch',stock_route)

    def test_database_contract_is_rollback_only(self):
        self.assertIn("Expected ingredient stock 8.5",CONTRACT)
        self.assertIn("Expected ingredient negative-stock protection",CONTRACT)
        self.assertIn("ROLLBACK;",CONTRACT)

if __name__=="__main__":unittest.main()
