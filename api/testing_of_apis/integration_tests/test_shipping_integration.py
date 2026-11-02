#!/usr/bin/env python3
"""Integration tests for shipping module."""

from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import shipping, cart

MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"
PRODUCT_URL = f"{MAGENTO_URL}/v8-energy-healthy-energy-drink-steady-energy-from-black-and-green-tea-pomegranate-blueberry-8-ounce-can-pack-of-24.html"

TEST_ADDRESS = shipping.Address(
    first_name="Test",
    last_name="Integration",
    street1="456 Integration St",
    city="Los Angeles",
    region="California",
    postcode="90001",
    country_code="US",
    phone="555-5678"
)


def test_shipping_step():
    """Test completing shipping step as guest."""
    print("\n🧪 Testing: Shipping step (guest checkout)")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # CRITICAL: Must have items in cart before checkout!
        print("  ℹ️  Adding product to cart first...")
        cart.empty_cart(page)
        import time
        time.sleep(1)
        cart.add_product_to_cart(page, PRODUCT_URL, quantity=1)
        time.sleep(2)  # Wait for cart to update
        
        # Now test shipping
        result = shipping.complete_shipping_step(
            page,
            address=TEST_ADDRESS,
            email="integration-test@example.com"
        )
        
        assert result.success, f"Shipping step failed: {result.error_message}"
        assert result.selected_shipping_method_code, "Should have selected shipping method"
        
        print(f"  ✓ Shipping completed successfully")
        print(f"  ✓ Shipping method: {result.selected_shipping_method_code}")
        print(f"  ✓ Shipping price: ${result.selected_shipping_price}")
        print("✅ Shipping step works!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_shipping_step()
        print("\n" + "="*70)
        print("ALL SHIPPING TESTS PASSED! ✅")
        print("="*70)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)