#!/usr/bin/env python3
import csv
import io
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replau_bi import build_report, date_window, percentile, report_csv


class ReplauBITests(unittest.TestCase):
    def window(self):
        return date_window("2026-08-01", "2026-08-02", "America/Lima", today=date(2026, 8, 2))

    def test_analytics_excludes_synthetic_and_calculates_core_metrics(self):
        orders = [
            {"id":1,"pedido_num":"PED-1","cliente_id":1,"estado":"ENTREGADO","metodo_pago":"COD","total":20,"subtotal":18,"delivery":2,"created_at":"2026-08-01T12:00:00-05:00","kitchen_started_at":"2026-08-01T12:02:00-05:00","kitchen_ready_at":"2026-08-01T12:12:00-05:00"},
            {"id":2,"pedido_num":"PED-2","cliente_id":2,"estado":"CONFIRMADO","metodo_pago":"YAPE","total":40,"subtotal":40,"delivery":0,"created_at":"2026-08-02T13:00:00-05:00"},
            {"id":3,"pedido_num":"PED-3","cliente_id":3,"estado":"CONFIRMADO","metodo_pago":"COD","total":999,"created_at":"2026-08-02T14:00:00-05:00","observacion":"synthetic smoke"},
        ]
        items = [{"pedido_id":1,"cdg_prod":"BURGER","producto_texto":"Burger","cantidad":2,"total_linea":18}]
        customers = [{"id":1,"nombre":"Ana"},{"id":2,"nombre":"Luis"},{"id":3,"nombre":"SIMULACION WEB"}]
        fulfillments = [{"pedido_id":1,"expected_amount":20,"received_amount":20,"refunded_amount":0,"status":"COD_COLLECTED"},{"pedido_id":2,"expected_amount":40,"received_amount":0,"refunded_amount":0,"status":"UNDER_REVIEW"}]
        assignments = [{"id":1,"pedido_id":1,"status":"COMPLETED","assigned_at":"2026-08-01T12:15:00-05:00","completed_at":"2026-08-01T12:45:00-05:00"}]
        prior = [{"cliente_id":1,"estado":"ENTREGADO"}]
        report = build_report(self.window(), orders, items, customers, fulfillments, assignments, prior)
        self.assertEqual(report["summary"]["orders"], 2)
        self.assertEqual(report["summary"]["revenue"], 60)
        self.assertEqual(report["summary"]["excluded_test_orders"], 1)
        self.assertEqual(report["summary"]["returning_customer_rate_pct"], 50)
        self.assertEqual(report["operations"]["kitchen_minutes_p50"], 10)
        self.assertEqual(report["operations"]["delivery_minutes_p50"], 30)
        self.assertEqual(report["payments"]["unreconciled_value"], 40)

    def test_date_range_is_bounded(self):
        with self.assertRaises(ValueError): date_window("2026-08-03","2026-08-02","America/Lima")
        with self.assertRaises(ValueError): date_window("2024-01-01","2026-08-02","America/Lima")

    def test_percentile_interpolates(self):
        self.assertEqual(percentile([10, 20, 30], .9), 28)
        self.assertIsNone(percentile([], .5))

    def test_csv_is_formula_safe(self):
        text = report_csv({"orders":[{"created_at":"now","pedido_num":"=1+1","customer":"@evil","channel":"WEB","status":"OK","payment_method":"COD","payment_status":"DUE","subtotal":1,"delivery":0,"total":1}]})
        row = next(csv.DictReader(io.StringIO(text)))
        self.assertEqual(row["pedido_num"], "'=1+1")
        self.assertEqual(row["customer"], "'@evil")


if __name__ == "__main__":
    unittest.main()
