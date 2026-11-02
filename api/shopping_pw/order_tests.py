"""Ad-hoc tests for order helpers."""

from __future__ import annotations

import sys
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

api_stub = types.ModuleType("api")
api_stub.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules["api"] = api_stub

playwright_stub = types.ModuleType("playwright")
playwright_stub.sync_api = types.SimpleNamespace(Page=object)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

from api.shopping_pw import order  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402
from api.shopping_pw import cart as cart_module  # noqa:E402


class OrderTests(unittest.TestCase):
    def test_parse_price_and_datapost(self) -> None:
        self.assertEqual(order._parse_price("$10.50"), 10.5)
        self.assertIsNone(order._parse_price("Not a price"))
        datapost = '{"action":"http:\\/\\/example.com\\/reorder"}'
        self.assertEqual(order._extract_datapost_action(datapost), "http://example.com/reorder")

    def test_get_order_history(self) -> None:
        row = FakeLocator(
            nested={
                "td.col.id": FakeLocator(text="0001"),
                "td.col.date": FakeLocator(text="Jan 1"),
                "td.col.total span.price": FakeLocator(text="$5.00"),
                "td.col.status": FakeLocator(text="Pending"),
                "td.col.actions": FakeLocator(
                    nested={
                        "a.action.view": FakeLocator(attributes={"href": "http://example.com/order/1"}),
                        "a.action.order": FakeLocator(attributes={"href": "http://example.com/order/1/reorder"}),
                    }
                ),
            }
        )
        rows = FakeLocator(children=[row], count_value=1)
        page = FakePage(locators={"table#my-orders-table tbody tr": rows})

        history = order.get_order_history(page)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].order_number, "0001")
        self.assertEqual(history[0].status, "Pending")
        self.assertEqual(history[0].total, 5.0)

    def test_get_order_details(self) -> None:
        name_cell = FakeLocator(
            nested={
                "strong.product-item-name": FakeLocator(text="Product 1"),
                "dl.item-options": FakeLocator(count_value=0),
            }
        )
        item_row = FakeLocator(
            nested={
                "td.col.name": name_cell,
                "td.col.sku": FakeLocator(text="SKU-1"),
                "td.col.price span.price": FakeLocator(text="$2.00"),
                "td.col.qty .items-qty .content": FakeLocator(children=[FakeLocator(text="2")], count_value=1),
                "td.col.subtotal span.price": FakeLocator(text="$4.00"),
            }
        )
        item_rows = FakeLocator(children=[item_row], count_value=1)
        total_row = FakeLocator(
            nested={
                "th": FakeLocator(text="Grand Total"),
                "td span.price": FakeLocator(text="$4.00"),
            }
        )
        total_rows = FakeLocator(children=[total_row], count_value=1)
        items_table = FakeLocator(
            nested={
                "tbody tr": item_rows,
                "tfoot tr": total_rows,
            }
        )
        page = FakePage(
            locators={
                "h1.page-title span.base": FakeLocator(text="Order #0001"),
                "span.order-status": FakeLocator(text="Pending"),
                "div.order-date span": FakeLocator(children=[FakeLocator(text="ignored"), FakeLocator(text="Jan 1")], count_value=2),
                ".order-actions-toolbar a.action.order": FakeLocator(count_value=1, attributes={"href": "http://reorder"}),
                "table#my-orders-table": items_table,
                ".box-order-shipping-address .box-content": FakeLocator(text="Ship Addr"),
                ".box-order-billing-address .box-content": FakeLocator(text="Bill Addr"),
                ".box-order-shipping-method .box-content": FakeLocator(text="Ground"),
                ".box-order-billing-method .box-content": FakeLocator(text="Visa"),
            }
        )

        details = order.get_order_details(page, "http://example.com/order/1")
        self.assertEqual(details.order_number, "0001")
        self.assertEqual(details.items[0].quantity, 2.0)
        self.assertEqual(details.totals["Grand Total"], 4.0)
        self.assertEqual(details.shipping_address, "Ship Addr")
        self.assertEqual(details.reorder_url, "http://reorder")

    def test_reorder_order_success_uses_cart_count(self) -> None:
        page = FakePage(
            locators={
                ".order-actions-toolbar a.action.order": FakeLocator(count_value=1),
                ".page.messages .message-error, .page.messages .error.message, div.messages .message-error, div.messages .error.message": FakeLocator(count_value=0),
            }
        )
        # Patch get_cart_items in the order module (not cart_module)
        # because order.py does "from .cart import get_cart_items"
        original_get_cart_items = order.get_cart_items
        order.get_cart_items = lambda _page: [1, 2, 3]  # type: ignore[assignment]
        try:
            result = order.reorder_order(page, "http://example.com/order/1")
        finally:
            order.get_cart_items = original_get_cart_items  # type: ignore[assignment]
        self.assertTrue(result.success)
        self.assertEqual(result.cart_count_after, 3)


if __name__ == "__main__":
    unittest.main()