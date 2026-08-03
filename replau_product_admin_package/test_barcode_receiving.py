#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(HERE))

SQL=(ROOT/"postgrest_local/add_barcode_receiving.sql").read_text()
SOURCE=(HERE/"replau_product_admin.py").read_text()

class BarcodeReceivingContractTests(unittest.TestCase):
    def test_gtin_contract_validates_checksums_and_types(self):
        for marker in ("api.gtin_check_digit","'EAN13'","'GTIN14'","Invalid EAN-13 / GTIN-13 check digit","Invalid GTIN-14 check digit"):
            self.assertIn(marker,SQL)
        self.assertIn("Barcode already belongs to another product",SQL)

    def test_receiving_is_explicit_atomic_and_reversible(self):
        for marker in ("api.inventory_receiving_sessions","api.inventory_receiving_scans","api.scan_inventory_receiving","api.post_inventory_receiving","api.void_inventory_receiving"):
            self.assertIn(marker,SQL)
        post=SQL.split("FUNCTION api.post_inventory_receiving",1)[1].split("FUNCTION api.void_inventory_receiving",1)[0]
        self.assertIn("'RECEPCION'",post); self.assertIn("status='POSTED'",post)
        void=SQL.split("FUNCTION api.void_inventory_receiving",1)[1].split("CREATE OR REPLACE VIEW",1)[0]
        self.assertIn("'AJUSTE_NEGATIVO'",void); self.assertIn("already been consumed",void)

    def test_admin_has_all_code_types_labels_and_scanner_flow(self):
        for marker in ('option>CODE128','option>QR','option>EAN13','option>GTIN14','def barcode_svg','action="/receiving/{session_id}/scan','Post receiving to stock'):
            self.assertIn(marker,SOURCE)

    def test_python_gtin_examples(self):
        try:
            import replau_product_admin as app
        except ModuleNotFoundError as exc:
            self.skipTest(str(exc))
        self.assertEqual(app.normalize_barcode("400638133393","EAN13"),"4006381333931")
        self.assertEqual(app.normalize_barcode("1400638133393","GTIN14"),"14006381333938")
        with self.assertRaises(ValueError): app.normalize_barcode("4006381333932","EAN13")

if __name__=="__main__": unittest.main()
