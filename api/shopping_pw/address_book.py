"""Address Book helpers (edit existing addresses and add new ones)."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

from .constants import address_book_url
from .shipping import Address, _address_inline_string, _norm


@dataclass
class AddressSaveResult:
    """Outcome of saving an address (edit or add)."""

    success: bool
    message: Optional[str] = None


def _fill_address_form(
    page: Page,
    addr: Address,
    set_default_billing: Optional[bool] = None,
    set_default_shipping: Optional[bool] = None,
) -> None:
    # Contact info
    page.locator("input#firstname").fill(addr.first_name)
    page.locator("input#lastname").fill(addr.last_name)
    page.locator("input#telephone").fill(addr.phone)

    if addr.company is not None:
        page.locator("input#company").fill(addr.company)

    # Address lines
    page.locator("input#street_1").fill(addr.street1)
    street2_input = page.locator("input#street_2")
    if addr.street2 is not None and street2_input.count() > 0:
        street2_input.fill(addr.street2)

    # Country / region
    country_sel = page.locator("select#country")
    if country_sel.count() > 0:
        try:
            country_sel.select_option(value=addr.country_code)
        except Exception:
            country_sel.select_option({"label": addr.country_code})

    region_sel = page.locator("select#region_id")
    if region_sel.count() > 0:
        try:
            region_sel.select_option({"label": addr.region})
        except Exception:
            pass

    page.locator("input#city").fill(addr.city)
    page.locator("input#zip").fill(addr.postcode)

    # Defaults toggles
    if set_default_billing is not None:
        billing_cb = page.locator("input#primary_billing")
        if billing_cb.count() > 0:
            billing_cb.set_checked(set_default_billing)
    if set_default_shipping is not None:
        shipping_cb = page.locator("input#primary_shipping")
        if shipping_cb.count() > 0:
            shipping_cb.set_checked(set_default_shipping)

    submit_btn = page.locator("form#form-validate button.action.save").first
    submit_btn.click()


    page.wait_for_load_state("networkidle")
def _extract_flash_message(page: Page, selector: str) -> Optional[str]:
    loc = page.locator(selector)
    if loc.count() > 0:
        txt = loc.nth(0).inner_text().strip()
        return txt or None
    return None


def _pick_additional_address_edit_link(page: Page, target_text: str) -> Optional[str]:
    rows = page.locator("#additional-addresses-table tbody tr")
    best_href = None
    best_score = 0.0
    target_tokens = set(_norm(target_text).split())

    for i in range(rows.count()):
        row = rows.nth(i)
        row_text = row.inner_text().strip()
        tokens = set(_norm(row_text).split())
        if not tokens or not target_tokens:
            continue
        overlap = len(target_tokens & tokens) / len(target_tokens | tokens)
        if overlap > best_score:
            best_score = overlap
            href = row.locator("a.action.edit").first.get_attribute("href")
            if href:
                best_href = href
                best_score = overlap

    return best_href


def edit_address(
    page: Page,
    target: str,
    updated_address: Address,
    set_default_billing: Optional[bool] = None,
    set_default_shipping: Optional[bool] = None,
    base_url: Optional[str] = None,
) -> AddressSaveResult:
    """
    Edit an address in the Address Book.
    `target` can be "default_billing", "default_shipping", or a fuzzy text/identifier
    that will be matched against rows in the Additional Addresses table.
    """
    page.goto(address_book_url(base_url))

    page.wait_for_load_state("networkidle")
    edit_href: Optional[str] = None

    if target == "default_billing":
        edit_href = page.locator(".box-address-billing a.action.edit").first.get_attribute("href")
    elif target == "default_shipping":
        edit_href = page.locator(".box-address-shipping a.action.edit").first.get_attribute("href")
    else:
        # Fuzzy match against additional addresses; use provided target text,
        # falling back to the updated address string for matching.
        lookup_text = target or _address_inline_string(updated_address)
        edit_href = _pick_additional_address_edit_link(page, lookup_text)

    if not edit_href:
        return AddressSaveResult(
            success=False,
            message="Could not locate an address to edit",
        )

    page.goto(edit_href)

    page.wait_for_load_state("networkidle")
    _fill_address_form(
        page,
        updated_address,
        set_default_billing=set_default_billing,
        set_default_shipping=set_default_shipping,
    )

    error_msg = _extract_flash_message(
        page,
        ".page.messages .message-error, div.messages .message-error",
    )
    if error_msg:
        return AddressSaveResult(success=False, message=error_msg)

    success_msg = _extract_flash_message(
        page,
        ".page.messages .message-success, div.messages .message-success",
    )
    return AddressSaveResult(success=True, message=success_msg)


def add_address(
    page: Page,
    new_address: Address,
    set_default_billing: bool = False,
    set_default_shipping: bool = False,
    base_url: Optional[str] = None,
) -> AddressSaveResult:
    """Add a new address entry via the Address Book add flow."""
    page.goto(address_book_url(base_url))

    page.wait_for_load_state("networkidle")
    add_btn = page.locator("button[role='add-address'], button.action.add").first
    if add_btn.count() == 0:
        return AddressSaveResult(
            success=False, message="Add New Address button not found"
        )

    add_btn.click()

    page.wait_for_load_state("networkidle")
    _fill_address_form(
        page,
        new_address,
        set_default_billing=set_default_billing,
        set_default_shipping=set_default_shipping,
    )

    error_msg = _extract_flash_message(
        page,
        ".page.messages .message-error, div.messages .message-error",
    )
    if error_msg:
        return AddressSaveResult(success=False, message=error_msg)

    success_msg = _extract_flash_message(
        page,
        ".page.messages .message-success, div.messages .message-success",
    )
    return AddressSaveResult(success=True, message=success_msg)
