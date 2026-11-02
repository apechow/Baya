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
playwright_stub.sync_api = types.SimpleNamespace(Page=object, TimeoutError=TimeoutError, expect=lambda x: x)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

from api.gitlab_pw import login  # noqa:E402
from api.gitlab_pw.constants import Selectors, base_url, login_url  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class LoginTests(unittest.TestCase):
    def test_login_successful_redirect(self) -> None:
        """Test successful login redirects to dashboard."""
        username_input = FakeLocator(count_value=1)
        password_input = FakeLocator(count_value=1)
        submit_btn = FakeLocator(count_value=1)

        dashboard_url = base_url()

        page = FakePage(
            locators={
                Selectors.LOGIN_USERNAME_INPUT: username_input,
                Selectors.LOGIN_PASSWORD_INPUT: password_input,
                Selectors.LOGIN_SUBMIT_BUTTON: submit_btn,
                Selectors.ERROR_CONTAINER: FakeLocator(count_value=0),
            },
            url=login_url(),
        )

        submit_btn.on_click = lambda: setattr(page, "url", dashboard_url)

        result = login.login_user(page, "testuser", "password123")

        self.assertTrue(result.success)
        self.assertEqual(result.redirect_url, dashboard_url)
        self.assertIn(login_url(), page.visited)
        self.assertEqual(username_input.text, "testuser")
        self.assertEqual(password_input.text, "password123")

    def test_login_invalid_credentials_surface_error(self) -> None:
        """Test that invalid credentials return error message."""
        error_msg = "Invalid login or password"
        error_item = FakeLocator(text=error_msg, count_value=1)
        error_list = FakeLocator(
            count_value=1,
            nested={"ul li": FakeLocator(children=[error_item], count_value=1)},
            all_inner_texts_result=[error_msg],
        )

        page = FakePage(
            locators={
                Selectors.LOGIN_USERNAME_INPUT: FakeLocator(count_value=1),
                Selectors.LOGIN_PASSWORD_INPUT: FakeLocator(count_value=1),
                Selectors.LOGIN_SUBMIT_BUTTON: FakeLocator(count_value=1),
                Selectors.ERROR_CONTAINER: error_list,
            },
            url=login_url(),
        )

        # Make error locator return the inner texts
        error_list.locator = lambda sel: FakeLocator(
            children=[error_item],
            count_value=1,
            all_inner_texts_result=[error_msg]
        ) if "li" in sel else FakeLocator()

        result = login.login_user(page, "baduser", "wrongpass")

        self.assertFalse(result.success)
        self.assertIn("Invalid", result.error_message or "")
        self.assertIsNone(result.redirect_url)

    def test_login_result_error_dataclass(self) -> None:
        """Test LoginResult dataclass for error case."""
        result = login.LoginResult(
            success=False,
            redirect_url=None,
            error_message="Login form not found on page",
        )
        self.assertFalse(result.success)
        self.assertIn("form", (result.error_message or "").lower())
        self.assertIsNone(result.redirect_url)

    def test_login_handles_survey_page(self) -> None:
        """Test that post-login survey is handled."""
        submit_btn = FakeLocator(count_value=1)
        survey_role = FakeLocator(count_value=1)
        survey_btn = FakeLocator(count_value=1)

        page = FakePage(
            locators={
                Selectors.LOGIN_USERNAME_INPUT: FakeLocator(count_value=1),
                Selectors.LOGIN_PASSWORD_INPUT: FakeLocator(count_value=1),
                Selectors.LOGIN_SUBMIT_BUTTON: submit_btn,
                Selectors.ERROR_CONTAINER: FakeLocator(count_value=0),
                Selectors.SURVEY_ROLE_SELECT: survey_role,
                Selectors.SURVEY_SUBMIT_BUTTON: survey_btn,
            },
            url=login_url(),
            content_text="To personalize your GitLab experience",
        )

        submit_btn.on_click = lambda: setattr(page, "url", base_url())

        result = login.login_user(page, "newuser", "password123")

        self.assertTrue(result.success)
        # Survey should have been handled
        self.assertTrue(
            any(action[0] == "select" for action in survey_role.actions)
        )

    def test_is_logged_in_detects_user_menu(self) -> None:
        """Test is_logged_in returns True when user menu is present."""
        page = FakePage(
            locators={
                ".header-user-dropdown-toggle, .user-menu": FakeLocator(count_value=1),
            },
            url=base_url(),
        )

        self.assertTrue(login.is_logged_in(page))

    def test_is_logged_in_returns_false_on_login_page(self) -> None:
        """Test is_logged_in returns False on sign-in page."""
        page = FakePage(
            locators={},
            url=f"{base_url()}/users/sign_in",
        )

        self.assertFalse(login.is_logged_in(page))


if __name__ == "__main__":
    unittest.main()
