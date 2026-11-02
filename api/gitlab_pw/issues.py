"""GitLab issue management helpers."""

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Page, TimeoutError

from .constants import (
    GITLAB_DOMAIN,
    Selectors,
    get_new_issue_url,
    get_project_issues_url,
    get_issue_url,
)


@dataclass
class Issue:
    """Representation of a GitLab issue."""

    issue_id: int
    title: str
    url: str
    state: Optional[str] = None  # "opened", "closed"
    author: Optional[str] = None
    labels: Optional[List[str]] = None


@dataclass
class CreateIssueResult:
    """Result of attempting to create an issue."""

    success: bool
    issue_url: Optional[str]
    issue_id: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class DeleteIssueResult:
    """Result of attempting to delete an issue."""

    success: bool
    error_message: Optional[str] = None


def create_issue(
    page: Page,
    namespace: str,
    project: str,
    title: str,
    description: Optional[str] = None,
) -> CreateIssueResult:
    """
    Create a new issue in a GitLab project.

    FIXED: Uses sync pattern (click + wait_for_load_state) instead of
    the incorrect expect_navigation context manager.

    Args:
        page: Playwright Page instance
        namespace: Project namespace (owner username or group name)
        project: Project name
        title: Issue title
        description: Optional issue description/body

    Returns:
        CreateIssueResult with success status, URL, and any error message
    """
    url = get_new_issue_url(namespace, project)
    page.goto(url, wait_until="networkidle")

    # Check for access errors
    if _is_page_not_found(page):
        return CreateIssueResult(
            success=False,
            issue_url=None,
            error_message=f"Cannot access issue creation page at {url}"
        )

    # Wait for form
    try:
        page.wait_for_selector(Selectors.ISSUE_TITLE_INPUT, timeout=10000)
    except TimeoutError:
        return CreateIssueResult(
            success=False,
            issue_url=None,
            error_message="Issue form not found on page"
        )

    # Fill in issue details
    page.fill(Selectors.ISSUE_TITLE_INPUT, title)

    if description:
        page.fill(Selectors.ISSUE_DESCRIPTION_TEXTAREA, description)

    # Submit - FIXED: sync pattern
    page.click(Selectors.ISSUE_CREATE_BUTTON)
    page.wait_for_load_state("networkidle")

    # Check for errors
    error_loc = page.locator(Selectors.ERROR_CONTAINER)
    if error_loc.count() > 0:
        errors = error_loc.locator("ul li").all_inner_texts()
        return CreateIssueResult(
            success=False,
            issue_url=None,
            error_message="; ".join(errors) if errors else "Failed to create issue"
        )

    # Verify we landed on the new issue page
    issue_url_pattern = rf".*{re.escape(namespace)}/{re.escape(project)}/-/issues/(\d+)$"
    match = re.match(issue_url_pattern, page.url)

    if match:
        issue_id = int(match.group(1))
        return CreateIssueResult(
            success=True,
            issue_url=page.url,
            issue_id=issue_id,
            error_message=None
        )

    return CreateIssueResult(
        success=False,
        issue_url=None,
        error_message=f"Issue creation may have failed; ended up at {page.url}"
    )


def create_issue_with_title(
    page: Page,
    namespace: str,
    project: str,
    title: str,
) -> CreateIssueResult:
    """Create an issue with just a title (convenience wrapper)."""
    return create_issue(page, namespace, project, title)


def delete_issue(page: Page, issue_url: str) -> DeleteIssueResult:
    """
    Delete an issue given its URL.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        issue_url: Full URL of the issue to delete

    Returns:
        DeleteIssueResult with success status and any error message
    """
    page.goto(issue_url, wait_until="networkidle")

    # Click issue actions dropdown
    try:
        page.get_by_role("button", name="Issue actions").click()
    except Exception:
        return DeleteIssueResult(
            success=False,
            error_message="Could not find Issue actions button"
        )

    # Wait for and click delete button
    try:
        page.wait_for_selector(Selectors.DELETE_ISSUE_BUTTON, state="visible", timeout=5000)
        page.locator(Selectors.DELETE_ISSUE_BUTTON).click()
    except TimeoutError:
        return DeleteIssueResult(
            success=False,
            error_message="Delete issue button not found in dropdown"
        )

    # Wait for confirmation modal
    try:
        page.wait_for_selector(Selectors.DELETE_ISSUE_MODAL, timeout=5000)
    except TimeoutError:
        return DeleteIssueResult(
            success=False,
            error_message="Delete confirmation modal did not appear"
        )

    # Confirm deletion - FIXED: sync pattern
    page.click(Selectors.CONFIRM_DELETE_ISSUE_BUTTON)
    page.wait_for_load_state("networkidle")

    # Check for success message
    try:
        page.wait_for_selector(
            'div.gl-alert-body:has-text("The issue was successfully deleted")',
            state="visible",
            timeout=5000
        )
        return DeleteIssueResult(success=True)
    except TimeoutError:
        return DeleteIssueResult(
            success=False,
            error_message="Did not receive deletion confirmation"
        )


def delete_issue_by_id(
    page: Page,
    namespace: str,
    project: str,
    issue_id: int,
) -> DeleteIssueResult:
    """Delete an issue by its ID."""
    url = get_issue_url(namespace, project, issue_id)
    return delete_issue(page, url)


def get_issues(
    page: Page,
    namespace: str,
    project: str,
) -> List[Issue]:
    """
    Get all issues in a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name

    Returns:
        List of Issue objects
    """
    url = get_project_issues_url(namespace, project)
    page.goto(url, wait_until="networkidle")

    issues: List[Issue] = []
    issue_links = page.locator(Selectors.ISSUE_TITLE_LINK)

    for i in range(issue_links.count()):
        link = issue_links.nth(i)
        title = link.inner_text().strip()
        href = link.get_attribute("href") or ""

        # Extract issue ID from URL
        match = re.search(r"/issues/(\d+)$", href)
        issue_id = int(match.group(1)) if match else 0

        # Normalize the URL to use the configured domain
        if href:
            parsed = urlparse(href)
            normalized_url = urlunparse(
                parsed._replace(
                    netloc=GITLAB_DOMAIN.replace("http://", "").replace("https://", "")
                )
            )
        else:
            normalized_url = ""

        issues.append(Issue(
            issue_id=issue_id,
            title=title,
            url=normalized_url,
        ))

    return issues


def delete_all_issues(
    page: Page,
    namespace: str,
    project: str,
    max_timeout: float = 2000.0,
) -> int:
    """
    Delete all issues in a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        max_timeout: Timeout in ms for finding issues between deletions

    Returns:
        Number of issues deleted
    """
    issues_url = get_project_issues_url(namespace, project)
    deleted_count = 0

    while True:
        page.goto(issues_url, wait_until="networkidle")

        issue_links = page.locator(Selectors.ISSUE_TITLE_LINK)
        if issue_links.count() == 0:
            break

        # Get the first issue's URL
        first_link = issue_links.first
        issue_href = first_link.get_attribute("href")

        if not issue_href:
            break

        # Normalize URL
        parsed = urlparse(issue_href)
        normalized_url = urlunparse(
            parsed._replace(
                netloc=GITLAB_DOMAIN.replace("http://", "").replace("https://", "")
            )
        )

        # Delete the issue
        result = delete_issue(page, normalized_url)
        if result.success:
            deleted_count += 1

        # Check for more issues
        if page.url != issues_url:
            page.goto(issues_url, wait_until="networkidle")

        try:
            page.wait_for_selector(Selectors.ISSUE_TITLE_LINK, timeout=max_timeout)
        except TimeoutError:
            # No more issues found
            break

    return deleted_count


def _is_page_not_found(page: Page) -> bool:
    """Check if the current page shows a 'Page Not Found' error."""
    container = page.locator(Selectors.PAGE_NOT_FOUND)
    if container.count() > 0:
        text = container.text_content()
        if text and "Page Not Found" in text:
            return True
    return False
