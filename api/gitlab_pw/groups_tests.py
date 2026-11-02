"""Checks for the groups helper."""

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

from api.gitlab_pw import groups  # noqa:E402
from api.gitlab_pw.constants import Selectors, GITLAB_DOMAIN, NEW_GROUP_URL  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class GroupDataclassTests(unittest.TestCase):
    def test_group_dataclass_fields(self) -> None:
        """Test Group dataclass has expected fields."""
        group = groups.Group(
            name="My Group",
            slug="my-group",
            url=f"{GITLAB_DOMAIN}/groups/my-group",
            visibility="private",
        )
        self.assertEqual(group.name, "My Group")
        self.assertEqual(group.slug, "my-group")
        self.assertEqual(group.visibility, "private")

    def test_group_member_dataclass(self) -> None:
        """Test GroupMember dataclass."""
        member = groups.GroupMember(
            username="testuser",
            display_name="Test User",
            role="Maintainer",
        )
        self.assertEqual(member.username, "testuser")
        self.assertEqual(member.display_name, "Test User")
        self.assertEqual(member.role, "Maintainer")

    def test_create_group_result_dataclass(self) -> None:
        """Test CreateGroupResult dataclass."""
        result = groups.CreateGroupResult(
            success=True,
            group_slug="new-group",
            group_url=f"{GITLAB_DOMAIN}/groups/new-group",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.group_slug, "new-group")

    def test_add_member_result_dataclass(self) -> None:
        """Test AddMemberResult dataclass."""
        result = groups.AddMemberResult(success=True)
        self.assertTrue(result.success)

        failed = groups.AddMemberResult(
            success=False,
            error_message="User not found",
        )
        self.assertFalse(failed.success)
        self.assertIn("not found", failed.error_message or "")


class CreateGroupTests(unittest.TestCase):
    def test_create_private_group_success(self) -> None:
        """Test successful group creation."""
        name_input = FakeLocator(count_value=1)
        visibility_checkbox = FakeLocator(count_value=1)
        role_select = FakeLocator(count_value=1)
        company_radio = FakeLocator(count_value=1)
        jobs_select = FakeLocator(count_value=1)
        create_btn = FakeLocator(count_value=1)

        group_url = f"{GITLAB_DOMAIN}/groups/my-new-group"

        page = FakePage(
            locators={
                Selectors.GROUP_NAME_INPUT: name_input,
                Selectors.GROUP_VISIBILITY_PRIVATE: visibility_checkbox,
                Selectors.GROUP_ROLE_SELECT: role_select,
                Selectors.GROUP_SETUP_COMPANY: company_radio,
                Selectors.GROUP_JOBS_SELECT: jobs_select,
                "role:button:Create group": create_btn,
                Selectors.ERROR_CONTAINER: FakeLocator(count_value=0),
            },
            url=NEW_GROUP_URL,
        )

        create_btn.on_click = lambda: setattr(page, "url", group_url)

        result = groups.create_private_group(page, "my-new-group")

        self.assertTrue(result.success)
        self.assertEqual(result.group_slug, "my-new-group")
        self.assertEqual(result.group_url, group_url)
        self.assertEqual(name_input.text, "my-new-group")

    def test_create_group_with_error(self) -> None:
        """Test group creation with server error."""
        error_item = FakeLocator(text="Path has already been taken", count_value=1)

        page = FakePage(
            locators={
                Selectors.GROUP_NAME_INPUT: FakeLocator(count_value=1),
                Selectors.GROUP_VISIBILITY_PRIVATE: FakeLocator(count_value=1),
                Selectors.GROUP_ROLE_SELECT: FakeLocator(count_value=1),
                Selectors.GROUP_SETUP_COMPANY: FakeLocator(count_value=1),
                Selectors.GROUP_JOBS_SELECT: FakeLocator(count_value=1),
                "role:button:Create group": FakeLocator(count_value=1),
                Selectors.ERROR_CONTAINER: FakeLocator(
                    count_value=1,
                    nested={"ul li": FakeLocator(children=[error_item], all_inner_texts_result=["Path has already been taken"])},
                ),
            },
            url=NEW_GROUP_URL,
        )

        result = groups.create_private_group(page, "existing-group")

        self.assertFalse(result.success)
        self.assertIn("taken", result.error_message or "")


class DeleteGroupTests(unittest.TestCase):
    def test_delete_group_success(self) -> None:
        """Test successful group deletion."""
        expand_btn = FakeLocator(count_value=1)
        delete_trigger = FakeLocator(count_value=1)
        confirm_input = FakeLocator(count_value=1)
        confirm_btn = FakeLocator(count_value=1)
        alert = FakeLocator(text="is in the process of being deleted.", count_value=1)

        page = FakePage(
            locators={
                f"section#js-advanced-settings {Selectors.EXPAND_BUTTON}": expand_btn,
                Selectors.DELETE_GROUP_BUTTON: delete_trigger,
                Selectors.CONFIRM_NAME_INPUT: confirm_input,
                Selectors.DELETE_GROUP_CONFIRM: confirm_btn,
                Selectors.ALERT_BODY: alert,
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/groups/mygroup/-/edit",
        )

        confirm_btn.on_click = lambda: setattr(page, "url", GITLAB_DOMAIN)

        result = groups.delete_group(page, "mygroup")

        self.assertTrue(result.success)

    def test_delete_group_not_found(self) -> None:
        """Test deleting already-deleted group."""
        page = FakePage(
            locators={
                Selectors.PAGE_NOT_FOUND: FakeLocator(text="Page Not Found", count_value=1),
            },
            url=f"{GITLAB_DOMAIN}/groups/mygroup/-/edit",
        )

        result = groups.delete_group(page, "mygroup")

        self.assertTrue(result.success)


class GetGroupMembersTests(unittest.TestCase):
    def test_get_group_members_parses_list(self) -> None:
        """Test parsing members from group page."""
        member1 = FakeLocator(text="user1", count_value=1)
        member2 = FakeLocator(text="user2", count_value=1)

        row1 = FakeLocator(
            count_value=1,
            nested={
                "td:nth-child(1)": FakeLocator(
                    count_value=1,
                    nested={".gl-avatar-labeled-sublabel": FakeLocator(text="@user1", count_value=1)},
                ),
            },
        )
        row2 = FakeLocator(
            count_value=1,
            nested={
                "td:nth-child(1)": FakeLocator(
                    count_value=1,
                    nested={".gl-avatar-labeled-sublabel": FakeLocator(text="@user2", count_value=1)},
                ),
            },
        )

        table = FakeLocator(
            count_value=1,
            nested={"tbody tr": FakeLocator(children=[row1, row2], count_value=2)},
        )

        page = FakePage(
            locators={
                Selectors.MEMBERS_TABLE: table,
            },
            url=f"{GITLAB_DOMAIN}/groups/mygroup/-/group_members",
        )

        result = groups.get_group_members(page, "mygroup")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].username, "user1")
        self.assertEqual(result[1].username, "user2")


class AddMemberTests(unittest.TestCase):
    def test_add_member_already_exists(self) -> None:
        """Test adding member who is already in group."""
        row = FakeLocator(
            count_value=1,
            nested={
                "td:nth-child(1)": FakeLocator(
                    count_value=1,
                    nested={".gl-avatar-labeled-sublabel": FakeLocator(text="@existinguser", count_value=1)},
                ),
            },
        )
        table = FakeLocator(
            count_value=1,
            nested={"tbody tr": FakeLocator(children=[row], count_value=1)},
        )

        page = FakePage(
            locators={
                Selectors.MEMBERS_TABLE: table,
            },
            url=f"{GITLAB_DOMAIN}/groups/mygroup/-/group_members",
        )

        result = groups.add_member_to_group(page, "mygroup", "existinguser")

        self.assertTrue(result.success)
        self.assertIn("already", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
