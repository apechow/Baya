"""GitLab merge request management helpers."""

import re
from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Page, TimeoutError, expect

from .constants import (
    GITLAB_DOMAIN,
    Selectors,
    get_new_merge_request_url,
    get_merge_request_url,
    get_project_merge_requests_url,
)


@dataclass
class CommentMergeRequestResult:
    """Result of attempting to post a comment on a merge request."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class MergeRequest:
    """Representation of a GitLab merge request."""

    mr_id: int
    title: str
    url: str
    source_branch: str
    target_branch: Optional[str] = None
    state: Optional[str] = None  # "opened", "merged", "closed"
    author: Optional[str] = None


@dataclass
class CreateMergeRequestResult:
    """Result of attempting to create a merge request."""

    success: bool
    mr_id: Optional[int]
    mr_url: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class CloseMergeRequestResult:
    """Result of attempting to close a merge request."""

    success: bool
    error_message: Optional[str] = None


def create_merge_request(
    page: Page,
    namespace: str,
    project: str,
    source_branch: str,
    title: str,
) -> CreateMergeRequestResult:
    """
    Create a merge request from a branch.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        source_branch: Branch to merge from
        title: Merge request title

    Returns:
        CreateMergeRequestResult with success status, ID, and any error message
    """
    url = get_new_merge_request_url(namespace, project, source_branch)
    page.goto(url, wait_until="networkidle")

    # Check for page not found
    if _is_page_not_found(page):
        return CreateMergeRequestResult(
            success=False,
            mr_id=None,
            error_message=f"Cannot access merge request page at {url}"
        )

    # Check for errors (e.g., branch doesn't have commits different from target)
    error_container = page.locator(Selectors.ERROR_CONTAINER)
    if error_container.count() > 0:
        errors = error_container.locator("ul li").all_inner_texts()
        return CreateMergeRequestResult(
            success=False,
            mr_id=None,
            error_message="; ".join(errors)
        )

    # Fill title
    try:
        page.wait_for_selector(Selectors.MR_TITLE_INPUT, timeout=10000)
        page.fill(Selectors.MR_TITLE_INPUT, title)
    except TimeoutError:
        return CreateMergeRequestResult(
            success=False,
            mr_id=None,
            error_message="Merge request title field not found"
        )

    # Submit - FIXED: sync pattern
    page.click(Selectors.MR_CREATE_BUTTON)
    page.wait_for_load_state("networkidle")

    # Verify we landed on the MR page
    gitlab_domain_escaped = re.escape(GITLAB_DOMAIN)
    namespace_escaped = re.escape(namespace)
    project_escaped = re.escape(project)
    mr_url_pattern = rf"^{gitlab_domain_escaped}/{namespace_escaped}/{project_escaped}/-/merge_requests/(\d+)$"

    match = re.match(mr_url_pattern, page.url)
    if match:
        mr_id = int(match.group(1))
        return CreateMergeRequestResult(
            success=True,
            mr_id=mr_id,
            mr_url=page.url,
            error_message=None
        )

    return CreateMergeRequestResult(
        success=False,
        mr_id=None,
        error_message=f"MR creation may have failed; ended up at {page.url}"
    )


def close_merge_request(
    page: Page,
    namespace: str,
    project: str,
    mr_id: int,
) -> CloseMergeRequestResult:
    """
    Close a merge request.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        mr_id: Merge request ID number

    Returns:
        CloseMergeRequestResult with success status and any error message
    """
    url = get_merge_request_url(namespace, project, mr_id)
    page.goto(url, wait_until="networkidle")

    if _is_page_not_found(page):
        return CloseMergeRequestResult(
            success=False,
            error_message=f"Merge request not found at {url}"
        )

    # Click close button
    try:
        page.click(Selectors.MR_CLOSE_BUTTON)
    except Exception:
        return CloseMergeRequestResult(
            success=False,
            error_message="Close button not found"
        )

    # Verify status changed to "Closed"
    try:
        expect(page.locator(Selectors.MR_STATUS_SPAN)).to_have_text("Closed", timeout=10000)
        return CloseMergeRequestResult(success=True)
    except TimeoutError:
        return CloseMergeRequestResult(
            success=False,
            error_message="Status did not change to 'Closed'"
        )


def close_merge_request_by_url(page: Page, mr_url: str) -> CloseMergeRequestResult:
    """
    Close a merge request given its URL.

    Args:
        page: Playwright Page instance
        mr_url: Full URL of the merge request

    Returns:
        CloseMergeRequestResult with success status and any error message
    """
    page.goto(mr_url, wait_until="networkidle")

    if _is_page_not_found(page):
        return CloseMergeRequestResult(
            success=False,
            error_message=f"Merge request not found at {mr_url}"
        )

    # Click close button
    try:
        page.click(Selectors.MR_CLOSE_BUTTON)
    except Exception:
        return CloseMergeRequestResult(
            success=False,
            error_message="Close button not found"
        )

    # Verify status
    try:
        expect(page.locator(Selectors.MR_STATUS_SPAN)).to_have_text("Closed", timeout=10000)
        return CloseMergeRequestResult(success=True)
    except TimeoutError:
        return CloseMergeRequestResult(
            success=False,
            error_message="Status did not change to 'Closed'"
        )


def get_mr_list(
    page: Page,
    namespace: str,
    project: str,
    state: Optional[str] = None,
) -> List[MergeRequest]:
    """
    Scrape and return the list of merge requests for a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        state: Optional state filter — "opened" | "closed" | "merged" | "all"
               Defaults to "all" so callers don't need two trips.

    Returns:
        List of MergeRequest objects with mr_id, title, and url populated.
    """
    url = get_project_merge_requests_url(namespace, project)
    if state:
        url += f"?state={state}"
    else:
        url += "?state=all"

    page.goto(url, wait_until="networkidle")

    mrs: List[MergeRequest] = []

    # GitLab renders MR titles as links inside
    # .merge-request-title-text > a   OR   a[data-qa-selector='issuable_title_link']
    title_links = page.locator(
        "a[data-qa-selector='issuable_title_link'], "
        ".merge-request-title-text a"
    )

    for i in range(title_links.count()):
        link = title_links.nth(i)
        title = link.inner_text().strip()
        href = link.get_attribute("href") or ""

        # Extract MR number from URL path …/-/merge_requests/NNN
        match = re.search(r"/merge_requests/(\d+)$", href)
        mr_id = int(match.group(1)) if match else 0

        full_url = f"{GITLAB_DOMAIN}{href}" if href.startswith("/") else href

        mrs.append(MergeRequest(
            mr_id=mr_id,
            title=title,
            url=full_url,
            source_branch="",
        ))

    return mrs


def post_mr_comment(
    page: Page,
    namespace: str,
    project: str,
    mr_id: int,
    body: str,
) -> "CommentMergeRequestResult":
    """
    Post a comment (note) on a GitLab merge request.

    Navigates to the MR page and submits a comment via the standard
    GitLab note form at the bottom of the discussion thread.

    Args:
        page: Playwright Page instance
        namespace: Project namespace (username or group)
        project: Project name
        mr_id: Merge request number
        body: Comment text to post

    Returns:
        CommentMergeRequestResult with success status and any error message
    """
    url = get_merge_request_url(namespace, project, mr_id)
    page.goto(url, wait_until="networkidle")

    if _is_page_not_found(page):
        return CommentMergeRequestResult(
            success=False,
            error_message=f"Merge request not found at {url}"
        )

    # Verified selectors for this GitLab version (confirmed against live instance):
    #   - Form:     form.js-main-target-form  (also matches form.new-note)
    #   - Textarea: textarea[name='note[note]']  (or .note-textarea)
    #   - Submit:   .note-form-actions .split-content-button
    #               NOTE: The button is type="button" (NOT type="submit") — do NOT use
    #               button[type='submit'] which matches 0 elements on this GitLab version.
    NOTE_FORM_SELECTOR = "form.js-main-target-form"
    NOTE_TEXTAREA_SELECTOR = "textarea[name='note[note]']"
    # The "Comment" button inside .note-form-actions; type=button (not submit)
    NOTE_SUBMIT_SELECTOR = ".note-form-actions .split-content-button"

    # Wait for the comment form to appear
    try:
        page.wait_for_selector(NOTE_FORM_SELECTOR, timeout=10000)
    except TimeoutError:
        return CommentMergeRequestResult(
            success=False,
            error_message="Comment form not found on MR page"
        )

    # Fill in the comment body
    try:
        page.wait_for_selector(NOTE_TEXTAREA_SELECTOR, timeout=8000)
        page.click(NOTE_TEXTAREA_SELECTOR)
        page.fill(NOTE_TEXTAREA_SELECTOR, body)
    except TimeoutError:
        return CommentMergeRequestResult(
            success=False,
            error_message="Comment textarea not found or not editable"
        )

    # Submit the comment — click the "Comment" split button in .note-form-actions.
    # Must wait for the button to become enabled (GitLab disables it until textarea has text).
    try:
        page.wait_for_selector(NOTE_SUBMIT_SELECTOR, timeout=5000)
        page.locator(NOTE_SUBMIT_SELECTOR).first.click()
    except TimeoutError:
        return CommentMergeRequestResult(
            success=False,
            error_message="Comment submit button not found"
        )

    # Wait for the page to finish posting
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except TimeoutError:
        pass  # Partial success — the click may have worked

    # Verify the comment appears in the notes list
    try:
        # Escape single quotes in body for the CSS :has-text() pseudo-selector
        safe_snippet = body[:40].replace("'", "\\'")
        page.wait_for_selector(
            f"#notes-list .note-body:has-text('{safe_snippet}')",
            timeout=8000
        )
        return CommentMergeRequestResult(success=True)
    except TimeoutError:
        # Comment may still have been posted; return partial success
        return CommentMergeRequestResult(
            success=True,
            error_message=(
                "Comment submitted but could not verify it appeared in the notes list"
            )
        )


def _is_page_not_found(page: Page) -> bool:
    """Check if the current page shows a 'Page Not Found' error."""
    container = page.locator(Selectors.PAGE_NOT_FOUND)
    if container.count() > 0:
        text = container.text_content()
        if text and "Page Not Found" in text:
            return True
    return False
