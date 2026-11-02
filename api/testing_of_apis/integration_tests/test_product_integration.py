#!/usr/bin/env python3
"""Integration tests for product module."""

from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import product

MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"
PRODUCT_URL = f"{MAGENTO_URL}/v8-energy-healthy-energy-drink-steady-energy-from-black-and-green-tea-pomegranate-blueberry-8-ounce-can-pack-of-24.html"


def test_product_extraction():
    """Test extracting product details from real Magento."""
    print("\n🧪 Testing: Product extraction")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        details = product.extract_product_details(page, PRODUCT_URL)
        
        assert details.name, "Product should have a name"
        assert details.sku == "B00CPTR7WS", f"Expected SKU B00CPTR7WS, got {details.sku}"
        assert details.price > 0, "Product should have a positive price"
        assert details.in_stock, "Product should be in stock"
        
        print(f"  ✓ Product name: {details.name}")
        print(f"  ✓ SKU: {details.sku}")
        print(f"  ✓ Price: ${details.price}")
        print(f"  ✓ In stock: {details.in_stock}")
        print("✅ Product extraction works!")
        
        browser.close()


def test_product_options():
    """Test extracting product options."""
    print("\n🧪 Testing: Product options")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        details = product.extract_product_details(page, PRODUCT_URL)
        
        # Simple product shouldn't have required options
        print(f"  ✓ Product has {len(details.options)} options")
        print(f"  ✓ Requires options: {details.requires_options}")
        print("✅ Product options work!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_product_extraction()
        test_product_options()
        print("\n" + "="*70)
        print("ALL PRODUCT TESTS PASSED! ✅")
        print("="*70)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)