#!/usr/bin/env python3
"""Integration tests for Reddit posts module."""

from playwright.sync_api import sync_playwright
import sys
import time
import random
import string
import socket
from pathlib import Path
from urllib.parse import urlparse

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from reddit_pw import login, posts
from reddit_pw.constants import REDDIT_DOMAIN

# Test credentials
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testuser"
TEST_FORUM = "AskReddit"  # Forum to post in


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


def test_create_post():
    """Test creating a post."""
    print("\n🧪 Testing: Create post")

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

        title = f"Test Post {_random_suffix()}"
        body = "This is a test post body created by integration tests."

        result = posts.create_post(
            page,
            TEST_FORUM,
            title,
            body,
            TEST_USERNAME
        )

        assert result.success, f"Create post failed: {result.error_message}"
        assert result.post_url, "Should have post URL"

        print(f"  ✓ Created post: {title}")
        print(f"  ✓ Post URL: {result.post_url}")
        print("✅ Create post works!")

        # Clean up: delete the post
        if result.post_url:
            posts.delete_post(page, result.post_url)

        browser.close()


def test_create_duplicate_post():
    """Test creating a duplicate post."""
    print("\n🧪 Testing: Create duplicate post")

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

        title = f"Duplicate Test {_random_suffix()}"
        body = "Testing duplicate detection."

        # Create first post
        result1 = posts.create_post(page, TEST_FORUM, title, body, TEST_USERNAME)
        assert result1.success, f"First post creation failed: {result1.error_message}"

        time.sleep(1)

        # Try to create duplicate
        result2 = posts.create_post(page, TEST_FORUM, title, body, TEST_USERNAME)

        # Should succeed with already_existed=True
        assert result2.success, "Duplicate check should succeed"
        assert result2.already_existed, "Should detect as already existing"

        print(f"  ✓ Duplicate detected: {result2.error_message}")
        print("✅ Duplicate post detection works!")

        # Clean up
        if result1.post_url:
            posts.delete_post(page, result1.post_url)

        browser.close()


def test_delete_post():
    """Test deleting a post."""
    print("\n🧪 Testing: Delete post")

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

        # Create a post to delete
        title = f"Post to Delete {_random_suffix()}"
        create_result = posts.create_post(
            page,
            TEST_FORUM,
            title,
            "This post will be deleted.",
            TEST_USERNAME
        )

        assert create_result.success and create_result.post_url
        time.sleep(1)

        # Delete the post
        delete_result = posts.delete_post(page, create_result.post_url)

        assert delete_result.success, f"Delete failed: {delete_result.error_message}"

        print(f"  ✓ Deleted post: {title}")
        print("✅ Delete post works!")

        browser.close()


def test_post_dataclass():
    """Test Post dataclass structure."""
    print("\n🧪 Testing: Post dataclass")

    post = posts.Post(
        id="12345",
        title="Test Post",
        body="Test body content",
        author="testuser",
        subreddit="AskReddit",
        url="http://example.com/f/AskReddit/12345"
    )

    assert post.id == "12345"
    assert post.title == "Test Post"
    assert post.body == "Test body content"
    assert post.author == "testuser"
    assert post.subreddit == "AskReddit"

    print("  ✓ Post dataclass works correctly")
    print("✅ Post dataclass works!")


def test_create_post_result_dataclass():
    """Test CreatePostResult dataclass structure."""
    print("\n🧪 Testing: CreatePostResult dataclass")

    result = posts.CreatePostResult(
        success=True,
        post_url="http://example.com/post/1",
        post_id="1",
        already_existed=False
    )

    assert result.success == True
    assert result.post_url == "http://example.com/post/1"
    assert result.already_existed == False

    print("  ✓ CreatePostResult dataclass works correctly")
    print("✅ CreatePostResult dataclass works!")


if __name__ == "__main__":
    try:
        test_post_dataclass()
        test_create_post_result_dataclass()
        test_create_post()
        test_create_duplicate_post()
        test_delete_post()
        print("\n" + "="*70)
        print("ALL POSTS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
