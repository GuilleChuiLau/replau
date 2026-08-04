#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
SQL=(ROOT/"postgrest_local/add_recipe_consumption_variance.sql").read_text()
CONTRACT=(ROOT/"postgrest_local/test_recipe_consumption_variance.sql").read_text()
PILOT_CONTRACT=(ROOT/"postgrest_local/test_pilot_burger_rollback_simulation.sql").read_text()
SOURCE=(ROOT/"replau_product_admin_package/replau_product_admin.py").read_text()

class RecipeConsumptionVarianceTests(unittest.TestCase):
    def test_consumption_is_transactional_and_idempotent(self):
        for marker in (
            "api.ingredient_consumption_batches",
            "ingredient_consumption_batch_order_unique",
            "ingredient_order_consumption_movement_unique",
            "api.consume_order_ingredients",
            "already_posted",
        ):
            self.assertIn(marker,SQL)

    def test_picking_completion_posts_ingredients_before_dispatch(self):
        complete=SQL.split("FUNCTION api.complete_scanner_picking",1)[1]
        consume_pos=complete.index("api.consume_order_ingredients")
        dispatch_pos=complete.index("estado='DESPACHADO'")
        self.assertLess(consume_pos,dispatch_pos)
        self.assertIn("'ingredients',ingredient_consumed",complete)

    def test_recipe_math_and_non_recipe_reporting_are_explicit(self):
        function=SQL.split("FUNCTION api.consume_order_ingredients",1)[1].split("CREATE OR REPLACE VIEW",1)[0]
        self.assertIn("api.get_producto_factor",function)
        self.assertIn("line.cantidad_g/1000.0/line.rendimiento_unidades",function)
        self.assertIn("skipped_products",function)

    def test_variance_view_and_admin_sections_exist(self):
        self.assertIn("api.v_ingredient_usage_variance_daily",SQL)
        for marker in ("Usage and waste variance","Automatic order consumption","Waste rate"):
            self.assertIn(marker,SOURCE)

    def test_contract_is_rollback_only_and_checks_retry(self):
        self.assertIn("Recipe consumption was not idempotent",CONTRACT)
        self.assertIn("Ingredient variance report failed",CONTRACT)
        self.assertIn("ROLLBACK;",CONTRACT)

    def test_generated_burger_simulation_scans_validates_and_rolls_back(self):
        for marker in (
            "api.scan_picking_barcode",
            "api.complete_scanner_picking",
            "0.492",
            "9.2390",
            "v_remaining_units <> 18",
            "already_posted",
            "ROLLBACK;",
            "Rollback retained the synthetic order",
            "Rollback retained generated ingredient movements",
            "Rollback left the pilot recipe active",
            "Rollback left pilot ingredient enforcement enabled",
        ):
            self.assertIn(marker,PILOT_CONTRACT)
        self.assertEqual(PILOT_CONTRACT.count("api.scan_picking_barcode("),2)

if __name__=="__main__":unittest.main()
