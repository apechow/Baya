"""Admin catalog product helpers."""

import re
from typing import Any, Dict, List, Optional, Union

from playwright.sync_api import Page

from .constants import admin_products_url

# Result keys → grid column headers to extract.
_COLUMNS = {
    "id": "ID",
    "name": "Name",
    "type": "Type",
    "sku": "SKU",
    "price": "Price",
    "quantity": "Quantity",
    "status": "Status",
}


def _parse_price(text: str) -> Union[float, str, None]:
    """Parse a displayed price like "$1,234.00" to a float; None when empty."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return text.strip()


def _parse_number(text: str) -> Union[int, float, str, None]:
    """Parse a displayed number like "100.0000" to int/float; None when empty."""
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return text.strip()
    return int(value) if value.is_integer() else value


def _wait_grid_idle(page: Page) -> None:
    """Wait for the product grid's AJAX loading mask to settle."""
    page.wait_for_selector(
        "div[data-role='spinner'][data-component$='product_columns']",
        state="hidden",
        timeout=30000,
    )
    page.wait_for_load_state("networkidle")


def fuzzy_search_products(
    page: Page, query: str, base_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Search the product catalog in the Luma admin panel by keyword.

    `base_url` pins which admin backend to read, and must match the instance
    the session in `page` was logged into.

    Requires an authenticated admin session (see login.login_admin). Navigates
    to Catalog > Products, types the query into the grid's "Search by keyword"
    box, applies it, and parses the resulting grid. The grid matches the
    keyword fuzzily across product attributes (name, SKU, ...).

    Returns a dict:
        success: whether the search ran and the grid was parsed.
        query: the applied search query.
        records_found: the grid's "N records found" counter.
        items: one dict per product row with keys id, name, type, sku, price,
               quantity, and status. Only the first grid page is returned
               (page size follows the grid's saved setting).
        error_message: set when success is False.
    """
    result: Dict[str, Any] = {
        "success": False,
        "query": query,
        "records_found": 0,
        "items": [],
        "error_message": None,
    }

    page.goto(admin_products_url(base_url))
    page.wait_for_load_state("networkidle")

    # The grid renders twice (main + hidden sticky clone); .first is the main one.
    search_input = page.locator("input#fulltext").first
    if search_input.count() == 0:
        result["error_message"] = (
            "Product grid search box not found — is the admin session logged in?"
        )
        return result
    _wait_grid_idle(page)

    # The grid persists the previous search/filters per admin user (bookmarks);
    # clear any active chips so the new query starts from the full catalog.
    clear_all = page.locator(
        ".admin__data-grid-filters-current._show button[data-action='grid-filter-reset']"
    ).first
    if clear_all.count() > 0 and clear_all.is_visible():
        clear_all.click()
        _wait_grid_idle(page)

    # Enter applies the search (the submit button hides behind the grid's
    # loading mask, so keyboard submit is the reliable path). Bracket the submit
    # with the grid's own AJAX data request: waiting on networkidle/the spinner
    # alone races, because the render request can fire *after* the mask has
    # already settled, leaving us parsing the stale, unfiltered catalog.
    search_input.fill(query)
    with page.expect_response(
        lambda r: "mui/index/render" in r.url, timeout=30000
    ):
        search_input.press("Enter")

    # The grid reloads via AJAX: the applied search shows up as a "Keyword"
    # filter chip and the loading spinner hides once rows are re-rendered.
    page.wait_for_selector("ul[data-role='filter-list'] li", timeout=30000)
    _wait_grid_idle(page)

    table = page.locator("table[data-role='grid']").first
    if table.count() == 0:
        result["error_message"] = "Product grid table not found"
        return result

    # Column positions are user-configurable, so map them by header label.
    headers = [h.strip() for h in table.locator("thead th").all_inner_texts()]
    col_idx = {key: headers.index(label) for key, label in _COLUMNS.items() if label in headers}
    if "name" not in col_idx:
        result["error_message"] = f"Expected grid columns not found (headers: {headers})"
        return result

    count_loc = page.locator(
        "div.admin__control-support-text", has_text="records found"
    ).first
    if count_loc.count() > 0:
        match = re.search(r"\d+", count_loc.inner_text().replace(",", ""))
        if match:
            result["records_found"] = int(match.group())

    items: List[Dict[str, Any]] = []
    for row in table.locator("tbody tr.data-row").all():
        cells = [c.strip() for c in row.locator("td").all_inner_texts()]
        item: Dict[str, Any] = {}
        for key, idx in col_idx.items():
            text = cells[idx] if idx < len(cells) else ""
            if key == "price":
                item[key] = _parse_price(text)
            elif key in ("id", "quantity"):
                item[key] = _parse_number(text)
            else:
                item[key] = text
        items.append(item)

    result["items"] = items
    result["success"] = True
    return result
