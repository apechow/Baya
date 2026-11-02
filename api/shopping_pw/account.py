"""Customer account management helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

from .constants import account_edit_url


@dataclass
class AccountUpdateResult:
    """Outcome of an account update attempt."""

    success: bool
    new_first_name: Optional[str]
    new_last_name: Optional[str]
    new_email: Optional[str]
    password_changed: bool
    message: Optional[str] = None


def update_account_info(
    page: Page,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    current_password: Optional[str] = None,
    new_password: Optional[str] = None,
    base_url: Optional[str] = None,
) -> AccountUpdateResult:
    """
    Update the customer's first/last name, email, and/or password via the account edit form.
    Any combination of fields can be provided; changing email or password requires the current password.
    """
    if not any([first_name, last_name, email, new_password]):
        return AccountUpdateResult(
            success=False,
            new_first_name=None,
            new_last_name=None,
            new_email=None,
            password_changed=False,
            message="No changes were requested",
        )

    page.goto(account_edit_url(base_url))

    page.wait_for_load_state("networkidle")
    # Update names
    if first_name:
        page.locator("input#firstname").fill(first_name)
    if last_name:
        page.locator("input#lastname").fill(last_name)

    # Toggle change email/password sections when needed.
    needs_current_password = False
    if email:
        needs_current_password = True
        change_email_box = page.locator("input#change-email")
        if change_email_box.count() > 0:
            change_email_box.check()
        email_input = page.locator("input#email")
        try:
            email_input.evaluate("el => el.removeAttribute('disabled')")
        except Exception:
            pass
        email_input.fill(email)

    if new_password:
        needs_current_password = True
        change_pw_box = page.locator("input#change-password")
        if change_pw_box.count() > 0:
            change_pw_box.check()

        current_input = page.locator("input#current-password")
        new_pw_input = page.locator("input#password")
        confirm_input = page.locator("input#password-confirmation")
        for loc in (current_input, new_pw_input, confirm_input):
            try:
                loc.evaluate("el => el.removeAttribute('disabled')")
            except Exception:
                pass
        new_pw_input.fill(new_password)
        confirm_input.fill(new_password)

    if needs_current_password:
        if not current_password:
            return AccountUpdateResult(
                success=False,
                new_first_name=first_name,
                new_last_name=last_name,
                new_email=email,
                password_changed=bool(new_password),
                message="Current password is required to change email or password",
            )
        current_pw_input = page.locator("input#current-password")
        if current_pw_input.count() > 0:
            try:
                current_pw_input.evaluate("el => el.removeAttribute('disabled')")
            except Exception:
                pass
            current_pw_input.fill(current_password)

    # Submit the form.
    submit_btn = page.locator("form#form-validate button.action.save").first
    submit_btn.click()

    page.wait_for_load_state("networkidle")
    # Check for Magento messages.
    error_loc = page.locator(
        ".page.messages .message-error, "
        ".page.messages .error.message, "
        "div.messages .message-error, "
        "div.messages .error.message"
    )
    if error_loc.count() > 0:
        msg = error_loc.nth(0).inner_text().strip() or "Unknown error updating account"
        return AccountUpdateResult(
            success=False,
            new_first_name=first_name,
            new_last_name=last_name,
            new_email=email,
            password_changed=bool(new_password),
            message=msg,
        )

    success_loc = page.locator(
        ".page.messages .message-success, div.messages .message-success"
    )
    success_msg = (
        success_loc.nth(0).inner_text().strip()
        if success_loc.count() > 0
        else None
    )

    return AccountUpdateResult(
        success=True,
        new_first_name=first_name,
        new_last_name=last_name,
        new_email=email,
        password_changed=bool(new_password),
        message=success_msg,
    )
