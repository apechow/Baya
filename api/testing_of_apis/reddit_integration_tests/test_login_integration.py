#!/usr/bin/env python3
"""Integration tests for Reddit login module."""

from playwright.sync_api import sync_playwright
import sys
import socket
from pathlib import Path
from urllib.parse import urlparse

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from reddit_pw import login
from reddit_pw.constants import REDDIT_DOMAIN

# Test credentials
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testuser"


def _is_reddit_reachable():
    """Check if the Reddit server is reachable."""
    try:
        parsed = urlparse(REDDIT_DOMAIN)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def test_login():
    """Test user login."""
    print("\n🧪 Testing: User login")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        print("  💡 To test login, set TEST_USERNAME and TEST_PASSWORD")
        return

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
        assert result.success, f"Login failed: {result.error_message}"

        print("  ✓ Logged in successfully")
        if result.redirect_url:
            print(f"  ✓ Redirected to: {result.redirect_url}")
        print("✅ Login works!")

        browser.close()


def test_login_with_invalid_credentials():
    """Test login with invalid credentials."""
    print("\n🧪 Testing: Login with invalid credentials")

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Try to login with invalid credentials
        result = login.login_user(page, "invalid_user_xyz123", "wrongpassword456")

        assert not result.success, "Login should fail with invalid credentials"
        assert result.error_message, "Should have an error message"

        print(f"  ✓ Login correctly failed: {result.error_message}")
        print("✅ Invalid credentials handling works!")

        browser.close()


def test_is_logged_in_not_logged_in():
    """Test is_logged_in returns False when not logged in."""
    print("\n🧪 Testing: is_logged_in when not logged in")

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Go to login page
        page.goto(f"{REDDIT_DOMAIN}/login", wait_until="networkidle")

        logged_in = login.is_logged_in(page)
        assert not logged_in, "Should not be logged in"

        print("  ✓ Correctly detected not logged in")
        print("✅ is_logged_in detection works!")

        browser.close()


def test_login_result_dataclass():
    """Test LoginResult dataclass structure."""
    print("\n🧪 Testing: LoginResult dataclass")

    # Can run without credentials - just testing dataclass
    result = login.LoginResult(
        success=True,
        redirect_url="http://localhost/",
        error_message=None
    )

    assert result.success == True
    assert result.redirect_url == "http://localhost/"
    assert result.error_message is None

    fail_result = login.LoginResult(
        success=False,
        redirect_url=None,
        error_message="Invalid credentials"
    )

    assert fail_result.success == False
    assert fail_result.redirect_url is None
    assert fail_result.error_message == "Invalid credentials"

    print("  ✓ LoginResult dataclass works correctly")
    print("✅ LoginResult dataclass works!")


def test_user_creation_result_dataclass():
    """Test UserCreationResult dataclass structure."""
    print("\n🧪 Testing: UserCreationResult dataclass")

    result = login.UserCreationResult(
        success=True,
        username="newuser",
        already_existed=False,
        error_message=None
    )

    assert result.success == True
    assert result.username == "newuser"
    assert result.already_existed == False

    existing_result = login.UserCreationResult(
        success=True,
        username="existinguser",
        already_existed=True,
        error_message="User already exists"
    )

    assert existing_result.already_existed == True

    print("  ✓ UserCreationResult dataclass works correctly")
    print("✅ UserCreationResult dataclass works!")


if __name__ == "__main__":
    try:
        test_login_result_dataclass()
        test_user_creation_result_dataclass()
        test_login_with_invalid_credentials()
        test_is_logged_in_not_logged_in()
        test_login()
        print("\n" + "="*70)
        print("ALL LOGIN TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   or unreachable Reddit server")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
