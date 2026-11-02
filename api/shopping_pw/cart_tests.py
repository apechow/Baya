"""Ad-hoc tests for cart helper functions."""

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

from api.shopping_pw import cart  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


ERROR_SELECTOR = (
    ".page.messages .message-error, "
    ".page.messages .error.message, "
    "div.messages .message-error, "
    "div.messages .error.message"
)


class CartTests(unittest.TestCase):
    def test_add_product_to_cart_success(self) -> None:
        form = FakeLocator(count_value=1)
        form.nested = {
            "#product-options-wrapper .field.required": FakeLocator(count_value=0),
            "input#qty": FakeLocator(count_value=1),
            "button#product-addtocart-button, button.action.primary.tocart": FakeLocator(count_value=1),
        }

        page = FakePage(
            locators={
                "form#product_addtocart_form": form,
                ERROR_SELECTOR: FakeLocator(count_value=0),
                ".minicart-wrapper .counter-number": FakeLocator(text="Cart (2)"),
            }
        )

        result = cart.add_product_to_cart(page, "http://example.com/p/sku123", quantity=2)

        self.assertTrue(result.success)
        self.assertEqual(result.cart_count_after, 2)
        self.assertEqual(result.requested_quantity, 2)
        self.assertIn("http://example.com/p/sku123", page.visited)

    def test_add_product_to_cart_missing_required_options(self) -> None:
        required_field = FakeLocator(
            count_value=1,
            nested={
                'input[name^="options["]': FakeLocator(count_value=1, attributes={"name": "options[100]"})
            },
        )
        form = FakeLocator(
            count_value=1,
            nested={
                "#product-options-wrapper .field.required": required_field,
            },
        )
        page = FakePage(
            locators={
                "form#product_addtocart_form": form,
                ERROR_SELECTOR: FakeLocator(count_value=0),
                "button#product-addtocart-button, button.action.primary.tocart": FakeLocator(count_value=0),
            }
        )

        result = cart.add_product_to_cart(page, "http://example.com/p/sku-required", quantity=1, option_values=None)

        self.assertFalse(result.success)
        self.assertIn("Missing required product options", result.error_message or "")

    def test_add_product_to_cart_without_button(self) -> None:
        form = FakeLocator(
            count_value=1,
            nested={
                "#product-options-wrapper .field.required": FakeLocator(count_value=0),
                "button#product-addtocart-button, button.action.primary.tocart": FakeLocator(count_value=0),
            },
        )
        page = FakePage(
            locators={
                "form#product_addtocart_form": form,
                ERROR_SELECTOR: FakeLocator(count_value=0),
            }
        )

        result = cart.add_product_to_cart(page, "http://example.com/p/missing-btn")
        self.assertFalse(result.success)
        self.assertIn("button", result.error_message.lower())

    def test_get_cart_items_parses_single_row(self) -> None:
        body = FakeLocator(
            nested={
                "td.col.item .product-item-details .product-item-name a": FakeLocator(
                    text="Cool Jacket", attributes={"href": "http://store/p/jacket"}
                ),
                "input[data-cart-item-id]": FakeLocator(
                    count_value=1, attributes={"data-cart-item-id": "SKU123", "value": "2", "id": "cart-571-qty"}
                ),
                "td.col.price span.price": FakeLocator(text="$9.99"),
                "td.col.subtotal span.price": FakeLocator(text="$19.98"),
                "td.col.item img.product-image-photo": FakeLocator(attributes={"src": "http://img/jacket.jpg"}),
            }
        )
        tbodies = FakeLocator(children=[body], count_value=1)

        page = FakePage(
            locators={
                "#shopping-cart-table tbody.cart.item": tbodies,
            }
        )

        items = cart.get_cart_items(page)
        self.assertEqual(len(items), 1)
        parsed = items[0]
        self.assertEqual(parsed.sku, "SKU123")
        self.assertEqual(parsed.quantity, 2)
        self.assertAlmostEqual(parsed.price, 9.99)
        self.assertEqual(parsed.item_id, "571")

    def test_set_cart_item_quantity_updates_match(self) -> None:
        target_input = FakeLocator(count_value=1)
        update_btn = FakeLocator(count_value=1)
        body = FakeLocator(
            nested={
                'input[data-cart-item-id="SKU1"]': target_input,
            }
        )
        tbodies = FakeLocator(children=[body], count_value=1)
        page = FakePage(
            locators={
                "#shopping-cart-table tbody.cart.item": tbodies,
                "button.action.update[name='update_cart_action']": FakeLocator(count_value=0),
                ".cart.main.actions button.action.update": update_btn,
            }
        )

        success = cart.set_cart_item_quantity(page, "SKU1", 3)
        self.assertTrue(success)
        self.assertEqual(target_input.text, "3")
        self.assertTrue(any(action[0] == "click" for action in update_btn.actions))

    def test_remove_cart_item_handles_missing(self) -> None:
        tbodies = FakeLocator(children=[], count_value=0)
        page = FakePage(
            locators={
                "#shopping-cart-table tbody.cart.item": tbodies,
            }
        )
        self.assertFalse(cart.remove_cart_item(page, "SKU-MISSING"))

    def test_empty_cart_stops_after_first_removal(self) -> None:
        page = FakePage()

        def locator_func(selector: str) -> FakeLocator:
            if selector == "#shopping-cart-table tbody.cart.item":
                if hasattr(page, "cleared") and page.cleared:
                    return FakeLocator(count_value=0)
                delete_link = FakeLocator(
                    count_value=1,
                    on_click=lambda: setattr(page, "cleared", True),
                )
                body = FakeLocator(
                    nested={
                        "a.action.action-delete": delete_link,
                    }
                )
                return FakeLocator(children=[body], count_value=1)
            return page.locators.get(selector, FakeLocator())

        page.locator = locator_func  # type: ignore[assignment]
        page.locators = {}

        cart.empty_cart(page)
        self.assertTrue(getattr(page, "cleared", False))


if __name__ == "__main__":
    unittest.main()
