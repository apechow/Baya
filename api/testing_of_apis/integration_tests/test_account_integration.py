#!/usr/bin/env python3
"""Integration tests for account and address_book modules."""

from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import account, address_book, login

# TODO: Add real test credentials
TEST_EMAIL = None  # "test@example.com"
TEST_PASSWORD = None  # "password123"


def test_account_info():
    """Test getting account information."""
    print("\n🧪 Testing: Get account info")
    
    if not TEST_EMAIL or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - Requires login credentials")
        print("  💡 Set TEST_EMAIL and TEST_PASSWORD to enable account tests")
        return
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        login.login(page, TEST_EMAIL, TEST_PASSWORD)
        
        # Get account info
        info = account.get_account_info(page)
        assert info.email == TEST_EMAIL, f"Expected email {TEST_EMAIL}"
        
        print(f"  ✓ Account email: {info.email}")
        print(f"  ✓ Account name: {info.first_name} {info.last_name}")
        print("✅ Get account info works!")
        
        browser.close()


def test_address_book():
    """Test address book operations."""
    print("\n🧪 Testing: Address book")
    
    if not TEST_EMAIL or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - Requires login credentials")
        return
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        login.login(page, TEST_EMAIL, TEST_PASSWORD)
        
        # Get addresses
        addresses = address_book.get_addresses(page)
        
        print(f"  ✓ Found {len(addresses)} addresses")
        if addresses:
            print(f"  ✓ First address: {addresses[0].street1}, {addresses[0].city}")
        print("✅ Address book works!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_account_info()
        test_address_book()
        print("\n" + "="*70)
        print("ALL ACCOUNT TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Tests were skipped due to missing credentials")
        print("   Set TEST_EMAIL and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
