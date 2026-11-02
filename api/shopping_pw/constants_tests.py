"""Quick checks for constants module."""

from __future__ import annotations

import os
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
playwright_stub.sync_api = types.SimpleNamespace(
    Page=object,
    TimeoutError=type("TimeoutError", (Exception,), {}),
    sync_playwright=lambda: None,
)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

from api.shopping_pw import constants  # noqa:E402


class ConstantsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("SHOPPING_DOMAIN")
        os.environ.pop("SHOPPING_DOMAIN", None)

    def tearDown(self) -> None:
        os.environ.pop("SHOPPING_DOMAIN", None)
        if self._saved is not None:
            os.environ["SHOPPING_DOMAIN"] = self._saved

    def test_explicit_base_wins(self) -> None:
        os.environ["SHOPPING_DOMAIN"] = "http://from-env:1"
        self.assertEqual(constants.base_url("http://from-arg:2/"), "http://from-arg:2")

    def test_env_beats_default(self) -> None:
        os.environ["SHOPPING_DOMAIN"] = "http://from-env:1/"
        self.assertEqual(constants.base_url(), "http://from-env:1")

    def test_falls_back_to_default(self) -> None:
        self.assertEqual(constants.base_url(), constants.DEFAULT_BASE_URL.rstrip("/"))

    def test_legacy_constant_follows_env(self) -> None:
        """The PEP 562 shim must resolve at access, not at import."""
        os.environ["SHOPPING_DOMAIN"] = "http://late-bound:3"
        self.assertEqual(constants.BASE_URL, "http://late-bound:3")
        self.assertTrue(constants.CART_URL.startswith("http://late-bound:3"))

    def test_urls_share_base(self) -> None:
        for name in (
            "CART_URL",
            "CHECKOUT_URL",
            "WISHLIST_URL",
            "ACCOUNT_EDIT_URL",
            "ADDRESS_BOOK_URL",
            "ORDER_HISTORY_URL",
            "LOGIN_URL",
        ):
            value = getattr(constants, name)
            self.assertTrue(value.startswith(constants.BASE_URL), f"{name} should start with BASE_URL")

    def test_base_url_shape(self) -> None:
        self.assertIn("http", constants.BASE_URL)
        self.assertTrue(len(constants.BASE_URL) > 10)


if __name__ == "__main__":
    unittest.main()
