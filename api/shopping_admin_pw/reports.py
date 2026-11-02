"""Admin sales report helpers."""

from typing import Any, Dict, List, Optional, Union

from playwright.sync_api import Page

from .constants import (
    admin_bestsellers_url,
    admin_customer_totals_url,
    admin_orders_report_url,
)

AGGREGATION_VALUES = ("day", "month", "year")

# Accepted order-status inputs (label or value, lowercased) → form option value.
ORDER_STATUS_VALUES = {
    "canceled": "canceled",
    "closed": "closed",
    "complete": "complete",
    "suspected fraud": "fraud",
    "fraud": "fraud",
    "on hold": "holded",
    "holded": "holded",
    "payment review": "payment_review",
    "payment_review": "payment_review",
    "paypal canceled reversal": "paypal_canceled_reversal",
    "paypal_canceled_reversal": "paypal_canceled_reversal",
    "paypal reversed": "paypal_reversed",
    "paypal_reversed": "paypal_reversed",
    "processing": "processing",
}

# Orders report grid: column header → result key.
_ORDERS_COLUMN_KEYS = {
    "Interval": "period",
    "Orders": "orders",
    "Sales Items": "sales_items",
    "Sales Total": "sales_total",
    "Invoiced": "invoiced",
    "Refunded": "refunded",
    "Sales Tax": "sales_tax",
    "Sales Shipping": "sales_shipping",
    "Sales Discount": "sales_discount",
    "Canceled": "canceled",
}
_ORDERS_COUNT_KEYS = ("orders", "sales_items")


def _parse_price(text: str) -> Union[float, str]:
    """Parse a displayed price like "$1,234.00" to a float; keep raw text on failure."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return text.strip()


def _parse_qty(text: str) -> Union[int, float, str]:
    """Parse a displayed quantity to int (or float if fractional); keep raw text on failure."""
    cleaned = text.strip().replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return text.strip()
    return int(value) if value.is_integer() else value


def get_bestsellers_report(
    page: Page,
    start_date: str,
    end_date: str,
    aggregation: str = "Day",
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch the Bestsellers report from the Luma admin panel.

    Requires an authenticated admin session (see login.login_admin). Navigates
    to Reports > Products > Bestsellers, fills the filter form, clicks
    "Show Report", and parses the resulting grid.

    Args:
        page: Playwright page with an active admin session.
        start_date: "From" date, e.g. "1/1/2018" (M/D/YYYY).
        end_date: "To" date, e.g. "12/31/2024" (M/D/YYYY).
        aggregation: reporting interval — "Day", "Month", or "Year"
                     (case-insensitive).

    Returns a dict:
        success: whether the report was retrieved and parsed.
        aggregation / start_date / end_date: the applied filter values.
        records_found: the grid's "N records found" counter (counts intervals,
                       not product rows).
        total_qty_ordered: the grid's Total footer for Order Quantity, if shown.
        items: one dict per product row with keys period, product, price,
               qty_ordered. The interval cell spans multiple rows in the HTML,
               so the period is carried forward to every product row in it.
        error_message: set when success is False.
    """
    result: Dict[str, Any] = {
        "success": False,
        "aggregation": aggregation,
        "start_date": start_date,
        "end_date": end_date,
        "records_found": 0,
        "total_qty_ordered": None,
        "items": [],
        "error_message": None,
    }

    period_value = aggregation.strip().lower()
    if period_value not in AGGREGATION_VALUES:
        result["error_message"] = (
            f"Invalid aggregation {aggregation!r}: expected \"Day\", \"Month\", or \"Year\""
        )
        return result
    result["aggregation"] = period_value

    page.goto(admin_bestsellers_url(base_url))
    page.wait_for_load_state("networkidle")

    if page.locator("#filter_form").count() == 0:
        result["error_message"] = (
            "Bestsellers filter form not found — is the admin session logged in?"
        )
        return result

    page.select_option("#sales_report_period_type", period_value)
    page.fill("#sales_report_from", start_date)
    page.fill("#sales_report_to", end_date)
    page.click("#filter_form_submit")
    page.wait_for_load_state("networkidle")

    # Client-side validation failures (e.g. malformed dates) block navigation
    # and annotate the form instead of loading the report.
    errors = page.locator("#filter_form .mage-error")
    if errors.count() > 0:
        messages = [t.strip() for t in errors.all_inner_texts() if t.strip()]
        result["error_message"] = "; ".join(messages) or "Filter form validation failed"
        return result

    count_loc = page.locator(
        'span[data-ui-id="adminhtml-sales-bestsellers-grid-total-count"]'
    )
    if count_loc.count() > 0:
        try:
            result["records_found"] = int(count_loc.first.inner_text().strip())
        except ValueError:
            pass

    table = page.locator(".admin__data-grid-wrap table.data-grid").first
    if table.count() == 0:
        result["error_message"] = "Bestsellers grid table not found"
        return result

    totals = table.locator("tfoot tr.totals th.col-qty_ordered")
    if totals.count() > 0:
        total_qty = _parse_qty(totals.first.inner_text())
        if not isinstance(total_qty, str):
            result["total_qty_ordered"] = total_qty

    items: List[Dict[str, Any]] = []
    period: Optional[str] = None
    for row in table.locator("tbody tr").all():
        cells = [c.strip() for c in row.locator("td").all_inner_texts()]
        if len(cells) == 4:
            period, product, price, qty = cells
        elif len(cells) == 3:
            # The interval cell has a rowspan, so follow-up rows omit it.
            product, price, qty = cells
        else:
            # Single-cell "We couldn't find any records." placeholder row.
            continue
        items.append({
            "period": period,
            "product": product,
            "price": _parse_price(price),
            "qty_ordered": _parse_qty(qty),
        })

    result["items"] = items
    result["success"] = True
    return result


def _parse_orders_cell(key: str, text: str) -> Any:
    """Parse an orders-report cell according to its column."""
    if key == "period":
        return text
    if key in _ORDERS_COUNT_KEYS:
        return _parse_qty(text)
    return _parse_price(text)


def get_orders_report(
    page: Page,
    start_date: str,
    end_date: str,
    aggregation: str = "Day",
    order_status: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch the Orders report from the Luma admin panel.

    Requires an authenticated admin session (see login.login_admin). Navigates
    to Reports > Sales > Orders, fills the filter form, clicks "Show Report",
    and parses the resulting grid: the number of orders per interval along
    with all the other report columns.

    Args:
        page: Playwright page with an active admin session.
        start_date: "From" date, e.g. "1/1/2018" (M/D/YYYY).
        end_date: "To" date, e.g. "12/31/2024" (M/D/YYYY).
        aggregation: reporting interval — "Day", "Month", or "Year"
                     (case-insensitive).
        order_status: restrict the report to one order status, by label or
                      value ("Complete", "processing", "On Hold", ...); None
                      or "Any" includes all statuses except canceled orders.

    Returns a dict:
        success: whether the report was retrieved and parsed.
        aggregation / start_date / end_date / order_status: the applied
            filter values (order_status is normalized to the form value,
            None when unrestricted).
        records_found: the grid's "N records found" counter (one per interval).
        totals: the grid's Total footer as a dict with the same keys as items
                (minus period), or None when the report is empty.
        items: one dict per interval row with keys period, orders,
               sales_items, sales_total, invoiced, refunded, sales_tax,
               sales_shipping, sales_discount, and canceled.
        error_message: set when success is False.
    """
    result: Dict[str, Any] = {
        "success": False,
        "aggregation": aggregation,
        "start_date": start_date,
        "end_date": end_date,
        "order_status": None,
        "records_found": 0,
        "totals": None,
        "items": [],
        "error_message": None,
    }

    period_value = aggregation.strip().lower()
    if period_value not in AGGREGATION_VALUES:
        result["error_message"] = (
            f"Invalid aggregation {aggregation!r}: expected \"Day\", \"Month\", or \"Year\""
        )
        return result
    result["aggregation"] = period_value

    status_value: Optional[str] = None
    if order_status is not None and order_status.strip().lower() != "any":
        status_value = ORDER_STATUS_VALUES.get(order_status.strip().lower())
        if status_value is None:
            options = sorted(set(ORDER_STATUS_VALUES.values()))
            result["error_message"] = (
                f"Invalid order_status {order_status!r}: expected \"Any\" or one of {options}"
            )
            return result
    result["order_status"] = status_value

    page.goto(admin_orders_report_url(base_url))
    page.wait_for_load_state("networkidle")

    if page.locator("#filter_form").count() == 0:
        result["error_message"] = (
            "Orders report filter form not found — is the admin session logged in?"
        )
        return result

    page.select_option("#sales_report_period_type", period_value)
    page.fill("#sales_report_from", start_date)
    page.fill("#sales_report_to", end_date)
    if status_value is not None:
        # Switching to "Specified" enables and reveals the status multiselect.
        page.select_option("#sales_report_show_order_statuses", "1")
        page.select_option("#sales_report_order_statuses", status_value)
    page.click("#filter_form_submit")
    page.wait_for_load_state("networkidle")

    # Client-side validation failures (e.g. malformed dates) block navigation
    # and annotate the form instead of loading the report.
    errors = page.locator("#filter_form .mage-error")
    if errors.count() > 0:
        messages = [t.strip() for t in errors.all_inner_texts() if t.strip()]
        result["error_message"] = "; ".join(messages) or "Filter form validation failed"
        return result

    count_loc = page.locator('span[data-ui-id="adminhtml-sales-sales-grid-total-count"]')
    if count_loc.count() > 0:
        try:
            result["records_found"] = int(count_loc.first.inner_text().strip())
        except ValueError:
            pass

    table = page.locator(".admin__data-grid-wrap table.data-grid").first
    if table.count() == 0:
        result["error_message"] = "Orders report grid table not found"
        return result

    headers = [h.strip() for h in table.locator("thead th").all_inner_texts()]
    keys = [_ORDERS_COLUMN_KEYS.get(h) for h in headers]
    if "period" not in keys or "orders" not in keys:
        result["error_message"] = f"Expected grid columns not found (headers: {headers})"
        return result

    totals_row = table.locator("tfoot tr.totals th")
    if totals_row.count() > 0:
        cells = [c.strip() for c in totals_row.all_inner_texts()]
        totals: Dict[str, Any] = {}
        for key, text in zip(keys, cells):
            if key is None or key == "period":
                continue
            totals[key] = _parse_orders_cell(key, text)
        result["totals"] = totals

    items: List[Dict[str, Any]] = []
    for row in table.locator("tbody tr").all():
        cells = [c.strip() for c in row.locator("td").all_inner_texts()]
        if len(cells) != len(keys):
            # Single-cell "We couldn't find any records." placeholder row.
            continue
        item = {
            key: _parse_orders_cell(key, text)
            for key, text in zip(keys, cells)
            if key is not None
        }
        items.append(item)

    result["items"] = items
    result["success"] = True
    return result


def get_customer_totals_report(
    page: Page,
    start_date: str,
    end_date: str,
    aggregation: str = "Day",
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch the customers Order Total report from the Luma admin panel.

    Requires an authenticated admin session (see login.login_admin). Navigates
    to Reports > Customers > Order Total, fills the filter form, clicks
    "Refresh", and parses the resulting grid: each customer's number of
    orders, average order amount, and order total per interval.

    Args:
        page: Playwright page with an active admin session.
        start_date: "From" date, e.g. "1/1/2019" (M/D/YYYY).
        end_date: "To" date, e.g. "7/31/2024" (M/D/YYYY).
        aggregation: reporting interval — "Day", "Month", or "Year"
                     (case-insensitive).

    Returns a dict:
        success: whether the report was retrieved and parsed.
        aggregation / start_date / end_date: the applied filter values.
        totals: the grid's Total footer as a dict with keys orders and total
                (across all customers and intervals), or None when the report
                has no footer.
        items: one dict per customer row with keys period, customer, orders,
               and total. The interval cell spans multiple rows in the HTML,
               so the period is carried forward to every customer row in it;
               intervals without orders are omitted.
        error_message: set when success is False.
    """
    result: Dict[str, Any] = {
        "success": False,
        "aggregation": aggregation,
        "start_date": start_date,
        "end_date": end_date,
        "totals": None,
        "items": [],
        "error_message": None,
    }

    period_value = aggregation.strip().lower()
    if period_value not in AGGREGATION_VALUES:
        result["error_message"] = (
            f"Invalid aggregation {aggregation!r}: expected \"Day\", \"Month\", or \"Year\""
        )
        return result
    result["aggregation"] = period_value

    page.goto(admin_customer_totals_url(base_url))
    page.wait_for_load_state("networkidle")

    if page.locator("#gridTotalsCustomer_period_date_from").count() == 0:
        result["error_message"] = (
            "Order Total report filter form not found — is the admin session logged in?"
        )
        return result

    page.select_option("#gridTotalsCustomer_report_period", period_value)
    page.fill("#gridTotalsCustomer_period_date_from", start_date)
    page.fill("#gridTotalsCustomer_period_date_to", end_date)
    page.click('button[data-ui-id="adminhtml-report-grid-refresh-button"]')
    page.wait_for_load_state("networkidle")

    # Client-side validation failures (e.g. malformed dates) block navigation
    # and annotate the date fields instead of loading the report.
    errors = page.locator("#gridTotalsCustomer_period_date_range .validation-advice")
    if errors.count() > 0:
        messages = [t.strip() for t in errors.all_inner_texts() if t.strip()]
        result["error_message"] = "; ".join(messages) or "Filter form validation failed"
        return result

    table = page.locator("#gridTotalsCustomer_table")
    if table.count() == 0:
        result["error_message"] = "Order Total report grid table not found"
        return result

    totals_row = table.locator("tfoot tr.totals")
    if totals_row.count() > 0:
        # An empty report still renders the footer, with blank money cells.
        orders_text = totals_row.locator(
            "th[data-column='orders_count']"
        ).first.inner_text().strip()
        total_text = totals_row.locator(
            "th[data-column='orders_sum_amount']"
        ).first.inner_text().strip()
        result["totals"] = {
            "orders": _parse_qty(orders_text) if orders_text else None,
            "total": _parse_price(total_text) if total_text else None,
        }

    items: List[Dict[str, Any]] = []
    period: Optional[str] = None
    for row in table.locator("tbody tr").all():
        # The interval cell has a rowspan, so follow-up rows omit it.
        period_cell = row.locator("td.col-period")
        if period_cell.count() > 0:
            period = period_cell.first.inner_text().strip()
        # Rows without a customer cell are "We can't find records for this
        # period." placeholders (per-interval or grid-wide).
        name_cell = row.locator("td[data-column='name']")
        if name_cell.count() == 0:
            continue
        items.append({
            "period": period,
            "customer": name_cell.first.inner_text().strip(),
            "orders": _parse_qty(
                row.locator("td[data-column='orders_count']").first.inner_text()
            ),
            "total": _parse_price(
                row.locator("td[data-column='orders_sum_amount']").first.inner_text()
            ),
        })

    result["items"] = items
    result["success"] = True
    return result
