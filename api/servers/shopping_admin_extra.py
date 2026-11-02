import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from typing import List, Optional

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

from api.shopping_admin_pw import (
    fuzzy_search_products,
    get_bestsellers_report,
    get_customer_totals_report,
    get_default_admin_credentials,
    get_orders_report,
    get_search_terms,
    login_admin,
)
# Aliased: `base_url` is also the wire-level name callers use to pin an admin
# backend, so the endpoint signatures below would otherwise shadow it.
from api.shopping_admin_pw.constants import admin_base_url as resolve_admin_base_url

app = FastAPI(
    title="Shopping Admin Extra API",
    description="Custom endpoints for the WebArena Shopping Admin (Luma Admin) website.",
    version="1.0.0",
)


# ── Auth helper ───────────────────────────────────────────────────────────────

def _make_admin_browser_page(playwright, admin_backend: Optional[str] = None):
    """Create a browser page authenticated as the default Luma admin user.

    `admin_backend` pins which Magento admin instance to log into. This process
    serves every eval worker, so without it a multi-docker run authenticates
    against the shared default instance and reads from there — while the grader
    inspects the worker's own backend.
    """
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    username, password = get_default_admin_credentials()
    login_result = login_admin(page, username, password, base_url=admin_backend)
    if not login_result.success:
        browser.close()
        raise RuntimeError(f"Admin login failed: {login_result.error_message or 'Unknown error'}")
    return browser, page


class BestsellerItem(BaseModel):
    period: Optional[str] = None
    product: str
    price: Optional[float] = None
    qty_ordered: Optional[int] = None


class BestsellersReportResponse(BaseModel):
    success: bool
    aggregation: str
    start_date: str
    end_date: str
    records_found: int = 0
    total_qty_ordered: Optional[int] = None
    items: List[BestsellerItem] = []
    error_message: Optional[str] = None


@app.get("/bestsellers_report", response_model=BestsellersReportResponse)
def bestsellers_report(
    start_date: str,
    end_date: str,
    aggregation: str = "Month",
    base_url: Optional[str] = None,
) -> BestsellersReportResponse:
    """
    Log in to the Luma admin panel and fetch the Reports > Products >
    Bestsellers grid for the given date range and aggregation interval
    ("Day", "Month", or "Year").

    `base_url` pins which admin backend to read; see _make_admin_browser_page.
    """
    admin_backend = resolve_admin_base_url(base_url)
    with sync_playwright() as p:
        try:
            browser, page = _make_admin_browser_page(p, admin_backend)
        except RuntimeError as exc:
            return BestsellersReportResponse(
                success=False,
                aggregation=aggregation,
                start_date=start_date,
                end_date=end_date,
                error_message=str(exc),
            )
        try:
            report = get_bestsellers_report(
                page, start_date, end_date, aggregation, base_url=admin_backend
            )
        finally:
            browser.close()

    return BestsellersReportResponse(
        success=report["success"],
        aggregation=report["aggregation"],
        start_date=report["start_date"],
        end_date=report["end_date"],
        records_found=report["records_found"],
        total_qty_ordered=report["total_qty_ordered"],
        items=[
            BestsellerItem(
                period=item["period"],
                product=item["product"],
                price=item["price"] if isinstance(item["price"], (int, float)) else None,
                qty_ordered=item["qty_ordered"] if isinstance(item["qty_ordered"], int) else None,
            )
            for item in report["items"]
        ],
        error_message=report["error_message"],
    )


class SearchTermItem(BaseModel):
    search_query: str
    results: Optional[int] = None
    uses: Optional[int] = None


class SearchTermsResponse(BaseModel):
    success: bool
    records_found: int = 0
    items: List[SearchTermItem] = []
    error_message: Optional[str] = None


@app.get("/search_terms", response_model=SearchTermsResponse)
def search_terms(base_url: Optional[str] = None) -> SearchTermsResponse:
    """
    Log in to the Luma admin panel and fetch the Marketing > SEO & Search >
    Search Terms grid: every search term with its number of results and uses.

    `base_url` pins which admin backend to read; see _make_admin_browser_page.
    """
    admin_backend = resolve_admin_base_url(base_url)
    with sync_playwright() as p:
        try:
            browser, page = _make_admin_browser_page(p, admin_backend)
        except RuntimeError as exc:
            return SearchTermsResponse(success=False, error_message=str(exc))
        try:
            report = get_search_terms(page, base_url=admin_backend)
        finally:
            browser.close()

    return SearchTermsResponse(
        success=report["success"],
        records_found=report["records_found"],
        items=[
            SearchTermItem(
                search_query=item["search_query"],
                results=item["results"] if isinstance(item["results"], int) else None,
                uses=item["uses"] if isinstance(item["uses"], int) else None,
            )
            for item in report["items"]
        ],
        error_message=report["error_message"],
    )


class OrdersReportTotals(BaseModel):
    orders: Optional[int] = None
    sales_items: Optional[int] = None
    sales_total: Optional[float] = None
    invoiced: Optional[float] = None
    refunded: Optional[float] = None
    sales_tax: Optional[float] = None
    sales_shipping: Optional[float] = None
    sales_discount: Optional[float] = None
    canceled: Optional[float] = None


class OrdersReportRow(OrdersReportTotals):
    period: Optional[str] = None


class OrdersReportResponse(BaseModel):
    success: bool
    aggregation: str
    start_date: str
    end_date: str
    order_status: Optional[str] = None
    records_found: int = 0
    totals: Optional[OrdersReportTotals] = None
    items: List[OrdersReportRow] = []
    error_message: Optional[str] = None


_ORDERS_COUNT_FIELDS = ("orders", "sales_items")
_ORDERS_MONEY_FIELDS = (
    "sales_total",
    "invoiced",
    "refunded",
    "sales_tax",
    "sales_shipping",
    "sales_discount",
    "canceled",
)


def _orders_number_fields(data: dict) -> dict:
    """Coerce the numeric orders-report fields, dropping unparsed raw strings."""
    fields = {}
    for key in _ORDERS_COUNT_FIELDS:
        fields[key] = data.get(key) if isinstance(data.get(key), int) else None
    for key in _ORDERS_MONEY_FIELDS:
        value = data.get(key)
        fields[key] = value if isinstance(value, (int, float)) else None
    return fields


@app.get("/orders_report", response_model=OrdersReportResponse)
def orders_report(
    start_date: str,
    end_date: str,
    aggregation: str = "Month",
    order_status: Optional[str] = None,
    base_url: Optional[str] = None,
) -> OrdersReportResponse:
    """
    Log in to the Luma admin panel and fetch the Reports > Sales > Orders grid
    for the given date range, aggregation interval ("Day", "Month", or
    "Year"), and optional order status: order counts and sales totals per
    interval.

    `base_url` pins which admin backend to read; see _make_admin_browser_page.
    """
    admin_backend = resolve_admin_base_url(base_url)
    with sync_playwright() as p:
        try:
            browser, page = _make_admin_browser_page(p, admin_backend)
        except RuntimeError as exc:
            return OrdersReportResponse(
                success=False,
                aggregation=aggregation,
                start_date=start_date,
                end_date=end_date,
                error_message=str(exc),
            )
        try:
            report = get_orders_report(
                page, start_date, end_date, aggregation, order_status,
                base_url=admin_backend,
            )
        finally:
            browser.close()

    return OrdersReportResponse(
        success=report["success"],
        aggregation=report["aggregation"],
        start_date=report["start_date"],
        end_date=report["end_date"],
        order_status=report["order_status"],
        records_found=report["records_found"],
        totals=(
            OrdersReportTotals(**_orders_number_fields(report["totals"]))
            if report["totals"] is not None
            else None
        ),
        items=[
            OrdersReportRow(period=item["period"], **_orders_number_fields(item))
            for item in report["items"]
        ],
        error_message=report["error_message"],
    )


class CustomerTotalsTotals(BaseModel):
    orders: Optional[int] = None
    total: Optional[float] = None


class CustomerTotalsRow(BaseModel):
    period: Optional[str] = None
    customer: str
    orders: Optional[int] = None
    total: Optional[float] = None


class CustomerTotalsResponse(BaseModel):
    success: bool
    aggregation: str
    start_date: str
    end_date: str
    totals: Optional[CustomerTotalsTotals] = None
    items: List[CustomerTotalsRow] = []
    error_message: Optional[str] = None


@app.get("/customer_totals_report", response_model=CustomerTotalsResponse)
def customer_totals_report(
    start_date: str,
    end_date: str,
    aggregation: str = "Month",
    base_url: Optional[str] = None,
) -> CustomerTotalsResponse:
    """
    Log in to the Luma admin panel and fetch the Reports > Customers >
    Order Total grid for the given date range and aggregation interval
    ("Day", "Month", or "Year"): each customer's number of orders and
    order total per interval.

    `base_url` pins which admin backend to read; see _make_admin_browser_page.
    """
    admin_backend = resolve_admin_base_url(base_url)
    with sync_playwright() as p:
        try:
            browser, page = _make_admin_browser_page(p, admin_backend)
        except RuntimeError as exc:
            return CustomerTotalsResponse(
                success=False,
                aggregation=aggregation,
                start_date=start_date,
                end_date=end_date,
                error_message=str(exc),
            )
        try:
            report = get_customer_totals_report(
                page, start_date, end_date, aggregation, base_url=admin_backend
            )
        finally:
            browser.close()

    return CustomerTotalsResponse(
        success=report["success"],
        aggregation=report["aggregation"],
        start_date=report["start_date"],
        end_date=report["end_date"],
        totals=(
            CustomerTotalsTotals(
                orders=report["totals"]["orders"] if isinstance(report["totals"]["orders"], int) else None,
                total=report["totals"]["total"] if isinstance(report["totals"]["total"], (int, float)) else None,
            )
            if report["totals"] is not None
            else None
        ),
        items=[
            CustomerTotalsRow(
                period=item["period"],
                customer=item["customer"],
                orders=item["orders"] if isinstance(item["orders"], int) else None,
                total=item["total"] if isinstance(item["total"], (int, float)) else None,
            )
            for item in report["items"]
        ],
        error_message=report["error_message"],
    )


class ProductSearchResult(BaseModel):
    id: Optional[int] = None
    name: str
    type: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[float] = None
    status: Optional[str] = None


class FuzzySearchResponse(BaseModel):
    success: bool
    query: str
    records_found: int = 0
    items: List[ProductSearchResult] = []
    error_message: Optional[str] = None


@app.get("/fuzzy_search", response_model=FuzzySearchResponse)
def fuzzy_search(q: str, base_url: Optional[str] = None) -> FuzzySearchResponse:
    """
    Log in to the Luma admin panel and search the Catalog > Products grid by
    keyword, returning each matching product's id, name, type, SKU, price,
    quantity, and status.

    `base_url` pins which admin backend to search; see _make_admin_browser_page.
    """
    admin_backend = resolve_admin_base_url(base_url)
    with sync_playwright() as p:
        try:
            browser, page = _make_admin_browser_page(p, admin_backend)
        except RuntimeError as exc:
            return FuzzySearchResponse(success=False, query=q, error_message=str(exc))
        try:
            report = fuzzy_search_products(page, q, base_url=admin_backend)
        finally:
            browser.close()

    return FuzzySearchResponse(
        success=report["success"],
        query=report["query"],
        records_found=report["records_found"],
        items=[
            ProductSearchResult(
                id=item["id"] if isinstance(item["id"], int) else None,
                name=item["name"],
                type=item["type"],
                sku=item["sku"],
                price=item["price"] if isinstance(item["price"], (int, float)) else None,
                quantity=item["quantity"] if isinstance(item["quantity"], (int, float)) else None,
                status=item["status"],
            )
            for item in report["items"]
        ],
        error_message=report["error_message"],
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7793)
