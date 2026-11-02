"""Compare-products helpers to batch add items and open the compare page."""

import re
from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Page


@dataclass
class AddToCompareResult:
    """Result of attempting to add a single product to the compare list."""

    success: bool
    product_url: str
    compare_count_after: Optional[int]
    error_message: Optional[str] = None


@dataclass
class CompareRunResult:
    """Outcome of adding multiple products to compare and opening the page."""

    add_results: List[AddToCompareResult]
    compare_page_url: Optional[str]


@dataclass
class ComparedProduct:
    """Details for a single product shown on the comparison grid."""

    name: str
    url: str
    price: Optional[float]
    image_url: Optional[str]


@dataclass
class ComparedAttributeRow:
    """Attribute row values aligned to the compared products."""

    label: str
    values: List[str]


@dataclass
class ComparePageData:
    """Structured representation of the comparison page."""

    page_url: str
    products: List[ComparedProduct]
    attributes: List[ComparedAttributeRow]


def add_product_to_compare(page: Page, product_url: str) -> AddToCompareResult:
    """Navigate to a product page and click the Add to Compare control."""
    page.goto(product_url)

    page.wait_for_load_state("networkidle")
    compare_btn = page.locator("a.action.tocompare[data-post]").first
    if compare_btn.count() == 0:
        return AddToCompareResult(
            success=False,
            product_url=product_url,
            compare_count_after=None,
            error_message="Add to Compare button not found",
        )

    compare_btn.click()

    page.wait_for_load_state("networkidle")
    error_loc = page.locator(
        ".page.messages .message-error, "
        ".page.messages .error.message, "
        "div.messages .message-error, "
        "div.messages .error.message"
    )
    if error_loc.count() > 0:
        text = error_loc.nth(0).inner_text().strip()
        if not text:
            text = "Unknown error adding product to compare"
        return AddToCompareResult(
            success=False,
            product_url=product_url,
            compare_count_after=None,
            error_message=text,
        )

    compare_count_after: Optional[int] = None
    counter = page.locator(".item.link.compare .counter, li.link.compare .counter")
    try:
        if counter.count() > 0:
            txt = counter.first.inner_text().strip()
            match = re.search(r"\d+", txt)
            if match:
                compare_count_after = int(match.group(0))
    except Exception:
        compare_count_after = None

    return AddToCompareResult(
        success=True,
        product_url=product_url,
        compare_count_after=compare_count_after,
        error_message=None,
    )


def open_compare_page(page: Page) -> Optional[str]:
    """Open the compare page using the header navigation link if available."""
    compare_link = page.locator("li.item.link.compare a.action.compare").first
    if compare_link.count() == 0:
        compare_link = page.locator("li.link.compare a.action.compare").first

    if compare_link.count() == 0:
        return None

    href = compare_link.get_attribute("href")

    if href:
        page.goto(href)
    else:
        compare_link.click()

    page.wait_for_load_state("networkidle")
    return href or page.url


def compare_products(
    page: Page,
    product_urls: List[str],
) -> CompareRunResult:
    """
    Add multiple products to compare and then open the compare page from the header.
    """
    add_results: List[AddToCompareResult] = []

    for url in product_urls:
        add_results.append(add_product_to_compare(page, url))

    compare_page_url = open_compare_page(page)

    return CompareRunResult(
        add_results=add_results,
        compare_page_url=compare_page_url,
    )


def extract_compare_page(page: Page) -> ComparePageData:
    """Parse the comparison page into strongly typed product + attribute rows."""
    table = page.locator("table#product-comparison").first

    if table.count() == 0:
        return ComparePageData(page_url=page.url, products=[], attributes=[])

    # First tbody holds the product info columns (names, links, price, image).
    bodies = table.locator("tbody")
    product_body = bodies.nth(0)
    product_cells = product_body.locator("td.cell.product.info")

    products: List[ComparedProduct] = []
    for i in range(product_cells.count()):
        cell = product_cells.nth(i)

        name_loc = cell.locator("strong.product-item-name a").first
        name = name_loc.inner_text().strip() if name_loc.count() > 0 else ""
        url = name_loc.get_attribute("href") if name_loc.count() > 0 else ""
        url = url or ""

        img_loc = cell.locator("img.product-image-photo").first
        image_url = img_loc.get_attribute("src") if img_loc.count() > 0 else None

        price_loc = cell.locator("span.price").first
        if price_loc.count() > 0:
            price_text = price_loc.inner_text()
            try:
                price = float(re.sub(r"[^0-9.]", "", price_text))
            except ValueError:
                price = None
        else:
            price = None

        products.append(
            ComparedProduct(
                name=name,
                url=url,
                price=price,
                image_url=image_url,
            )
        )

    # Remaining tbodies represent attribute rows (SKU, Description, etc.).
    attributes: List[ComparedAttributeRow] = []
    for b in range(1, bodies.count()):
        body = bodies.nth(b)
        rows = body.locator("tr")

        for r in range(rows.count()):
            row = rows.nth(r)
            label_loc = row.locator("th .attribute.label, th.cell.label .attribute.label").first
            if label_loc.count() == 0:
                label_loc = row.locator("th.cell.label span").first

            label = label_loc.inner_text().strip() if label_loc.count() > 0 else ""

            value_cells = row.locator("td.cell.product.attribute .attribute.value")
            values: List[str] = []
            for c in range(value_cells.count()):
                val = value_cells.nth(c).inner_text().strip()
                values.append(val)

            attributes.append(
                ComparedAttributeRow(
                    label=label,
                    values=values,
                )
            )

    return ComparePageData(
        page_url=page.url,
        products=products,
        attributes=attributes,
    )
