"""Search helpers for Magento product listings."""

from dataclasses import dataclass
import re
from typing import List, Optional

from playwright.sync_api import Page

from .constants import advanced_search_url, base_url as _base_url, search_terms_url


def _norm(s: str) -> str:
    """Normalize text for loose matching."""
    return re.sub(r"\s+", " ", s or "").strip().lower()


@dataclass
class ProductSummary:
    """Summary of a product as shown on the search results page."""

    product_id: Optional[str]
    sku: Optional[str]
    name: str
    price: float
    url: str
    image: Optional[str]
    image_alt: Optional[str]
    rating_percent: Optional[int]
    review_count: int
    add_to_cart_url: Optional[str]
    position_page: int  # 1-based page index in search results
    position_index: int  # 0-based index on that page


@dataclass
class AdvancedSearchQuery:
    """Search filters available on Magento's Advanced Search page."""

    name: Optional[str] = None
    sku: Optional[str] = None
    description: Optional[str] = None
    short_description: Optional[str] = None
    price_from: Optional[float] = None
    price_to: Optional[float] = None




@dataclass
class SearchTerm:
    """A popular search term as shown on Magento's Search Terms page."""

    term_id: Optional[str]
    term: str
    result_url: str
    font_size_percent: Optional[float] = None


def search_and_extract_products(
    page: Page, query: str, base_url: Optional[str] = None
) -> List[ProductSummary]:
    """
    Perform a search via the header search box and return all products across paginated results.
    """
    page.goto(_base_url(base_url))
    page.wait_for_load_state("networkidle")
    page.fill("#search", query)
    page.press("#search", "Enter")
    page.wait_for_load_state("networkidle")
    return _collect_paginated_results(page)


def advanced_search_and_extract_products(
    page: Page, query: AdvancedSearchQuery, base_url: Optional[str] = None
) -> List[ProductSummary]:
    """
    Use Magento's Advanced Search form to search by multiple fields and return all results.
    """
    page.goto(advanced_search_url(base_url))

    page.wait_for_load_state("networkidle")
    def _fill_if_present(selector: str, value: Optional[object]) -> None:
        if value is None:
            return
        field = page.locator(selector)
        if field.count() > 0:
            field.fill(str(value))

    _fill_if_present("input#name", query.name)
    _fill_if_present("input#sku", query.sku)
    _fill_if_present("input#description", query.description)
    _fill_if_present("input#short_description", query.short_description)
    _fill_if_present("input[name='price[from]']", query.price_from)
    _fill_if_present("input[name='price[to]']", query.price_to)

    submit_btn = page.locator("form#form-validate button[type='submit']").first
    if submit_btn.count() == 0:
        submit_btn = page.locator("form.search.advanced button[type='submit']").first

    submit_btn.click()

    page.wait_for_load_state("networkidle")
    return _collect_paginated_results(page)


def get_popular_search_terms(
    page: Page, base_url: Optional[str] = None
) -> List[SearchTerm]:
    """Return the list of popular search terms shown on /search/term/popular/."""
    page.goto(search_terms_url(base_url))

    page.wait_for_load_state("networkidle")
    terms: List[SearchTerm] = []
    items = page.locator("ul.search-terms li")

    for i in range(items.count()):
        item = items.nth(i)
        item_id_attr = item.get_attribute("id") or ""
        match = re.search(r"term-(\d+)", item_id_attr)
        term_id = match.group(1) if match else None

        link = item.locator("a").first
        if link.count() == 0:
            continue

        term_text = link.inner_text().strip()
        href = link.get_attribute("href") or ""

        # Inline font-size is a loose proxy for popularity weight.
        style_attr = item.get_attribute("style") or ""
        font_size_percent: Optional[float] = None
        style_match = re.search(r"font-size:\s*([\d.]+)%", style_attr)
        if style_match:
            try:
                font_size_percent = float(style_match.group(1))
            except ValueError:
                font_size_percent = None

        terms.append(
            SearchTerm(
                term_id=term_id,
                term=term_text,
                result_url=href,
                font_size_percent=font_size_percent,
            )
        )

    return terms


def navigate_category_and_extract_products(
    page: Page, category_name: str, base_url: Optional[str] = None
) -> List[ProductSummary]:
    """
    Navigate via the site menu to a given category/subcategory and return all products across pages.
    """
    page.goto(_base_url(base_url))

    page.wait_for_load_state("networkidle")
    target = _norm(category_name)
    nav_links = page.locator("nav.navigation a")
    match_href: Optional[str] = None

    # Prefer exact match, then fall back to substring contains.
    for i in range(nav_links.count()):
        txt = _norm(nav_links.nth(i).inner_text())
        if not txt:
            continue
        if txt == target:
            href = nav_links.nth(i).get_attribute("href")
            if href:
                match_href = href
                break

    if match_href is None:
        for i in range(nav_links.count()):
            txt = _norm(nav_links.nth(i).inner_text())
            if not txt or target not in txt:
                continue
            href = nav_links.nth(i).get_attribute("href")
            if href:
                match_href = href
                break

    if match_href is None:
        raise ValueError(
            f"Category/subcategory '{category_name}' not found in navigation."
        )

    results: List[ProductSummary] = []
    page_num = 1

    page.goto(match_href)

    page.wait_for_load_state("networkidle")
    while True:
        results.extend(_extract_products_from_listing(page, page_num))

        next_button = page.locator("li.pages-item-next a")
        if next_button.count() == 0:
            break

        next_href = next_button.get_attribute("href")
        if not next_href:
            break

        page_num += 1
        page.goto(next_href)

        page.wait_for_load_state("networkidle")
    return results


def _collect_paginated_results(page: Page) -> List[ProductSummary]:
    """Collect search results across paginated listing pages."""
    results: List[ProductSummary] = []
    page_num = 1

    while True:
        results.extend(_extract_products_from_listing(page, page_num))

        next_button = page.locator("li.pages-item-next a")

        if next_button.count() == 0:
            break

        next_href = next_button.get_attribute("href")
        if not next_href:
            break

        page_num += 1
        page.goto(next_href)

        page.wait_for_load_state("networkidle")
    return results


def _extract_products_from_listing(
    page: Page, page_num: int
) -> List[ProductSummary]:
    """Scrape all products from the current listing page."""
    product_items = page.locator("ol.products.list.items.product-items > li")
    count = product_items.count()
    page_results: List[ProductSummary] = []

    for i in range(count):
        item = product_items.nth(i)

        name_loc = item.locator("strong.product.name.product-item-name a")
        name = name_loc.inner_text().strip()
        url = name_loc.get_attribute("href") or ""

        price_box = item.locator("div.price-box[data-product-id]").first
        product_id = (
            price_box.get_attribute("data-product-id")
            if price_box.count() > 0
            else None
        )

        price_text = item.locator("span.price").first.inner_text()
        price = float(re.sub(r"[^0-9.]", "", price_text))

        img_loc = item.locator("img.product-image-photo").first
        if img_loc.count() > 0:
            image = img_loc.get_attribute("src")
            image_alt = img_loc.get_attribute("alt")
        else:
            image = None
            image_alt = None

        form_loc = item.locator("form[data-role='tocart-form']").first
        if form_loc.count() > 0:
            sku = form_loc.get_attribute("data-product-sku")
            add_to_cart_url = form_loc.get_attribute("action")
        else:
            sku = None
            add_to_cart_url = None

        rating_percent: Optional[int] = None
        rating_loc = item.locator("div.product-reviews-summary .rating-result").first
        if rating_loc.count() > 0:
            title_attr = rating_loc.get_attribute("title") or ""
            match = re.search(r"(\d+)", title_attr)
            if match:
                rating_percent = int(match.group(1))

        review_count = 0
        reviews_loc = item.locator("div.reviews-actions a.action.view").first
        if reviews_loc.count() > 0:
            reviews_text = reviews_loc.inner_text()
            match = re.search(r"(\d+)", reviews_text)
            if match:
                review_count = int(match.group(1))

        page_results.append(
            ProductSummary(
                product_id=product_id,
                sku=sku,
                name=name,
                price=price,
                url=url,
                image=image,
                image_alt=image_alt,
                rating_percent=rating_percent,
                review_count=review_count,
                add_to_cart_url=add_to_cart_url,
                position_page=page_num,
                position_index=i,
            )
        )

    return page_results
