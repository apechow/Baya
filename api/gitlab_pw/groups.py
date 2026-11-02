"""GitLab group management helpers."""

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError

from .constants import (
    GITLAB_DOMAIN,
    NEW_GROUP_URL,
    Selectors,
    get_group_url,
    get_group_members_url,
    get_group_settings_url,
)


@dataclass
class Group:
    """Representation of a GitLab group."""

    name: str
    slug: str
    url: str
    visibility: Optional[str] = None


@dataclass
class GroupMember:
    """Representation of a group member."""

    username: str
    display_name: Optional[str] = None
    role: Optional[str] = None


@dataclass
class CreateGroupResult:
    """Result of attempting to create a group."""

    success: bool
    group_slug: Optional[str]
    group_url: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class DeleteGroupResult:
    """Result of attempting to delete a group."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class AddMemberResult:
    """Result of attempting to add a member to a group."""

    success: bool
    error_message: Optional[str] = None


def create_private_group(page: Page, group_name: str) -> CreateGroupResult:
    """
    Create a new private group in GitLab.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        group_name: Name for the new group

    Returns:
        CreateGroupResult with success status, slug, and any error message
    """
    page.goto(NEW_GROUP_URL, wait_until="networkidle")

    # Wait for group name field
    try:
        page.locator(Selectors.GROUP_NAME_INPUT).wait_for(state="visible", timeout=10000)
    except TimeoutError:
        return CreateGroupResult(
            success=False,
            group_slug=None,
            error_message="Group name field not found"
        )

    # Fill group name
    page.locator(Selectors.GROUP_NAME_INPUT).fill(group_name)

    # Wait for URL slug generation (GitLab may append numbers for conflicts)
    page.wait_for_timeout(3000)

    # Select private visibility
    page.locator(Selectors.GROUP_VISIBILITY_PRIVATE).check()

    # Fill required role dropdown
    page.locator(Selectors.GROUP_ROLE_SELECT).select_option("software_developer")

    # Select company setup option
    page.locator(Selectors.GROUP_SETUP_COMPANY).click()

    # Select "exploring" for jobs dropdown
    page.locator(Selectors.GROUP_JOBS_SELECT).select_option("exploring")

    # Submit - FIXED: sync pattern
    page.get_by_role("button", name="Create group").click()
    page.wait_for_load_state("networkidle")

    # Check for errors
    error_container = page.locator(Selectors.ERROR_CONTAINER)
    if error_container.count() > 0:
        errors = error_container.locator("ul li").all_inner_texts()
        return CreateGroupResult(
            success=False,
            group_slug=None,
            error_message="; ".join(errors)
        )

    # Extract group slug from final URL
    final_url = page.url
    parsed = urlparse(final_url)
    group_slug = parsed.path.strip("/").split("/")[-1]

    return CreateGroupResult(
        success=True,
        group_slug=group_slug,
        group_url=final_url,
        error_message=None
    )


def delete_group(page: Page, group_name: str) -> DeleteGroupResult:
    """
    Delete a group from GitLab.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        group_name: Name/slug of the group to delete

    Returns:
        DeleteGroupResult with success status and any error message
    """
    settings_url = get_group_settings_url(group_name)
    page.goto(settings_url, wait_until="networkidle")

    # Check for page not found
    if _is_page_not_found(page):
        return DeleteGroupResult(success=True)  # Already deleted

    # Expand advanced settings section
    try:
        expand_selector = f"section#{Selectors.DELETE_GROUP_SECTION.lstrip('#')} {Selectors.EXPAND_BUTTON}"
        page.click(expand_selector)
    except Exception:
        return DeleteGroupResult(
            success=False,
            error_message="Could not expand settings section"
        )

    # Click delete trigger button
    try:
        page.wait_for_selector(Selectors.DELETE_GROUP_BUTTON, timeout=3000)
        page.locator(Selectors.DELETE_GROUP_BUTTON).click()
    except TimeoutError:
        return DeleteGroupResult(
            success=False,
            error_message="Delete button not found"
        )

    # Fill confirmation input
    try:
        page.wait_for_selector(Selectors.CONFIRM_NAME_INPUT, timeout=3000)
        page.locator(Selectors.CONFIRM_NAME_INPUT).fill(group_name)
    except TimeoutError:
        return DeleteGroupResult(
            success=False,
            error_message="Confirmation input not found"
        )

    # Confirm deletion - FIXED: sync pattern
    page.locator(Selectors.DELETE_GROUP_CONFIRM).click()
    page.wait_for_load_state("networkidle")

    # Verify deletion
    if page.url.rstrip("/") == GITLAB_DOMAIN.rstrip("/"):
        alert = page.locator(Selectors.ALERT_BODY)
        if alert.count() > 0:
            text = alert.text_content() or ""
            if "deleted" in text.lower():
                return DeleteGroupResult(success=True)
        return DeleteGroupResult(success=True)

    return DeleteGroupResult(
        success=False,
        error_message=f"Deletion may have failed; ended up at {page.url}"
    )


def get_group_members(page: Page, group_name: str) -> List[GroupMember]:
    """
    Get all members of a group.

    Args:
        page: Playwright Page instance
        group_name: Name/slug of the group

    Returns:
        List of GroupMember objects
    """
    url = get_group_members_url(group_name)
    page.goto(url, wait_until="networkidle")

    members: List[GroupMember] = []

    table = page.locator(Selectors.MEMBERS_TABLE)
    if table.count() == 0:
        return members

    rows = table.locator("tbody tr")
    for i in range(rows.count()):
        row = rows.nth(i)
        account_cell = row.locator("td:nth-child(1)")

        if account_cell.count() > 0:
            username_elem = account_cell.locator(".gl-avatar-labeled-sublabel")
            if username_elem.count() > 0:
                username = username_elem.inner_text().strip().lstrip("@")
                members.append(GroupMember(username=username))

    return members


def add_member_to_group(
    page: Page,
    group_name: str,
    username: str,
    role: str = "40",  # 40 = Maintainer
    timeout_after_actions: int = 2000,
) -> AddMemberResult:
    """
    Add a user to a group as a maintainer (or other role).

    Args:
        page: Playwright Page instance
        group_name: Name/slug of the group
        username: Username to add
        role: Role value (default "40" = Maintainer)
        timeout_after_actions: Wait time in ms after actions

    Returns:
        AddMemberResult with success status and any error message
    """
    url = get_group_members_url(group_name)
    page.goto(url, wait_until="networkidle")

    # Check if user is already a member
    existing_members = get_group_members(page, group_name)
    existing_usernames = [m.username for m in existing_members]

    if username in existing_usernames:
        return AddMemberResult(
            success=True,
            error_message=f"User '{username}' is already a member"
        )

    # Click invite button
    try:
        page.click(Selectors.INVITE_MEMBERS_BUTTON)
    except Exception:
        return AddMemberResult(
            success=False,
            error_message="Invite members button not found"
        )

    # Wait for modal
    try:
        page.wait_for_selector(Selectors.INVITE_MODAL, state="visible", timeout=5000)
    except TimeoutError:
        return AddMemberResult(
            success=False,
            error_message="Invite modal did not appear"
        )

    # Search for user
    page.click(Selectors.INVITE_SEARCH_INPUT)
    page.fill(Selectors.INVITE_SEARCH_INPUT, username)
    page.wait_for_timeout(timeout_after_actions)

    # Select user from dropdown
    user_option = page.locator(
        f"ul.dropdown-menu li:has(span.gl-avatar-labeled-sublabel:text-is('{username}')) button"
    )
    if user_option.count() == 0:
        return AddMemberResult(
            success=False,
            error_message=f"User '{username}' not found in search results"
        )
    user_option.click()

    # Select role
    page.select_option(Selectors.INVITE_ROLE_SELECT, role)
    page.wait_for_timeout(timeout_after_actions)

    # Click invite
    page.click(Selectors.INVITE_CONFIRM_BUTTON)

    # Wait for success message
    try:
        page.wait_for_selector(
            "div.gl-alert-body:has-text('Members were successfully added')",
            state="visible",
            timeout=5000
        )
    except TimeoutError:
        return AddMemberResult(
            success=False,
            error_message="Did not receive success confirmation"
        )

    # Verify member was added
    updated_members = get_group_members(page, group_name)
    updated_usernames = [m.username for m in updated_members]

    if username in updated_usernames:
        return AddMemberResult(success=True)

    return AddMemberResult(
        success=False,
        error_message=f"User '{username}' not found in members after adding"
    )


def _is_page_not_found(page: Page) -> bool:
    """Check if the current page shows a 'Page Not Found' error."""
    container = page.locator(Selectors.PAGE_NOT_FOUND)
    if container.count() > 0:
        text = container.text_content()
        if text and "Page Not Found" in text:
            return True
    return False
