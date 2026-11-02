"""Admin search term helpers."""

from typing import Any, Dict, List, Optional, Union

from playwright.sync_api import Page

# The URL carries limit=200, which keeps every term on one grid page (the store
# only has a handful).
from .constants import admin_search_terms_url


def _parse_int(text: str) -> Union[int, str]:
    """Parse a displayed count to an int; keep raw text on failure."""
    cleaned = text.strip().replace(",", "")
    try:
        return int(cleaned)
    except ValueError:
        return text.strip()


def get_search_terms(page: Page, base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the store's search terms from the Luma admin panel.

    `base_url` pins which admin backend to read, and must match the instance
    the session in `page` was logged into.

    Requires an authenticated admin session (see login.login_admin). Navigates
    to Marketing > SEO & Search > Search Terms and parses the grid. The "uses"
    count (the grid's Uses / popularity column) ranks how often customers
    searched each term.

    Returns a dict:
        success: whether the grid was retrieved and parsed.
        records_found: the grid's "N records found" counter.
        items: one dict per search term with keys search_query, results
               (number of matching products), and uses (times searched).
        error_message: set when success is False.
    """
    result: Dict[str, Any] = {
        "success": False,
        "records_found": 0,
        "items": [],
        "error_message": None,
    }

    page.goto(admin_search_terms_url(base_url))
    page.wait_for_load_state("networkidle")

    if page.locator("#search_term_grid_table").count() == 0:
        result["error_message"] = (
            "Search terms grid not found — is the admin session logged in?"
        )
        return result

    count_loc = page.locator("#search_term_grid-total-count")
    if count_loc.count() > 0:
        try:
            result["records_found"] = int(count_loc.first.inner_text().strip())
        except ValueError:
            pass

    items: List[Dict[str, Any]] = []
    # data-role="row" marks data rows only, excluding the filter row and the
    # "We couldn't find any records." placeholder.
    for row in page.locator("#search_term_grid_table tbody tr[data-role='row']").all():
        query_cell = row.locator("td[data-column='search_query']")
        results_cell = row.locator("td[data-column='num_results']")
        uses_cell = row.locator("td[data-column='popularity']")
        if query_cell.count() == 0:
            continue
        items.append({
            "search_query": query_cell.first.inner_text().strip(),
            "results": _parse_int(results_cell.first.inner_text()) if results_cell.count() else None,
            "uses": _parse_int(uses_cell.first.inner_text()) if uses_cell.count() else None,
        })

    result["items"] = items
    result["success"] = True
    return result
