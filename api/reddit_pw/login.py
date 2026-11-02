"""Reddit authentication helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError

from .constants import (
    base_url,
    login_url,
    registration_url,
    Selectors,
)


@dataclass
class LoginResult:
    """Outcome of attempting to sign in to Reddit."""

    success: bool
    redirect_url: Optional[str]
    error_message: Optional[str] = None


@dataclass
class UserCreationResult:
    """Outcome of attempting to create a new Reddit account."""

    success: bool
    username: str
    already_existed: bool = False
    error_message: Optional[str] = None


def login_user(page: Page, username: str, password: str, base: Optional[str] = None) -> LoginResult:
    """
    Sign in through the Reddit login form.

    Navigates to the login page, fills the username/password fields, submits the form,
    and reports whether the attempt succeeded.

    NOTE: This uses the correct sync pattern:
        page.click(selector)
        page.wait_for_load_state("networkidle")

    The original reddit_editor.py incorrectly used async context managers:
        with page.expect_navigation():  # WRONG in sync API
            page.click(selector)

    Args:
        page: Playwright Page instance
        username: Reddit username
        password: Reddit password

    Returns:
        LoginResult with success status, redirect URL, and any error message
    """
    domain = base_url(base)
    page.goto(login_url(base), wait_until="networkidle")
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

    # Check if we're still on login page (login failed)
    current_url = page.url.strip("/")
    expected_domain = domain.strip("/")

    if current_url == login_url(base).strip("/"):
        return LoginResult(
            success=False,
            redirect_url=None,
            error_message="Still on login page after submission"
        )

    # Successful login should redirect to home or dashboard
    if current_url == expected_domain or current_url.startswith(expected_domain):
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


def create_user(
    page: Page,
    username: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    base: Optional[str] = None,
) -> UserCreationResult:
    """
    Create a new Reddit account.

    Navigates to the registration page, fills in the username and password fields,
    and submits the form. If the username already exists, attempts to log in instead.

    NOTE: This uses the correct sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        username: Desired username
        password: Desired password
        first_name: Optional first name (may not be used by all Reddit instances)
        last_name: Optional last name (may not be used by all Reddit instances)
        email: Optional email (may not be used by all Reddit instances)

    Returns:
        UserCreationResult with success status and whether user already existed
    """
    page.goto(registration_url(base), wait_until="networkidle")
    page.set_viewport_size({"width": 1280, "height": 720})

    # Check if registration form exists
    try:
        page.wait_for_selector(Selectors.REGISTER_USERNAME_INPUT, timeout=10000)
    except TimeoutError:
        # Form not found - maybe already logged in or page different
        # Try to login instead
        try:
            login_result = login_user(page, username, password, base=base)
            if login_result.success:
                return UserCreationResult(
                    success=True,
                    username=username,
                    already_existed=True,
                    error_message="Registration form not found, logged in instead"
                )
        except Exception:
            pass

        return UserCreationResult(
            success=False,
            username=username,
            error_message=f"Registration form not found at {page.url}"
        )

    # Fill registration form
    page.fill(Selectors.REGISTER_USERNAME_INPUT, username)
    page.fill(Selectors.REGISTER_PASSWORD_FIRST, password)
    page.fill(Selectors.REGISTER_PASSWORD_SECOND, password)

    # Submit form - FIXED: using sync pattern instead of expect_navigation context manager
    page.click(Selectors.REGISTER_SUBMIT_BUTTON)
    page.wait_for_load_state("networkidle")

    # Check if we got a "username already used" error
    if page.url.strip("/") == registration_url(base).strip("/"):
        if Selectors.ALREADY_USED_ERROR in page.content():
            # Account already exists - try logging in
            login_result = login_user(page, username, password, base=base)
            if login_result.success:
                return UserCreationResult(
                    success=True,
                    username=username,
                    already_existed=True,
                    error_message=f"Account {username} already exists, logged in instead"
                )
            else:
                return UserCreationResult(
                    success=False,
                    username=username,
                    already_existed=True,
                    error_message=f"Account exists but login failed: {login_result.error_message}"
                )

        # Some other error
        return UserCreationResult(
            success=False,
            username=username,
            error_message="Registration failed - still on registration page"
        )

    # Registration successful
    return UserCreationResult(
        success=True,
        username=username,
        already_existed=False,
        error_message=None
    )


def is_logged_in(page: Page, base: Optional[str] = None) -> bool:
    """
    Check if the user appears to be logged in based on page state.

    Returns True if the page shows indicators of being logged in.
    """
    # Check if we're on the login page
    if "/login" in page.url:
        return False

    # Check for login form (if present, not logged in)
    login_form = page.locator(Selectors.LOGIN_USERNAME_INPUT)
    if login_form.count() > 0:
        return False

    return True
