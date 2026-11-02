"""Ad-hoc tests for wishlist helper functions."""

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


class _StubTimeoutError(Exception):
    """Stand-in for playwright's TimeoutError, which wishlist.py imports."""


playwright_stub.sync_api = types.SimpleNamespace(
    Page=object, TimeoutError=_StubTimeoutError
)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

from api.shopping_pw import wishlist  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


ERROR_SELECTOR = (
    ".page.messages .message-error, "
    ".page.messages .error.message, "
    "div.messages .message-error, "
    "div.messages .error.message"
)


GRID_SELECTOR = "div.products-grid.wishlist ol.product-items > li"
NEXT_SELECTOR = ".pages li.pages-item-next a.action.next"


def _wishlist_row(item_id: str, name: str, href: str, qty: str = "1") -> FakeLocator:
    """A single <li> of the wishlist grid, shaped as get_wishlist_items reads it."""
    return FakeLocator(
        attributes={"id": f"item_{item_id}"},
        nested={
            "strong.product-item-name a": FakeLocator(text=name, attributes={"href": href}),
            "input[type='number'][name^='qty[']": FakeLocator(count_value=1, attributes={"value": qty}),
            "span.price": FakeLocator(text="$10.00"),
            "img.product-image-photo": FakeLocator(attributes={"src": "http://img/wish.jpg"}),
        },
    )


class WishlistTests(unittest.TestCase):
    def test_add_product_to_wishlist_success(self) -> None:
        form = FakeLocator(
            nested={
                "#product-options-wrapper .field.required": FakeLocator(count_value=0),
                "input#qty": FakeLocator(count_value=1),
            }
        )
        # Success is confirmed by reading the product back out of the wishlist
        # grid, so the grid has to actually contain it.
        added = _wishlist_row("3", "Wish Item", "http://example.com/p/sku123.html")
        page = FakePage(
            locators={
                "form#product_addtocart_form": form,
                "a.action.towishlist[data-post]": FakeLocator(count_value=1),
                GRID_SELECTOR: FakeLocator(children=[added], count_value=1),
                ERROR_SELECTOR: FakeLocator(count_value=0),
            }
        )

        result = wishlist.add_product_to_wishlist(page, "http://example.com/p/sku123", quantity=2)
        self.assertTrue(result.success)
        self.assertEqual(result.wishlist_count_after, 1)
        self.assertEqual(result.requested_quantity, 2)

    def test_add_product_to_wishlist_reports_failure_when_absent(self) -> None:
        form = FakeLocator(
            nested={
                "#product-options-wrapper .field.required": FakeLocator(count_value=0),
                "input#qty": FakeLocator(count_value=1),
            }
        )
        other = _wishlist_row("3", "Something Else", "http://example.com/p/other.html")
        page = FakePage(
            locators={
                "form#product_addtocart_form": form,
                "a.action.towishlist[data-post]": FakeLocator(count_value=1),
                GRID_SELECTOR: FakeLocator(children=[other], count_value=1),
                ERROR_SELECTOR: FakeLocator(count_value=0),
            }
        )

        result = wishlist.add_product_to_wishlist(page, "http://example.com/p/sku123")
        self.assertFalse(result.success)
        self.assertIn("did not persist", result.error_message or "")

    def test_add_product_to_wishlist_missing_options(self) -> None:
        required_field = FakeLocator(
            count_value=1,
            nested={
                'input[name^="options["]': FakeLocator(count_value=1, attributes={"name": "options[200]"})
            },
        )
        form = FakeLocator(
            nested={
                "#product-options-wrapper .field.required": required_field,
            }
        )
        page = FakePage(
            locators={
                "form#product_addtocart_form": form,
                ERROR_SELECTOR: FakeLocator(count_value=0),
            }
        )

        result = wishlist.add_product_to_wishlist(page, "http://example.com/p/sku123", option_values=None)
        self.assertFalse(result.success)
        self.assertIn("Missing required", result.error_message or "")

    def test_get_wishlist_items_parses_row(self) -> None:
        item = FakeLocator(
            attributes={"id": "item_3"},
            nested={
                "strong.product-item-name a": FakeLocator(text="Wish Item", attributes={"href": "http://example.com/p"}),
                "input[type='number'][name^='qty[']": FakeLocator(count_value=1, attributes={"value": "4"}),
                "span.price": FakeLocator(text="$10.00"),
                "img.product-image-photo": FakeLocator(attributes={"src": "http://img/wish.jpg"}),
            },
        )
        product_items = FakeLocator(children=[item], count_value=1)
        page = FakePage(
            locators={
                "div.products-grid.wishlist ol.product-items > li": product_items,
            }
        )

        items = wishlist.get_wishlist_items(page)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_id, "3")
        self.assertEqual(items[0].quantity, 4)
        self.assertEqual(items[0].price, 10.0)

    def test_get_wishlist_items_reads_every_page(self) -> None:
        """A wishlist longer than one page must be read in full.

        Regression: the grid paginates at 10, so reading only page 1 reported
        a freshly added item as missing whenever it landed further down.
        """
        base = "http://example.com"
        listing = wishlist.wishlist_url(base)
        page_rows = {
            listing: [_wishlist_row(str(i), f"Item {i}", f"http://example.com/p/item{i}.html") for i in range(1, 11)],
            f"{listing}?p=2": [_wishlist_row(str(i), f"Item {i}", f"http://example.com/p/item{i}.html") for i in range(11, 19)],
        }
        page = FakePage()

        def on_goto(url: str) -> None:
            rows = page_rows[url]
            next_link = (
                FakeLocator(attributes={"href": f"{listing}?p=2"})
                if url == listing
                else FakeLocator(count_value=0)
            )
            page.locators = {
                GRID_SELECTOR: FakeLocator(children=rows, count_value=len(rows)),
                NEXT_SELECTOR: next_link,
            }

        page.on_goto = on_goto

        items = wishlist.get_wishlist_items(page, base)

        self.assertEqual(len(items), 18)
        self.assertEqual(items[-1].name, "Item 18")
        self.assertEqual(page.visited, [listing, f"{listing}?p=2"])

    def test_get_wishlist_items_stops_on_last_page(self) -> None:
        """No "Next" link means one page only — no speculative extra fetch.

        An out-of-range `?p=` re-serves page 1 rather than an empty grid, so
        over-fetching would silently duplicate every item.
        """
        rows = [_wishlist_row("1", "Only Item", "http://example.com/p/only.html")]
        page = FakePage(
            locators={
                GRID_SELECTOR: FakeLocator(children=rows, count_value=1),
                NEXT_SELECTOR: FakeLocator(count_value=0),
            }
        )

        items = wishlist.get_wishlist_items(page, "http://example.com")

        self.assertEqual(len(items), 1)
        self.assertEqual(len(page.visited), 1)

    def test_set_wishlist_item_quantity_defaults_minimum(self) -> None:
        qty_input = FakeLocator(count_value=1)
        update_btn = FakeLocator(count_value=1)
        page = FakePage(
            locators={
                "form.form-wishlist-items input[name='qty[abc]']": qty_input,
                "form.form-wishlist-items button.action.update": update_btn,
            }
        )
        result = wishlist.set_wishlist_item_quantity(page, "abc", 0)
        self.assertTrue(result)
        self.assertEqual(qty_input.text, "1")
        self.assertTrue(any(action[0] == "click" for action in update_btn.actions))

    def test_remove_and_empty_wishlist(self) -> None:
        remove_link = FakeLocator(count_value=1)
        page = FakePage(
            locators={
                "li#item_5": FakeLocator(
                    count_value=1,
                    nested={
                        "a[data-role='remove'], a.action.delete": remove_link,
                    },
                ),
            }
        )
        self.assertTrue(wishlist.remove_wishlist_item(page, "5"))
        self.assertTrue(any(action[0] == "click" for action in remove_link.actions))

        # Fixed: Mock should return the delete link directly, not the parent item
        def locator_func(selector: str) -> FakeLocator:
            # Match the actual selector used in empty_wishlist()
            # which selects DELETE LINKS, not parent items
            if "a[data-role='remove']" in selector or "a.action.delete" in selector:
                if getattr(page, "cleared", False):
                    return FakeLocator(count_value=0)
                
                # Return the delete link directly (with on_click handler)
                return FakeLocator(
                    count_value=1,
                    on_click=lambda: setattr(page, "cleared", True),
                )
            
            return page.locators.get(selector, FakeLocator(count_value=0))

        page.locator = locator_func  # type: ignore[assignment]
        page.locators = {}
        wishlist.empty_wishlist(page)
        self.assertTrue(getattr(page, "cleared", False))


if __name__ == "__main__":
    unittest.main()