#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

from provision_templates import load_and_validate


class TemplateManifestTests(unittest.TestCase):
    def test_spanish_utility_manifest_is_valid(self) -> None:
        templates = load_and_validate(Path(__file__).with_name("templates.es_PE.json"))
        self.assertEqual(len(templates), 3)
        self.assertEqual({item["category"] for item in templates}, {"UTILITY"})
        self.assertEqual(
            {item["name"] for item in templates},
            {"replau_order_confirmed_v1", "replau_order_ready_v1", "replau_order_dispatched_v1"},
        )


if __name__ == "__main__":
    unittest.main()
