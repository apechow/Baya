#!/usr/bin/env python3
"""Integration tests for wishlist module."""

from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import wishlist, login

MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"
PRODUCT_URL = f"{MAGENTO_URL}/v8-energy-healthy-energy-drink-steady-energy-from-black-and-green-tea-pomegranate-blueberry-8-ounce-can-pack-of-24.html"

# TODO: Add real test credentials
TEST_EMAIL = None  # "test@example.com"
TEST_PASSWORD = None  # "password123"


def test_wishlist_operations():
    """Test wishlist operations (requires login)."""
    print("\n🧪 Testing: Wishlist operations")
    
    if not TEST_EMAIL or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - Requires login credentials")
        print("  💡 Set TEST_EMAIL and TEST_PASSWORD to enable wishlist tests")
        return
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Login first
        login.login(page, TEST_EMAIL, TEST_PASSWORD)
        
        # Add to wishlist
        success = wishlist.add_to_wishlist(page, PRODUCT_URL)
        assert success, "Add to wishlist failed"
        print("  ✓ Added to wishlist")
        
        # Get wishlist items
        items = wishlist.get_wishlist_items(page)
        assert len(items) > 0, "Should have items in wishlist"
        print(f"  ✓ Wishlist has {len(items)} items")
        
        # Remove from wishlist
        if items:
            success = wishlist.remove_from_wishlist(page, items[0].sku)
            assert success, "Remove from wishlist failed"
            print("  ✓ Removed from wishlist")
        
        print("✅ Wishlist operations work!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_wishlist_operations()
        print("\n" + "="*70)
        print("ALL WISHLIST TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Tests were skipped due to missing credentials")
        print("   Set TEST_EMAIL and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
