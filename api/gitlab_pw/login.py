"""GitLab authentication helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError

from .constants import login_url, base_url as resolve_base_url, Selectors


@dataclass
class LoginResult:
    """Outcome of attempting to sign in to GitLab."""

    success: bool
    redirect_url: Optional[str]
    error_message: Optional[str] = None


def login_user(
    page: Page,
    username: str,
    password: str,
    base_url: Optional[str] = None,
) -> LoginResult:
    """
    Sign in through the GitLab login form.

    Navigates to the login page, fills the username/password fields, submits the form,
    and reports whether the attempt succeeded. Handles the optional survey page that
    appears for new accounts.

    NOTE: This uses the correct sync pattern:
        page.click(selector)
        page.wait_for_load_state("networkidle")

    The original gitlab_editor.py incorrectly used async context managers:
        with page.expect_navigation():  # WRONG in sync API
            page.click(selector)

    Args:
        page: Playwright Page instance
        username: GitLab username
        password: GitLab password
        base_url: GitLab instance to sign in to. Callers serving several
            instances (multi-docker) must pass their own, or the login lands on
            whichever instance this process resolved by default.

    Returns:
        LoginResult with success status, redirect URL, and any error message
    """
    page.goto(login_url(base_url), wait_until="domcontentloaded", timeout=60000)
    page.set_viewport_size({"width": 1280, "height": 1500})

    # Wait for login form
    try:
        page.wait_for_selector(Selectors.LOGIN_USERNAME_INPUT, timeout=10000)
    except TimeoutError:
        return LoginResult(
            success=False,
            redirect_url=None,
            error_message=f"Login form not found at {page.url}"
        )

    # Fill credentials
    page.fill(Selectors.LOGIN_USERNAME_INPUT, username)
    page.fill(Selectors.LOGIN_PASSWORD_INPUT, password)

    # Submit form - FIXED: using sync pattern instead of expect_navigation context manager
    page.click(Selectors.LOGIN_SUBMIT_BUTTON)
    page.wait_for_load_state("networkidle")

    # Check for error messages
    error_loc = page.locator(Selectors.ERROR_CONTAINER)
    if error_loc.count() > 0:
        errors = error_loc.locator("ul li").all_inner_texts()
        error_message = "; ".join(errors) if errors else "Login failed"
        return LoginResult(
            success=False,
            redirect_url=None,
            error_message=error_message
        )

    # Handle survey page for new accounts
    if "To personalize your GitLab experience" in page.content():
        _handle_post_login_survey(page)

    # Verify we're logged in (should be at dashboard)
    current_url = page.url.rstrip("/")
    expected_domain = resolve_base_url(base_url)

    if current_url == expected_domain or current_url.startswith(expected_domain + "/"):
        # Check if still on login page
        if "/users/sign_in" in current_url:
            return LoginResult(
                success=False,
                redirect_url=None,
                error_message="Still on login page after submission"
            )
        return LoginResult(
            success=True,
            redirect_url=page.url,
            error_message=None
        )

    return LoginResult(
        success=True,
        redirect_url=page.url,
        error_message=None
    )


def _handle_post_login_survey(page: Page) -> None:
    """
    Handle the optional survey page that appears for new GitLab accounts.

    FIXED: Uses sync pattern instead of expect_navigation context manager.
    """
    try:
        page.select_option(Selectors.SURVEY_ROLE_SELECT, value="software_developer")

        # FIXED: sync pattern
        page.click(Selectors.SURVEY_SUBMIT_BUTTON)
        page.wait_for_load_state("networkidle")
    except Exception:
        # Survey handling is optional; don't fail login if it errors
        pass


def is_logged_in(page: Page) -> bool:
    """
    Check if the user appears to be logged in based on page state.

    Returns True if the page shows indicators of being logged in
    (e.g., user avatar, dashboard elements).
    """
    # Check for user dropdown/avatar which indicates logged-in state
    user_dropdown = page.locator(".header-user-dropdown-toggle, .user-menu")
    if user_dropdown.count() > 0:
        return True

    # Check if we're on the sign-in page
    if "/users/sign_in" in page.url:
        return False

    # Check for logged-in-only elements
    logged_in_indicators = [
        ".navbar-nav .dropdown.user-menu",
        "[data-testid='user-menu']",
        ".header-user-avatar",
    ]
    for selector in logged_in_indicators:
        if page.locator(selector).count() > 0:
            return True

    return False
