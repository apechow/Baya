"""Smoke tests for package exports."""

from __future__ import annotations

import sys
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

api_stub = types.ModuleType("api")
api_stub.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules["api"] = api_stub

playwright_stub = types.ModuleType("playwright")
playwright_stub.sync_api = types.SimpleNamespace(Page=object)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

import api.shopping_pw as pkg  # noqa:E402


class InitTests(unittest.TestCase):
    def test_reexports_present(self) -> None:
        for name in ("add_product_to_cart", "complete_shipping_step", "leave_product_review"):
            self.assertTrue(hasattr(pkg, name), f"{name} should be exported")
        self.assertIn("add_product_to_cart", pkg.__all__)


if __name__ == "__main__":
    unittest.main()
