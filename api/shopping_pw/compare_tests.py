"""Ad-hoc tests for compare helpers."""

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

from api.shopping_pw import compare  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


ERROR_SELECTOR = (
    ".page.messages .message-error, "
    ".page.messages .error.message, "
    "div.messages .message-error, "
    "div.messages .error.message"
)


class CompareTests(unittest.TestCase):
    def test_add_product_to_compare_success(self) -> None:
        page = FakePage(
            locators={
                "a.action.tocompare[data-post]": FakeLocator(count_value=1),
                ERROR_SELECTOR: FakeLocator(count_value=0),
                ".item.link.compare .counter, li.link.compare .counter": FakeLocator(text="3"),
            }
        )
        result = compare.add_product_to_compare(page, "http://example.com/p/sku1")
        self.assertTrue(result.success)
        self.assertEqual(result.compare_count_after, 3)

    def test_add_product_to_compare_missing_button(self) -> None:
        page = FakePage(
            locators={
                "a.action.tocompare[data-post]": FakeLocator(count_value=0),
                ERROR_SELECTOR: FakeLocator(count_value=0),
            }
        )
        result = compare.add_product_to_compare(page, "http://example.com/p/missing")
        self.assertFalse(result.success)
        self.assertIn("not found", result.error_message.lower())

    def test_open_compare_page(self) -> None:
        link = FakeLocator(count_value=1, attributes={"href": "http://example.com/compare"})
        page = FakePage(
            locators={
                "li.item.link.compare a.action.compare": link,
            }
        )
        url = compare.open_compare_page(page)
        self.assertEqual(url, "http://example.com/compare")
        self.assertIn("http://example.com/compare", page.visited)

    def test_compare_products_runs_additions(self) -> None:
        page = FakePage(
            locators={
                "a.action.tocompare[data-post]": FakeLocator(count_value=1),
                ERROR_SELECTOR: FakeLocator(count_value=0),
                ".item.link.compare .counter, li.link.compare .counter": FakeLocator(text="1"),
                "li.item.link.compare a.action.compare": FakeLocator(count_value=1, attributes={"href": "http://example.com/compare"}),
            }
        )
        result = compare.compare_products(page, ["http://example.com/p/1", "http://example.com/p/2"])
        self.assertEqual(len(result.add_results), 2)
        self.assertEqual(result.compare_page_url, "http://example.com/compare")

    def test_extract_compare_page_parses_products_and_attributes(self) -> None:
        product_a = FakeLocator(
            nested={
                "strong.product-item-name a": FakeLocator(text="Item A", attributes={"href": "http://example.com/a"}),
                "img.product-image-photo": FakeLocator(attributes={"src": "http://img/a.jpg"}),
                "span.price": FakeLocator(text="$1.00"),
            }
        )
        product_b = FakeLocator(
            nested={
                "strong.product-item-name a": FakeLocator(text="Item B", attributes={"href": "http://example.com/b"}),
                "img.product-image-photo": FakeLocator(attributes={"src": "http://img/b.jpg"}),
                "span.price": FakeLocator(text="$2.00"),
            }
        )
        product_cells = FakeLocator(children=[product_a, product_b], count_value=2)
        product_body = FakeLocator(nested={"td.cell.product.info": product_cells})

        attr_row = FakeLocator(
            nested={
                "th .attribute.label, th.cell.label .attribute.label": FakeLocator(text="Color"),
                "td.cell.product.attribute .attribute.value": FakeLocator(
                    children=[FakeLocator(text="Red"), FakeLocator(text="Blue")], count_value=2
                ),
            }
        )
        attr_body = FakeLocator(children=[attr_row], count_value=1, nested={"tr": FakeLocator(children=[attr_row], count_value=1)})

        table = FakeLocator(
            count_value=1,
            nested={
                "tbody": FakeLocator(children=[product_body, attr_body], count_value=2),
            },
        )

        page = FakePage(
            locators={
                "table#product-comparison": table,
            },
            url="http://example.com/compare",
        )

        data = compare.extract_compare_page(page)
        self.assertEqual(len(data.products), 2)
        self.assertEqual(data.products[0].name, "Item A")
        self.assertEqual(data.attributes[0].label, "Color")
        self.assertEqual(data.attributes[0].values, ["Red", "Blue"])


if __name__ == "__main__":
    unittest.main()
