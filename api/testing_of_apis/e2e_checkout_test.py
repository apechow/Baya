#!/usr/bin/env python3
"""
End-to-End Checkout Flow Test

Tests the complete user journey from product page to order placement.

IMPORTANT: Run this from the parent directory (PTE/api/), not from shipping_pw/

Usage:
    cd /path/to/PTE/api/
    python shipping_pw/e2e_checkout_test.py
"""

from playwright.sync_api import sync_playwright
from pathlib import Path

# Import normally - works when run from parent directory
from shopping_pw import product, cart, shipping, login

# ============================================================================
# CONFIGURATION
# ============================================================================

MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"
PRODUCT_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082/v8-energy-healthy-energy-drink-steady-energy-from-black-and-green-tea-pomegranate-blueberry-8-ounce-can-pack-of-24.html"

# Test shipping address
TEST_ADDRESS = shipping.Address(
    first_name="Test",
    last_name="User",
    street1="123 Test Street",
    city="San Francisco",
    region="California",
    postcode="94102",
    country_code="US",
    phone="555-1234"
)

# ============================================================================
# TEST FUNCTIONS
# ============================================================================

def test_product_extraction(page):
    """Test 1: Extract product details."""
    print(f"\n{'='*70}")
    print("TEST 1: EXTRACT PRODUCT DETAILS")
    print(f"{'='*70}")
    
    try:
        details = product.extract_product_details(page, PRODUCT_URL)
        
        print(f"✓ Product name: {details.name}")
        print(f"✓ SKU: {details.sku}")
        print(f"✓ Price: ${details.price}")
        print(f"✓ In stock: {details.in_stock}")
        
        assert details.name and details.sku and details.price > 0
        print("\n✅ TEST 1 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ TEST 1 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_add_to_cart(page):
    """Test 2: Add product to cart."""
    print(f"\n{'='*70}")
    print("TEST 2: ADD PRODUCT TO CART")
    print(f"{'='*70}")
    
    try:
        print("Emptying cart...")
        cart.empty_cart(page)
        print("✓ Cart emptied")
        
        print("\nAdding product (quantity: 2)...")
        result = cart.add_product_to_cart(page, PRODUCT_URL, quantity=2)
        
        if not result.success:
            print(f"❌ Add to cart failed: {result.error_message}")
            return False
        
        print(f"✓ Add to cart succeeded")
        if result.cart_count_after:
            print(f"✓ Cart count: {result.cart_count_after}")
        else:
            print(f"ℹ️  Cart count not available (this is OK)")
        
        print("\n✅ TEST 2 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ TEST 2 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_view_cart(page):
    """Test 3: View cart and verify items."""
    print(f"\n{'='*70}")
    print("TEST 3: VIEW CART ITEMS")
    print(f"{'='*70}")
    
    try:
        items = cart.get_cart_items(page)
        print(f"✓ Found {len(items)} items in cart")
        
        for i, item in enumerate(items, 1):
            print(f"\n  Item {i}:")
            print(f"    Name: {item.name}")
            print(f"    SKU: {item.sku}")
            print(f"    Quantity: {item.quantity}")
            print(f"    Price: ${item.price}")
        
        assert len(items) > 0 and items[0].quantity > 0
        print("\n✅ TEST 3 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ TEST 3 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_update_cart_quantity(page):
    """Test 4: Update item quantity in cart."""
    print(f"\n{'='*70}")
    print("TEST 4: UPDATE CART ITEM QUANTITY")
    print(f"{'='*70}")
    
    try:
        items = cart.get_cart_items(page)
        if not items:
            print("⚠️  No items in cart")
            return False
        
        sku = items[0].sku
        new_quantity = 3
        
        print(f"Updating SKU '{sku}' to quantity: {new_quantity}")
        success = cart.set_cart_item_quantity(page, sku, new_quantity)
        
        if not success:
            print("❌ Failed to update quantity")
            return False
        
        print("✓ Quantity updated")
        
        # Wait a moment for page to stabilize
        import time
        time.sleep(2)
        
        # Retry getting cart items if first attempt fails
        for attempt in range(3):
            try:
                items = cart.get_cart_items(page)
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  ⚠️  Retry {attempt + 1}/3...")
                    time.sleep(1)
                else:
                    raise
        
        updated_item = next((i for i in items if i.sku == sku), None)
        if updated_item:
            print(f"✓ Verified new quantity: {updated_item.quantity}")
            assert updated_item.quantity == new_quantity
        
        print("\n✅ TEST 4 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ TEST 4 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_checkout_shipping_guest(page):
    """Test 5: Complete shipping step as guest."""
    print(f"\n{'='*70}")
    print("TEST 5: CHECKOUT - SHIPPING STEP (GUEST)")
    print(f"{'='*70}")
    
    try:
        print(f"Shipping address: {TEST_ADDRESS.first_name} {TEST_ADDRESS.last_name}")
        print(f"  {TEST_ADDRESS.city}, {TEST_ADDRESS.region} {TEST_ADDRESS.postcode}")
        
        result = shipping.complete_shipping_step(
            page,
            address=TEST_ADDRESS,
            email="guest-test@example.com"
        )
        
        if not result.success:
            print(f"❌ Shipping step failed: {result.error_message}")
            return False
        
        print(f"\n✓ Shipping completed")
        print(f"✓ Method: {result.selected_shipping_method_code}")
        print(f"✓ Price: ${result.selected_shipping_price}")
        
        assert result.selected_shipping_method_code
        print("\n✅ TEST 5 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ TEST 5 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_verify_payment_page(page):
    """Test 6: Verify we reached payment page."""
    print(f"\n{'='*70}")
    print("TEST 6: VERIFY PAYMENT PAGE")
    print(f"{'='*70}")
    
    try:
        print(f"Current URL: {page.url}")
        
        payment_selectors = ["li#payment", "#checkout-step-payment", ".checkout-payment-method"]
        found = [s for s in payment_selectors if page.locator(s).count() > 0]
        
        if found:
            print(f"✓ Found payment elements: {', '.join(found)}")
            print("\n✅ TEST 6 PASSED")
            return True
        else:
            print("⚠️  Payment section not found")
            return False
    except Exception as e:
        print(f"\n❌ TEST 6 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def take_screenshot(page, name):
    """Take a screenshot for debugging."""
    try:
        screenshot_dir = Path("e2e_test_screenshots")
        screenshot_dir.mkdir(exist_ok=True)
        path = screenshot_dir / f"{name}.png"
        page.screenshot(path=str(path))
        print(f"📸 Screenshot: {path}")
    except Exception as e:
        print(f"⚠️  Screenshot failed: {e}")


def main():
    """Run all end-to-end tests."""
    print("="*70)
    print("END-TO-END CHECKOUT FLOW TEST")
    print("="*70)
    print(f"Magento: {MAGENTO_URL}")
    print(f"Product: V8 Energy Drink")
    print("="*70)
    
    results = {
        "test_1_product": False,
        "test_2_add_to_cart": False,
        "test_3_view_cart": False,
        "test_4_update_quantity": False,
        "test_5_checkout_shipping": False,
        "test_6_payment_page": False,
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        results["test_1_product"] = test_product_extraction(page)
        if results["test_1_product"]:
            take_screenshot(page, "1_product_details")
        
        if results["test_1_product"]:
            results["test_2_add_to_cart"] = test_add_to_cart(page)
            if results["test_2_add_to_cart"]:
                take_screenshot(page, "2_added_to_cart")
        
        if results["test_2_add_to_cart"]:
            results["test_3_view_cart"] = test_view_cart(page)
            if results["test_3_view_cart"]:
                take_screenshot(page, "3_cart_view")
        
        if results["test_3_view_cart"]:
            results["test_4_update_quantity"] = test_update_cart_quantity(page)
            if results["test_4_update_quantity"]:
                take_screenshot(page, "4_quantity_updated")
        
        if results["test_4_update_quantity"]:
            results["test_5_checkout_shipping"] = test_checkout_shipping_guest(page)
            if results["test_5_checkout_shipping"]:
                take_screenshot(page, "5_shipping_completed")
        
        if results["test_5_checkout_shipping"]:
            results["test_6_payment_page"] = test_verify_payment_page(page)
            if results["test_6_payment_page"]:
                take_screenshot(page, "6_payment_page")
        
        print("\n" + "="*70)
        print("Keeping browser open for 10 seconds...")
        print("="*70)
        
        import time
        time.sleep(10)
        browser.close()
    
    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print("\n" + "="*70)
    print(f"Results: {passed}/{total} passed, {failed}/{total} failed")
    print("="*70)
    
    if failed == 0:
        print("\n🎉 ALL TESTS PASSED!")
        print("Your API is working correctly with this Magento instance!")
    else:
        print("\n⚠️  SOME TESTS FAILED")
        print("Review error messages above")
    
    print("="*70)
    return failed == 0


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)