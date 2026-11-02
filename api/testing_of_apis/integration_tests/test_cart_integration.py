#!/usr/bin/env python3
"""Integration tests for cart module."""

from playwright.sync_api import sync_playwright
import sys
import time
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import cart

MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"
PRODUCT_URL = f"{MAGENTO_URL}/v8-energy-healthy-energy-drink-steady-energy-from-black-and-green-tea-pomegranate-blueberry-8-ounce-can-pack-of-24.html"


def test_empty_cart():
    """Test emptying the cart."""
    print("\n🧪 Testing: Empty cart")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        cart.empty_cart(page)
        time.sleep(1)  # Give Magento a moment
        items = cart.get_cart_items(page)
        
        assert len(items) == 0, f"Cart should be empty but has {len(items)} items"
        
        print("  ✓ Cart is empty")
        print("✅ Empty cart works!")
        
        browser.close()


def test_add_to_cart():
    """Test adding product to cart."""
    print("\n🧪 Testing: Add to cart")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Empty cart first
        cart.empty_cart(page)
        time.sleep(1)
        
        # Add product
        result = cart.add_product_to_cart(page, PRODUCT_URL, quantity=2)
        
        assert result.success, f"Add to cart failed: {result.error_message}"
        
        print(f"  ✓ Added to cart successfully")
        print(f"  ✓ Requested quantity: {result.requested_quantity}")
        print("✅ Add to cart works!")
        
        browser.close()


def test_get_cart_items():
    """Test getting cart items."""
    print("\n🧪 Testing: Get cart items")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Start fresh
        cart.empty_cart(page)
        time.sleep(1)
        
        # Add a product
        cart.add_product_to_cart(page, PRODUCT_URL, quantity=2)
        time.sleep(2)  # Wait for Magento to process
        
        # Get items with retry
        items = None
        for attempt in range(3):
            items = cart.get_cart_items(page)
            if items and len(items) > 0:
                break
            print(f"  ⚠️  Retry {attempt + 1}/3 - cart appears empty")
            time.sleep(2)
        
        assert items and len(items) > 0, f"Should have items in cart, got {len(items) if items else 0}"
        assert items[0].quantity == 2, f"Should have quantity 2 but has {items[0].quantity}"
        assert items[0].sku == "B00CPTR7WS", f"Wrong SKU: {items[0].sku}"
        
        print(f"  ✓ Found {len(items)} items")
        print(f"  ✓ Item: {items[0].name}")
        print(f"  ✓ Quantity: {items[0].quantity}")
        print(f"  ✓ Price: ${items[0].price}")
        print("✅ Get cart items works!")
        
        browser.close()


def test_update_quantity():
    """Test updating item quantity in cart."""
    print("\n🧪 Testing: Update quantity")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Add a product
        cart.empty_cart(page)
        time.sleep(1)
        cart.add_product_to_cart(page, PRODUCT_URL, quantity=2)
        time.sleep(2)
        
        # Get SKU
        items = cart.get_cart_items(page)
        if not items or len(items) == 0:
            print("  ⚠️  Cart is empty, retrying...")
            time.sleep(2)
            items = cart.get_cart_items(page)
        
        assert items and len(items) > 0, "Cart should have items"
        sku = items[0].sku
        
        # Update quantity
        success = cart.set_cart_item_quantity(page, sku, 5)
        assert success, "Update quantity failed"
        
        time.sleep(3)  # Wait for update
        
        # Verify with retry
        for attempt in range(3):
            items = cart.get_cart_items(page)
            if items and len(items) > 0 and items[0].quantity == 5:
                break
            time.sleep(1)
        
        assert items and len(items) > 0, "Cart should have items after update"
        assert items[0].quantity == 5, f"Quantity should be 5 but is {items[0].quantity}"
        
        print(f"  ✓ Updated quantity from 2 to 5")
        print("✅ Update quantity works!")
        
        browser.close()


def test_remove_item():
    """Test removing item from cart."""
    print("\n🧪 Testing: Remove item")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Add a product
        cart.empty_cart(page)
        time.sleep(1)
        cart.add_product_to_cart(page, PRODUCT_URL, quantity=1)
        time.sleep(2)
        
        # Get SKU
        items = cart.get_cart_items(page)
        if not items:
            time.sleep(2)
            items = cart.get_cart_items(page)
        
        assert items and len(items) > 0, "Should have items to remove"
        sku = items[0].sku
        
        # Remove
        success = cart.remove_cart_item(page, sku)
        assert success, "Remove item failed"
        
        time.sleep(1)
        
        # Verify
        items = cart.get_cart_items(page)
        assert len(items) == 0, "Cart should be empty after removal"
        
        print("  ✓ Removed item successfully")
        print("✅ Remove item works!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_empty_cart()
        test_add_to_cart()
        test_get_cart_items()
        test_update_quantity()
        test_remove_item()
        print("\n" + "="*70)
        print("ALL CART TESTS PASSED! ✅")
        print("="*70)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)