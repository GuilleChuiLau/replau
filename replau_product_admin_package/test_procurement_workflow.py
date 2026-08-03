#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
SQL=(ROOT/"postgrest_local/add_procurement_workflow.sql").read_text()
SOURCE=(ROOT/"replau_product_admin_package/replau_product_admin.py").read_text()

class ProcurementContractTests(unittest.TestCase):
    def test_po_lifecycle_is_versioned_and_audited(self):
        for marker in ("api.purchase_order_events","api.create_purchase_order","api.add_purchase_order_line","api.transition_purchase_order","Stale purchase order version","Cannot approve an empty purchase order"):
            self.assertIn(marker,SQL)
        self.assertIn("'BORRADOR'",SQL);self.assertIn("'APROBADA'",SQL);self.assertIn("'ENVIADA'",SQL)

    def test_po_receiving_blocks_wrong_and_excess_products(self):
        scan=SQL.split("FUNCTION api.scan_inventory_receiving",1)[1].split("FUNCTION api.post_inventory_receiving",1)[0]
        self.assertIn("not pending on this purchase order",scan)
        self.assertIn("exceeds remaining purchase-order quantity",scan)
        post=SQL.split("FUNCTION api.post_inventory_receiving",1)[1].split("FUNCTION api.refresh_purchase_order_receipts",1)[0]
        self.assertIn("api.recepciones",post);self.assertIn("api.recepcion_detalle",post);self.assertIn("purchase_order_line_id",post)

    def test_void_reverses_stock_and_reopens_po_quantities(self):
        void=SQL.split("FUNCTION api.void_inventory_receiving",1)[1].split("CREATE OR REPLACE VIEW",1)[0]
        self.assertIn("AJUSTE_NEGATIVO",void);self.assertIn("refresh_purchase_order_receipts",void);self.assertIn("already been consumed",void)

    def test_low_stock_and_ui_workspaces_exist(self):
        self.assertIn("api.create_low_stock_draft",SQL)
        for marker in ('@app.get("/procurement"','Create recommended draft','Supplier product terms','Receive against this PO','Lines and variance'):
            self.assertIn(marker,SOURCE)

if __name__=="__main__":unittest.main()
