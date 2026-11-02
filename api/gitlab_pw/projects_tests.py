"""Checks for the projects helper."""

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

from api.gitlab_pw import projects  # noqa:E402
from api.gitlab_pw.constants import Selectors, GITLAB_DOMAIN, NEW_PROJECT_URL  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class ProjectDataclassTests(unittest.TestCase):
    def test_project_dataclass_fields(self) -> None:
        """Test Project dataclass has expected fields."""
        proj = projects.Project(
            name="My Project",
            slug="my-project",
            namespace="testuser",
            url=f"{GITLAB_DOMAIN}/testuser/my-project",
            visibility="private",
        )
        self.assertEqual(proj.name, "My Project")
        self.assertEqual(proj.slug, "my-project")
        self.assertEqual(proj.namespace, "testuser")
        self.assertEqual(proj.visibility, "private")

    def test_create_project_result_dataclass(self) -> None:
        """Test CreateProjectResult dataclass."""
        result = projects.CreateProjectResult(
            success=True,
            project_slug="new-project",
            project_url=f"{GITLAB_DOMAIN}/user/new-project",
            error_message=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.project_slug, "new-project")

        failed = projects.CreateProjectResult(
            success=False,
            project_slug=None,
            error_message="Name has already been taken",
        )
        self.assertFalse(failed.success)
        self.assertIn("taken", failed.error_message or "")

    def test_delete_project_result_dataclass(self) -> None:
        """Test DeleteProjectResult dataclass."""
        result = projects.DeleteProjectResult(success=True)
        self.assertTrue(result.success)


class CreateProjectTests(unittest.TestCase):
    def test_create_private_project_success(self) -> None:
        """Test successful project creation."""
        name_input = FakeLocator(count_value=1)
        create_btn = FakeLocator(count_value=1)
        visibility_checkbox = FakeLocator(count_value=1)

        project_url = f"{GITLAB_DOMAIN}/testuser/my-new-project"

        page = FakePage(
            locators={
                "label:Project name": name_input,
                "role:button:Create project": create_btn,
                "#blank-project-pane": FakeLocator(
                    count_value=1,
                    nested={"text:PrivateProject access must be": visibility_checkbox},
                ),
                Selectors.PROJECT_ERROR_CONTAINER: FakeLocator(count_value=0),
                Selectors.NAMESPACE_DROPDOWN_BUTTON: FakeLocator(count_value=0, is_visible=False),
            },
            url=NEW_PROJECT_URL,
        )

        create_btn.on_click = lambda: setattr(page, "url", project_url)

        result = projects.create_private_project(page, "my-new-project")

        self.assertTrue(result.success)
        self.assertEqual(result.project_slug, "my-new-project")
        self.assertEqual(result.project_url, project_url)

    def test_create_project_name_taken(self) -> None:
        """Test project creation when name is taken."""
        error_item = FakeLocator(text="has already been taken", count_value=1)
        error_container = FakeLocator(
            count_value=1,
            nested={"ul li": FakeLocator(children=[error_item], all_inner_texts_result=["has already been taken"])},
        )

        page = FakePage(
            locators={
                "label:Project name": FakeLocator(count_value=1),
                "role:button:Create project": FakeLocator(count_value=1),
                "#blank-project-pane": FakeLocator(count_value=1),
                Selectors.PROJECT_ERROR_CONTAINER: error_container,
                Selectors.NAMESPACE_DROPDOWN_BUTTON: FakeLocator(count_value=0),
            },
            url=NEW_PROJECT_URL,
        )

        result = projects.create_private_project(page, "existing-project")

        # Should succeed because the project exists
        self.assertTrue(result.success)
        self.assertEqual(result.project_slug, "existing-project")

    def test_create_project_with_namespace(self) -> None:
        """Test project creation under specific namespace."""
        name_input = FakeLocator(count_value=1)
        create_btn = FakeLocator(count_value=1)
        dropdown_btn = FakeLocator(count_value=1, is_visible=True)
        namespace_item = FakeLocator(count_value=1, is_visible=True)

        project_url = f"{GITLAB_DOMAIN}/mygroup/grouped-project"

        page = FakePage(
            locators={
                "label:Project name": name_input,
                "role:button:Create project": create_btn,
                "#blank-project-pane": FakeLocator(count_value=1),
                Selectors.PROJECT_ERROR_CONTAINER: FakeLocator(count_value=0),
                Selectors.NAMESPACE_DROPDOWN_BUTTON: dropdown_btn,
                "li.gl-dropdown-item >> text=mygroup": namespace_item,
            },
            url=NEW_PROJECT_URL,
        )

        create_btn.on_click = lambda: setattr(page, "url", project_url)

        result = projects.create_private_project(page, "grouped-project", namespace_name="mygroup")

        self.assertTrue(result.success)
        self.assertEqual(result.project_slug, "grouped-project")


class DeleteProjectTests(unittest.TestCase):
    def test_delete_project_success(self) -> None:
        """Test successful project deletion."""
        expand_btn = FakeLocator(count_value=1)
        delete_trigger = FakeLocator(count_value=1)
        confirm_input = FakeLocator(count_value=1)
        confirm_btn = FakeLocator(count_value=1)
        alert = FakeLocator(text="is in the process of being deleted.", count_value=1)

        settings_url = f"{GITLAB_DOMAIN}/ns/proj/edit"
        dashboard_url = f"{GITLAB_DOMAIN}/dashboard/projects"

        page = FakePage(
            locators={
                f"section#js-project-advanced-settings {Selectors.EXPAND_BUTTON}": expand_btn,
                'button:has-text("Delete project")': delete_trigger,
                Selectors.CONFIRM_NAME_INPUT: confirm_input,
                'button:has-text("Yes, delete project")': confirm_btn,
                Selectors.ALERT_BODY: alert,
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=settings_url,
        )

        confirm_btn.on_click = lambda: setattr(page, "url", dashboard_url)

        result = projects.delete_project(page, "ns", "proj")

        self.assertTrue(result.success)

    def test_delete_project_not_found(self) -> None:
        """Test deleting already-deleted project."""
        page = FakePage(
            locators={
                Selectors.PAGE_NOT_FOUND: FakeLocator(text="Page Not Found", count_value=1),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/edit",
        )

        result = projects.delete_project(page, "ns", "proj")

        # Should succeed because project is already gone
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
