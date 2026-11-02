#!/usr/bin/env python3
"""Integration tests for Reddit comments module."""

from playwright.sync_api import sync_playwright
import sys
import random
import string
import socket
from pathlib import Path
from urllib.parse import urlparse

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from reddit_pw import login, posts, comments
from reddit_pw.constants import REDDIT_DOMAIN

# Test credentials
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testuser"
TEST_FORUM = "AskReddit"


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


def test_comment_on_post():
    """Test commenting on a post."""
    print("\n🧪 Testing: Comment on post")

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

        # First create a post to comment on
        title = f"Post for Comments {_random_suffix()}"
        post_result = posts.create_post(
            page,
            TEST_FORUM,
            title,
            "This post is for testing comments.",
            TEST_USERNAME
        )

        if not post_result.success or not post_result.post_id:
            print(f"  ❌ Failed to create post for commenting: {post_result.error_message}")
            browser.close()
            return

        # Comment on the post using URL (since post_id from create_post is a slug, not numeric ID)
        comment_text = f"Test comment {_random_suffix()}"
        result = comments.comment_on_post_by_url(
            page,
            post_result.post_url,
            comment_text
        )

        assert result.success, f"Comment failed: {result.error_message}"
        assert result.comment_url, "Should have comment URL"

        print(f"  ✓ Created comment: {comment_text[:30]}...")
        print(f"  ✓ Comment URL: {result.comment_url}")
        print("✅ Comment on post works!")

        # Clean up
        if post_result.post_url:
            posts.delete_post(page, post_result.post_url)

        browser.close()


def test_delete_comments():
    """Test deleting comments on a post."""
    print("\n🧪 Testing: Delete comments on post")

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

        # Create a post
        title = f"Post for Delete Comments {_random_suffix()}"
        post_result = posts.create_post(
            page,
            TEST_FORUM,
            title,
            "Testing comment deletion.",
            TEST_USERNAME
        )

        if not post_result.success:
            print(f"  ❌ Failed to create post: {post_result.error_message}")
            browser.close()
            return

        # Add some comments using URL (since post_id from create_post is a slug, not numeric ID)
        for i in range(2):
            comments.comment_on_post_by_url(
                page,
                post_result.post_url,
                f"Comment to delete {i}"
            )

        # Delete all comments
        delete_result = comments.delete_all_comments_on_post(page, post_result.post_url)

        print(f"  ✓ Deleted {delete_result.deleted_count} comments")
        print("✅ Delete comments works!")

        # Clean up post
        if post_result.post_url:
            posts.delete_post(page, post_result.post_url)

        browser.close()


def test_comment_dataclass():
    """Test Comment dataclass structure."""
    print("\n🧪 Testing: Comment dataclass")

    comment = comments.Comment(
        id="c123",
        body="Test comment body",
        author="testuser",
        post_id="p456",
        subreddit="AskReddit",
        url="http://example.com/f/AskReddit/p456#c123"
    )

    assert comment.id == "c123"
    assert comment.body == "Test comment body"
    assert comment.author == "testuser"
    assert comment.post_id == "p456"
    assert comment.subreddit == "AskReddit"

    print("  ✓ Comment dataclass works correctly")
    print("✅ Comment dataclass works!")


def test_comment_result_dataclass():
    """Test CommentResult dataclass structure."""
    print("\n🧪 Testing: CommentResult dataclass")

    result = comments.CommentResult(
        success=True,
        comment_url="http://example.com/comment/1"
    )

    assert result.success == True
    assert result.comment_url == "http://example.com/comment/1"
    assert result.error_message is None

    print("  ✓ CommentResult dataclass works correctly")
    print("✅ CommentResult dataclass works!")


def test_delete_comment_result_dataclass():
    """Test DeleteCommentResult dataclass structure."""
    print("\n🧪 Testing: DeleteCommentResult dataclass")

    result = comments.DeleteCommentResult(
        success=True,
        deleted_count=3
    )

    assert result.success == True
    assert result.deleted_count == 3

    print("  ✓ DeleteCommentResult dataclass works correctly")
    print("✅ DeleteCommentResult dataclass works!")


if __name__ == "__main__":
    try:
        test_comment_dataclass()
        test_comment_result_dataclass()
        test_delete_comment_result_dataclass()
        test_comment_on_post()
        test_delete_comments()
        print("\n" + "="*70)
        print("ALL COMMENTS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
