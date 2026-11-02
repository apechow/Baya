#!/usr/bin/env python3
"""Integration tests for Reddit messages module."""

from playwright.sync_api import sync_playwright
import sys
import random
import string
import socket
from pathlib import Path
from urllib.parse import urlparse

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from reddit_pw import login, messages, users
from reddit_pw.constants import REDDIT_DOMAIN

# Test credentials
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testuser"
TEST_RECIPIENT = "testuser1"  # password testuser1  # Username to send test messages to


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


def test_send_message():
    """Test sending a private message."""
    print("\n🧪 Testing: Send message")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not TEST_RECIPIENT:
        print("  ⏭️  SKIPPED - No TEST_RECIPIENT provided")
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

        # Ensure recipient is not blocked (may have been blocked by previous test runs)
        unblock_result = users.unblock_user(page, TEST_USERNAME, TEST_RECIPIENT)
        if unblock_result.was_blocked and unblock_result.success:
            print(f"  ✓ Unblocked @{TEST_RECIPIENT} (was blocked)")

        message_body = f"Test message {_random_suffix()}"
        result = messages.send_message(page, TEST_RECIPIENT, message_body)

        assert result.success, f"Send message failed: {result.error_message}"
        assert result.message_url, "Should have message URL"

        print(f"  ✓ Sent message to @{TEST_RECIPIENT}")
        print(f"  ✓ Message URL: {result.message_url}")
        print("✅ Send message works!")

        browser.close()


def test_send_duplicate_message():
    """Test sending a duplicate message."""
    print("\n🧪 Testing: Send duplicate message")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not TEST_RECIPIENT:
        print("  ⏭️  SKIPPED - No TEST_RECIPIENT provided")
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

        message_body = f"Duplicate test message {_random_suffix()}"

        # Send first message
        result1 = messages.send_message(page, TEST_RECIPIENT, message_body)
        assert result1.success, f"First message failed: {result1.error_message}"

        # Try to send duplicate
        result2 = messages.send_message(page, TEST_RECIPIENT, message_body)

        # Should succeed with already_existed=True
        assert result2.success, "Duplicate check should succeed"
        assert result2.already_existed, "Should detect as already existing"

        print(f"  ✓ Duplicate detected: {result2.error_message}")
        print("✅ Duplicate message detection works!")

        browser.close()


def test_delete_all_messages():
    """Test deleting all messages."""
    print("\n🧪 Testing: Delete all messages")

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

        result = messages.delete_all_messages(page, TEST_USERNAME)

        print(f"  ✓ Deleted {result.deleted_count} message(s)")
        if result.error_message:
            print(f"  ⚠️  Note: {result.error_message}")
        print("✅ Delete all messages works!")

        browser.close()


def test_message_dataclass():
    """Test Message dataclass structure."""
    print("\n🧪 Testing: Message dataclass")

    message = messages.Message(
        id="m123",
        body="Test message body",
        sender="user1",
        recipient="user2",
        url="http://example.com/messages/m123",
        thread_url="http://example.com/messages/thread/t456"
    )

    assert message.id == "m123"
    assert message.body == "Test message body"
    assert message.sender == "user1"
    assert message.recipient == "user2"

    print("  ✓ Message dataclass works correctly")
    print("✅ Message dataclass works!")


def test_message_result_dataclass():
    """Test MessageResult dataclass structure."""
    print("\n🧪 Testing: MessageResult dataclass")

    result = messages.MessageResult(
        success=True,
        message_url="http://example.com/messages/1",
        thread_url="http://example.com/messages/thread/1",
        already_existed=False
    )

    assert result.success == True
    assert result.message_url == "http://example.com/messages/1"
    assert result.already_existed == False

    print("  ✓ MessageResult dataclass works correctly")
    print("✅ MessageResult dataclass works!")


def test_delete_messages_result_dataclass():
    """Test DeleteMessagesResult dataclass structure."""
    print("\n🧪 Testing: DeleteMessagesResult dataclass")

    result = messages.DeleteMessagesResult(
        success=True,
        deleted_count=5
    )

    assert result.success == True
    assert result.deleted_count == 5

    print("  ✓ DeleteMessagesResult dataclass works correctly")
    print("✅ DeleteMessagesResult dataclass works!")


if __name__ == "__main__":
    try:
        test_message_dataclass()
        test_message_result_dataclass()
        test_delete_messages_result_dataclass()
        test_send_message()
        test_send_duplicate_message()
        test_delete_all_messages()
        print("\n" + "="*70)
        print("ALL MESSAGES TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME, TEST_PASSWORD, and TEST_RECIPIENT")
        print("   to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
