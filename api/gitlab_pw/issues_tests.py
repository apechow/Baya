"""Checks for the issues helper."""

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

from api.gitlab_pw import issues  # noqa:E402
from api.gitlab_pw.constants import Selectors, GITLAB_DOMAIN, get_new_issue_url  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class IssueDataclassTests(unittest.TestCase):
    def test_issue_dataclass_fields(self) -> None:
        """Test Issue dataclass has expected fields."""
        issue = issues.Issue(
            issue_id=42,
            title="Test Issue",
            url="http://gitlab/namespace/project/-/issues/42",
            state="opened",
            author="testuser",
            labels=["bug", "urgent"],
        )
        self.assertEqual(issue.issue_id, 42)
        self.assertEqual(issue.title, "Test Issue")
        self.assertEqual(issue.state, "opened")
        self.assertEqual(issue.author, "testuser")
        self.assertEqual(issue.labels, ["bug", "urgent"])

    def test_create_issue_result_dataclass(self) -> None:
        """Test CreateIssueResult dataclass."""
        result = issues.CreateIssueResult(
            success=True,
            issue_url="http://gitlab/ns/proj/-/issues/1",
            issue_id=1,
            error_message=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.issue_id, 1)

        failed_result = issues.CreateIssueResult(
            success=False,
            issue_url=None,
            error_message="Title is required",
        )
        self.assertFalse(failed_result.success)
        self.assertIn("Title", failed_result.error_message or "")

    def test_delete_issue_result_dataclass(self) -> None:
        """Test DeleteIssueResult dataclass."""
        result = issues.DeleteIssueResult(success=True)
        self.assertTrue(result.success)
        self.assertIsNone(result.error_message)


class CreateIssueTests(unittest.TestCase):
    def test_create_issue_success(self) -> None:
        """Test successful issue creation."""
        title_input = FakeLocator(count_value=1)
        desc_input = FakeLocator(count_value=1)
        create_btn = FakeLocator(count_value=1)

        issue_url = f"{GITLAB_DOMAIN}/testns/testproj/-/issues/123"

        page = FakePage(
            locators={
                Selectors.ISSUE_TITLE_INPUT: title_input,
                Selectors.ISSUE_DESCRIPTION_TEXTAREA: desc_input,
                Selectors.ISSUE_CREATE_BUTTON: create_btn,
                Selectors.ERROR_CONTAINER: FakeLocator(count_value=0),
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=get_new_issue_url("testns", "testproj"),
        )

        create_btn.on_click = lambda: setattr(page, "url", issue_url)

        result = issues.create_issue(
            page,
            namespace="testns",
            project="testproj",
            title="Bug: Something is broken",
            description="This needs to be fixed.",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.issue_url, issue_url)
        self.assertEqual(result.issue_id, 123)
        self.assertEqual(title_input.text, "Bug: Something is broken")
        self.assertEqual(desc_input.text, "This needs to be fixed.")

    def test_create_issue_without_description(self) -> None:
        """Test issue creation with title only."""
        title_input = FakeLocator(count_value=1)
        create_btn = FakeLocator(count_value=1)

        issue_url = f"{GITLAB_DOMAIN}/ns/proj/-/issues/1"

        page = FakePage(
            locators={
                Selectors.ISSUE_TITLE_INPUT: title_input,
                Selectors.ISSUE_DESCRIPTION_TEXTAREA: FakeLocator(count_value=1),
                Selectors.ISSUE_CREATE_BUTTON: create_btn,
                Selectors.ERROR_CONTAINER: FakeLocator(count_value=0),
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=get_new_issue_url("ns", "proj"),
        )

        create_btn.on_click = lambda: setattr(page, "url", issue_url)

        result = issues.create_issue_with_title(page, "ns", "proj", "Quick issue")

        self.assertTrue(result.success)
        self.assertEqual(title_input.text, "Quick issue")

    def test_create_issue_page_not_found(self) -> None:
        """Test issue creation when project page not found."""
        page = FakePage(
            locators={
                Selectors.PAGE_NOT_FOUND: FakeLocator(text="Page Not Found", count_value=1),
            },
            url=get_new_issue_url("ns", "proj"),
        )

        result = issues.create_issue(page, "ns", "proj", "Test")

        self.assertFalse(result.success)
        self.assertIn("cannot access", (result.error_message or "").lower())

    def test_create_issue_form_error(self) -> None:
        """Test issue creation with server-side error."""
        error_item = FakeLocator(text="Title is too short", count_value=1)

        page = FakePage(
            locators={
                Selectors.ISSUE_TITLE_INPUT: FakeLocator(count_value=1),
                Selectors.ISSUE_DESCRIPTION_TEXTAREA: FakeLocator(count_value=1),
                Selectors.ISSUE_CREATE_BUTTON: FakeLocator(count_value=1),
                Selectors.ERROR_CONTAINER: FakeLocator(
                    count_value=1,
                    nested={"ul li": FakeLocator(children=[error_item], all_inner_texts_result=["Title is too short"])},
                ),
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=get_new_issue_url("ns", "proj"),
        )

        result = issues.create_issue(page, "ns", "proj", "X")

        self.assertFalse(result.success)


class DeleteIssueTests(unittest.TestCase):
    def test_delete_issue_success(self) -> None:
        """Test successful issue deletion."""
        issue_actions_btn = FakeLocator(count_value=1)
        delete_btn = FakeLocator(count_value=1)
        confirm_btn = FakeLocator(count_value=1)
        success_alert = FakeLocator(text="The issue was successfully deleted", count_value=1)

        issue_url = f"{GITLAB_DOMAIN}/ns/proj/-/issues/42"

        page = FakePage(
            locators={
                "role:button:Issue actions": issue_actions_btn,
                Selectors.DELETE_ISSUE_BUTTON: delete_btn,
                Selectors.DELETE_ISSUE_MODAL: FakeLocator(count_value=1),
                Selectors.CONFIRM_DELETE_ISSUE_BUTTON: confirm_btn,
                'div.gl-alert-body:has-text("The issue was successfully deleted")': success_alert,
            },
            url=issue_url,
        )

        result = issues.delete_issue(page, issue_url)

        self.assertTrue(result.success)

    def test_delete_issue_by_id(self) -> None:
        """Test delete_issue_by_id constructs correct URL."""
        page = FakePage(
            locators={
                "role:button:Issue actions": FakeLocator(count_value=1),
                Selectors.DELETE_ISSUE_BUTTON: FakeLocator(count_value=1),
                Selectors.DELETE_ISSUE_MODAL: FakeLocator(count_value=1),
                Selectors.CONFIRM_DELETE_ISSUE_BUTTON: FakeLocator(count_value=1),
                'div.gl-alert-body:has-text("The issue was successfully deleted")': FakeLocator(count_value=1),
            },
            url="",
        )

        result = issues.delete_issue_by_id(page, "ns", "proj", 99)

        # Should have visited the correct URL
        expected_url = f"{GITLAB_DOMAIN}/ns/proj/-/issues/99"
        self.assertIn(expected_url, page.visited)


class GetIssuesTests(unittest.TestCase):
    def test_get_issues_parses_list(self) -> None:
        """Test parsing issues from project page."""
        issue1 = FakeLocator(
            text="First Issue",
            attributes={"href": f"{GITLAB_DOMAIN}/ns/proj/-/issues/1"},
        )
        issue2 = FakeLocator(
            text="Second Issue",
            attributes={"href": f"{GITLAB_DOMAIN}/ns/proj/-/issues/2"},
        )

        page = FakePage(
            locators={
                Selectors.ISSUE_TITLE_LINK: FakeLocator(children=[issue1, issue2], count_value=2),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/issues",
        )

        result = issues.get_issues(page, "ns", "proj")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].title, "First Issue")
        self.assertEqual(result[0].issue_id, 1)
        self.assertEqual(result[1].title, "Second Issue")
        self.assertEqual(result[1].issue_id, 2)

    def test_get_issues_empty_project(self) -> None:
        """Test get_issues on project with no issues."""
        page = FakePage(
            locators={
                Selectors.ISSUE_TITLE_LINK: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/issues",
        )

        result = issues.get_issues(page, "ns", "proj")

        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
