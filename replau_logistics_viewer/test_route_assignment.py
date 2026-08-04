#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import call, patch

import logistics_viewer as viewer


class RouteAssignmentTests(unittest.TestCase):
    def test_delivered_order_can_use_latest_coordinate_bearing_assignment(self) -> None:
        current = {"id": 9, "driver_latitude": None, "driver_longitude": None}
        completed = {"id": 7, "driver_latitude": -12.1, "driver_longitude": -77.03}
        with patch.object(viewer, "fetch_delivery_assignment", side_effect=[current, completed]) as fetch:
            selected = viewer.fetch_route_assignment({"id": 42, "estado": "ENTREGADO"})
        self.assertEqual(selected, completed)
        self.assertEqual(fetch.call_args_list, [call(42), call(42, require_driver_location=True)])

    def test_active_or_annulled_order_never_reuses_historical_driver_coordinates(self) -> None:
        current = {"id": 9, "driver_latitude": None, "driver_longitude": None}
        for state in ("DESPACHADO", "ANULADO"):
            with self.subTest(state=state), patch.object(
                viewer, "fetch_delivery_assignment", return_value=current
            ) as fetch:
                selected = viewer.fetch_route_assignment({"id": 42, "estado": state})
            self.assertEqual(selected, current)
            fetch.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
