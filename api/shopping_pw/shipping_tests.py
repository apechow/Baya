"""Ad-hoc tests for shipping + checkout helpers."""

from __future__ import annotations

import sys
from pathlib import Path
import types
import unittest

from bs4 import BeautifulSoup

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

from api.shopping_pw import shipping  # noqa:E402
from api.shopping_pw.test_utils import FakeClient, FakeLocator, FakePage  # noqa:E402


class ShippingTests(unittest.TestCase):
    def _address(self) -> shipping.Address:
        return shipping.Address(
            first_name="Ada",
            last_name="Lovelace",
            street1="123 Rd",
            city="Town",
            region="CA",
            postcode="90210",
            country_code="US",
            phone="555-5555",
        )

    def test_parse_price_and_address_inline(self) -> None:
        self.assertEqual(shipping._parse_price("$10.50"), 10.5)
        inline = shipping._address_inline_string(self._address())
        self.assertIn("lovelace", inline)

    def test_find_existing_shipping_address_index(self) -> None:
        addr = self._address()
        items = FakeLocator(
            children=[
                FakeLocator(text="Random address"),
                FakeLocator(text="Ada Lovelace 123 Rd Town CA 90210 US"),
            ],
            count_value=2,
        )
        page = FakePage(locators={".shipping-address-items .shipping-address-item": items})
        idx = shipping._find_existing_shipping_address_index(page, addr)
        self.assertEqual(idx, 1)

    def test_select_shipping_method_picks_cheapest(self) -> None:
        row1 = FakeLocator(
            nested={
                "input[type=radio]": FakeLocator(count_value=1, attributes={"value": "slow"}),
                ".col-price .price": FakeLocator(text="$5.00"),
            }
        )
        row2 = FakeLocator(
            nested={
                "input[type=radio]": FakeLocator(count_value=1, attributes={"value": "fast"}),
                ".col-price .price": FakeLocator(text="$10.00"),
            }
        )
        rows = FakeLocator(children=[row1, row2], count_value=2)
        page = FakePage(locators={"table.table-checkout-shipping-method tbody tr.row": rows})
        code, price = shipping._select_shipping_method(page, desired_code=None)
        self.assertEqual(code, "slow")
        self.assertEqual(price, 5.0)

    def test_extract_inline_addresses(self) -> None:
        html = """
        <div class="shipping-address-item">Ship To</div>
        <div class="billing-address-details">Bill To</div>
        """
        soup = BeautifulSoup(html, "html.parser")
        found = shipping._extract_inline_addresses_from_page(soup)
        self.assertEqual(len(found), 2)

    def test_complete_shipping_step_existing_address(self) -> None:
        rows = FakeLocator(
            children=[
                FakeLocator(
                    nested={
                        "input[type=radio]": FakeLocator(count_value=1, attributes={"value": "ground"}),
                        ".col-price .price": FakeLocator(text="$1.00"),
                    }
                )
            ],
            count_value=1,
        )
        page = FakePage(
            locators={
                "li#shipping": FakeLocator(count_value=1),
                ".shipping-address-items .shipping-address-item": FakeLocator(count_value=1),
                "form#co-shipping-form": FakeLocator(count_value=0),
                "table.table-checkout-shipping-method tbody tr.row": rows,
                "form#co-shipping-method-form": FakeLocator(
                    nested={
                        "button[data-role='opc-continue'], button.button.action.continue.primary": FakeLocator(count_value=1),
                    }
                ),
                "li#payment": FakeLocator(count_value=1),
            }
        )

        result = shipping.complete_shipping_step(page, address=None)
        self.assertTrue(result.success)
        self.assertTrue(result.used_existing_address)
        self.assertEqual(result.selected_shipping_method_code, "ground")

    def test_fill_inline_shipping_form(self) -> None:
        form = FakeLocator(
            nested={
                "input[name=firstname]": FakeLocator(count_value=1),
                "input[name=lastname]": FakeLocator(count_value=1),
                "input[name=company]": FakeLocator(count_value=1),
                "input[name='street[0]']": FakeLocator(count_value=1),
                "input[name='street[1]']": FakeLocator(count_value=1),
                "input[name=city]": FakeLocator(count_value=1),
                "select[name=country_id]": FakeLocator(count_value=1),
                "select[name=region_id]": FakeLocator(count_value=1),
                "input[name=postcode]": FakeLocator(count_value=1),
                "input[name=telephone]": FakeLocator(count_value=1),
            }
        )
        page = FakePage(
            locators={
                "form[data-role='email-with-possible-login'] input#customer-email": FakeLocator(count_value=1),
                "form#co-shipping-form": form,
            }
        )
        shipping._fill_inline_shipping_form(page, self._address(), email="guest@example.com")
        self.assertEqual(form.nested["input[name=firstname]"].text, "Ada")

    def test_create_new_shipping_address_popup(self) -> None:
        form = FakeLocator(
            nested={
                "input[name=firstname]": FakeLocator(count_value=1),
                "input[name=lastname]": FakeLocator(count_value=1),
                "input[name=company]": FakeLocator(count_value=1),
                "input[name='street[0]']": FakeLocator(count_value=1),
                "input[name='street[1]']": FakeLocator(count_value=1),
                "input[name=city]": FakeLocator(count_value=1),
                "select[name=country_id]": FakeLocator(count_value=1),
                "select[name=region_id]": FakeLocator(count_value=1),
                "input[name=postcode]": FakeLocator(count_value=1),
                "input[name=telephone]": FakeLocator(count_value=1),
                "input#shipping-save-in-address-book": FakeLocator(count_value=1),
            }
        )
        modal = FakeLocator(
            nested={
                "form#co-shipping-form": form,
            },
            count_value=1,
        )
        page = FakePage(
            locators={
                ".new-address-popup button.action-show-popup": FakeLocator(count_value=1),
                "aside.new-shipping-address-modal": modal,
            }
        )
        shipping._create_new_shipping_address_popup(page, self._address())
        self.assertEqual(form.nested["input[name=firstname]"].text, "Ada")

    def test_find_best_matching_existing_address(self) -> None:
        html = """
        <div class="shipping-address-item">Ada Lovelace 123 Rd Town CA 90210 US</div>
        <div class="billing-address-details">Other Person 1 Main</div>
        """
        soup = BeautifulSoup(html, "html.parser")
        match = shipping._find_best_matching_existing_address(soup, self._address())
        self.assertIsNotNone(match)

    def test_is_inline_billing_mode_and_fill_inputs(self) -> None:
        html = """
        <form class="billing-address-form">
            <input name="firstname"/>
            <input name="lastname"/>
            <input name="street[0]"/>
            <input name="city"/>
            <input name="postcode"/>
            <input name="telephone"/>
            <input name="country_id"/>
            <input name="region"/>
        </form>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertTrue(shipping._is_inline_billing_mode(soup))
        data = shipping._fill_billing_form_inputs(soup, self._address())
        self.assertEqual(data["firstname"], "Ada")
        self.assertEqual(data["region"], "CA")

    def test_complete_billing_and_place_order_inline_flow(self) -> None:
        payment_html = """
        <form id="co-payment-form">
            <div class="billing-address-form">
                <input name="firstname"/>
                <input name="lastname"/>
                <input name="street[0]"/>
                <input name="city"/>
                <input name="postcode"/>
                <input name="telephone"/>
                <input name="country_id"/>
                <input name="region"/>
            </div>
            <input type="checkbox" name="billing-address-same-as-shipping" />
            <input id="discount-code" name="discount_code" />
            <button class="action-apply"></button>
            <button class="action primary checkout"></button>
        </form>
        """
        success_html = """
        <div class="checkout-success">
            <p>Order number: <span>000099</span></p>
        </div>
        """
        payment_page = FakePage()
        payment_page.soup = BeautifulSoup(payment_html, "html.parser")
        success_page = FakePage()
        success_page.soup = BeautifulSoup(success_html, "html.parser")

        client = FakeClient([payment_page, success_page])
        result = shipping.complete_billing_and_place_order(
            client,
            billing_address=self._address(),
            discount_code="SALE10",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.order_number, "000099")
        self.assertTrue(result.applied_discount)
        self.assertTrue(client.submissions)


if __name__ == "__main__":
    unittest.main()
