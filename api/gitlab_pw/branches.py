"""GitLab branch management helpers."""

from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Page, TimeoutError, expect

from .constants import (
    Selectors,
    get_new_branch_url,
    get_project_branches_url,
)


@dataclass
class Branch:
    """Representation of a GitLab branch."""

    name: str
    is_default: bool = False
    is_protected: bool = False


@dataclass
class CreateBranchResult:
    """Result of attempting to create a branch."""

    success: bool
    branch_name: Optional[str]
    error_message: Optional[str] = None


@dataclass
class DeleteBranchResult:
    """Result of attempting to delete a branch."""

    success: bool
    error_message: Optional[str] = None


def create_branch(
    page: Page,
    namespace: str,
    project: str,
    branch_name: str,
) -> CreateBranchResult:
    """
    Create a new branch from main (default branch).

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        branch_name: Name for the new branch

    Returns:
        CreateBranchResult with success status and any error message
    """
    url = get_new_branch_url(namespace, project)
    page.goto(url, wait_until="networkidle")

    if _is_page_not_found(page):
        return CreateBranchResult(
            success=False,
            branch_name=None,
            error_message=f"Cannot access branch creation page at {url}"
        )

    # Fill branch name
    try:
        page.wait_for_selector(Selectors.BRANCH_NAME_INPUT, timeout=10000)
        page.fill(Selectors.BRANCH_NAME_INPUT, branch_name)
    except TimeoutError:
        return CreateBranchResult(
            success=False,
            branch_name=None,
            error_message="Branch name field not found"
        )

    # Submit - FIXED: sync pattern
    try:
        page.wait_for_selector(Selectors.BRANCH_CREATE_BUTTON, timeout=5000)
        page.click(Selectors.BRANCH_CREATE_BUTTON)
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        return CreateBranchResult(
            success=False,
            branch_name=None,
            error_message="Create branch button not found"
        )

    # Verify we're no longer on the new branch page
    if "branches/new" not in page.url:
        return CreateBranchResult(
            success=True,
            branch_name=branch_name,
            error_message=None
        )

    return CreateBranchResult(
        success=False,
        branch_name=None,
        error_message=f"Branch creation may have failed; still at {page.url}"
    )


def delete_branch(
    page: Page,
    namespace: str,
    project: str,
    branch_name: str,
) -> DeleteBranchResult:
    """
    Delete a branch from a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        branch_name: Name of the branch to delete

    Returns:
        DeleteBranchResult with success status and any error message
    """
    url = get_project_branches_url(namespace, project)
    page.goto(url, wait_until="networkidle")

    # Find the branch item
    branch_selector = f'li.branch-item[data-name="{branch_name}"]'
    branch_item = page.locator(branch_selector)

    if branch_item.count() == 0:
        return DeleteBranchResult(
            success=False,
            error_message=f"Branch '{branch_name}' not found in active branches"
        )

    # Click delete button for this branch
    delete_button = branch_item.locator(Selectors.BRANCH_DELETE_BUTTON)
    if delete_button.count() == 0:
        return DeleteBranchResult(
            success=False,
            error_message=f"Delete button not found for branch '{branch_name}'"
        )

    delete_button.click()

    # Wait for and click confirmation
    try:
        page.wait_for_selector(Selectors.BRANCH_DELETE_CONFIRM, timeout=5000)
        page.click(Selectors.BRANCH_DELETE_CONFIRM)
    except TimeoutError:
        return DeleteBranchResult(
            success=False,
            error_message="Delete confirmation button not found"
        )

    # Verify deletion
    alert_selector = "#content-body > div.flash-container.flash-container-page.sticky > div > div > div"
    try:
        expect(page.locator(alert_selector)).to_have_text("Branch was deleted", timeout=5000)
        return DeleteBranchResult(success=True)
    except TimeoutError:
        return DeleteBranchResult(
            success=False,
            error_message="Did not receive deletion confirmation"
        )


def get_branches(
    page: Page,
    namespace: str,
    project: str,
) -> List[Branch]:
    """
    Get all branches in a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name

    Returns:
        List of Branch objects
    """
    url = get_project_branches_url(namespace, project)
    page.goto(url, wait_until="networkidle")

    branches: List[Branch] = []
    branch_items = page.locator(Selectors.BRANCH_ITEM)

    for i in range(branch_items.count()):
        item = branch_items.nth(i)
        name = item.get_attribute("data-name") or ""

        is_default = item.locator(".badge:has-text('default')").count() > 0
        is_protected = item.locator(".badge:has-text('protected')").count() > 0

        if name:
            branches.append(Branch(
                name=name,
                is_default=is_default,
                is_protected=is_protected,
            ))

    return branches


def _is_page_not_found(page: Page) -> bool:
    """Check if the current page shows a 'Page Not Found' error."""
    container = page.locator(Selectors.PAGE_NOT_FOUND)
    if container.count() > 0:
        text = container.text_content()
        if text and "Page Not Found" in text:
            return True
    return False
