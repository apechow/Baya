"""Ad-hoc tests for account update helpers."""

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

from api.shopping_pw import account  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class AccountTests(unittest.TestCase):
    def test_no_changes_requested(self) -> None:
        result = account.update_account_info(FakePage(), first_name=None, last_name=None)
        self.assertFalse(result.success)
        self.assertIn("No changes", result.message or "")

    def test_requires_current_password_for_sensitive_fields(self) -> None:
        page = FakePage(
            locators={
                "input#change-email": FakeLocator(count_value=1),
                "input#email": FakeLocator(count_value=1),
                "input#change-password": FakeLocator(count_value=1),
                "input#current-password": FakeLocator(count_value=1),
                "input#password": FakeLocator(count_value=1),
                "input#password-confirmation": FakeLocator(count_value=1),
            }
        )
        result = account.update_account_info(
            page,
            email="new@example.com",
            new_password="hunter2",
            current_password=None,
        )
        self.assertFalse(result.success)
        self.assertIn("Current password", result.message or "")

    def test_update_names_only_succeeds(self) -> None:
        page = FakePage(
            locators={
                "input#firstname": FakeLocator(count_value=1),
                "input#lastname": FakeLocator(count_value=1),
                "form#form-validate button.action.save": FakeLocator(count_value=1),
                ".page.messages .message-error, .page.messages .error.message, div.messages .message-error, div.messages .error.message": FakeLocator(count_value=0),
                ".page.messages .message-success, div.messages .message-success": FakeLocator(count_value=1, text="Saved"),
            }
        )
        result = account.update_account_info(page, first_name="Jane", last_name="Doe")
        self.assertTrue(result.success)
        self.assertEqual(result.new_first_name, "Jane")
        self.assertEqual(result.message, "Saved")


if __name__ == "__main__":
    unittest.main()
