"""GitLab file management helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError

from .constants import (
    Selectors,
    get_new_file_url,
    get_file_url,
)


@dataclass
class CreateFileResult:
    """Result of attempting to create a file."""

    success: bool
    file_path: Optional[str]
    error_message: Optional[str] = None


@dataclass
class ReplaceFileResult:
    """Result of attempting to replace a file."""

    success: bool
    error_message: Optional[str] = None


def create_empty_file(
    page: Page,
    namespace: str,
    project: str,
    branch: str,
    filename: str,
) -> CreateFileResult:
    """
    Create an empty file on a branch.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        branch: Branch to create file on
        filename: Name of the file to create

    Returns:
        CreateFileResult with success status and any error message
    """
    url = get_new_file_url(namespace, project, branch)
    page.goto(url, wait_until="networkidle")

    if _is_page_not_found(page):
        return CreateFileResult(
            success=False,
            file_path=None,
            error_message=f"Cannot access file creation page at {url}"
        )

    # Fill filename
    try:
        page.wait_for_selector(Selectors.FILE_NAME_INPUT, timeout=10000)
        page.fill(Selectors.FILE_NAME_INPUT, filename)
    except TimeoutError:
        return CreateFileResult(
            success=False,
            file_path=None,
            error_message="File name field not found"
        )

    # Submit - FIXED: sync pattern
    try:
        page.wait_for_selector(Selectors.FILE_COMMIT_BUTTON, timeout=5000)
        page.click(Selectors.FILE_COMMIT_BUTTON)
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        return CreateFileResult(
            success=False,
            file_path=None,
            error_message="Commit button not found"
        )

    # Verify success (should navigate away from new file page)
    if "/new/" not in page.url:
        return CreateFileResult(
            success=True,
            file_path=filename,
            error_message=None
        )

    return CreateFileResult(
        success=False,
        file_path=None,
        error_message=f"File creation may have failed; still at {page.url}"
    )


def create_file_with_content(
    page: Page,
    namespace: str,
    project: str,
    branch: str,
    filename: str,
    content: str,
) -> CreateFileResult:
    """
    Create a file with content on a branch.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        branch: Branch to create file on
        filename: Name of the file to create
        content: Content to put in the file

    Returns:
        CreateFileResult with success status and any error message
    """
    url = get_new_file_url(namespace, project, branch)
    page.goto(url, wait_until="networkidle")

    if _is_page_not_found(page):
        return CreateFileResult(
            success=False,
            file_path=None,
            error_message=f"Cannot access file creation page at {url}"
        )

    # Fill filename
    try:
        page.wait_for_selector(Selectors.FILE_NAME_INPUT, timeout=10000)
        page.fill(Selectors.FILE_NAME_INPUT, filename)
    except TimeoutError:
        return CreateFileResult(
            success=False,
            file_path=None,
            error_message="File name field not found"
        )

    # Fill content in the editor
    # GitLab uses a Monaco editor, so we need to interact with it
    editor = page.locator(".monaco-editor textarea")
    if editor.count() > 0:
        editor.fill(content)
    else:
        # Fallback to simple textarea
        textarea = page.locator("textarea.file-editor")
        if textarea.count() > 0:
            textarea.fill(content)

    # Submit
    try:
        page.wait_for_selector(Selectors.FILE_COMMIT_BUTTON, timeout=5000)
        page.click(Selectors.FILE_COMMIT_BUTTON)
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        return CreateFileResult(
            success=False,
            file_path=None,
            error_message="Commit button not found"
        )

    if "/new/" not in page.url:
        return CreateFileResult(
            success=True,
            file_path=filename,
            error_message=None
        )

    return CreateFileResult(
        success=False,
        file_path=None,
        error_message=f"File creation may have failed; still at {page.url}"
    )


def replace_file_with_upload(
    page: Page,
    namespace: str,
    project: str,
    branch: str,
    filename: str,
    local_file_path: str,
) -> ReplaceFileResult:
    """
    Replace a file by uploading a local file.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name
        branch: Branch the file is on
        filename: Name of the file to replace
        local_file_path: Path to local file to upload

    Returns:
        ReplaceFileResult with success status and any error message
    """
    url = get_file_url(namespace, project, branch, filename)
    page.goto(url, wait_until="networkidle")

    # Click replace button
    try:
        page.click(Selectors.FILE_REPLACE_BUTTON)
    except Exception:
        return ReplaceFileResult(
            success=False,
            error_message="Replace button not found"
        )

    # Wait for upload modal
    try:
        page.wait_for_selector(Selectors.FILE_REPLACE_MODAL, timeout=5000)
    except TimeoutError:
        return ReplaceFileResult(
            success=False,
            error_message="Upload modal did not appear"
        )

    # Upload file
    page.locator(Selectors.FILE_UPLOAD_INPUT).set_input_files(local_file_path)

    # Confirm replacement
    try:
        page.wait_for_selector(Selectors.FILE_REPLACE_CONFIRM, timeout=5000)
        page.click(Selectors.FILE_REPLACE_CONFIRM)
    except TimeoutError:
        return ReplaceFileResult(
            success=False,
            error_message="Confirm button not found"
        )

    # Wait for success message
    try:
        page.wait_for_selector(
            "div.gl-alert-body:has-text('Your changes have been successfully committed')",
            state="visible",
            timeout=10000
        )
        return ReplaceFileResult(success=True)
    except TimeoutError:
        return ReplaceFileResult(
            success=False,
            error_message="Did not receive success confirmation"
        )


def _is_page_not_found(page: Page) -> bool:
    """Check if the current page shows a 'Page Not Found' error."""
    container = page.locator(Selectors.PAGE_NOT_FOUND)
    if container.count() > 0:
        text = container.text_content()
        if text and "Page Not Found" in text:
            return True
    return False
