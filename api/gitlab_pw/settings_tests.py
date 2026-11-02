"""Checks for the settings helper."""

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

from api.gitlab_pw import settings  # noqa:E402
from api.gitlab_pw.constants import Selectors, GITLAB_DOMAIN, PROFILE_URL, ACCOUNT_URL, ACCESS_TOKENS_URL  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class SettingsDataclassTests(unittest.TestCase):
    def test_profile_update_result_dataclass(self) -> None:
        """Test ProfileUpdateResult dataclass."""
        result = settings.ProfileUpdateResult(success=True)
        self.assertTrue(result.success)

        failed = settings.ProfileUpdateResult(
            success=False,
            error_message="Update failed",
        )
        self.assertFalse(failed.success)

    def test_username_change_result_dataclass(self) -> None:
        """Test UsernameChangeResult dataclass."""
        result = settings.UsernameChangeResult(
            success=True,
            new_username="newname",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.new_username, "newname")

    def test_delete_result_dataclass(self) -> None:
        """Test DeleteResult dataclass."""
        result = settings.DeleteResult(success=True)
        self.assertTrue(result.success)

        result_with_msg = settings.DeleteResult(
            success=True,
            error_message="Deleted 3 token(s)",
        )
        self.assertTrue(result_with_msg.success)
        self.assertIn("3", result_with_msg.error_message or "")


class TogglePrivateProfileTests(unittest.TestCase):
    def test_toggle_private_profile_enable(self) -> None:
        """Test enabling private profile."""
        checkbox = FakeLocator(count_value=1, is_checked=False)
        update_btn = FakeLocator(count_value=1)
        success_alert = FakeLocator(
            text="Profile was successfully updated",
            count_value=1,
        )

        page = FakePage(
            locators={
                Selectors.PRIVATE_PROFILE_CHECKBOX: checkbox,
                Selectors.UPDATE_PROFILE_BUTTON: update_btn,
                'div.gl-alert-body:has-text("Profile was successfully updated")': success_alert,
            },
            url=PROFILE_URL,
        )

        # Simulate checkbox getting checked after check() is called
        def on_check():
            checkbox._is_checked = True
        checkbox.check = lambda: (checkbox.actions.append(("check", None)), on_check())[-1]

        result = settings.toggle_private_profile(page, make_private=True)

        self.assertTrue(result.success)

    def test_toggle_private_profile_already_correct(self) -> None:
        """Test toggling when already in desired state."""
        checkbox = FakeLocator(count_value=1, is_checked=True)

        page = FakePage(
            locators={
                Selectors.PRIVATE_PROFILE_CHECKBOX: checkbox,
            },
            url=PROFILE_URL,
        )

        result = settings.toggle_private_profile(page, make_private=True)

        self.assertTrue(result.success)
        # Should not have clicked update since already in correct state
        self.assertEqual(len(checkbox.actions), 0)


class ChangeUsernameTests(unittest.TestCase):
    def test_change_username_same_as_current(self) -> None:
        """Test changing to same username."""
        page = FakePage(
            locators={
                Selectors.USERNAME_INPUT: FakeLocator(count_value=1),
            },
            url=ACCOUNT_URL,
        )
        page._input_values[Selectors.USERNAME_INPUT] = "currentuser"

        result = settings.change_username(page, "currentuser")

        self.assertTrue(result.success)
        self.assertEqual(result.new_username, "currentuser")


class DeleteDeployKeyTests(unittest.TestCase):
    def test_delete_deploy_key_dataclass(self) -> None:
        """Test DeleteResult dataclass for deploy keys."""
        result = settings.DeleteResult(
            success=True,
            error_message="No deploy key found (may already be deleted)",
        )
        self.assertTrue(result.success)
        self.assertIn("already be deleted", result.error_message or "")


class DeleteWebhooksTests(unittest.TestCase):
    def test_delete_all_webhooks_dataclass(self) -> None:
        """Test DeleteResult dataclass for webhooks."""
        result = settings.DeleteResult(
            success=True,
            error_message="No webhooks found",
        )
        self.assertTrue(result.success)
        self.assertIn("No webhooks", result.error_message or "")


class DeleteAccessTokensTests(unittest.TestCase):
    def test_delete_all_access_tokens_dataclass(self) -> None:
        """Test DeleteResult dataclass for access tokens."""
        result = settings.DeleteResult(
            success=True,
            error_message="No tokens found",
        )
        self.assertTrue(result.success)
        self.assertIn("No tokens", result.error_message or "")


class CreateAccessTokenTests(unittest.TestCase):
    # The real GitLab uses Bootstrap custom-control: the <input> is hidden and
    # a <label for="..."> sits on top.  We click the label, not the input.
    # Candidates in order (settings.py):
    #   1. label[for='personal_access_token_scopes_{scope}']   ← Bootstrap label (real GitLab)
    #   2. label[data-qa-selector='{scope}_label']             ← qa-selector label
    #   3. #personal_access_token_scopes_{scope}               ← direct input (non-Bootstrap)
    #   4. input[name='personal_access_token[scopes][]'][value='{scope}']
    #   5. input[type='checkbox'][value='{scope}']

    def _base_locators(self) -> dict:
        return {
            Selectors.ACCESS_TOKEN_NAME_INPUT: FakeLocator(count_value=1),
            Selectors.ACCESS_TOKEN_EXPIRES_INPUT: FakeLocator(count_value=1),
            Selectors.ACCESS_TOKEN_SUBMIT: FakeLocator(count_value=1),
            Selectors.ACCESS_TOKEN_CREATED_VALUE: FakeLocator(count_value=1),
        }

    def _make_page(self, token_value: str = "glpat-abc123") -> FakePage:
        """Bootstrap label pattern — matches real GitLab (pattern 1)."""
        locators = self._base_locators()
        for scope in settings.ALL_SCOPES:
            locators[f"label[for='personal_access_token_scopes_{scope}']"] = FakeLocator(count_value=1)
        page = FakePage(locators=locators, url=ACCESS_TOKENS_URL)
        page._input_values[Selectors.ACCESS_TOKEN_CREATED_VALUE] = token_value
        return page

    def _make_page_skip_to(self, first_hit: int, token_value: str) -> FakePage:
        """
        Build a page where patterns 1..(first_hit-1) time out and pattern
        first_hit is present.  first_hit is 1-indexed matching the candidate list.
        """
        all_patterns = [
            lambda s: f"label[for='personal_access_token_scopes_{s}']",
            lambda s: f"label[data-qa-selector='{s}_label']",
            lambda s: f"#personal_access_token_scopes_{s}",
            lambda s: f"input[name='personal_access_token[scopes][]'][value='{s}']",
            lambda s: f"input[type='checkbox'][value='{s}']",
        ]
        locators = self._base_locators()
        page = FakePage(locators=locators, url=ACCESS_TOKENS_URL)
        for scope in settings.ALL_SCOPES:
            for i, fn in enumerate(all_patterns, 1):
                sel = fn(scope)
                if i < first_hit:
                    page._wait_for_selector_raises[sel] = True
                elif i == first_hit:
                    locators[sel] = FakeLocator(count_value=1)
        page._input_values[Selectors.ACCESS_TOKEN_CREATED_VALUE] = token_value
        return page

    def test_create_access_token_success_all_scopes(self) -> None:
        """Successful creation with default (all) scopes returns the token value."""
        page = self._make_page("glpat-abc123")
        result = settings.create_access_token(page, token_name="my-token")

        self.assertTrue(result.success)
        self.assertEqual(result.token, "glpat-abc123")
        self.assertEqual(result.token_name, "my-token")
        self.assertIsNone(result.error_message)

    def test_create_access_token_custom_scopes(self) -> None:
        """Only the requested scope is clicked; others are untouched."""
        page = self._make_page("glpat-xyz")
        result = settings.create_access_token(
            page, token_name="read-only", scopes=["read_repository"]
        )
        self.assertTrue(result.success)
        self.assertEqual(result.token, "glpat-xyz")
        untouched = page.locators.get("label[for='personal_access_token_scopes_api']")
        if untouched:
            self.assertEqual(len(untouched.actions), 0)

    def test_scope_selector_pattern1_label_for(self) -> None:
        """Pattern 1: Bootstrap label[for='personal_access_token_scopes_<scope>'] — real GitLab."""
        page = self._make_page_skip_to(1, "glpat-p1")
        result = settings.create_access_token(page, token_name="tok", scopes=["api"])
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(result.token, "glpat-p1")
        actions = page.locators["label[for='personal_access_token_scopes_api']"].actions
        self.assertIn(("click", None), actions)

    def test_scope_selector_pattern2_label_qa(self) -> None:
        """Pattern 2: label[data-qa-selector='{scope}_label']."""
        page = self._make_page_skip_to(2, "glpat-p2")
        result = settings.create_access_token(page, token_name="tok", scopes=["api"])
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(result.token, "glpat-p2")
        actions = page.locators["label[data-qa-selector='api_label']"].actions
        self.assertIn(("click", None), actions)

    def test_scope_selector_pattern3_input_id(self) -> None:
        """Pattern 3: #personal_access_token_scopes_<scope> direct input."""
        page = self._make_page_skip_to(3, "glpat-p3")
        result = settings.create_access_token(page, token_name="tok", scopes=["api"])
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(result.token, "glpat-p3")
        actions = page.locators["#personal_access_token_scopes_api"].actions
        self.assertIn(("click", None), actions)

    def test_scope_selector_pattern4_name_value(self) -> None:
        """Pattern 4: input[name='personal_access_token[scopes][]'][value='<scope>']."""
        page = self._make_page_skip_to(4, "glpat-p4")
        result = settings.create_access_token(page, token_name="tok", scopes=["api"])
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(result.token, "glpat-p4")
        actions = page.locators["input[name='personal_access_token[scopes][]'][value='api']"].actions
        self.assertIn(("click", None), actions)

    def test_scope_selector_pattern5_generic_checkbox(self) -> None:
        """Pattern 5: input[type='checkbox'][value='<scope>']."""
        page = self._make_page_skip_to(5, "glpat-p5")
        result = settings.create_access_token(page, token_name="tok", scopes=["api"])
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(result.token, "glpat-p5")
        actions = page.locators["input[type='checkbox'][value='api']"].actions
        self.assertIn(("click", None), actions)

    def test_create_access_token_form_not_found(self) -> None:
        """Returns failure when the token form is absent."""
        page = FakePage(url=ACCESS_TOKENS_URL)
        page._wait_for_selector_raises[Selectors.ACCESS_TOKEN_NAME_INPUT] = True
        result = settings.create_access_token(page, token_name="bad")

        self.assertFalse(result.success)
        self.assertIn("not found", (result.error_message or "").lower())

    def test_create_access_token_result_dataclass(self) -> None:
        """CreateAccessTokenResult dataclass fields are accessible."""
        result = settings.CreateAccessTokenResult(
            success=True,
            token="glpat-test",
            token_name="ci-token",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.token, "glpat-test")
        self.assertEqual(result.token_name, "ci-token")
        self.assertIsNone(result.error_message)


class DeleteAccountTests(unittest.TestCase):
    def test_delete_account_button_not_found(self) -> None:
        """Test account deletion when button is missing."""
        page = FakePage(
            locators={
                Selectors.DELETE_ACCOUNT_BUTTON: FakeLocator(count_value=0),
            },
            url=ACCOUNT_URL,
        )

        result = settings.delete_account(page, "password123")

        self.assertFalse(result.success)
        self.assertIn("not found", (result.error_message or "").lower())


class IntegrationGetGlpatTest(unittest.TestCase):
    """
    Live integration test — calls get_glpat against a real GitLab instance.
    Run with:
        python3 api/gitlab_pw/settings_tests.py IntegrationGetGlpatTest
    or set GITLAB_DOMAIN env var to point at the worker, e.g.:
        GITLAB_DOMAIN=http://127.0.0.1:8025 python3 api/gitlab_pw/settings_tests.py IntegrationGetGlpatTest
    """

    def test_get_glpat_live(self) -> None:
        import os

        # Un-stub playwright so the real library is used.
        for mod in ["playwright", "playwright.sync_api"]:
            sys.modules.pop(mod, None)

        gitlab_url = os.getenv("GITLAB_DOMAIN", "http://127.0.0.1:8025")
        print(f"\n  [INTEGRATION] Targeting GitLab at: {gitlab_url}")

        # Import get_glpat AFTER removing the stub so it gets real playwright.
        from api.gitlab_pw.tokens import get_glpat  # noqa: PLC0415

        try:
            token = get_glpat(gitlab_url, token_name="integration-test-token")
            print(f"  [INTEGRATION] SUCCESS — token: {token[:12]}...")
            self.assertTrue(token.startswith("glpat-") or len(token) > 10,
                            f"Unexpected token format: {token!r}")
        except Exception as e:
            self.fail(f"get_glpat raised: {e}")


class RssTokenTests(unittest.TestCase):
    """The feed token is scraped from a page, so the URL must follow `base_url`.

    api/servers/gitlab_extra.py serves every multi-docker worker from one
    process; if these helpers ignored the caller's instance they would report a
    token read from a GitLab the task is not graded on.
    """

    COPY_BUTTON = "button[aria-label='Copy feed token']"
    RESET_LINK = "a[href*='reset_feed_token']"

    def _page(self, token: str = "feed-tok-abc", **kwargs) -> FakePage:
        return FakePage(
            locators={self.COPY_BUTTON: FakeLocator(attributes={"data-clipboard-text": token})},
            **kwargs,
        )

    def test_get_rss_token_returns_token(self) -> None:
        page = self._page()
        result = settings.get_rss_token(page)
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(result.token, "feed-tok-abc")

    def test_get_rss_token_follows_explicit_base(self) -> None:
        page = self._page()
        pinned = "http://worker-3:9999"
        settings.get_rss_token(page, base_url=pinned)
        self.assertEqual(
            page.visited, [f"{pinned}/-/profile/personal_access_tokens"]
        )

    def test_get_rss_token_defaults_without_base(self) -> None:
        page = self._page()
        settings.get_rss_token(page)
        self.assertEqual(page.visited, [ACCESS_TOKENS_URL])

    def test_get_rss_token_reports_missing_field(self) -> None:
        page = self._page()

        # FakePage raises the builtin TimeoutError, but settings.py catches the
        # one it imported from playwright — which is the real class here, since
        # the package is already loaded by the time this module stubs it. Raise
        # exactly what the code under test catches.
        def _timeout(_selector: str, **_kwargs) -> None:
            raise settings.TimeoutError("Timeout waiting for selector")

        page.wait_for_selector = _timeout
        result = settings.get_rss_token(page)
        self.assertFalse(result.success)
        self.assertIn("not found", (result.error_message or "").lower())

    def test_get_rss_token_reports_empty_value(self) -> None:
        page = FakePage(locators={self.COPY_BUTTON: FakeLocator(attributes={})})
        result = settings.get_rss_token(page)
        self.assertFalse(result.success)
        self.assertIsNone(result.token)

    def test_reset_rss_token_follows_explicit_base(self) -> None:
        page = self._page(token="new-tok-xyz")
        page.locators[self.RESET_LINK] = FakeLocator(count_value=1)
        pinned = "http://worker-7:8888"
        result = settings.reset_rss_token(page, base_url=pinned)
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(result.token, "new-tok-xyz")
        self.assertTrue(all(u.startswith(pinned) for u in page.visited), page.visited)

    def test_reset_rss_token_reports_missing_link(self) -> None:
        page = self._page()
        page.locators[self.RESET_LINK] = FakeLocator(count_value=0)
        result = settings.reset_rss_token(page)
        self.assertFalse(result.success)
        self.assertIn("reset feed token link", (result.error_message or "").lower())


if __name__ == "__main__":
    unittest.main()
