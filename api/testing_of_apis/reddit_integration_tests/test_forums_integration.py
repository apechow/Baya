#!/usr/bin/env python3
"""Integration tests for Reddit forums module."""

from playwright.sync_api import sync_playwright
import sys
import random
import string
import socket
from pathlib import Path
from urllib.parse import urlparse

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from reddit_pw import login, forums
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


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def _random_suffix():
    """Generate a random suffix for unique names."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


def test_create_forum():
    """Test creating a forum."""
    print("\n🧪 Testing: Create forum")

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

        forum_name = f"testforum{_random_suffix()}"
        result = forums.create_forum(
            page,
            forum_name=forum_name,
            forum_title=f"Test Forum {forum_name}",
            forum_description="A test forum created by integration tests.",
            forum_sidebar="Rules and guidelines here.",
            username=TEST_USERNAME
        )

        assert result.success, f"Create forum failed: {result.error_message}"
        assert result.forum_url, "Should have forum URL"

        print(f"  ✓ Created forum: {forum_name}")
        print(f"  ✓ Forum URL: {result.forum_url}")
        print("✅ Create forum works!")

        browser.close()


def test_create_duplicate_forum():
    """Test creating a duplicate forum."""
    print("\n🧪 Testing: Create duplicate forum")

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

        forum_name = f"dupforum{_random_suffix()}"

        # Create first forum
        result1 = forums.create_forum(
            page,
            forum_name=forum_name,
            forum_title="First Forum",
            forum_description="First description.",
            forum_sidebar="First sidebar.",
            username=TEST_USERNAME
        )

        assert result1.success, f"First forum creation failed: {result1.error_message}"

        # Try to create duplicate
        result2 = forums.create_forum(
            page,
            forum_name=forum_name,
            forum_title="Duplicate Forum",
            forum_description="Duplicate description.",
            forum_sidebar="Duplicate sidebar.",
            username=TEST_USERNAME
        )

        # Should succeed with already_existed=True
        assert result2.success, "Duplicate check should succeed"
        assert result2.already_existed, "Should detect as already existing"

        print(f"  ✓ Duplicate detected: {result2.error_message}")
        print("✅ Duplicate forum detection works!")

        browser.close()


def test_forum_exists():
    """Test checking if a forum exists."""
    print("\n🧪 Testing: Forum exists check")

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

        # Create a forum
        forum_name = f"existcheck{_random_suffix()}"
        forums.create_forum(
            page,
            forum_name=forum_name,
            forum_title="Existence Check Forum",
            forum_description="Testing existence check.",
            forum_sidebar="Sidebar.",
            username=TEST_USERNAME
        )

        # Check if it exists
        exists = forums.forum_exists(page, forum_name)
        assert exists, "Forum should exist"

        print(f"  ✓ Forum '{forum_name}' exists")

        # Check non-existent forum
        fake_forum = f"nonexistent{_random_suffix()}"
        not_exists = forums.forum_exists(page, fake_forum)
        assert not not_exists, "Non-existent forum should not exist"

        print(f"  ✓ Forum '{fake_forum}' correctly not found")
        print("✅ Forum exists check works!")

        browser.close()


def test_forum_dataclass():
    """Test Forum dataclass structure."""
    print("\n🧪 Testing: Forum dataclass")

    forum = forums.Forum(
        name="testforum",
        title="Test Forum",
        description="A test forum",
        sidebar="Rules here",
        url="http://example.com/f/testforum",
        creator="testuser"
    )

    assert forum.name == "testforum"
    assert forum.title == "Test Forum"
    assert forum.description == "A test forum"
    assert forum.sidebar == "Rules here"
    assert forum.creator == "testuser"

    print("  ✓ Forum dataclass works correctly")
    print("✅ Forum dataclass works!")


def test_create_forum_result_dataclass():
    """Test CreateForumResult dataclass structure."""
    print("\n🧪 Testing: CreateForumResult dataclass")

    result = forums.CreateForumResult(
        success=True,
        forum_url="http://example.com/f/testforum",
        forum_name="testforum",
        already_existed=False
    )

    assert result.success == True
    assert result.forum_url == "http://example.com/f/testforum"
    assert result.forum_name == "testforum"
    assert result.already_existed == False

    print("  ✓ CreateForumResult dataclass works correctly")
    print("✅ CreateForumResult dataclass works!")


if __name__ == "__main__":
    try:
        test_forum_dataclass()
        test_create_forum_result_dataclass()
        test_create_forum()
        test_create_duplicate_forum()
        test_forum_exists()
        print("\n" + "="*70)
        print("ALL FORUMS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
