"""Wishlist management helpers mirroring the cart flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .constants import wishlist_url

# Magento surfaces add-to-wishlist errors (e.g. "You must login…", out of stock)
# in the page message area under any of these selectors.
_ERROR_SELECTOR = (
    ".page.messages .message-error, "
    ".page.messages .error.message, "
    "div.messages .message-error, "
    "div.messages .error.message"
)

# Backstop on the pagination walk so a malformed pager can't spin forever.
# At 10 items per page this covers wishlists far larger than any test fixture.
_MAX_WISHLIST_PAGES = 50


class _WishlistAddError(Exception):
    """A non-retryable problem preparing the add (bad/missing options, no button)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _product_slug(url: str) -> str:
    """Last path segment of a product URL, lowercased, with any '.html' dropped.

    Lets us match a product across the forms it appears in (absolute vs slug,
    with or without '.html', with query/fragment) when verifying the wishlist.
    """
    seg = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    return seg[:-5] if seg.endswith(".html") else seg


def _wishlist_contains(
    page: Page, target_slug: str, base_url: Optional[str] = None
) -> tuple[bool, int]:
    """(is the product present, total item count) by reading the real wishlist grid.

    Uses the same grid the grader inspects (get_wishlist_items → the
    .products-grid.wishlist page), so a truthful add is confirmed against the
    exact state the evaluation checks — not a fire-and-forget click.

    `base_url` pins the storefront: under ``--multi-docker`` the wishlist that
    matters lives on the worker instance, and reading the process-wide default
    would confirm the add against a different Magento entirely.
    """
    items = get_wishlist_items(page, base_url)
    present = any(_product_slug(it.product_url) == target_slug for it in items)
    return present, len(items)

@dataclass
class AddToWishlistResult:
    """Result of attempting to add a product to the wishlist from its product page."""

    success: bool
    product_url: str
    requested_quantity: int
    wishlist_count_after: Optional[int]
    error_message: Optional[str] = None


@dataclass
class WishlistItem:
    """Representation of a wishlist line item on the wishlist page."""

    item_id: Optional[str]  # Magento wishlist item id, e.g. "3"
    name: str
    quantity: int
    price: Optional[float]
    product_url: str
    image_url: Optional[str]


def add_product_to_wishlist(
    page: Page,
    product_url: str,
    quantity: int = 1,
    option_values: Optional[dict[str, str]] = None,
    base_url: Optional[str] = None,
) -> AddToWishlistResult:
    """
    Navigate to a product page, set quantity/required options, and add the item to the wishlist.
    Mirrors the cart add flow but clicks the Add to Wish List control instead.

    `base_url` is the storefront whose wishlist is read back to confirm the add.
    Pass it whenever `product_url` points at a non-default instance (multi-docker
    workers); it must be the same instance `page` is logged into.
    """
    if quantity < 1:
        quantity = 1

    target_slug = _product_slug(product_url)

    def _attempt() -> Optional[str]:
        """Run one full add: open the product page, set qty/options, click
        "Add to Wish List", and wait for the action to commit.

        Returns a Magento-surfaced error message (e.g. login required, out of
        stock) if one appears, else None. Raises _WishlistAddError for
        non-retryable preparation problems (missing/invalid options, no button).
        """
        page.goto(product_url)
        page.wait_for_load_state("networkidle")
        form = page.locator("form#product_addtocart_form")
        form.wait_for()

        # Identify required product options.
        required_option_names: list[str] = []
        required_fields = form.locator("#product-options-wrapper .field.required")
        for i in range(required_fields.count()):
            field = required_fields.nth(i)
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

        if required_option_names:
            if option_values is None:
                raise _WishlistAddError(
                    "Missing required product options: "
                    + ", ".join(sorted(set(required_option_names)))
                )
            missing = [
                name for name in required_option_names if name not in option_values
            ]
            if missing:
                raise _WishlistAddError(
                    "Missing required product options: " + ", ".join(missing)
                )

        # Set quantity on the product form.
        qty_input = form.locator("input#qty")
        if qty_input.count() > 0:
            qty_input.fill(str(quantity))

        # Apply provided option values (required and optional).
        if option_values:
            for input_name, input_value in option_values.items():
                radio_or_checkbox = form.locator(
                    f'input[name="{input_name}"][value="{input_value}"]'
                ).first
                if radio_or_checkbox.count() > 0:
                    radio_or_checkbox.check()
                    continue

                select_el = form.locator(f'select[name="{input_name}"]').first
                if select_el.count() > 0:
                    try:
                        select_el.select_option(value=input_value)
                    except Exception:
                        raise _WishlistAddError(
                            f"Could not select option '{input_name}' "
                            f"with value '{input_value}'"
                        )
                    continue

                textarea_el = form.locator(f'textarea[name="{input_name}"]').first
                if textarea_el.count() > 0:
                    textarea_el.fill(input_value)
                    continue

                raise _WishlistAddError(
                    f"No matching input found on page for option '{input_name}'"
                )

        # Trigger Add to Wish List. The control is a Magento data-post link that
        # submits a form and performs a full navigation to the wishlist page.
        wishlist_btn = page.locator("a.action.towishlist[data-post]").first
        if wishlist_btn.count() == 0:
            raise _WishlistAddError("Add to Wish List button not found")

        try:
            wishlist_btn.scroll_into_view_if_needed()
        except Exception:
            pass

        # Wait for the navigation the click triggers, rather than a bare
        # networkidle that can settle on the pre-click page and let the add
        # silently no-op. Tolerate a timeout — the wishlist is verified below
        # regardless, so a missed navigation just falls through to the check.
        try:
            with page.expect_navigation(wait_until="networkidle", timeout=15000):
                wishlist_btn.click()
        except PlaywrightTimeoutError:
            page.wait_for_load_state("networkidle")

        # Surface any explicit Magento error message (login required, etc.).
        error_loc = page.locator(_ERROR_SELECTOR)
        if error_loc.count() > 0:
            text = error_loc.nth(0).inner_text().strip()
            return text or "Unknown error adding product to wishlist"

        return None

    # Try up to twice: the data-post click occasionally lands before Magento has
    # wired up its handler, so the first add can no-op. Each attempt is confirmed
    # against the real wishlist grid — success is only reported when the item is
    # actually there.
    last_error: Optional[str] = None
    count_after: Optional[int] = None
    try:
        for _ in range(2):
            magento_error = _attempt()
            if magento_error is not None:
                # The site rejected the add outright — retrying won't help.
                return AddToWishlistResult(
                    success=False,
                    product_url=product_url,
                    requested_quantity=quantity,
                    wishlist_count_after=None,
                    error_message=magento_error,
                )
            present, count_after = _wishlist_contains(page, target_slug, base_url)
            if present:
                return AddToWishlistResult(
                    success=True,
                    product_url=product_url,
                    requested_quantity=quantity,
                    wishlist_count_after=count_after,
                )
            last_error = (
                "Add reported no error but the product is not present in the "
                "wishlist after the click — the add did not persist"
            )
    except _WishlistAddError as exc:
        return AddToWishlistResult(
            success=False,
            product_url=product_url,
            requested_quantity=quantity,
            wishlist_count_after=None,
            error_message=exc.message,
        )

    return AddToWishlistResult(
        success=False,
        product_url=product_url,
        requested_quantity=quantity,
        wishlist_count_after=count_after,
        error_message=last_error,
    )


def get_wishlist_items(
    page: Page, base_url: Optional[str] = None
) -> List[WishlistItem]:
    """Return the list of products currently in the wishlist, with quantities.

    The wishlist grid paginates at 10 items, so this walks every page — a
    partial read would report a freshly added item as missing simply because
    it landed at the end of a long list.
    """
    listing_url = wishlist_url(base_url)
    page.goto(listing_url)
    page.wait_for_load_state("networkidle")

    items: List[WishlistItem] = []
    for _ in range(_MAX_WISHLIST_PAGES):
        items.extend(_parse_wishlist_page(page))

        next_page = _next_page_number(page)
        if next_page is None:
            break
        # Build the next URL from `listing_url` rather than following the
        # pager's href: Magento renders absolute links from its own configured
        # base URL, which under --multi-docker is not the instance we were
        # asked to read.
        page.goto(f"{listing_url}?p={next_page}")
        page.wait_for_load_state("networkidle")

    return items


def _next_page_number(page: Page) -> Optional[int]:
    """Page number of the pager's "Next" link, or None on the last page.

    Paging is driven by this link rather than by incrementing until a page
    comes back empty: an out-of-range `?p=` re-serves page 1, so an
    emptiness test would loop forever re-reading the same items.
    """
    next_link = page.locator(".pages li.pages-item-next a.action.next").first
    if next_link.count() == 0:
        return None

    href = next_link.get_attribute("href") or ""
    match = re.search(r"[?&]p=(\d+)", href)
    return int(match.group(1)) if match else None


def _parse_wishlist_page(page: Page) -> List[WishlistItem]:
    """Extract the wishlist items rendered on the currently loaded page."""
    items: List[WishlistItem] = []
    product_items = page.locator("div.products-grid.wishlist ol.product-items > li")

    for i in range(product_items.count()):
        item = product_items.nth(i)

        # Item id comes from the li id="item_<id>".
        raw_id = item.get_attribute("id") or ""
        match = re.search(r"item_(\d+)", raw_id)
        item_id = match.group(1) if match else None

        name_loc = item.locator("strong.product-item-name a").first
        if name_loc.count() == 0:
            continue
        name = name_loc.inner_text().strip()
        product_url = name_loc.get_attribute("href") or ""

        qty_input = item.locator("input[type='number'][name^='qty[']").first
        qty_val = qty_input.get_attribute("value") if qty_input.count() > 0 else "0"
        try:
            quantity = int(float(qty_val or "0"))
        except ValueError:
            quantity = 0

        price_loc = item.locator("span.price").first
        if price_loc.count() > 0:
            price_text = price_loc.inner_text()
            try:
                price = float(re.sub(r"[^0-9.]", "", price_text))
            except ValueError:
                price = None
        else:
            price = None

        img_loc = item.locator("img.product-image-photo").first
        image_url = img_loc.get_attribute("src") if img_loc.count() > 0 else None

        items.append(
            WishlistItem(
                item_id=item_id,
                name=name,
                quantity=quantity,
                price=price,
                product_url=product_url,
                image_url=image_url,
            )
        )

    return items


def set_wishlist_item_quantity(
    page: Page, item_id: str, quantity: int, base_url: Optional[str] = None
) -> bool:
    """Change the quantity of an item in the wishlist based on its item id."""
    if quantity < 1:
        quantity = 1

    page.goto(wishlist_url(base_url))

    page.wait_for_load_state("networkidle")
    # Locate the specific quantity input for this item.
    qty_input = page.locator(
        f"form.form-wishlist-items input[name='qty[{item_id}]']"
    ).first
    if qty_input.count() == 0:
        # Fallback: search within the list item container.
        qty_input = page.locator(
            f"li#item_{item_id} input[type='number'][name^='qty[']"
        ).first

    if qty_input.count() == 0:
        return False

    qty_input.fill(str(quantity))

    update_button = page.locator("form.form-wishlist-items button.action.update").first
    if update_button.count() == 0:
        return False

    update_button.click()

    page.wait_for_load_state("networkidle")
    return True


def remove_wishlist_item(
    page: Page, item_id: str, base_url: Optional[str] = None
) -> bool:
    """Delete an item from the wishlist based on its item id."""
    page.goto(wishlist_url(base_url))

    page.wait_for_load_state("networkidle")
    item = page.locator(f"li#item_{item_id}").first
    if item.count() == 0:
        return False

    remove_link = item.locator("a[data-role='remove'], a.action.delete").first
    if remove_link.count() == 0:
        return False

    remove_link.click()

    page.wait_for_load_state("networkidle")
    return True


def empty_wishlist(page: Page, base_url: Optional[str] = None) -> None:
    """Remove all items from the wishlist by iteratively deleting them."""
    while True:
        page.goto(wishlist_url(base_url))

        page.wait_for_load_state("networkidle")
        item_links = page.locator(
            "div.products-grid.wishlist ol.product-items > li a[data-role='remove'], "
            "div.products-grid.wishlist ol.product-items > li a.action.delete"
        )
        if item_links.count() == 0:
            break

        item_links.nth(0).click()
        page.wait_for_load_state("networkidle")
