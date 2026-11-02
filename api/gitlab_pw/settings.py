"""GitLab settings management helpers (deploy keys, tokens, webhooks, profile)."""

from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Page, TimeoutError

from .constants import (
    GITLAB_DOMAIN,
    PROFILE_URL,
    ACCOUNT_URL,
    SSH_KEYS_URL,
    ACCESS_TOKENS_URL,
    access_tokens_url,
    Selectors,
    get_deploy_keys_url,
    get_deploy_tokens_url,
    get_webhooks_url,
)

ALL_SCOPES: tuple[str, ...] = (
    "api",
    "read_api",
    "read_user",
    "read_repository",
    "write_repository",
)


@dataclass
class ProfileUpdateResult:
    """Result of attempting to update profile settings."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class UsernameChangeResult:
    """Result of attempting to change username."""

    success: bool
    new_username: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class DeleteResult:
    """Generic result for deletion operations."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class CreateAccessTokenResult:
    """Result of creating a personal access token."""

    success: bool
    token: Optional[str] = None
    error_message: Optional[str] = None


def toggle_private_profile(
    page: Page,
    make_private: bool,
) -> ProfileUpdateResult:
    """
    Set the private profile checkbox to the desired state.

    Args:
        page: Playwright Page instance
        make_private: True to make profile private, False to make public

    Returns:
        ProfileUpdateResult with success status
    """
    page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)

    checkbox = page.locator(Selectors.PRIVATE_PROFILE_CHECKBOX)
    if checkbox.count() == 0:
        return ProfileUpdateResult(
            success=False,
            error_message="Private profile checkbox not found"
        )

    current_state = checkbox.is_checked()

    if current_state == make_private:
        return ProfileUpdateResult(success=True)

    # Toggle the checkbox
    if make_private:
        checkbox.check()
    else:
        checkbox.uncheck()

    # Save settings
    page.locator(Selectors.UPDATE_PROFILE_BUTTON).click()

    # Wait for success message
    try:
        page.wait_for_selector(
            "div.gl-alert-body:has-text('Profile was successfully updated')",
            state="visible",
            timeout=5000
        )
    except TimeoutError:
        return ProfileUpdateResult(
            success=False,
            error_message="Did not receive success confirmation"
        )

    # Verify state changed
    final_state = page.locator(Selectors.PRIVATE_PROFILE_CHECKBOX).is_checked()
    if final_state != make_private:
        return ProfileUpdateResult(
            success=False,
            error_message="Checkbox state did not change"
        )

    return ProfileUpdateResult(success=True)


def change_username(
    page: Page,
    new_username: str,
) -> UsernameChangeResult:
    """
    Change the current user's username.

    Args:
        page: Playwright Page instance
        new_username: New username to set

    Returns:
        UsernameChangeResult with success status
    """
    page.goto(ACCOUNT_URL, wait_until="domcontentloaded", timeout=60000)

    # Get current username
    username_input = page.locator(Selectors.USERNAME_INPUT)
    if username_input.count() == 0:
        return UsernameChangeResult(
            success=False,
            error_message="Username input not found"
        )

    current_username = page.input_value(Selectors.USERNAME_INPUT)
    if current_username.strip() == new_username:
        return UsernameChangeResult(
            success=True,
            new_username=new_username
        )

    # Fill new username
    page.fill(Selectors.USERNAME_INPUT, new_username)

    # Click first confirmation trigger
    try:
        page.wait_for_selector(Selectors.USERNAME_CHANGE_TRIGGER, timeout=5000)
        page.click(Selectors.USERNAME_CHANGE_TRIGGER)
    except TimeoutError:
        return UsernameChangeResult(
            success=False,
            error_message="Username change trigger not found"
        )

    # Click second confirmation button
    try:
        page.wait_for_selector(Selectors.USERNAME_CHANGE_CONFIRM, timeout=10000)
        page.click(Selectors.USERNAME_CHANGE_CONFIRM)
    except TimeoutError:
        return UsernameChangeResult(
            success=False,
            error_message="Confirmation button not found"
        )

    # Wait for success
    try:
        page.wait_for_selector(Selectors.FLASH_CONTAINER, timeout=10000)
        success_msg = page.locator("div", has_text="Username successfully changed")
        if success_msg.count() > 0:
            return UsernameChangeResult(
                success=True,
                new_username=new_username
            )
    except TimeoutError:
        pass

    return UsernameChangeResult(
        success=False,
        error_message="Did not receive success confirmation"
    )


def delete_deploy_key(
    page: Page,
    namespace: str,
    project: str,
) -> DeleteResult:
    """
    Delete a deploy key from a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name

    Returns:
        DeleteResult with success status
    """
    url = get_deploy_keys_url(namespace, project)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    delete_btn_selector = "#js-deploy-keys-settings > div.settings-content > div > div.deploy-keys-panel.table-holder > div.gl-responsive-table-row.deploy-key > div.table-section.section-15.table-button-footer.deploy-key-actions > div > button"
    confirm_btn_selector = "#confirm-remove-deploy-key___BV_modal_footer_ > button.btn.js-modal-action-primary.btn-danger.btn-md.gl-button"

    try:
        page.wait_for_selector(delete_btn_selector, timeout=3000)
        page.click(delete_btn_selector)
        page.wait_for_timeout(1000)

        page.wait_for_selector(confirm_btn_selector, timeout=3000)
        page.click(confirm_btn_selector)
        page.wait_for_timeout(1000)

        return DeleteResult(success=True)
    except TimeoutError:
        return DeleteResult(
            success=True,
            error_message="No deploy key found (may already be deleted)"
        )


def delete_deploy_token(
    page: Page,
    namespace: str,
    project: str,
) -> DeleteResult:
    """
    Delete a deploy token from a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name

    Returns:
        DeleteResult with success status
    """
    url = get_deploy_tokens_url(namespace, project)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    expand_selector = "#js-deploy-tokens > div.settings-header > button"
    delete_btn_selector = "#js-deploy-tokens > div.settings-content > div.table-responsive.deploy-tokens > table > tbody > tr > td:nth-child(6) > div > button"
    confirm_btn_selector = "#revoke-modal-1___BV_modal_footer_ > a"

    try:
        page.wait_for_selector(expand_selector, timeout=3000)
        page.click(expand_selector)
        page.wait_for_timeout(1000)

        page.wait_for_selector(delete_btn_selector, timeout=3000)
        page.click(delete_btn_selector)
        page.wait_for_timeout(1000)

        page.wait_for_selector(confirm_btn_selector, timeout=3000)
        page.click(confirm_btn_selector)
        page.wait_for_timeout(1000)

        return DeleteResult(success=True)
    except TimeoutError:
        return DeleteResult(
            success=True,
            error_message="No deploy token found (may already be deleted)"
        )


def delete_all_webhooks(
    page: Page,
    namespace: str,
    project: str,
) -> DeleteResult:
    """
    Delete all webhooks from a project.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name

    Returns:
        DeleteResult with success status
    """
    url = get_webhooks_url(namespace, project)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    delete_btn_selector = "a >> text=Delete"
    confirm_btn_selector = "#confirmationModal___BV_modal_footer_ > button.btn.js-modal-action-primary.btn-danger.btn-md.gl-button"

    deleted_count = 0
    while True:
        try:
            page.wait_for_selector(delete_btn_selector, timeout=3000)
            page.click(delete_btn_selector)
            page.wait_for_timeout(1000)

            page.wait_for_selector(confirm_btn_selector, timeout=3000)
            page.click(confirm_btn_selector)
            page.wait_for_timeout(1000)

            deleted_count += 1
        except TimeoutError:
            break

    return DeleteResult(
        success=True,
        error_message=f"Deleted {deleted_count} webhook(s)" if deleted_count > 0 else "No webhooks found"
    )


def delete_ssh_key(page: Page) -> DeleteResult:
    """
    Delete an SSH key from the current user's profile.

    Args:
        page: Playwright Page instance

    Returns:
        DeleteResult with success status
    """
    page.goto(SSH_KEYS_URL, wait_until="domcontentloaded", timeout=60000)

    delete_btn_selector = "#content-body > div.row.gl-mt-3.js-search-settings-section > div.col-lg-8 > div.gl-mb-3 > ul > li > div > span > div > div > button"
    confirm_btn_selector = "#confirm-modal-1___BV_modal_footer_ > button.btn.btn-danger"

    try:
        page.wait_for_selector(delete_btn_selector, timeout=3000)
        page.click(delete_btn_selector)
        page.wait_for_timeout(1000)

        page.wait_for_selector(confirm_btn_selector, timeout=3000)
        page.click(confirm_btn_selector)
        page.wait_for_timeout(1000)

        return DeleteResult(success=True)
    except TimeoutError:
        return DeleteResult(
            success=True,
            error_message="No SSH key found (may already be deleted)"
        )


@dataclass
class CreateAccessTokenResult:
    """Result of attempting to create a personal access token."""

    success: bool
    token: Optional[str] = None
    token_name: Optional[str] = None
    error_message: Optional[str] = None


def create_access_token(
    page: Page,
    token_name: str,
    scopes: Optional[List[str]] = None,
    expires_at: Optional[str] = None,
) -> CreateAccessTokenResult:
    """
    Create a personal access token (GLPAT) for the currently logged-in user.

    Navigates to /-/profile/personal_access_tokens, fills in the token name,
    checks the requested scope checkboxes, optionally sets an expiry date,
    submits the form, and captures the token value shown on the confirmation page.

    The token value is only shown once by GitLab — it is returned in the result.

    Args:
        page: Playwright Page instance (must be logged in)
        token_name: Name for the token
        scopes: List of scopes to enable. Defaults to all scopes:
                ["api", "read_api", "read_user", "read_repository", "write_repository"]
        expires_at: Optional expiry date in "YYYY-MM-DD" format. If None, no expiry is set.

    Returns:
        CreateAccessTokenResult with success status, the token value, and any error message
    """
    if scopes is None:
        scopes = list(ALL_SCOPES)

    page.goto(ACCESS_TOKENS_URL, wait_until="domcontentloaded", timeout=60000)

    # Fill token name
    try:
        page.wait_for_selector(Selectors.ACCESS_TOKEN_NAME_INPUT, timeout=10000)
    except TimeoutError:
        print(f"  [DEBUG] Token name input not found. Page title: {page.title()}, URL: {page.url}")
        return CreateAccessTokenResult(
            success=False,
            error_message="Access token form not found"
        )

    page.fill(Selectors.ACCESS_TOKEN_NAME_INPUT, token_name)

    # Set expiry date if provided
    if expires_at:
        page.fill(Selectors.ACCESS_TOKEN_EXPIRES_INPUT, expires_at)

    # Check each requested scope via label click (Bootstrap custom-control pattern:
    # the real <input> is visually hidden, the <label> sits on top and intercepts clicks).
    for scope in scopes:
        sel = f"label[for='personal_access_token_scopes_{scope}']"
        try:
            page.wait_for_selector(sel, timeout=3000, state="attached")
            page.locator(sel).click()
        except Exception:
            print(f"  [DEBUG] Scope checkbox not found: {scope} ({sel})")
            return CreateAccessTokenResult(
                success=False,
                error_message=f"Scope checkbox not found: {scope}",
            )

    # Submit the form
    try:
        page.wait_for_selector('button[type="submit"]', timeout=3000)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
    except Exception:
        print(f"  [DEBUG] Token submit button not found")
        return CreateAccessTokenResult(success=False, error_message="Token submit button not found")

    # Capture the token — field is initially masked, click reveal then read
    try:
        page.wait_for_selector("#new-access-token", timeout=10000)
        val = page.input_value("#new-access-token")
        if val and set(val) == {"*"}:
            page.locator("[data-testid='toggle-visibility-button']").first.click()
            val = page.input_value("#new-access-token")
    except Exception:
        val = None

    if not val or set(val) == {"*"}:
        error_loc = page.locator("#error_explanation")
        if error_loc.count() > 0:
            errors = error_loc.locator("ul li").all_inner_texts()
            msg = "; ".join(errors) if errors else "Token creation failed"
        else:
            msg = "Token field (#new-access-token) not found or still masked after submit"
        print(f"  [DEBUG] GLPAT creation failed: {msg}")
        return CreateAccessTokenResult(success=False, error_message=msg)

    token_value = val

    return CreateAccessTokenResult(success=True, token=token_value, token_name=token_name)


def delete_all_access_tokens(page: Page) -> DeleteResult:
    """
    Delete all personal access tokens from the current user's profile.

    Args:
        page: Playwright Page instance

    Returns:
        DeleteResult with success status
    """
    page.goto(ACCESS_TOKENS_URL, wait_until="domcontentloaded", timeout=60000)

    delete_btn_selector = "a[aria-label='Revoke']"
    confirm_btn_selector = "#confirmationModal___BV_modal_footer_ > button.btn.js-modal-action-primary.btn-danger.btn-md.gl-button"

    deleted_count = 0
    while True:
        try:
            page.wait_for_selector(delete_btn_selector, timeout=3000)
            page.click(delete_btn_selector)
            page.wait_for_timeout(1000)

            page.wait_for_selector(confirm_btn_selector, timeout=3000)
            page.click(confirm_btn_selector)
            page.wait_for_timeout(1000)

            deleted_count += 1
        except TimeoutError:
            break

    return DeleteResult(
        success=True,
        error_message=f"Deleted {deleted_count} token(s)" if deleted_count > 0 else "No tokens found"
    )


@dataclass
class RssTokenResult:
    """Result of a get/reset RSS token operation."""

    success: bool
    token: Optional[str] = None
    error_message: Optional[str] = None


def get_rss_token(page: Page, base_url: Optional[str] = None) -> RssTokenResult:
    """
    Read the current RSS/feed token from the personal access tokens page.

    This GitLab version shows the feed token in a "Feed token" section on
    /-/profile/personal_access_tokens (not on the main profile page). The
    #feed_token input is masked; the real value lives in the "Copy feed
    token" button's data-clipboard-text attribute.

    Args:
        page: Playwright Page instance (must be logged in)
        base_url: GitLab instance to read from; must match the one `page` was
            logged in to. See api/gitlab_pw/constants.py for why this is
            resolved per call rather than at import.

    Returns:
        RssTokenResult with the current token value
    """
    page.goto(access_tokens_url(base_url), wait_until="domcontentloaded", timeout=60000)

    try:
        page.wait_for_selector("#feed_token", timeout=10000)
    except TimeoutError:
        return RssTokenResult(
            success=False,
            error_message="Feed token field not found on personal access tokens page",
        )

    token = page.locator("button[aria-label='Copy feed token']").get_attribute("data-clipboard-text")
    if not token:
        return RssTokenResult(success=False, error_message="Feed token value not found")
    return RssTokenResult(success=True, token=token)


def reset_rss_token(page: Page, base_url: Optional[str] = None) -> RssTokenResult:
    """
    Reset (regenerate) the RSS/feed token and return the new value.

    Navigates to the personal access tokens page, clicks "reset this token"
    in the Feed token section, confirms the modal, then reads the new value.

    Args:
        page: Playwright Page instance (must be logged in)
        base_url: GitLab instance to act on; must match the one `page` was
            logged in to. Getting this wrong rotates the token on an instance
            the caller is not grading.

    Returns:
        RssTokenResult with the newly generated token value
    """
    page.goto(access_tokens_url(base_url), wait_until="domcontentloaded", timeout=60000)

    try:
        page.wait_for_selector("#feed_token", timeout=10000)
    except TimeoutError:
        return RssTokenResult(
            success=False,
            error_message="Feed token field not found on personal access tokens page",
        )

    reset_link = page.locator("a[href*='reset_feed_token']")
    if reset_link.count() == 0:
        return RssTokenResult(success=False, error_message="Reset feed token link not found")

    reset_link.first.click()

    # GitLab shows a confirmation modal before issuing the PUT request.
    try:
        page.wait_for_selector("#confirmationModal___BV_modal_footer_", timeout=3000)
        page.locator("#confirmationModal___BV_modal_footer_ .js-modal-action-primary").click()
    except TimeoutError:
        pass  # No modal — request may have gone through directly

    page.wait_for_load_state("networkidle", timeout=15000)

    try:
        page.wait_for_selector("#feed_token", timeout=5000)
    except TimeoutError:
        return RssTokenResult(success=False, error_message="Feed token field not found after reset")

    token = page.locator("button[aria-label='Copy feed token']").get_attribute("data-clipboard-text")
    if not token:
        return RssTokenResult(success=False, error_message="Feed token value not found after reset")
    return RssTokenResult(success=True, token=token)


def delete_account(page: Page, password: str) -> DeleteResult:
    """
    Delete the current user's account.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        password: Account password for confirmation

    Returns:
        DeleteResult with success status
    """
    page.goto(ACCOUNT_URL, wait_until="domcontentloaded", timeout=60000)

    delete_btn = page.locator(Selectors.DELETE_ACCOUNT_BUTTON)
    if delete_btn.count() == 0:
        # Check for error message
        delete_section = page.locator("text=Delete account").locator(
            "xpath=ancestor::div[contains(@class, 'row')]"
        )
        if delete_section.count() > 0:
            text_column = delete_section.locator(".col-lg-8")
            if text_column.count() > 0:
                error_text = text_column.inner_text()
                return DeleteResult(
                    success=False,
                    error_message=f"Cannot delete account: {error_text}"
                )
        return DeleteResult(
            success=False,
            error_message="Delete account button not found"
        )

    page.click(Selectors.DELETE_ACCOUNT_BUTTON)

    # Fill password confirmation
    try:
        page.wait_for_selector(Selectors.PASSWORD_CONFIRM_FIELD, timeout=5000)
        page.locator(Selectors.PASSWORD_CONFIRM_FIELD).fill(password)
    except TimeoutError:
        return DeleteResult(
            success=False,
            error_message="Password confirmation field not found"
        )

    # Confirm deletion - FIXED: sync pattern
    page.locator(Selectors.CONFIRM_DELETE_ACCOUNT).click()
    page.wait_for_load_state("networkidle")

    # Check if we're redirected to sign-in page
    expected_url = f"{GITLAB_DOMAIN}/users/sign_in"
    if page.url.rstrip("/") == expected_url.rstrip("/"):
        # Check for confirmation message
        alert = page.locator(Selectors.ALERT_BODY)
        if alert.count() > 0:
            text = alert.text_content() or ""
            if "scheduled for removal" in text.lower():
                return DeleteResult(success=True)
        return DeleteResult(success=True)

    # Check for error
    alert = page.locator(Selectors.ALERT_BODY)
    if alert.count() > 0:
        texts = alert.all_inner_texts()
        return DeleteResult(
            success=False,
            error_message="; ".join(texts)
        )

    return DeleteResult(
        success=False,
        error_message=f"Deletion may have failed; ended up at {page.url}"
    )
