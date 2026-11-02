#!/usr/bin/env python3
"""Integration tests for login module."""

from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from shopping_pw import login

MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"

# TODO: Add real test credentials
TEST_EMAIL = None  # "test@example.com"
TEST_PASSWORD = None  # "password123"


def test_login():
    """Test user login."""
    print("\n🧪 Testing: User login")
    
    if not TEST_EMAIL or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        print("  💡 To test login, set TEST_EMAIL and TEST_PASSWORD")
        return
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Use the actual function name: login_customer
        result = login.login_customer(page, TEST_EMAIL, TEST_PASSWORD)
        assert result.success, f"Login failed: {result.error_message}"
        
        print("  ✓ Logged in successfully")
        if result.redirect_url:
            print(f"  ✓ Redirected to: {result.redirect_url}")
        print("✅ Login works!")
        
        browser.close()


def test_login_with_invalid_credentials():
    """Test login with invalid credentials."""
    print("\n🧪 Testing: Login with invalid credentials")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Try to login with invalid credentials
        result = login.login_customer(page, "invalid@example.com", "wrongpassword")
        
        assert not result.success, "Login should fail with invalid credentials"
        assert result.error_message, "Should have an error message"
        
        print(f"  ✓ Login correctly failed: {result.error_message}")
        print("✅ Invalid credentials handling works!")
        
        browser.close()


if __name__ == "__main__":
    try:
        test_login_with_invalid_credentials()
        test_login()
        print("\n" + "="*70)
        print("ALL LOGIN TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_EMAIL and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)