"""Ad-hoc tests for address book helpers."""

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

from api.shopping_pw import address_book, shipping  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class AddressBookTests(unittest.TestCase):
    def _basic_address(self) -> shipping.Address:
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

    def _form_locators(self) -> dict:
        return {
            "input#firstname": FakeLocator(count_value=1),
            "input#lastname": FakeLocator(count_value=1),
            "input#telephone": FakeLocator(count_value=1),
            "input#company": FakeLocator(count_value=1),
            "input#street_1": FakeLocator(count_value=1),
            "input#street_2": FakeLocator(count_value=1),
            "select#country": FakeLocator(count_value=1),
            "select#region_id": FakeLocator(count_value=1),
            "input#city": FakeLocator(count_value=1),
            "input#zip": FakeLocator(count_value=1),
            "input#primary_billing": FakeLocator(count_value=1),
            "input#primary_shipping": FakeLocator(count_value=1),
            "form#form-validate button.action.save": FakeLocator(count_value=1),
            ".page.messages .message-error, div.messages .message-error": FakeLocator(count_value=0),
            ".page.messages .message-success, div.messages .message-success": FakeLocator(count_value=1, text="Saved"),
        }

    def test_pick_additional_address(self) -> None:
        row1 = FakeLocator(text="123 Rd Town CA", nested={"a.action.edit": FakeLocator(attributes={"href": "http://edit/1"})})
        row2 = FakeLocator(text="Another St City", nested={"a.action.edit": FakeLocator(attributes={"href": "http://edit/2"})})
        rows = FakeLocator(children=[row1, row2], count_value=2)
        page = FakePage(locators={"#additional-addresses-table tbody tr": rows})
        href = address_book._pick_additional_address_edit_link(page, "Town")
        self.assertEqual(href, "http://edit/1")

    def test_edit_address_uses_default_billing(self) -> None:
        page = FakePage(
            locators={
                ".box-address-billing a.action.edit": FakeLocator(count_value=1, attributes={"href": "http://edit/billing"}),
                **self._form_locators(),
            }
        )
        result = address_book.edit_address(page, "default_billing", self._basic_address())
        self.assertTrue(result.success)
        self.assertIn("Saved", result.message or "")
        self.assertIn("http://edit/billing", page.visited)

    def test_add_address_flow(self) -> None:
        locators = self._form_locators()
        locators.update(
            {
                "button[role='add-address'], button.action.add": FakeLocator(count_value=1),
            }
        )
        page = FakePage(locators=locators)
        result = address_book.add_address(page, self._basic_address(), set_default_billing=True, set_default_shipping=True)
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Saved")


if __name__ == "__main__":
    unittest.main()
