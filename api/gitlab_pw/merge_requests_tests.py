"""Checks for the merge_requests helper."""

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

# Create a mock expect function that returns a mock object
class MockExpect:
    def __init__(self, locator):
        self.locator = locator

    def to_have_text(self, text, timeout=None):
        if self.locator.text != text:
            raise TimeoutError(f"Expected '{text}' but got '{self.locator.text}'")

playwright_stub = types.ModuleType("playwright")
playwright_stub.sync_api = types.SimpleNamespace(
    Page=object,
    TimeoutError=TimeoutError,
    expect=MockExpect,
)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

from api.gitlab_pw import merge_requests  # noqa:E402
from api.gitlab_pw.constants import Selectors, GITLAB_DOMAIN  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class MergeRequestDataclassTests(unittest.TestCase):
    def test_merge_request_dataclass_fields(self) -> None:
        """Test MergeRequest dataclass has expected fields."""
        mr = merge_requests.MergeRequest(
            mr_id=42,
            title="Add new feature",
            url=f"{GITLAB_DOMAIN}/ns/proj/-/merge_requests/42",
            source_branch="feature-branch",
            target_branch="main",
            state="opened",
            author="developer",
        )
        self.assertEqual(mr.mr_id, 42)
        self.assertEqual(mr.title, "Add new feature")
        self.assertEqual(mr.source_branch, "feature-branch")
        self.assertEqual(mr.target_branch, "main")
        self.assertEqual(mr.state, "opened")
        self.assertEqual(mr.author, "developer")

    def test_create_merge_request_result_dataclass(self) -> None:
        """Test CreateMergeRequestResult dataclass."""
        result = merge_requests.CreateMergeRequestResult(
            success=True,
            mr_id=123,
            mr_url=f"{GITLAB_DOMAIN}/ns/proj/-/merge_requests/123",
            error_message=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.mr_id, 123)

        failed = merge_requests.CreateMergeRequestResult(
            success=False,
            mr_id=None,
            error_message="Branch has no commits",
        )
        self.assertFalse(failed.success)
        self.assertIn("commits", failed.error_message or "")

    def test_close_merge_request_result_dataclass(self) -> None:
        """Test CloseMergeRequestResult dataclass."""
        result = merge_requests.CloseMergeRequestResult(success=True)
        self.assertTrue(result.success)

        failed = merge_requests.CloseMergeRequestResult(
            success=False,
            error_message="Permission denied",
        )
        self.assertFalse(failed.success)


class CreateMergeRequestTests(unittest.TestCase):
    def test_create_merge_request_success(self) -> None:
        """Test successful merge request creation."""
        title_input = FakeLocator(count_value=1)
        create_btn = FakeLocator(count_value=1)

        mr_url = f"{GITLAB_DOMAIN}/ns/proj/-/merge_requests/99"

        page = FakePage(
            locators={
                Selectors.MR_TITLE_INPUT: title_input,
                Selectors.MR_CREATE_BUTTON: create_btn,
                Selectors.ERROR_CONTAINER: FakeLocator(count_value=0),
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/merge_requests/new?merge_request%5Bsource_branch%5D=feature",
        )

        create_btn.on_click = lambda: setattr(page, "url", mr_url)

        result = merge_requests.create_merge_request(
            page, "ns", "proj", "feature", "Fix critical bug"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.mr_id, 99)
        self.assertEqual(result.mr_url, mr_url)
        self.assertEqual(title_input.text, "Fix critical bug")

    def test_create_merge_request_page_not_found(self) -> None:
        """Test MR creation when page not found."""
        page = FakePage(
            locators={
                Selectors.PAGE_NOT_FOUND: FakeLocator(text="Page Not Found", count_value=1),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/merge_requests/new",
        )

        result = merge_requests.create_merge_request(
            page, "ns", "proj", "branch", "Title"
        )

        self.assertFalse(result.success)
        self.assertIn("cannot access", (result.error_message or "").lower())

    def test_create_merge_request_with_error(self) -> None:
        """Test MR creation with server error."""
        error_item = FakeLocator(text="Source branch cannot be the same as target", count_value=1)

        page = FakePage(
            locators={
                Selectors.MR_TITLE_INPUT: FakeLocator(count_value=1),
                Selectors.ERROR_CONTAINER: FakeLocator(
                    count_value=1,
                    nested={"ul li": FakeLocator(children=[error_item], all_inner_texts_result=["Source branch cannot be the same as target"])},
                ),
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/merge_requests/new",
        )

        result = merge_requests.create_merge_request(
            page, "ns", "proj", "main", "Test MR"
        )

        self.assertFalse(result.success)


class CloseMergeRequestTests(unittest.TestCase):
    def test_close_merge_request_result_success(self) -> None:
        """Test CloseMergeRequestResult dataclass for success."""
        result = merge_requests.CloseMergeRequestResult(success=True)
        self.assertTrue(result.success)
        self.assertIsNone(result.error_message)

    def test_close_merge_request_not_found(self) -> None:
        """Test closing non-existent MR."""
        page = FakePage(
            locators={
                Selectors.PAGE_NOT_FOUND: FakeLocator(text="Page Not Found", count_value=1),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/merge_requests/999",
        )

        result = merge_requests.close_merge_request(page, "ns", "proj", 999)

        self.assertFalse(result.success)
        self.assertIn("not found", (result.error_message or "").lower())

    def test_close_merge_request_result_failure(self) -> None:
        """Test CloseMergeRequestResult dataclass for failure."""
        result = merge_requests.CloseMergeRequestResult(
            success=False,
            error_message="Status did not change to 'Closed'",
        )
        self.assertFalse(result.success)
        self.assertIn("Closed", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
