#!/usr/bin/env python3
"""Integration tests for compare module."""

from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import compare

MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"
PRODUCT_URL = f"{MAGENTO_URL}/v8-energy-healthy-energy-drink-steady-energy-from-black-and-green-tea-pomegranate-blueberry-8-ounce-can-pack-of-24.html"


def test_add_to_compare():
    """Test adding product to compare list."""
    print("\n🧪 Testing: Add to compare")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Add to compare
        result = compare.add_product_to_compare(page, PRODUCT_URL)
        assert result.success, f"Add to compare failed: {result.error_message}"
        
        print("  ✓ Added to compare successfully")
        if result.compare_count_after:
            print(f"  ✓ Compare count: {result.compare_count_after}")
        print("✅ Add to compare works!")
        
        browser.close()


def test_compare_multiple_products():
    """Test comparing multiple products."""
    print("\n🧪 Testing: Compare multiple products")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Use the compare_products function
        result = compare.compare_products(page, [PRODUCT_URL])
        
        assert len(result.add_results) == 1, "Should have 1 add result"
        assert result.add_results[0].success, "Should successfully add product"
        
        print(f"  ✓ Added {len(result.add_results)} products")
        if result.compare_page_url:
            print(f"  ✓ Opened compare page: {result.compare_page_url}")
        print("✅ Compare multiple products works!")
        
        browser.close()


def test_extract_compare_page():
    """Test extracting data from compare page."""
    print("\n🧪 Testing: Extract compare page")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Add product and open compare page
        compare.add_product_to_compare(page, PRODUCT_URL)
        compare.open_compare_page(page)
        
        # Extract data
        data = compare.extract_compare_page(page)
        
        print(f"  ✓ Found {len(data.products)} products on compare page")
        print(f"  ✓ Found {len(data.attributes)} attribute rows")
        print("✅ Extract compare page works!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_add_to_compare()
        test_compare_multiple_products()
        test_extract_compare_page()
        print("\n" + "="*70)
        print("ALL COMPARE TESTS PASSED! ✅")
        print("="*70)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)