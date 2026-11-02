"""Product detail scraping helpers."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.sync_api import Page


def _norm(s: str) -> str:
    """Simple normalization for text matching."""
    return re.sub(r"\s+", " ", s or "").strip().lower()


@dataclass
class ProductOptionChoice:
    # Human-facing label shown on the page, e.g. "Brown", "8.5"
    label: str
    # Raw form name used in POST, e.g. "options[23290]"
    input_name: str
    # Raw form value used in POST, e.g. "149401"
    input_value: str
    # Optional price delta (Magento’s "price" attribute)
    price_delta: Optional[float] = None


@dataclass
class ProductOption:
    # Option name shown on the page, e.g. "Color", "Size"
    name: str
    # Choices available for this option
    choices: List[ProductOptionChoice] = field(default_factory=list)
    # Whether the option is required
    required: bool = False


@dataclass
class ProductDetails:
    url: str
    name: str
    sku: Optional[str]
    price: Optional[float]
    in_stock: bool
    description_html: Optional[str] = None

    # New fields:
    options: List[ProductOption] = field(default_factory=list)

    @property
    def requires_options(self) -> bool:
        """True if this product has any required options (e.g., Color/Size)."""
        return any(opt.required for opt in self.options)


def _parse_product_options(page: Page) -> List[ProductOption]:
    """Extract Magento custom options (e.g., Color, Size) from the product page."""
    options: List[ProductOption] = []

    # Each option is rendered as a `.field` inside the product-options-wrapper fieldset
    fields = page.locator("#product-options-wrapper .fieldset > .field")
    field_count = fields.count()

    for i in range(field_count):
        field_el = fields.nth(i)

        # Only keep fields that actually contain a Magento custom option input
        if (
            field_el.locator(
                'input[name^="options["], select[name^="options["], textarea[name^="options["]'
            ).count()
            == 0
        ):
            continue

        # Option name, e.g. "Color", "Size"
        label_loc = field_el.locator("label span")
        if label_loc.count() > 0:
            option_name = label_loc.inner_text().strip()
        else:
            option_name = ""

        # Required flag comes from the "required" class on the field
        classes = field_el.get_attribute("class") or ""
        required = "required" in classes.split()

        choices: List[ProductOptionChoice] = []

        # Radio/checkbox style options
        choice_inputs = field_el.locator(
            'input[type="radio"][name^="options["], input[type="checkbox"][name^="options["]'
        )
        for j in range(choice_inputs.count()):
            inp = choice_inputs.nth(j)
            input_name = inp.get_attribute("name") or ""
            input_value = inp.get_attribute("value") or ""
            price_attr = inp.get_attribute("price")

            # Associated label (e.g., "Black", "8.5") via the "for" attribute
            label_text = ""
            input_id = inp.get_attribute("id")
            if input_id:
                label_span = field_el.locator(f'label[for="{input_id}"] span')
                if label_span.count() > 0:
                    label_text = label_span.inner_text().strip()

            price_delta: Optional[float]
            if price_attr and price_attr != "0":
                try:
                    price_delta = float(price_attr)
                except ValueError:
                    price_delta = None
            else:
                price_delta = None

            if input_name and input_value:
                choices.append(
                    ProductOptionChoice(
                        label=label_text,
                        input_name=input_name,
                        input_value=input_value,
                        price_delta=price_delta,
                    )
                )

        # Select-style options (if any)
        select_locs = field_el.locator('select[name^="options["]')
        for s in range(select_locs.count()):
            sel = select_locs.nth(s)
            input_name = sel.get_attribute("name") or ""
            option_locs = sel.locator("option")

            for k in range(option_locs.count()):
                opt_el = option_locs.nth(k)
                value = opt_el.get_attribute("value") or ""
                if not value:
                    # Skip placeholder / "Please select" entries
                    continue

                label_text = opt_el.inner_text().strip()
                price_attr = opt_el.get_attribute("price")

                price_delta: Optional[float]
                if price_attr and price_attr != "0":
                    try:
                        price_delta = float(price_attr)
                    except ValueError:
                        price_delta = None
                else:
                    price_delta = None

                choices.append(
                    ProductOptionChoice(
                        label=label_text,
                        input_name=input_name,
                        input_value=value,
                        price_delta=price_delta,
                    )
                )

        if choices:
            options.append(
                ProductOption(
                    name=option_name,
                    choices=choices,
                    required=required,
                )
            )

    return options


def extract_product_details(page: Page, url: str) -> ProductDetails:
    """Navigate to a Magento product page and extract detailed product information."""
    page.goto(url)

    page.wait_for_load_state("networkidle")
    # Name / title
    name_loc = page.locator("h1.page-title span.base")
    if name_loc.count() > 0:
        name = name_loc.inner_text().strip()
    else:
        name = ""

    # Price
    price_loc = page.locator("span.price-container span.price")
    if price_loc.count() > 0:
        price_text = price_loc.inner_text()
        try:
            price_val: Optional[float] = float(
                re.sub(r"[^0-9.]", "", price_text)
            )
        except ValueError:
            price_val = None
    else:
        price_val = None

    # SKU
    sku_loc = page.locator(".product.attribute.sku .value")
    sku = sku_loc.inner_text().strip() if sku_loc.count() > 0 else None

    # Stock status → bool
    stock_loc = page.locator(".product-info-stock-sku .stock")
    if stock_loc.count() > 0:
        txt = stock_loc.inner_text().lower()
        in_stock = "in stock" in txt
    else:
        # If Magento doesn't show a stock block, assume not in stock by default
        in_stock = False

    # Description HTML
    desc_loc = page.locator(
        "#description .product.attribute.description .value"
    )
    description_html = desc_loc.inner_html() if desc_loc.count() > 0 else None

    # Custom options (Color, Size, etc.)
    options = _parse_product_options(page)

    return ProductDetails(
        url=url,
        name=name,
        sku=sku,
        price=price_val,
        in_stock=in_stock,
        description_html=description_html,
        options=options,
    )