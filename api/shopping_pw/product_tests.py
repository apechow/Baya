"""Ad-hoc tests for product detail helpers."""

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

from api.shopping_pw import product  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class ProductTests(unittest.TestCase):
    def test_norm_collapses_whitespace(self) -> None:
        self.assertEqual(product._norm("  Hello   World  "), "hello world")

    def test_parse_product_options_reads_radio_choices(self) -> None:
        option_id = "opt-1"
        radio = FakeLocator(attributes={"name": "options[10]", "value": "123", "id": option_id, "price": "2"})
        choices = FakeLocator(children=[radio], count_value=1)
        field = FakeLocator(
            text="Color",
            attributes={"class": "field required"},
            nested={
                'input[name^="options["], select[name^="options["], textarea[name^="options["]': FakeLocator(count_value=1),
                'input[type="radio"][name^="options["], input[type="checkbox"][name^="options["]': choices,
                f'label[for="{option_id}"] span': FakeLocator(text="Red"),
                "label span": FakeLocator(text="Color"),
            },
        )
        fields = FakeLocator(children=[field], count_value=1)
        page = FakePage(locators={"#product-options-wrapper .fieldset > .field": fields})

        options = product._parse_product_options(page)
        self.assertEqual(len(options), 1)
        opt = options[0]
        self.assertTrue(opt.required)
        self.assertEqual(opt.choices[0].label, "Red")
        self.assertEqual(opt.choices[0].price_delta, 2.0)

    def test_extract_product_details(self) -> None:
        page = FakePage(
            locators={
                "h1.page-title span.base": FakeLocator(text="Item"),
                "span.price-container span.price": FakeLocator(text="$5.00"),
                ".product.attribute.sku .value": FakeLocator(text="SKU-1"),
                ".product-info-stock-sku .stock": FakeLocator(text="In stock"),
                "#description .product.attribute.description .value": FakeLocator(html="<p>desc</p>"),
            }
        )

        original = product._parse_product_options
        product._parse_product_options = lambda _page: []  # type: ignore[assignment]
        try:
            details = product.extract_product_details(page, "http://example.com/p")
        finally:
            product._parse_product_options = original

        self.assertEqual(details.name, "Item")
        self.assertEqual(details.sku, "SKU-1")
        self.assertTrue(details.in_stock)
        self.assertEqual(details.price, 5.0)
        self.assertEqual(details.description_html, "<p>desc</p>")


if __name__ == "__main__":
    unittest.main()
