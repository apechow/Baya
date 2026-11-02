"""Ad-hoc tests for search helpers."""

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

from api.shopping_pw import search  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class SearchTests(unittest.TestCase):
    def test_norm_simplifies_text(self) -> None:
        self.assertEqual(search._norm("  Foo   Bar  "), "foo bar")

    def test_search_and_advanced_search_delegate_to_collector(self) -> None:
        collected = [{"name": "placeholder"}]

        original = search._collect_paginated_results
        search._collect_paginated_results = lambda _page: collected  # type: ignore[assignment]
        try:
            # Pre-populate search box for simple search
            page = FakePage(locators={"#search": FakeLocator(count_value=1)})
            results = search.search_and_extract_products(page, "query")
            self.assertEqual(results, collected)
            self.assertEqual(page.locators["#search"].text, "query")

            # Pre-populate form fields for advanced search
            adv_page = FakePage(
                locators={
                    "input#name": FakeLocator(count_value=1),
                    "input#sku": FakeLocator(count_value=1),
                    "input[name='price[from]']": FakeLocator(count_value=1),
                    "input[name='price[to]']": FakeLocator(count_value=1),
                    "form#form-validate button[type='submit']": FakeLocator(count_value=1),
                }
            )
            query = search.AdvancedSearchQuery(name="Hat", sku="123", price_from=1, price_to=5)
            adv_results = search.advanced_search_and_extract_products(adv_page, query)
            self.assertEqual(adv_results, collected)
            self.assertEqual(adv_page.locators["input#name"].text, "Hat")
            self.assertEqual(adv_page.locators["input#sku"].text, "123")
            self.assertEqual(adv_page.locators["input[name='price[from]']"].text, "1")
            self.assertEqual(adv_page.locators["input[name='price[to]']"].text, "5")
        finally:
            search._collect_paginated_results = original  # type: ignore[assignment]

    def test_get_popular_search_terms(self) -> None:
        item = FakeLocator(
            attributes={"id": "term-9", "style": "font-size: 120%"},
            nested={
                "a": FakeLocator(text="Bags", attributes={"href": "http://example.com/search?q=bags"}),
            },
        )
        items = FakeLocator(children=[item], count_value=1)
        page = FakePage(locators={"ul.search-terms li": items})
        terms = search.get_popular_search_terms(page)
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].term_id, "9")
        self.assertEqual(terms[0].term, "Bags")
        self.assertAlmostEqual(terms[0].font_size_percent, 120.0)

    def test_navigate_category_walks_pages(self) -> None:
        calls: list[int] = []

        def fake_extract(_page: FakePage, page_num: int):
            calls.append(page_num)
            return [
                search.ProductSummary(
                    product_id=str(page_num),
                    sku=None,
                    name=f"Item {page_num}",
                    price=1.0,
                    url="http://example.com",
                    image=None,
                    image_alt=None,
                    rating_percent=None,
                    review_count=0,
                    add_to_cart_url=None,
                    position_page=page_num,
                    position_index=0,
                )
            ]

        original = search._extract_products_from_listing
        search._extract_products_from_listing = fake_extract  # type: ignore[assignment]

        class NavPage(FakePage):
            def __init__(self) -> None:
                super().__init__()
                self.page_hits = 0

            def locator(self, selector: str) -> FakeLocator:
                if selector == "nav.navigation a":
                    return FakeLocator(
                        children=[FakeLocator(text="Category", attributes={"href": "http://example.com/cat"})],
                        count_value=1,
                    )
                if selector == "li.pages-item-next a":
                    # Show a next button only on the first pass
                    if self.page_hits == 0:
                        self.page_hits += 1
                        return FakeLocator(count_value=1, attributes={"href": "http://example.com/cat?p=2"})
                    return FakeLocator(count_value=0)
                return super().locator(selector)

        try:
            page = NavPage()
            results = search.navigate_category_and_extract_products(page, "Category")
        finally:
            search._extract_products_from_listing = original  # type: ignore[assignment]

        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(results), 2)

    def test_extract_products_from_listing(self) -> None:
        item = FakeLocator(
            nested={
                "strong.product.name.product-item-name a": FakeLocator(text="Item", attributes={"href": "http://example.com/p"}),
                "div.price-box[data-product-id]": FakeLocator(count_value=1, attributes={"data-product-id": "123"}),
                "span.price": FakeLocator(text="$5.00"),
                "img.product-image-photo": FakeLocator(attributes={"src": "http://img/p.jpg", "alt": "Item img"}),
                "form[data-role='tocart-form']": FakeLocator(count_value=1, attributes={"data-product-sku": "SKU", "action": "http://add"}),
                "div.product-reviews-summary .rating-result": FakeLocator(count_value=1, attributes={"title": "80%"}),
                "div.reviews-actions a.action.view": FakeLocator(count_value=1, text="3 Reviews"),
            }
        )
        products = FakeLocator(children=[item], count_value=1)
        page = FakePage(locators={"ol.products.list.items.product-items > li": products})

        parsed = search._extract_products_from_listing(page, 1)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].product_id, "123")
        self.assertEqual(parsed[0].rating_percent, 80)
        self.assertEqual(parsed[0].review_count, 3)
        self.assertEqual(parsed[0].position_page, 1)


if __name__ == "__main__":
    unittest.main()