"""Checks for the login helper."""

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

from api.shopping_pw import login  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class LoginTests(unittest.TestCase):
    def test_login_successful_redirect(self) -> None:
        email_input = FakeLocator(count_value=1)
        password_input = FakeLocator(count_value=1)
        submit_btn = FakeLocator(count_value=1)
        button_locator = FakeLocator(children=[submit_btn], count_value=1)
        form = FakeLocator(
            count_value=1,
            nested={
                "input#email": email_input,
                "input#pass": password_input,
                "button#send2, button.action.login.primary": button_locator,
            },
        )

        page = FakePage(
            locators={
                "form#login-form": form,
                login.ERROR_SELECTOR: FakeLocator(count_value=0),
            },
            url=login.LOGIN_URL,
        )

        account_url = login.LOGIN_URL.replace("/customer/account/login/", "/customer/account/")
        submit_btn.on_click = lambda: setattr(page, "url", account_url)

        result = login.login_customer(page, "user@example.com", "password123")
        self.assertTrue(result.success)
        self.assertEqual(result.redirect_url, account_url)
        self.assertIn(login.LOGIN_URL, page.visited)
        self.assertEqual(email_input.text, "user@example.com")
        self.assertEqual(password_input.text, "password123")

    def test_login_invalid_credentials_surface_error(self) -> None:
        error_msg = (
            "The account sign-in was incorrect or your account is disabled temporarily. "
            "Please wait and try again later."
        )
        submit_btn = FakeLocator(count_value=1)
        button_locator = FakeLocator(children=[submit_btn], count_value=1)
        error_loc = FakeLocator(
            children=[FakeLocator(text=error_msg, count_value=1)],
            count_value=1,
        )
        form = FakeLocator(
            count_value=1,
            nested={
                "input#email": FakeLocator(count_value=1),
                "input#pass": FakeLocator(count_value=1),
                "button#send2, button.action.login.primary": button_locator,
            },
        )
        page = FakePage(
            locators={
                "form#login-form": form,
                login.ERROR_SELECTOR: error_loc,
            },
            url=login.LOGIN_URL,
        )

        result = login.login_customer(page, "bad@example.com", "wrong")
        self.assertFalse(result.success)
        self.assertIn("incorrect", (result.error_message or "").lower())
        self.assertIsNone(result.redirect_url)

    def test_missing_form_returns_error(self) -> None:
        page = FakePage(locators={}, url=login.LOGIN_URL)
        result = login.login_customer(page, "any@example.com", "pw")
        self.assertFalse(result.success)
        self.assertIn("form", (result.error_message or "").lower())


if __name__ == "__main__":
    unittest.main()
