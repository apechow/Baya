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
playwright_stub.sync_api = types.SimpleNamespace(Page=object, TimeoutError=TimeoutError, expect=lambda x: x)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

from api.gitlab_pw import constants  # noqa:E402


class BaseUrlResolutionTests(unittest.TestCase):
    """The base URL must follow the caller, not whatever was set at import.

    api/servers/gitlab_extra.py is one long-lived process serving every
    multi-docker worker, so a frozen value reads the wrong GitLab instance.
    """

    def setUp(self) -> None:
        self._saved = os.environ.get("GITLAB_DOMAIN")
        os.environ.pop("GITLAB_DOMAIN", None)

    def tearDown(self) -> None:
        os.environ.pop("GITLAB_DOMAIN", None)
        if self._saved is not None:
            os.environ["GITLAB_DOMAIN"] = self._saved

    def test_explicit_base_wins(self) -> None:
        os.environ["GITLAB_DOMAIN"] = "http://from-env:1"
        self.assertEqual(constants.base_url("http://from-arg:2/"), "http://from-arg:2")

    def test_env_beats_default(self) -> None:
        os.environ["GITLAB_DOMAIN"] = "http://from-env:1/"
        self.assertEqual(constants.base_url(), "http://from-env:1")

    def test_falls_back_to_default(self) -> None:
        self.assertEqual(constants.base_url(), constants.DEFAULT_BASE_URL.rstrip("/"))

    def test_legacy_constant_follows_env(self) -> None:
        """The PEP 562 shim must resolve at access, not at import."""
        os.environ["GITLAB_DOMAIN"] = "http://late-bound:3"
        self.assertEqual(constants.GITLAB_DOMAIN, "http://late-bound:3")
        self.assertTrue(constants.ACCESS_TOKENS_URL.startswith("http://late-bound:3"))

    def test_helpers_follow_explicit_base(self) -> None:
        os.environ["GITLAB_DOMAIN"] = "http://wrong-instance:1"
        pinned = "http://right-instance:2"
        self.assertEqual(
            constants.access_tokens_url(pinned),
            f"{pinned}/-/profile/personal_access_tokens",
        )
        self.assertEqual(constants.login_url(pinned), f"{pinned}/users/sign_in")
        self.assertTrue(
            constants.get_project_url("ns", "proj", base=pinned).startswith(pinned)
        )

    def test_dashboard_url_has_no_trailing_slash(self) -> None:
        self.assertEqual(constants.dashboard_url("http://host:1/"), "http://host:1")

    def test_unknown_attribute_still_raises(self) -> None:
        with self.assertRaises(AttributeError):
            constants.NOT_A_REAL_CONSTANT


class ConstantsTests(unittest.TestCase):
    def test_urls_share_base(self) -> None:
        url_names = (
            "LOGIN_URL",
            "SIGNUP_URL",
            "DASHBOARD_URL",
            "DASHBOARD_PROJECTS_URL",
            "DASHBOARD_GROUPS_URL",
            "PROFILE_URL",
            "ACCOUNT_URL",
            "SSH_KEYS_URL",
            "ACCESS_TOKENS_URL",
            "NEW_PROJECT_URL",
            "NEW_GROUP_URL",
        )
        for name in url_names:
            value = getattr(constants, name)
            self.assertTrue(
                value.startswith(constants.GITLAB_DOMAIN),
                f"{name} should start with GITLAB_DOMAIN"
            )

    def test_gitlab_domain_shape(self) -> None:
        self.assertIn("http", constants.GITLAB_DOMAIN)
        self.assertTrue(len(constants.GITLAB_DOMAIN) > 10)

    def test_url_helper_functions(self) -> None:
        self.assertIn("namespace/project", constants.get_project_url("namespace", "project"))
        self.assertIn("issues", constants.get_project_issues_url("ns", "proj"))
        self.assertIn("issues/new", constants.get_new_issue_url("ns", "proj"))
        self.assertIn("issues/42", constants.get_issue_url("ns", "proj", 42))
        self.assertIn("branches", constants.get_project_branches_url("ns", "proj"))
        self.assertIn("branches/new", constants.get_new_branch_url("ns", "proj"))
        self.assertIn("new/main", constants.get_new_file_url("ns", "proj", "main"))
        self.assertIn("blob/main/file.py", constants.get_file_url("ns", "proj", "main", "file.py"))
        self.assertIn("merge_requests", constants.get_project_merge_requests_url("ns", "proj"))
        self.assertIn("merge_requests/new", constants.get_new_merge_request_url("ns", "proj", "feature"))
        self.assertIn("merge_requests/5", constants.get_merge_request_url("ns", "proj", 5))
        self.assertIn("edit", constants.get_project_settings_url("ns", "proj"))
        self.assertIn("groups/mygroup", constants.get_group_url("mygroup"))
        self.assertIn("group_members", constants.get_group_members_url("mygroup"))
        self.assertIn("edit", constants.get_group_settings_url("mygroup"))
        self.assertIn("deploy_keys", constants.get_deploy_keys_url("ns", "proj"))
        self.assertIn("deploy-tokens", constants.get_deploy_tokens_url("ns", "proj"))
        self.assertIn("hooks", constants.get_webhooks_url("ns", "proj"))

    def test_selectors_class_exists(self) -> None:
        self.assertTrue(hasattr(constants, "Selectors"))
        # Check some key selectors exist
        self.assertTrue(hasattr(constants.Selectors, "LOGIN_USERNAME_INPUT"))
        self.assertTrue(hasattr(constants.Selectors, "LOGIN_PASSWORD_INPUT"))
        self.assertTrue(hasattr(constants.Selectors, "ISSUE_TITLE_INPUT"))
        self.assertTrue(hasattr(constants.Selectors, "ERROR_CONTAINER"))


if __name__ == "__main__":
    unittest.main()
