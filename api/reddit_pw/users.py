"""Reddit user management helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError

from .constants import (
    Selectors,
    base_url,
    get_user_profile_url,
    get_user_account_url,
    get_user_block_list_url,
    get_block_user_url,
)


@dataclass
class BlockUserResult:
    """Result of attempting to block a user."""

    success: bool
    already_blocked: bool = False
    error_message: Optional[str] = None


@dataclass
class ResetEmailResult:
    """Result of attempting to reset/clear email."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class UpdateEmailResult:
    """Result of attempting to update email."""

    success: bool
    new_email: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class UserInfo:
    """Information about a Reddit user."""

    username: str
    profile_url: str
    exists: bool = True


@dataclass
class UpdateBiographyResult:
    """Result of updating user biography."""

    success: bool
    error_message: Optional[str] = None


def block_user(
    page: Page,
    username: str,
    username_to_block: str,
    base: Optional[str] = None,
) -> BlockUserResult:
    """
    Block a user.

    Checks if the user is already blocked before attempting to block.

    Args:
        page: Playwright Page instance
        username: The logged-in user's username
        username_to_block: The username to block

    Returns:
        BlockUserResult with success status and any error message
    """
    # First check if already blocked
    blocklist_url = get_user_block_list_url(username, base)
    page.goto(blocklist_url, wait_until="networkidle")

    if username_to_block in page.content():
        return BlockUserResult(
            success=True,
            already_blocked=True,
            error_message=f"User '{username_to_block}' is already blocked"
        )

    # Navigate to block user page
    try:
        block_url = get_block_user_url(username_to_block, base)
        page.goto(block_url, wait_until="networkidle")

        # Click block button
        page.click(Selectors.BLOCK_SUBMIT_BUTTON)
        page.wait_for_load_state("networkidle")
    except Exception as e:
        return BlockUserResult(
            success=False,
            error_message=f"Failed to block user: {str(e)}"
        )

    # Verify we're back at blocklist
    if page.url.strip("/") != blocklist_url.strip("/"):
        return BlockUserResult(
            success=False,
            error_message=f"Block may have failed - ended up at {page.url}"
        )

    return BlockUserResult(
        success=True,
        already_blocked=False,
        error_message=None
    )


@dataclass
class UnblockUserResult:
    """Result of attempting to unblock a user."""

    success: bool
    was_blocked: bool = True
    error_message: Optional[str] = None


def unblock_user(
    page: Page,
    username: str,
    username_to_unblock: str,
    base: Optional[str] = None,
) -> UnblockUserResult:
    """
    Unblock a user.

    Args:
        page: Playwright Page instance
        username: The logged-in user's username
        username_to_unblock: The username to unblock

    Returns:
        UnblockUserResult with success status and any error message
    """
    # Navigate to block list
    blocklist_url = get_user_block_list_url(username, base)
    page.goto(blocklist_url, wait_until="networkidle")

    # Check if user is actually blocked
    if username_to_unblock not in page.content():
        return UnblockUserResult(
            success=True,
            was_blocked=False,
            error_message=f"User '{username_to_unblock}' was not blocked"
        )

    # Set up dialog handler to accept confirmation
    def handle_dialog(dialog):
        dialog.accept()
    page.once("dialog", handle_dialog)

    # Find and click the unblock button for this user
    # Look for unblock link/button associated with the username
    try:
        # Try to find an unblock link that contains the username
        unblock_link = page.query_selector(f"a[href*='unblock'][href*='{username_to_unblock}']")
        if not unblock_link:
            # Try finding a delete/remove button near the username
            user_row = page.query_selector(f"text={username_to_unblock}")
            if user_row:
                parent = user_row.evaluate_handle("e => e.closest('tr') || e.closest('li') || e.closest('div')")
                if parent:
                    unblock_link = parent.as_element().query_selector("a[href*='unblock'], button:has-text('Unblock'), button:has-text('Remove')")

        if unblock_link:
            unblock_link.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)

            # Verify user is no longer in block list
            page.goto(blocklist_url, wait_until="networkidle")
            if username_to_unblock not in page.content():
                return UnblockUserResult(success=True, was_blocked=True)
            else:
                return UnblockUserResult(
                    success=False,
                    was_blocked=True,
                    error_message="User still appears in block list after unblock attempt"
                )
        else:
            return UnblockUserResult(
                success=False,
                was_blocked=True,
                error_message="Could not find unblock button/link"
            )
    except Exception as e:
        return UnblockUserResult(
            success=False,
            was_blocked=True,
            error_message=f"Failed to unblock user: {str(e)}"
        )


def reset_email(page: Page, username: str, base: Optional[str] = None) -> ResetEmailResult:
    """
    Reset/clear the email for a user account.

    Args:
        page: Playwright Page instance
        username: The username whose email to reset

    Returns:
        ResetEmailResult with success status and any error message
    """
    account_url = get_user_account_url(username, base)
    page.goto(account_url, wait_until="networkidle")

    try:
        page.wait_for_selector(Selectors.ACCOUNT_EMAIL_INPUT, timeout=10000)
        page.fill(Selectors.ACCOUNT_EMAIL_INPUT, "")
        page.click(Selectors.ACCOUNT_SAVE_BUTTON)
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        return ResetEmailResult(
            success=False,
            error_message="Email field not found on account page"
        )
    except Exception as e:
        return ResetEmailResult(
            success=False,
            error_message=f"Failed to reset email: {str(e)}"
        )

    return ResetEmailResult(success=True)


def update_email(
    page: Page,
    username: str,
    new_email: str,
    base: Optional[str] = None,
) -> UpdateEmailResult:
    """
    Update the email for a user account.

    Args:
        page: Playwright Page instance
        username: The username whose email to update
        new_email: The new email address

    Returns:
        UpdateEmailResult with success status and any error message
    """
    account_url = get_user_account_url(username, base)
    page.goto(account_url, wait_until="networkidle")

    try:
        page.wait_for_selector(Selectors.ACCOUNT_EMAIL_INPUT, timeout=10000)
        page.fill(Selectors.ACCOUNT_EMAIL_INPUT, new_email)
        page.click(Selectors.ACCOUNT_SAVE_BUTTON)
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        return UpdateEmailResult(
            success=False,
            error_message="Email field not found on account page"
        )
    except Exception as e:
        return UpdateEmailResult(
            success=False,
            error_message=f"Failed to update email: {str(e)}"
        )

    return UpdateEmailResult(
        success=True,
        new_email=new_email
    )


def update_biography(page: Page, username: str, biography: str, base: Optional[str] = None) -> UpdateBiographyResult:
    """
    Update the biography for the logged-in user.

    Args:
        page: Playwright Page instance
        username: The logged-in user's username
        biography: New biography text (markdown supported)

    Returns:
        UpdateBiographyResult with success status
    """
    domain = base_url(base)
    bio_url = f"{domain}/user/{username}/edit_biography"
    page.goto(bio_url, wait_until="networkidle")

    textarea_selector = "#user_biography_biography"
    try:
        page.wait_for_selector(textarea_selector, timeout=5000)
    except TimeoutError:
        return UpdateBiographyResult(success=False, error_message="Biography form not found (check username or login state)")

    page.fill(textarea_selector, biography)

    # Submit — button has no type attr, match by text
    try:
        page.click('button:has-text("Save")', timeout=5000)
    except TimeoutError:
        return UpdateBiographyResult(success=False, error_message="Submit button not found on edit_biography page")

    page.wait_for_load_state("networkidle")

    # Postmill redirects back to edit_biography after save — check flash message instead of URL
    flash = page.query_selector(".flash, .alert, [class*='flash']")
    if flash and "updated" in flash.inner_text().lower():
        return UpdateBiographyResult(success=True)

    # Also accept if we navigated away from the edit page
    if "edit_biography" not in page.url:
        return UpdateBiographyResult(success=True)

    return UpdateBiographyResult(success=False, error_message="Biography may not have saved (no confirmation message)")


def get_user_info(page: Page, username: str, base: Optional[str] = None) -> UserInfo:
    """
    Get information about a user.

    Args:
        page: Playwright Page instance
        username: The username to get info for

    Returns:
        UserInfo object with user details
    """
    profile_url = get_user_profile_url(username, base)
    page.goto(profile_url, wait_until="networkidle")

    # Check if user exists (not a 404 page)
    content = page.content().lower()
    exists = not ("not found" in content or "404" in content or "doesn't exist" in content)

    return UserInfo(
        username=username,
        profile_url=profile_url,
        exists=exists
    )


def user_exists(page: Page, username: str, base: Optional[str] = None) -> bool:
    """
    Check if a user exists.

    Args:
        page: Playwright Page instance
        username: The username to check

    Returns:
        True if user exists, False otherwise
    """
    info = get_user_info(page, username, base=base)
    return info.exists


def get_blocked_users(page: Page, username: str, base: Optional[str] = None) -> list:
    """
    Get list of blocked usernames.

    Args:
        page: Playwright Page instance
        username: The logged-in user's username

    Returns:
        List of blocked usernames
    """
    blocklist_url = get_user_block_list_url(username, base)
    page.goto(blocklist_url, wait_until="networkidle")

    blocked_users = []

    # Find blocked user entries - selectors may vary
    user_links = page.query_selector_all("a[href*='/user/']")

    for link in user_links:
        href = link.get_attribute("href") or ""
        if "/user/" in href and "/block" not in href:
            # Extract username from URL
            parts = href.split("/user/")
            if len(parts) > 1:
                blocked_username = parts[1].strip("/").split("/")[0]
                if blocked_username and blocked_username != username:
                    blocked_users.append(blocked_username)

    return list(set(blocked_users))  # Remove duplicates
