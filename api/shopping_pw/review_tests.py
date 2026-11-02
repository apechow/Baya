"""Ad-hoc tests for review helpers."""

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

from api.shopping_pw import review  # noqa:E402
from api.shopping_pw.test_utils import FakeLocator, FakePage  # noqa:E402


ERROR_SELECTOR = (
    ".page.messages .message-error, "
    ".page.messages .error.message, "
    "div.messages .message-error, "
    "div.messages .error.message"
)


class ReviewTests(unittest.TestCase):
    def test_leave_product_review_success_clamps_rating(self) -> None:
        rating_inputs = FakeLocator(children=[FakeLocator() for _ in range(5)], count_value=5)
        page = FakePage(
            locators={
                "a#tab-label-reviews-title": FakeLocator(count_value=1),
                ".review-control-vote input.radio": rating_inputs,
                "input#nickname_field": FakeLocator(count_value=1),
                "input#summary_field": FakeLocator(count_value=1),
                "textarea#review_field": FakeLocator(count_value=1),
                "form#review-form button.action.submit": FakeLocator(count_value=1),
                ERROR_SELECTOR: FakeLocator(count_value=0),
                ".page.messages .message-success, div.messages .message-success": FakeLocator(count_value=1),
            }
        )

        result = review.leave_product_review(
            page,
            product_url="http://example.com/p",
            rating=7,
            nickname="nick",
            title="title",
            detail="detail",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.rating, 5)

    def test_leave_product_review_error_path(self) -> None:
        rating_inputs = FakeLocator(children=[FakeLocator()], count_value=1)
        page = FakePage(
            locators={
                ".review-control-vote input.radio": rating_inputs,
                "input#nickname_field": FakeLocator(count_value=1),
                "input#summary_field": FakeLocator(count_value=1),
                "textarea#review_field": FakeLocator(count_value=1),
                "form#review-form button.action.submit": FakeLocator(count_value=1),
                ERROR_SELECTOR: FakeLocator(count_value=1, text="Bad review"),
            }
        )
        result = review.leave_product_review(
            page,
            product_url="http://example.com/p",
            rating=0,
            nickname="nick",
            title="title",
            detail="detail",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.rating, 1)
        self.assertIn("Bad review", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
