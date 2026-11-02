"""Cart management helpers, including add-to-cart flows."""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from playwright.sync_api import Page

from .constants import cart_url


@dataclass
class AddToCartResult:
    """Result of attempting to add a product to the cart from its product page."""

    success: bool
    product_url: str
    requested_quantity: int
    cart_count_after: Optional[int]
    error_message: Optional[str] = None


@dataclass
class CartItem:
    """Representation of a cart line item from the shopping cart page."""

    item_id: Optional[str]  # Magento cart item id, e.g. "571"
    sku: str  # product SKU
    name: str
    quantity: int
    price: float  # unit price
    subtotal: float  # line subtotal
    product_url: str
    image_url: Optional[str]


def add_product_to_cart(
    page: Page,
    product_url: str,
    quantity: int = 1,
    option_values: Optional[Dict[str, str]] = None,
) -> AddToCartResult:
    """
    Navigate to a product page, set quantity, and add the item to the cart.

    `option_values` must contain entries for all required product options,
    keyed by the raw input name (e.g. "options[23290]" -> "149401").
    If any required option is missing, this function returns an error result
    and does not attempt to submit the form.
    """
    if quantity < 1:
        quantity = 1

    page.goto(product_url)

    page.wait_for_load_state("networkidle")
    form = page.locator("form#product_addtocart_form")
    form.wait_for()

    # 1) Determine required product option input names
    required_option_names: List[str] = []

    # Fields that Magento marks as required will have the "required" class.
    required_fields = form.locator("#product-options-wrapper .field.required")
    for i in range(required_fields.count()):
        field = required_fields.nth(i)

        # Look for an input/select/textarea with name starting with "options["
        input_loc = field.locator('input[name^="options["]').first
        select_loc = field.locator('select[name^="options["]').first
        textarea_loc = field.locator('textarea[name^="options["]').first

        input_name: Optional[str] = None
        for loc in (input_loc, select_loc, textarea_loc):
            if loc.count() > 0:
                input_name = loc.get_attribute("name")
                break

        if input_name:
            required_option_names.append(input_name)

    # 2) Validate that all required options are present in option_values
    if required_option_names:
        if option_values is None:
            return AddToCartResult(
                success=False,
                product_url=product_url,
                requested_quantity=quantity,
                cart_count_after=None,
                error_message=(
                    "Missing required product options: "
                    + ", ".join(sorted(set(required_option_names)))
                ),
            )

        missing = [
            name for name in required_option_names if name not in option_values
        ]
        if missing:
            return AddToCartResult(
                success=False,
                product_url=product_url,
                requested_quantity=quantity,
                cart_count_after=None,
                error_message=(
                    "Missing required product options: " + ", ".join(missing)
                ),
            )

    # 3) Set quantity
    qty_input = form.locator("input#qty")
    if qty_input.count() > 0:
        qty_input.fill(str(quantity))

    # 4) Apply provided option values (both required + optional)
    if option_values:
        for input_name, input_value in option_values.items():
            # Try radio/checkbox inputs first
            radio_or_checkbox = form.locator(
                f'input[name="{input_name}"][value="{input_value}"]'
            ).first
            if radio_or_checkbox.count() > 0:
                radio_or_checkbox.check()
                continue

            # Try select dropdown
            select_el = form.locator(f'select[name="{input_name}"]').first
            if select_el.count() > 0:
                try:
                    select_el.select_option(value=input_value)
                except Exception:
                    # If select_option fails, treat as an error
                    return AddToCartResult(
                        success=False,
                        product_url=product_url,
                        requested_quantity=quantity,
                        cart_count_after=None,
                        error_message=(
                            f"Could not select option '{input_name}' "
                            f"with value '{input_value}'"
                        ),
                    )
                continue

            # Try textarea (less common, but custom options can be text)
            textarea_el = form.locator(f'textarea[name="{input_name}"]').first
            if textarea_el.count() > 0:
                textarea_el.fill(input_value)
                continue

            # If we get here, the page does not contain any matching input
            # for the given option name; treat as an error to avoid
            # silently submitting a broken configuration.
            return AddToCartResult(
                success=False,
                product_url=product_url,
                requested_quantity=quantity,
                cart_count_after=None,
                error_message=(
                    f"No matching input found on page for option '{input_name}'"
                ),
            )

    # 5) Click Add to Cart
    btn = form.locator(
        "button#product-addtocart-button, button.action.primary.tocart"
    ).first
    if btn.count() == 0:
        return AddToCartResult(
            success=False,
            product_url=product_url,
            requested_quantity=quantity,
            cart_count_after=None,
            error_message="Add to Cart button not found",
        )

    btn.click()

    page.wait_for_load_state("networkidle")
    # 6) Check for Magento error messages after submit
    error_loc = page.locator(
        ".page.messages .message-error, "
        ".page.messages .error.message, "
        "div.messages .message-error, "
        "div.messages .error.message"
    )

    if error_loc.count() > 0:
        text = error_loc.nth(0).inner_text().strip()
        if not text:
            text = "Unknown error adding product to cart"
        return AddToCartResult(
            success=False,
            product_url=product_url,
            requested_quantity=quantity,
            cart_count_after=None,
            error_message=text,
        )

    # 7) Read cart count after adding
    cart_count_after: Optional[int] = None
    counter = page.locator(".minicart-wrapper .counter-number")
    try:
        if counter.count() > 0:
            txt = counter.inner_text().strip()
            match = re.search(r"\d+", txt)
            if match:
                cart_count_after = int(match.group(0))
    except Exception:
        cart_count_after = None

    return AddToCartResult(
        success=True,
        product_url=product_url,
        requested_quantity=quantity,
        cart_count_after=cart_count_after,
        error_message=None,
    )


def get_cart_items(page: Page, base_url: Optional[str] = None) -> List[CartItem]:
    """Return the list of products currently in the shopping cart, with quantities."""
    page.goto(cart_url(base_url))

    page.wait_for_load_state("networkidle")
    items: List[CartItem] = []

    tbodies = page.locator("#shopping-cart-table tbody.cart.item")
    for i in range(tbodies.count()):
        body = tbodies.nth(i)

        name_loc = body.locator(
            "td.col.item .product-item-details .product-item-name a"
        ).first
        if name_loc.count() == 0:
            continue
        name = name_loc.inner_text().strip()
        product_url = name_loc.get_attribute("href") or ""

        qty_input = body.locator("input[data-cart-item-id]").first
        if qty_input.count() == 0:
            continue

        sku = qty_input.get_attribute("data-cart-item-id") or ""
        qty_val_str = qty_input.get_attribute("value") or "0"
        try:
            quantity = int(float(qty_val_str))
        except ValueError:
            quantity = 0

        input_id = qty_input.get_attribute("id") or ""
        match = re.search(r"cart-(\d+)-qty", input_id)
        item_id = match.group(1) if match else None

        price_loc = body.locator("td.col.price span.price").first
        price_text = price_loc.inner_text() if price_loc.count() > 0 else "0"
        price = float(re.sub(r"[^0-9.]", "", price_text))

        subtotal_loc = body.locator("td.col.subtotal span.price").first
        subtotal_text = (
            subtotal_loc.inner_text() if subtotal_loc.count() > 0 else "0"
        )
        subtotal = float(re.sub(r"[^0-9.]", "", subtotal_text))

        img_loc = body.locator("td.col.item img.product-image-photo").first
        image_url = (
            img_loc.get_attribute("src") if img_loc.count() > 0 else None
        )

        items.append(
            CartItem(
                item_id=item_id,
                sku=sku,
                name=name,
                quantity=quantity,
                price=price,
                subtotal=subtotal,
                product_url=product_url,
                image_url=image_url,
            )
        )

    return items


def set_cart_item_quantity(
    page: Page, sku: str, quantity: int, base_url: Optional[str] = None
) -> bool:
    """Change the quantity of a product in the cart based on its SKU."""
    if quantity < 0:
        quantity = 0

    page.goto(cart_url(base_url))

    page.wait_for_load_state("networkidle")
    tbodies = page.locator("#shopping-cart-table tbody.cart.item")
    target_input = None

    for i in range(tbodies.count()):
        body = tbodies.nth(i)
        inp = body.locator(f'input[data-cart-item-id="{sku}"]').first
        if inp.count() > 0:
            target_input = inp
            break

    if target_input is None:
        return False

    target_input.fill(str(quantity))

    update_button = page.locator(
        "button.action.update[name='update_cart_action']"
    ).first
    if update_button.count() == 0:
        update_button = page.locator(
            ".cart.main.actions button.action.update"
        ).first

    if update_button.count() == 0:
        return False

    update_button.click()

    page.wait_for_load_state("networkidle")
    
    # Wait a moment for Magento to process the update
    import time
    time.sleep(2)
    
    return True


def remove_cart_item(page: Page, sku: str, base_url: Optional[str] = None) -> bool:
    """Delete an item from the cart based on its SKU."""
    page.goto(cart_url(base_url))

    page.wait_for_load_state("networkidle")
    tbodies = page.locator("#shopping-cart-table tbody.cart.item")

    for i in range(tbodies.count()):
        body = tbodies.nth(i)
        inp = body.locator(f'input[data-cart-item-id="{sku}"]').first
        if inp.count() == 0:
            continue

        delete_link = body.locator("a.action.action-delete").first
        if delete_link.count() == 0:
            continue

        delete_link.click()
        page.wait_for_load_state("networkidle")
        return True

    return False


def empty_cart(page: Page, base_url: Optional[str] = None) -> None:
    """Remove all items from the cart by iteratively deleting them."""
    while True:
        page.goto(cart_url(base_url))

        page.wait_for_load_state("networkidle")
        tbodies = page.locator("#shopping-cart-table tbody.cart.item")
        if tbodies.count() == 0:
            break

        first_body = tbodies.nth(0)
        delete_link = first_body.locator("a.action.action-delete").first
        if delete_link.count() == 0:
            break

        delete_link.click()
        page.wait_for_load_state("networkidle")
