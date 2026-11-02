"""Admin login helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

from .constants import admin_login_url

# Magento surfaces login failures through the standard message-error blocks.
ERROR_SELECTOR = (
    ".page.messages .message-error, "
    ".page.messages .error.message, "
    "div.messages .message-error, "
    "div.messages .error.message"
)


@dataclass
class LoginResult:
    """Outcome of attempting to sign in."""

    success: bool
    redirect_url: Optional[str]
    error_message: Optional[str] = None


def login_admin(
    page: Page, username: str, password: str, base_url: Optional[str] = None
) -> LoginResult:
    """
    Sign in to the Magento Luma admin panel.

    Navigates to the admin login URL, fills the username/password fields,
    submits the form, and reports whether the attempt succeeded. Magento
    surfaces invalid admin credentials through the same `.message-error`
    blocks as the storefront, plus a `#login-error` container on some themes.

    `base_url` pins which admin backend to sign in to; every subsequent helper
    called with the same page must be given the same value.
    """
    page.goto(admin_login_url(base_url))
    page.wait_for_load_state("networkidle")

    username_input = page.locator(
        'input[name="login[username]"], input#username'
    ).first
    password_input = page.locator(
        'input[name="login[password]"], input#login'
    ).first

    if username_input.count() == 0:
        return LoginResult(False, None, "Admin username input not found on login page")
    if password_input.count() == 0:
        return LoginResult(False, None, "Admin password input not found on login page")

    username_input.fill(username)
    password_input.fill(password)

    submit_btn = page.locator(
        'button.action-login, button.action-primary, button[type="submit"]'
    ).first
    if submit_btn.count() == 0:
        return LoginResult(False, None, "Admin sign-in submit button not found")

    submit_btn.click()
    page.wait_for_load_state("networkidle")

    error_loc = page.locator(
        f"{ERROR_SELECTOR}, #login-error"
    )
    if error_loc.count() > 0:
        message = error_loc.nth(0).inner_text().strip()
        if not message:
            message = "Invalid admin username or password"
        return LoginResult(False, None, message)

    redirect_url = page.url or None
    # Magento secures the admin panel behind a random URL key after login
    # (e.g. /admin/admin/index/key/<hash>/). Staying on the /admin login
    # endpoint after submit means credentials were rejected.
    if redirect_url and redirect_url.rstrip("/").endswith("/admin"):
        return LoginResult(False, redirect_url, "Did not redirect away from admin login page")

    return LoginResult(True, redirect_url)
