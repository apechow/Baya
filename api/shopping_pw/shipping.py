from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Page

from .constants import checkout_url


@dataclass
class Address:
    # Required
    first_name: str
    last_name: str
    street1: str
    city: str
    region: str  # e.g. "California"
    postcode: str
    country_code: str  # e.g. "US"
    phone: str

    # Optional
    company: Optional[str] = None
    street2: Optional[str] = None
    save_in_address_book: bool = False


@dataclass
class ShippingStepResult:
    success: bool
    used_existing_address: bool
    created_new_address: bool
    selected_shipping_method_code: Optional[str]
    selected_shipping_price: Optional[float]
    error_message: Optional[str] = None


@dataclass
class BillingAndOrderResult:
    success: bool
    used_shipping_address: bool
    used_existing_billing: bool
    created_new_billing: bool
    applied_discount: bool
    order_number: Optional[str]
    error_message: Optional[str] = None


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _find_existing_shipping_address_index(
    page: Page, addr: Address, threshold: float = 0.6
) -> Optional[int]:
    items = page.locator(".shipping-address-items .shipping-address-item")
    count = items.count()
    if count == 0:
        return None

    target_str = _address_inline_string(addr)
    target_tokens = set(_norm(target_str).split())
    best_score = 0.0
    best_index = None

    for i in range(count):
        txt = _norm(items.nth(i).inner_text())
        item_tokens = set(txt.split())
        if not target_tokens or not item_tokens:
            continue
        overlap = len(target_tokens & item_tokens) / len(
            target_tokens | item_tokens
        )
        if overlap > best_score:
            best_score = overlap
            best_index = i

    if best_score >= threshold:
        return best_index
    return None


def _create_new_shipping_address_popup(page: Page, addr: Address) -> None:
    """
    Logged-in flow with address list + "New Address" popup.
    """
    new_addr_btn = page.locator(".new-address-popup button.action-show-popup")
    if new_addr_btn.count() == 0:
        raise RuntimeError(
            "New-address popup button not found (expected logged-in popup flow)."
        )

    new_addr_btn.click()
    modal = page.locator("aside.new-shipping-address-modal")
    modal.wait_for()

    form = modal.locator("form#co-shipping-form")

    form.locator("input[name=firstname]").fill(addr.first_name)
    form.locator("input[name=lastname]").fill(addr.last_name)

    if addr.company:
        form.locator("input[name=company]").fill(addr.company)

    form.locator("input[name='street[0]']").fill(addr.street1)
    if addr.street2:
        form.locator("input[name='street[1]']").fill(addr.street2)

    form.locator("input[name=city]").fill(addr.city)

    country_select = form.locator("select[name=country_id]")
    if country_select.count() > 0:
        try:
            country_select.select_option(value=addr.country_code)
        except Exception:
            country_select.select_option({"label": addr.country_code})

    region_select = form.locator("select[name=region_id]")
    if region_select.count() > 0:
        page.wait_for_timeout(1000)  # Wait for region options to load
        try:
            region_select.select_option(label=addr.region)
        except Exception:
            try:
                region_select.select_option(value=addr.region)
            except Exception:
                pass  # Region selection failed

    form.locator("input[name=postcode]").fill(addr.postcode)
    form.locator("input[name=telephone]").fill(addr.phone)

    if addr.save_in_address_book:
        save_cb = form.locator("input#shipping-save-in-address-book")
        if save_cb.count() > 0:
            save_cb.set_checked(True)

    ship_here_btn = modal.locator("button.action-save-address")
    ship_here_btn.click()

    page.wait_for_load_state("networkidle")
    modal.wait_for(state="hidden")


def _fill_inline_shipping_form(
    page: Page, addr: Address, email: Optional[str]
) -> None:
    """
    Guest / no-address flow where the shipping form is inline (no popup).
    Also handles the guest email field if provided.
    """
    # Guest email-with-possible-login block
    if email:
        email_input = page.locator(
            "form[data-role='email-with-possible-login'] input#customer-email"
        )
        if email_input.count() > 0:
            email_input.fill(email)

    form = page.locator("form#co-shipping-form")
    if form.count() == 0:
        raise RuntimeError("Inline shipping form #co-shipping-form not found.")

    form.locator("input[name=firstname]").fill(addr.first_name)
    form.locator("input[name=lastname]").fill(addr.last_name)

    if addr.company:
        form.locator("input[name=company]").fill(addr.company)

    form.locator("input[name='street[0]']").fill(addr.street1)
    if addr.street2:
        form.locator("input[name='street[1]']").fill(addr.street2)

    form.locator("input[name=city]").fill(addr.city)

    country_select = form.locator("select[name=country_id]")
    if country_select.count() > 0:
        try:
            country_select.select_option(value=addr.country_code)
        except Exception:
            country_select.select_option({"label": addr.country_code})

    region_select = form.locator("select[name=region_id]")
    if region_select.count() > 0:
        page.wait_for_timeout(1000)  # Wait for region options to load
        try:
            region_select.select_option(label=addr.region)
        except Exception:
            try:
                region_select.select_option(value=addr.region)
            except Exception:
                pass  # Region selection failed

    form.locator("input[name=postcode]").fill(addr.postcode)
    form.locator("input[name=telephone]").fill(addr.phone)

    # The inline form does *not* have a "Ship Here" button; it's saved when you click Next,
    # so nothing else to click here.


def _select_shipping_method(
    page: Page,
    desired_code: Optional[str],
) -> Tuple[Optional[str], Optional[float]]:
    rows = page.locator("table.table-checkout-shipping-method tbody tr.row")
    row_count = rows.count()
    if row_count == 0:
        return None, None

    chosen_index = None
    chosen_code = None
    chosen_price = None

    if desired_code is not None:
        for i in range(row_count):
            row = rows.nth(i)
            radio = row.locator("input[type=radio]")
            if radio.count() == 0:
                continue
            value = radio.first.get_attribute("value")
            if value == desired_code:
                chosen_index = i
                chosen_code = value
                price_text = (
                    row.locator(".col-price .price").last.inner_text().strip()
                )
                chosen_price = _parse_price(price_text)
                break
    else:
        for i in range(row_count):
            row = rows.nth(i)
            radio = row.locator("input[type=radio]")
            if radio.count() == 0:
                continue
            value = radio.first.get_attribute("value")
            price_text = row.locator(".col-price .price").last.inner_text().strip()
            price = _parse_price(price_text)
            if price is None:
                continue
            if chosen_price is None or price < chosen_price:
                chosen_price = price
                chosen_code = value
                chosen_index = i

    if chosen_index is None:
        return None, None

    row = rows.nth(chosen_index)
    radio = row.locator("input[type=radio]").first
    radio.check()

    return chosen_code, chosen_price


def complete_shipping_step(
    page: Page,
    address: Optional[Address] = None,
    shipping_method_code: Optional[str] = None,
    email: Optional[str] = None,  # required for *guest* flow
    base_url: Optional[str] = None,
) -> ShippingStepResult:
    """
    - Goes to the checkout page.
    - If address is None:
        * If an existing shipping address is present (logged-in), leaves it as-is.
        * If NO existing address (guest or logged-in with none), returns an error.
    - If address is provided:
        * Logged-in with address list: select matching, or create via popup.
        * Guest / no list: fill inline shipping form (#co-shipping-form).
    - Selects shipping method (given code or cheapest).
    - Clicks "Next" and waits for payment step.
    """
    used_existing = False
    created_new = False

    try:
        page.goto(checkout_url(base_url))

        page.wait_for_load_state("networkidle")
        shipping_step = page.locator("li#shipping")
        shipping_step.wait_for()

        # Detect if we are in "address list + popup" mode vs "inline form" mode.
        addr_items = page.locator(
            ".shipping-address-items .shipping-address-item"
        )
        addr_items_count = addr_items.count()
        inline_form = page.locator("form#co-shipping-form")

        addr_list_mode = addr_items_count > 0  # logged-in with saved addresses
        inline_mode = (addr_items_count == 0) and (inline_form.count() > 0)

        # ADDRESS HANDLING
        if address is None:
            if addr_list_mode:
                # There is a default selected address; use it.
                used_existing = True
            elif inline_mode:
                # Guest / no address: we *must* have an address; can't proceed.
                return ShippingStepResult(
                    success=False,
                    used_existing_address=False,
                    created_new_address=False,
                    selected_shipping_method_code=None,
                    selected_shipping_price=None,
                    error_message="No existing shipping address; an Address must be provided for guest checkout.",
                )
            else:
                return ShippingStepResult(
                    success=False,
                    used_existing_address=False,
                    created_new_address=False,
                    selected_shipping_method_code=None,
                    selected_shipping_price=None,
                    error_message="Could not detect any shipping address UI.",
                )
        else:
            if addr_list_mode:
                idx = _find_existing_shipping_address_index(page, address)
                if idx is not None:
                    addr_items.nth(idx).click()
                    used_existing = True
                else:
                    _create_new_shipping_address_popup(page, address)
                    created_new = True
            elif inline_mode:
                _fill_inline_shipping_form(page, address, email=email)
                created_new = True
            else:
                return ShippingStepResult(
                    success=False,
                    used_existing_address=False,
                    created_new_address=False,
                    selected_shipping_method_code=None,
                    selected_shipping_price=None,
                    error_message="Unknown shipping address layout: neither list nor inline form found.",
                )

        # SHIPPING METHOD
        method_code, method_price = _select_shipping_method(
            page, shipping_method_code
        )
        if method_code is None:
            return ShippingStepResult(
                success=False,
                used_existing_address=used_existing,
                created_new_address=created_new,
                selected_shipping_method_code=None,
                selected_shipping_price=None,
                error_message="No shipping methods available or matching the requested code.",
            )

        # Click Next
        shipping_form = page.locator("form#co-shipping-method-form")
        next_btn = shipping_form.locator(
            "button[data-role='opc-continue'], button.button.action.continue.primary"
        )

        next_btn.first.click()

        page.wait_for_load_state("networkidle")
        payment_step = page.locator("li#payment")
        payment_step.wait_for(state="visible", timeout=10000)

        return ShippingStepResult(
            success=True,
            used_existing_address=used_existing,
            created_new_address=created_new,
            selected_shipping_method_code=method_code,
            selected_shipping_price=method_price,
            error_message=None,
        )

    except Exception as e:
        return ShippingStepResult(
            success=False,
            used_existing_address=used_existing,
            created_new_address=created_new,
            selected_shipping_method_code=None,
            selected_shipping_price=None,
            error_message=str(e),
        )


def _norm(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _address_inline_string(addr: Address) -> str:
    """Approximate the inline representation used in Magento summary."""
    # Example inline: "julien piet, sadfsdf, asdf, American Samoa 92342, United States"
    country_text = getattr(addr, "country", None) or getattr(
        addr, "country_code", None
    )
    parts = [
        addr.first_name,
        addr.last_name,
        addr.street1,
        addr.city,
        addr.region,
        addr.postcode,
        country_text,
    ]
    # Use spaces instead of commas to avoid comma-in-token issues during matching
    return _norm(" ".join(str(p) for p in parts if p))


def _extract_inline_addresses_from_page(
    soup: BeautifulSoup,
) -> list[tuple[Tag, str]]:
    """
    Collect inline address strings from:
    - shipping-address-items
    - billing-address-details
    """
    results = []

    # Shipping address items
    for item in soup.select(
        ".shipping-address-item, .shipping-information .ship-to"
    ):
        text = _norm(item.get_text(" ", strip=True))
        if text:
            results.append((item, text))

    # Explicit billing-address-details blocks
    for block in soup.select(".billing-address-details"):
        text = _norm(block.get_text(" ", strip=True))
        if text:
            results.append((block, text))

    return results


def _find_best_matching_existing_address(
    soup: BeautifulSoup, target: Address, threshold: float = 0.6
) -> Optional[Tag]:
    """
    Heuristic: find an existing address block whose text overlaps heavily
    with our target address. Returns the DOM node (e.g., .billing-address-details
    or .shipping-address-item) to 'select' if you do click-based automation.
    """
    target_str = _address_inline_string(target)
    candidates = _extract_inline_addresses_from_page(soup)

    best_score = 0.0
    best_node = None

    for node, text in candidates:
        # Simple token overlap score
        t_tokens = set(_norm(target_str).split())
        c_tokens = set(_norm(text).split())
        if not t_tokens or not c_tokens:
            continue
        overlap = len(t_tokens & c_tokens) / len(t_tokens | c_tokens)
        if overlap > best_score:
            best_score = overlap
            best_node = node

    if best_score >= threshold:
        return best_node
    return None


def _is_inline_billing_mode(soup: BeautifulSoup) -> bool:
    """
    Detect 'inline only' billing mode (guest / no saved addresses) using text heuristics:
      - no <select name='billing_address_id'> with useful options
      - a visible billing-address-form exists
    """
    dropdown = soup.select_one("select[name='billing_address_id']")
    if dropdown:
        # If there is a dropdown AND it has at least one non-"New Address" option,
        # treat as saved-address mode.
        options = [o.get_text(strip=True) for o in dropdown.find_all("option")]
        non_new = [o for o in options if o and "new address" not in o.lower()]
        if non_new:
            return False

    # Look for billing-address-form fields (firstname/lastname under 'billing...')
    billing_form = soup.select_one(".billing-address-form") or soup.select_one(
        "form[id^='co-payment-form'] .billing-address-form"
    )
    if billing_form:
        # Presence of required fields inside billing form is a good indicator
        if billing_form.select_one(
            "input[name='firstname']"
        ) and billing_form.select_one("input[name='lastname']"):
            return True

    # Fallback: no dropdown and no details means inline
    details = soup.select_one(".billing-address-details")
    return dropdown is None and details is None


def _fill_billing_form_inputs(form_root: Tag, addr: Address) -> dict[str, str]:
    """
    Build a dict of form fields for billing address from the form DOM.
    Works for both:
      - billingAddresscheckmo.* forms
      - guest/inline forms (same input names)
    """
    data: dict[str, str] = {}

    def set_if_present(selector: str, value: str):
        el = form_root.select_one(selector)
        if el and el.get("name"):
            data[el["name"]] = value

    # Required fields
    set_if_present("input[name='firstname']", addr.first_name)
    set_if_present("input[name='lastname']", addr.last_name)
    if addr.company:
        set_if_present("input[name='company']", addr.company)
    set_if_present("input[name='street[0]']", addr.street1)
    if addr.street2:
        set_if_present("input[name='street[1]']", addr.street2)
    set_if_present("input[name='city']", addr.city)
    set_if_present("input[name='postcode']", addr.postcode)
    set_if_present("input[name='telephone']", addr.phone)

    # Country: either select[name=country_id] or input[name=country_id]
    country_el = form_root.select_one(
        "select[name='country_id'], input[name='country_id']"
    )
    if country_el and country_el.get("name"):
        # Try to match by value (ISO) or label via text heuristics
        name = country_el["name"]
        # If it's a <select>, we try to pick the closest option
        if country_el.name == "select":
            norm_target = _norm(addr.country)
            best_option = None
            best_score = 0.0
            for opt in country_el.find_all("option"):
                label = opt.get("data-title") or opt.get_text(strip=True)
                value = opt.get("value", "")
                opt_norm = _norm(label or value)
                if not opt_norm:
                    continue
                # simple overlap
                t_tokens = set(norm_target.split())
                o_tokens = set(opt_norm.split())
                overlap = len(t_tokens & o_tokens) / max(
                    1, len(t_tokens | o_tokens)
                )
                if overlap > best_score:
                    best_score = overlap
                    best_option = opt
            if best_option is not None and best_option.get("value"):
                data[name] = best_option["value"]
        else:
            data[name] = addr.country_code

    # Region: Magento has both region_id (select) and region (text)
    region_select = form_root.select_one("select[name='region_id']")
    region_input = form_root.select_one("input[name='region']")
    norm_region = _norm(addr.region)

    if region_select and region_select.get("name"):
        best_opt = None
        best_score = 0.0
        for opt in region_select.find_all("option"):
            if not opt.get("value"):
                continue
            label = opt.get("data-title") or opt.get_text(strip=True)
            opt_norm = _norm(label or "")
            if not opt_norm:
                continue
            t_tokens = set(norm_region.split())
            o_tokens = set(opt_norm.split())
            overlap = len(t_tokens & o_tokens) / max(
                1, len(t_tokens | o_tokens)
            )
            if overlap > best_score:
                best_score = overlap
                best_opt = opt
        if best_opt is not None and best_opt.get("value"):
            data[region_select["name"]] = best_opt["value"]
    elif region_input and region_input.get("name"):
        data[region_input["name"]] = addr.region

    return data


def complete_billing_and_place_order(
    client,
    billing_address: Optional[Address] = None,
    discount_code: Optional[str] = None,
) -> BillingAndOrderResult:
    """
    1. Decide billing mode:
       - saved-address mode (logged-in, saved addresses)
       - inline-only (guest/no saved addresses)
    2. If billing_address is given:
       - override "same as shipping" via data (Option B)
       - either select existing matching address or create new billing
    3. Optionally apply discount_code
    4. Click "Place Order" and return the order number (if any).
    """
    page = client.current_page
    soup: BeautifulSoup = page.soup

    # --- 1) Determine billing layout mode ---
    inline_billing = _is_inline_billing_mode(soup)

    used_shipping_address = billing_address is None
    used_existing_billing = False
    created_new_billing = False
    applied_discount = False

    # Locate payment form root
    payment_form = soup.select_one("form#co-payment-form")
    if payment_form is None:
        return BillingAndOrderResult(
            success=False,
            used_shipping_address=used_shipping_address,
            used_existing_billing=False,
            created_new_billing=False,
            applied_discount=False,
            order_number=None,
            error_message="Could not find payment form (co-payment-form).",
        )

    # ========== BILLING ADDRESS HANDLING ==========
    billing_data: dict[str, str] = {}

    if billing_address is not None:
        # Always override "same as shipping"
        checkbox = payment_form.select_one(
            "input[type='checkbox'][name='billing-address-same-as-shipping']"
        )
        if checkbox and checkbox.get("name"):
            # Force unchecked by setting no/falsey value. Many Magento setups
            # interpret absence or "0" as false.
            billing_data[checkbox["name"]] = "0"

        if inline_billing:
            # Guest / no saved addresses: fill the inline billing form
            billing_form_root = (
                payment_form.select_one(".billing-address-form") or payment_form
            )
            extra = _fill_billing_form_inputs(
                billing_form_root, billing_address
            )
            billing_data.update(extra)
            created_new_billing = True
        else:
            # Saved-address mode
            # 1) Try to match an existing inline address (shipping or billing summaries)
            node = _find_best_matching_existing_address(soup, billing_address)
            dropdown = payment_form.select_one(
                "select[name='billing_address_id']"
            )
            if dropdown and dropdown.get("name"):
                if node is not None:
                    # We have some node with matching text, but we need to map it
                    # to a dropdown option. Since options only have inline string,
                    # we do a second heuristic match against option texts.
                    target_str = _address_inline_string(billing_address)
                    best_opt = None
                    best_score = 0.0
                    for opt in dropdown.find_all("option"):
                        label = _norm(opt.get_text(" ", strip=True))
                        if not label:
                            continue
                        t_tokens = set(target_str.split())
                        o_tokens = set(label.split())
                        overlap = len(t_tokens & o_tokens) / max(
                            1, len(t_tokens | o_tokens)
                        )
                        if overlap > best_score:
                            best_score = overlap
                            best_opt = opt
                    if best_opt is not None and best_opt.get("value"):
                        billing_data[dropdown["name"]] = best_opt["value"]
                        used_existing_billing = True
                    else:
                        # Fallback: choose "New Address" if present
                        new_opt = None
                        for opt in dropdown.find_all("option"):
                            if (
                                "new address"
                                in opt.get_text(" ", strip=True).lower()
                            ):
                                new_opt = opt
                                break
                        if new_opt is not None and new_opt.get("value"):
                            billing_data[dropdown["name"]] = new_opt["value"]
                        # Fill new address form
                        form_root = (
                            payment_form.select_one(".billing-address-form")
                            or payment_form
                        )
                        extra = _fill_billing_form_inputs(
                            form_root, billing_address
                        )
                        billing_data.update(extra)
                        created_new_billing = True
                else:
                    # No good match; select "New Address" + create new one
                    new_opt = None
                    for opt in dropdown.find_all("option"):
                        if (
                            "new address"
                            in opt.get_text(" ", strip=True).lower()
                        ):
                            new_opt = opt
                            break
                    if new_opt is not None and new_opt.get("value"):
                        billing_data[dropdown["name"]] = new_opt["value"]
                    form_root = (
                        payment_form.select_one(".billing-address-form")
                        or payment_form
                    )
                    extra = _fill_billing_form_inputs(
                        form_root, billing_address
                    )
                    billing_data.update(extra)
                    created_new_billing = True
            else:
                # Saved-address layout but no dropdown (shouldn't really happen),
                # treat as inline billing.
                form_root = (
                    payment_form.select_one(".billing-address-form")
                    or payment_form
                )
                extra = _fill_billing_form_inputs(form_root, billing_address)
                billing_data.update(extra)
                created_new_billing = True

    # If no billing_address is provided, we rely on whatever Magento has:
    # for logged-in: default billing or “same as shipping”;
    # for guests: shipping -> billing.

    # ========== DISCOUNT CODE HANDLING ==========
    if discount_code:
        # Payment discount block exists in both layouts
        discount_input = soup.select_one(
            "input#discount-code[name='discount_code']"
        )
        discount_form = (
            discount_input.find_parent("form") if discount_input else None
        )
        apply_button = None
        if discount_form:
            apply_button = discount_form.select_one("button.action-apply")
        if discount_input and discount_form and apply_button:
            # Fill discount code
            # (Your HTML/HTTP layer should pick up this field when submitting.)
            discount_data = {discount_input["name"]: discount_code}
            # Submit the discount form (this may reload totals and same page)
            page = client.submit_form(discount_form, extra_data=discount_data)
            soup = page.soup
            applied_discount = True
            # Refresh payment form and roots after re-load
            payment_form = (
                soup.select_one("form#co-payment-form") or payment_form
            )

    # ========== PLACE ORDER ==========
    # Attach billing_data to payment form submission and click Place Order
    place_button = payment_form.select_one("button.action.primary.checkout")
    if place_button is None:
        return BillingAndOrderResult(
            success=False,
            used_shipping_address=used_shipping_address,
            used_existing_billing=used_existing_billing,
            created_new_billing=created_new_billing,
            applied_discount=applied_discount,
            order_number=None,
            error_message="Could not find Place Order button.",
        )

    # Your submit_form helper should:
    #   - serialize co-payment-form
    #   - include billing_data overrides
    #   - follow redirects to success page
    page = client.submit_form(payment_form, extra_data=billing_data)
    soup = page.soup

    # Heuristic: extract order number from success page, preferring checkout-success block
    order_number = None

    # 1) Preferred: the explicit element in checkout-success
    success_span = soup.select_one("div.checkout-success p span")
    if success_span:
        order_number = success_span.get_text(strip=True)

    # 2) Fallback: regex over full text (in case theme/layout differs)
    if not order_number:
        text = soup.get_text(" ", strip=True)
        m = re.search(
            r"(order\s*(number)?\s*#?\s*is[:\s]+)([A-Z0-9-]+)",
            text,
            re.IGNORECASE,
        )
        if m:
            order_number = m.group(3)

    success = order_number is not None

    return BillingAndOrderResult(
        success=success,
        used_shipping_address=used_shipping_address,
        used_existing_billing=used_existing_billing,
        created_new_billing=created_new_billing,
        applied_discount=applied_discount,
        order_number=order_number,
        error_message=(
            None if success else "Could not find order number on success page."
        ),
    )