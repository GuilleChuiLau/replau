#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
SOURCE=(ROOT/"replau_logistics_viewer/logistics_viewer.py").read_text()
SQL=(ROOT/"postgrest_local/add_scanner_picking.sql").read_text()

class ScannerPickingContractTests(unittest.TestCase):
    def test_schema_is_audited_and_barcode_aware(self):
        for marker in ("api.product_barcodes","api.picking_sessions","api.picking_session_items","api.picking_scan_events","api.v_picking_progress"):
            self.assertIn(marker,SQL)
        self.assertIn("CONSTRAINT product_barcodes_unique UNIQUE (barcode)",SQL)

    def test_scan_locks_and_rejects_wrong_or_excess_items(self):
        block=SQL.split("FUNCTION api.scan_picking_barcode",1)[1].split("FUNCTION api.complete_scanner_picking",1)[0]
        for marker in ("FOR UPDATE","UNKNOWN_BARCODE","NOT_IN_ORDER","OVER_PICK","scanned_quantity<required_quantity"):
            self.assertIn(marker,block)

    def test_completion_is_atomic_and_requires_exact_quantities(self):
        block=SQL.split("FUNCTION api.complete_scanner_picking",1)[1].split("CREATE OR REPLACE VIEW",1)[0]
        self.assertIn("scanned_quantity<>required_quantity",block)
        self.assertIn("api.consumir_reserva_pedido(oid)",block)
        self.assertIn("status='COMPLETED'",block)
        self.assertIn("estado='DESPACHADO'",block)

    def test_ui_requires_scanner_completion_for_dispatch(self):
        for marker in ('action="/ops/picking/scan"','action="/ops/picking/complete"','autofocus','complete_scanner_picking'):
            self.assertIn(marker,SOURCE)
        picking=SOURCE.split("def render_picking_page",1)[1].split("def delivery_priority_key",1)[0]
        self.assertNotIn('name="estado" value="DESPACHADO"',picking)

if __name__=="__main__": unittest.main()
