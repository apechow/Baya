"""Order history, details, and reorder helpers."""

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from playwright.sync_api import Page

from .cart import get_cart_items
from .constants import order_history_url


@dataclass
class OrderSummary:
    """Summary row from the order history table."""

    order_number: str
    date: str
    total: Optional[float]
    status: str
    view_url: str
    reorder_url: Optional[str]


@dataclass
class OrderItem:
    """Single item on an order detail page."""

    name: str
    sku: str
    quantity: float
    price: Optional[float]
    subtotal: Optional[float]
    options: Dict[str, str]


@dataclass
class OrderDetails:
    """Detailed view of a specific order."""

    order_number: str
    status: str
    order_date: Optional[str]
    items: List[OrderItem]
    totals: Dict[str, Optional[float]]
    shipping_address: Optional[str]
    billing_address: Optional[str]
    shipping_method: Optional[str]
    payment_method: Optional[str]
    reorder_url: Optional[str]
    view_url: str


@dataclass
class ReorderResult:
    """Outcome of pressing the Reorder button on an order detail page."""

    success: bool
    cart_count_after: Optional[int]
    cart_url: Optional[str]
    error_message: Optional[str] = None


def _text_or_blank(locator) -> str:
    try:
        if hasattr(locator, "count") and locator.count() == 0:
            return ""
        return locator.inner_text().strip()
    except Exception:
        return ""


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"-?\d[\d,]*\.?\d*", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _extract_datapost_action(data_post: Optional[str]) -> Optional[str]:
    if not data_post:
        return None
    try:
        payload = json.loads(data_post)
        action = payload.get("action")
        if action:
            return action.replace("\\/", "/")
    except Exception:
        pass

    match = re.search(r'"action"\s*:\s*"([^"]+)"', data_post)
    if match:
        return match.group(1).replace("\\/", "/")
    return None


def _clean_block_text(node) -> Optional[str]:
    if node is None:
        return None
    if hasattr(node, "count") and node.count() == 0:
        return None
    try:
        raw = node.inner_text()
    except Exception:
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return None
    return "\n".join(lines)


def get_order_history(
    page: Page, base_url: Optional[str] = None
) -> List[OrderSummary]:
    """Return the list of orders from the account order history page."""
    page.goto(order_history_url(base_url))

    page.wait_for_load_state("networkidle")
    rows = page.locator("table#my-orders-table tbody tr")
    count = rows.count()
    orders: List[OrderSummary] = []

    for i in range(count):
        row = rows.nth(i)
        order_number = _text_or_blank(row.locator("td.col.id"))
        date_text = _text_or_blank(row.locator("td.col.date"))
        total_text = _text_or_blank(row.locator("td.col.total span.price"))
        status_text = _text_or_blank(row.locator("td.col.status"))

        actions_cell = row.locator("td.col.actions")
        view_link = actions_cell.locator("a.action.view").first
        view_url = view_link.get_attribute("href") or order_history_url(base_url)

        reorder_link = actions_cell.locator("a.action.order").first
        reorder_url = None
        if reorder_link.count() > 0:
            reorder_url = reorder_link.get_attribute("href")
            if not reorder_url or reorder_url == "#":
                reorder_url = _extract_datapost_action(
                    reorder_link.get_attribute("data-post")
                )

        orders.append(
            OrderSummary(
                order_number=order_number,
                date=date_text,
                total=_parse_price(total_text),
                status=status_text,
                view_url=view_url,
                reorder_url=reorder_url,
            )
        )

    return orders


def get_order_details(page: Page, order_url: str) -> OrderDetails:
    """Go to an order detail page and extract item + address details."""
    page.goto(order_url)

    page.wait_for_load_state("networkidle")
    title_loc = page.locator("h1.page-title span.base")
    title_text = _text_or_blank(title_loc)
    order_number = title_text.replace("Order #", "").strip() or title_text

    status = _text_or_blank(page.locator("span.order-status"))
    order_date_nodes = page.locator("div.order-date span")
    order_date = (
        _text_or_blank(order_date_nodes.nth(1))
        if order_date_nodes.count() > 1
        else None
    )

    reorder_link = page.locator(".order-actions-toolbar a.action.order").first
    reorder_url = None
    if reorder_link.count() > 0:
        reorder_url = reorder_link.get_attribute("href")
        if not reorder_url or reorder_url == "#":
            reorder_url = _extract_datapost_action(
                reorder_link.get_attribute("data-post")
            )

    items_table = page.locator("table#my-orders-table")
    rows = items_table.locator("tbody tr")
    items: List[OrderItem] = []
    for i in range(rows.count()):
        row = rows.nth(i)
        name_cell = row.locator("td.col.name")
        name = _text_or_blank(name_cell.locator("strong.product-item-name"))

        options: Dict[str, str] = {}
        dl = name_cell.locator("dl.item-options")
        if dl.count() > 0:
            dts = dl.locator("dt")
            dds = dl.locator("dd")
            option_pairs = min(dts.count(), dds.count())
            for idx in range(option_pairs):
                option_name = dts.nth(idx).inner_text().strip()
                option_value = dds.nth(idx).inner_text().strip()
                options[option_name] = option_value

        sku = _text_or_blank(row.locator("td.col.sku"))
        price_text = _text_or_blank(row.locator("td.col.price span.price"))
        qty_text = _text_or_blank(
            row.locator("td.col.qty .items-qty .content").first
        )
        subtotal_text = _text_or_blank(row.locator("td.col.subtotal span.price"))

        try:
            quantity = float(qty_text)
        except ValueError:
            quantity = 0.0

        items.append(
            OrderItem(
                name=name,
                sku=sku,
                quantity=quantity,
                price=_parse_price(price_text),
                subtotal=_parse_price(subtotal_text),
                options=options,
            )
        )

    totals: Dict[str, Optional[float]] = {}
    total_rows = items_table.locator("tfoot tr")
    for i in range(total_rows.count()):
        row = total_rows.nth(i)
        label = _text_or_blank(row.locator("th"))
        amount_text = _text_or_blank(row.locator("td span.price"))
        totals[label] = _parse_price(amount_text)

    shipping_address = _clean_block_text(
        page.locator(".box-order-shipping-address .box-content").first
    )
    billing_address = _clean_block_text(
        page.locator(".box-order-billing-address .box-content").first
    )
    shipping_method = _clean_block_text(
        page.locator(".box-order-shipping-method .box-content").first
    )
    payment_method = _clean_block_text(
        page.locator(".box-order-billing-method .box-content").first
    )

    return OrderDetails(
        order_number=order_number,
        status=status,
        order_date=order_date,
        items=items,
        totals=totals,
        shipping_address=shipping_address,
        billing_address=billing_address,
        shipping_method=shipping_method,
        payment_method=payment_method,
        reorder_url=reorder_url,
        view_url=order_url,
    )


def reorder_order(page: Page, order_url: str) -> ReorderResult:
    """
    Click the Reorder action on an order detail page.

    Magento handles the add-to-cart and redirects to the cart; this helper
    simply presses the button and reports any visible errors plus the cart
    item count afterward.
    """
    page.goto(order_url)

    page.wait_for_load_state("networkidle")
    reorder_link = page.locator(".order-actions-toolbar a.action.order").first
    if reorder_link.count() == 0:
        return ReorderResult(
            success=False,
            cart_count_after=None,
            cart_url=page.url,
            error_message="Reorder link not found on order page.",
        )

    reorder_link.click()

    page.wait_for_load_state("networkidle")
    error_loc = page.locator(
        ".page.messages .message-error, "
        ".page.messages .error.message, "
        "div.messages .message-error, "
        "div.messages .error.message"
    )
    if error_loc.count() > 0:
        return ReorderResult(
            success=False,
            cart_count_after=None,
            cart_url=page.url,
            error_message=error_loc.nth(0).inner_text().strip() or "Unknown reorder error.",
        )

    cart_count = None
    try:
        cart_items = get_cart_items(page)
        cart_count = len(cart_items)
    except Exception:
        cart_count = None

    return ReorderResult(
        success=True,
        cart_count_after=cart_count,
        cart_url=page.url,
        error_message=None,
    )
