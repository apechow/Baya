"""Checks for the branches helper."""

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

# Create a mock expect function
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

from api.gitlab_pw import branches  # noqa:E402
from api.gitlab_pw.constants import Selectors, GITLAB_DOMAIN  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class BranchDataclassTests(unittest.TestCase):
    def test_branch_dataclass_fields(self) -> None:
        """Test Branch dataclass has expected fields."""
        branch = branches.Branch(
            name="feature-branch",
            is_default=False,
            is_protected=True,
        )
        self.assertEqual(branch.name, "feature-branch")
        self.assertFalse(branch.is_default)
        self.assertTrue(branch.is_protected)

    def test_create_branch_result_dataclass(self) -> None:
        """Test CreateBranchResult dataclass."""
        result = branches.CreateBranchResult(
            success=True,
            branch_name="new-feature",
            error_message=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.branch_name, "new-feature")

        failed = branches.CreateBranchResult(
            success=False,
            branch_name=None,
            error_message="Branch already exists",
        )
        self.assertFalse(failed.success)
        self.assertIn("exists", failed.error_message or "")

    def test_delete_branch_result_dataclass(self) -> None:
        """Test DeleteBranchResult dataclass."""
        result = branches.DeleteBranchResult(success=True)
        self.assertTrue(result.success)


class CreateBranchTests(unittest.TestCase):
    def test_create_branch_success(self) -> None:
        """Test successful branch creation."""
        name_input = FakeLocator(count_value=1)
        create_btn = FakeLocator(count_value=1)

        branches_url = f"{GITLAB_DOMAIN}/ns/proj/-/branches"

        page = FakePage(
            locators={
                Selectors.BRANCH_NAME_INPUT: name_input,
                Selectors.BRANCH_CREATE_BUTTON: create_btn,
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/branches/new",
        )

        create_btn.on_click = lambda: setattr(page, "url", branches_url)

        result = branches.create_branch(page, "ns", "proj", "feature-xyz")

        self.assertTrue(result.success)
        self.assertEqual(result.branch_name, "feature-xyz")
        self.assertEqual(name_input.text, "feature-xyz")

    def test_create_branch_page_not_found(self) -> None:
        """Test branch creation when page not found."""
        page = FakePage(
            locators={
                Selectors.PAGE_NOT_FOUND: FakeLocator(text="Page Not Found", count_value=1),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/branches/new",
        )

        result = branches.create_branch(page, "ns", "proj", "new-branch")

        self.assertFalse(result.success)
        self.assertIn("cannot access", (result.error_message or "").lower())


class DeleteBranchTests(unittest.TestCase):
    def test_delete_branch_result_success(self) -> None:
        """Test DeleteBranchResult dataclass for success."""
        result = branches.DeleteBranchResult(success=True)
        self.assertTrue(result.success)
        self.assertIsNone(result.error_message)

    def test_delete_branch_result_failure(self) -> None:
        """Test DeleteBranchResult dataclass for failure."""
        result = branches.DeleteBranchResult(
            success=False,
            error_message="Did not receive deletion confirmation",
        )
        self.assertFalse(result.success)
        self.assertIn("confirmation", result.error_message or "")

    def test_delete_branch_not_found(self) -> None:
        """Test deleting non-existent branch."""
        page = FakePage(
            locators={
                'li.branch-item[data-name="nonexistent"]': FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/branches",
        )

        result = branches.delete_branch(page, "ns", "proj", "nonexistent")

        self.assertFalse(result.success)
        self.assertIn("not found", (result.error_message or "").lower())


class GetBranchesTests(unittest.TestCase):
    def test_get_branches_parses_list(self) -> None:
        """Test parsing branches from project page."""
        branch1 = FakeLocator(
            count_value=1,
            attributes={"data-name": "main"},
            nested={
                ".badge:has-text('default')": FakeLocator(count_value=1),
                ".badge:has-text('protected')": FakeLocator(count_value=1),
            },
        )
        branch2 = FakeLocator(
            count_value=1,
            attributes={"data-name": "feature"},
            nested={
                ".badge:has-text('default')": FakeLocator(count_value=0),
                ".badge:has-text('protected')": FakeLocator(count_value=0),
            },
        )

        page = FakePage(
            locators={
                Selectors.BRANCH_ITEM: FakeLocator(children=[branch1, branch2], count_value=2),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/branches",
        )

        result = branches.get_branches(page, "ns", "proj")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].name, "main")
        self.assertTrue(result[0].is_default)
        self.assertTrue(result[0].is_protected)
        self.assertEqual(result[1].name, "feature")
        self.assertFalse(result[1].is_default)
        self.assertFalse(result[1].is_protected)

    def test_get_branches_empty_repo(self) -> None:
        """Test get_branches on repo with no branches."""
        page = FakePage(
            locators={
                Selectors.BRANCH_ITEM: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/branches",
        )

        result = branches.get_branches(page, "ns", "proj")

        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
