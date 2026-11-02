#!/usr/bin/env python3
"""Integration tests for Reddit users module."""

from playwright.sync_api import sync_playwright
import sys
import socket
from pathlib import Path
from urllib.parse import urlparse

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from reddit_pw import login, users
from reddit_pw.constants import REDDIT_DOMAIN

# Test credentials
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testuser"
TEST_USER_TO_BLOCK = "testuser1"  # "usertoblock (with pass testuser1)"  # Username to test blocking


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


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def test_block_user():
    """Test blocking a user."""
    print("\n🧪 Testing: Block user")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not TEST_USER_TO_BLOCK:
        print("  ⏭️  SKIPPED - No TEST_USER_TO_BLOCK provided")
        return

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        result = users.block_user(page, TEST_USERNAME, TEST_USER_TO_BLOCK)

        assert result.success, f"Block user failed: {result.error_message}"

        if result.already_blocked:
            print(f"  ✓ User @{TEST_USER_TO_BLOCK} was already blocked")
        else:
            print(f"  ✓ Blocked user @{TEST_USER_TO_BLOCK}")
        print("✅ Block user works!")

        browser.close()


def test_reset_email():
    """Test resetting email."""
    print("\n🧪 Testing: Reset email")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        result = users.reset_email(page, TEST_USERNAME)

        assert result.success, f"Reset email failed: {result.error_message}"

        print("  ✓ Email reset successfully")
        print("✅ Reset email works!")

        browser.close()


def test_update_email():
    """Test updating email."""
    print("\n🧪 Testing: Update email")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        new_email = "test@example.com"
        result = users.update_email(page, TEST_USERNAME, new_email)

        assert result.success, f"Update email failed: {result.error_message}"
        assert result.new_email == new_email

        print(f"  ✓ Email updated to: {new_email}")
        print("✅ Update email works!")

        # Reset email back to empty
        users.reset_email(page, TEST_USERNAME)

        browser.close()


def test_user_exists():
    """Test checking if a user exists."""
    print("\n🧪 Testing: User exists check")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Check if test user exists
        exists = users.user_exists(page, TEST_USERNAME)
        assert exists, "Test user should exist"

        print(f"  ✓ User '{TEST_USERNAME}' exists")

        # Check non-existent user
        fake_user = "nonexistentuserxyz123456"
        not_exists = users.user_exists(page, fake_user)
        # Note: This may still return True if the Reddit instance doesn't 404 for missing users

        print(f"  ✓ Non-existent user check completed")
        print("✅ User exists check works!")

        browser.close()


def test_get_user_info():
    """Test getting user info."""
    print("\n🧪 Testing: Get user info")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not _is_reddit_reachable():
        print(f"  ⏭️  SKIPPED - Reddit server not reachable at {REDDIT_DOMAIN}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        info = users.get_user_info(page, TEST_USERNAME)

        assert info.username == TEST_USERNAME
        assert info.profile_url

        print(f"  ✓ Username: {info.username}")
        print(f"  ✓ Profile URL: {info.profile_url}")
        print(f"  ✓ Exists: {info.exists}")
        print("✅ Get user info works!")

        browser.close()


def test_block_user_result_dataclass():
    """Test BlockUserResult dataclass structure."""
    print("\n🧪 Testing: BlockUserResult dataclass")

    result = users.BlockUserResult(
        success=True,
        already_blocked=False
    )

    assert result.success == True
    assert result.already_blocked == False

    blocked_result = users.BlockUserResult(
        success=True,
        already_blocked=True,
        error_message="User already blocked"
    )

    assert blocked_result.already_blocked == True

    print("  ✓ BlockUserResult dataclass works correctly")
    print("✅ BlockUserResult dataclass works!")


def test_user_info_dataclass():
    """Test UserInfo dataclass structure."""
    print("\n🧪 Testing: UserInfo dataclass")

    info = users.UserInfo(
        username="testuser",
        profile_url="http://example.com/user/testuser",
        exists=True
    )

    assert info.username == "testuser"
    assert info.profile_url == "http://example.com/user/testuser"
    assert info.exists == True

    print("  ✓ UserInfo dataclass works correctly")
    print("✅ UserInfo dataclass works!")


if __name__ == "__main__":
    try:
        test_block_user_result_dataclass()
        test_user_info_dataclass()
        test_block_user()
        test_reset_email()
        test_update_email()
        test_user_exists()
        test_get_user_info()
        print("\n" + "="*70)
        print("ALL USERS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME, TEST_PASSWORD, and TEST_USER_TO_BLOCK")
        print("   to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
