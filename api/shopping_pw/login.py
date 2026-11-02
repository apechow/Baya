"""Customer login helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

from .constants import login_url

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


def login_customer(
    page: Page, email: str, password: str, base_url: Optional[str] = None
) -> LoginResult:
    """
    Sign in through the storefront login form.

    Navigates to the login page, fills the email/password fields, submits the form,
    and reports whether the attempt succeeded. Magento renders invalid credentials
    as a `.message-error` block; those are surfaced as explicit failures.

    `base_url` overrides the LOGIN_URL derived from the env-var default — use it
    to target a specific Magento instance in multi-worker multi-docker runs.
    """
    page.goto(login_url(base_url))

    page.wait_for_load_state("networkidle")
    form = page.locator("form#login-form")
    if form.count() == 0:
        return LoginResult(False, None, "Login form not found on page")

    email_input = form.locator("input#email")
    password_input = form.locator("input#pass").first

    if email_input.count() == 0:
        return LoginResult(False, None, "Email input not found on login page")
    if password_input.count() == 0:
        return LoginResult(False, None, "Password input not found on login page")

    email_input.fill(email)
    password_input.fill(password)

    submit_btn = form.locator(
        "button#send2, button.action.login.primary"
    ).first
    if submit_btn.count() == 0:
        return LoginResult(False, None, "Sign-in submit button not found")

    submit_btn.click()

    page.wait_for_load_state("networkidle")
    error_loc = page.locator(ERROR_SELECTOR)
    if error_loc.count() > 0:
        message = error_loc.nth(0).inner_text().strip()
        if not message:
            message = "Invalid email or password"
        return LoginResult(False, None, message)

    redirect_url = page.url or None
    # If Magento redirected elsewhere, keep that; otherwise treat staying put as unknown.
    if redirect_url and "customer/account/login" in redirect_url:
        redirect_url = None

    return LoginResult(True, redirect_url)
