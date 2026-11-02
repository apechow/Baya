#!/usr/bin/env python3
"""Integration tests for order module."""

from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import order, login

# TODO: Add real test credentials
TEST_EMAIL = None  # "test@example.com"
TEST_PASSWORD = None  # "password123"


def test_order_history():
    """Test getting order history."""
    print("\n🧪 Testing: Order history")
    
    if not TEST_EMAIL or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - Requires login credentials")
        print("  💡 Set TEST_EMAIL and TEST_PASSWORD to enable order tests")
        return
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        login.login(page, TEST_EMAIL, TEST_PASSWORD)
        
        # Get orders
        orders = order.get_order_history(page)
        
        print(f"  ✓ Found {len(orders)} orders")
        if orders:
            print(f"  ✓ Most recent order: {orders[0].order_number}")
            print(f"  ✓ Order date: {orders[0].date}")
            print(f"  ✓ Order total: ${orders[0].total}")
        print("✅ Get order history works!")
        
        browser.close()


def test_order_details():
    """Test getting order details."""
    print("\n🧪 Testing: Order details")
    
    if not TEST_EMAIL or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - Requires login credentials")
        return
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        login.login(page, TEST_EMAIL, TEST_PASSWORD)
        
        # Get orders
        orders = order.get_order_history(page)
        
        if not orders:
            print("  ⏭️  SKIPPED - No orders found to test details")
            return
        
        # Get details of first order
        order_details = order.get_order_details(page, orders[0].order_number)
        
        assert order_details is not None, "Should get order details"
        assert order_details.order_number == orders[0].order_number
        
        print(f"  ✓ Got details for order: {order_details.order_number}")
        print(f"  ✓ Order has {len(order_details.items)} items")
        print("✅ Get order details works!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_order_history()
        test_order_details()
        print("\n" + "="*70)
        print("ALL ORDER TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Tests were skipped due to missing credentials")
        print("   Set TEST_EMAIL and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
