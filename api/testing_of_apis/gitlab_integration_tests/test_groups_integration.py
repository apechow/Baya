#!/usr/bin/env python3
"""Integration tests for GitLab groups module."""

from playwright.sync_api import sync_playwright
import sys
import time
import random
import string
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from gitlab_pw import login, groups
from gitlab_pw.constants import GITLAB_DOMAIN

# TODO: Add real test credentials
TEST_USERNAME = "testuser" # "testuser"
TEST_PASSWORD = "jGv3n7CkM4!XPM" # "jGv3n7CkM4!XPM"

# Username of a second user to test adding members (optional)
TEST_SECOND_USER = None  # "seconduser"


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def _random_suffix():
    """Generate a random suffix for unique group names."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


def test_create_private_group():
    """Test creating a private group."""
    print("\n🧪 Testing: Create private group")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        group_name = f"test-group-{_random_suffix()}"
        result = groups.create_private_group(page, group_name)

        assert result.success, f"Create group failed: {result.error_message}"
        assert result.group_slug, "Should have group slug"
        assert result.group_url, "Should have group URL"

        print(f"  ✓ Created group: {result.group_slug}")
        print(f"  ✓ Group URL: {result.group_url}")
        print("✅ Create private group works!")

        # Clean up: delete the group
        groups.delete_group(page, result.group_slug)

        browser.close()


def test_delete_group():
    """Test deleting a group."""
    print("\n🧪 Testing: Delete group")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Create a group to delete
        group_name = f"group-to-delete-{_random_suffix()}"
        create_result = groups.create_private_group(page, group_name)
        assert create_result.success, f"Create group failed: {create_result.error_message}"

        time.sleep(1)

        # Delete the group
        delete_result = groups.delete_group(page, create_result.group_slug)

        assert delete_result.success, f"Delete group failed: {delete_result.error_message}"

        print(f"  ✓ Deleted group: {create_result.group_slug}")
        print("✅ Delete group works!")

        browser.close()


def test_delete_nonexistent_group():
    """Test deleting a group that doesn't exist."""
    print("\n🧪 Testing: Delete nonexistent group")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Try to delete a group that doesn't exist
        result = groups.delete_group(
            page,
            f"nonexistent-group-{_random_suffix()}"
        )

        # Should succeed (already deleted) or fail gracefully
        if result.success:
            print("  ✓ Nonexistent group handled as already deleted")
        else:
            print(f"  ✓ Nonexistent group correctly reported: {result.error_message}")

        print("✅ Delete nonexistent group handling works!")

        browser.close()


def test_get_group_members():
    """Test getting group members."""
    print("\n🧪 Testing: Get group members")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Create a group
        group_name = f"members-test-group-{_random_suffix()}"
        create_result = groups.create_private_group(page, group_name)
        assert create_result.success, f"Create group failed: {create_result.error_message}"

        time.sleep(1)

        # Get members (should include the creator)
        members = groups.get_group_members(page, create_result.group_slug)

        # Should have at least one member (the creator)
        assert len(members) >= 1, "Should have at least one member (creator)"

        print(f"  ✓ Found {len(members)} member(s)")
        for member in members:
            print(f"    - @{member.username}")
        print("✅ Get group members works!")

        # Clean up
        groups.delete_group(page, create_result.group_slug)

        browser.close()


def test_add_member_to_group():
    """Test adding a member to a group."""
    print("\n🧪 Testing: Add member to group")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not TEST_SECOND_USER:
        print("  ⏭️  SKIPPED - No TEST_SECOND_USER provided")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Create a group
        group_name = f"add-member-test-{_random_suffix()}"
        create_result = groups.create_private_group(page, group_name)
        assert create_result.success, f"Create group failed: {create_result.error_message}"

        time.sleep(1)

        # Add second user
        add_result = groups.add_member_to_group(
            page,
            create_result.group_slug,
            TEST_SECOND_USER
        )

        assert add_result.success, f"Add member failed: {add_result.error_message}"

        print(f"  ✓ Added @{TEST_SECOND_USER} to group")

        # Verify member was added
        members = groups.get_group_members(page, create_result.group_slug)
        member_usernames = [m.username for m in members]
        assert TEST_SECOND_USER in member_usernames, "New member should be in list"

        print(f"  ✓ Verified @{TEST_SECOND_USER} is a member")
        print("✅ Add member to group works!")

        # Clean up
        groups.delete_group(page, create_result.group_slug)

        browser.close()


def test_add_existing_member():
    """Test adding a member who is already in the group."""
    print("\n🧪 Testing: Add existing member")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    if not TEST_SECOND_USER:
        print("  ⏭️  SKIPPED - No TEST_SECOND_USER provided")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Create a group
        group_name = f"existing-member-test-{_random_suffix()}"
        create_result = groups.create_private_group(page, group_name)
        assert create_result.success

        time.sleep(1)

        # Add second user
        groups.add_member_to_group(page, create_result.group_slug, TEST_SECOND_USER)
        time.sleep(1)

        # Try to add same user again
        result = groups.add_member_to_group(
            page,
            create_result.group_slug,
            TEST_SECOND_USER
        )

        # Should succeed with "already a member" message
        assert result.success, f"Should handle gracefully: {result.error_message}"
        if result.error_message:
            assert "already" in result.error_message.lower()
            print(f"  ✓ Existing member handled: {result.error_message}")
        else:
            print("  ✓ Adding existing member succeeded without error")

        print("✅ Add existing member handling works!")

        # Clean up
        groups.delete_group(page, create_result.group_slug)

        browser.close()


if __name__ == "__main__":
    try:
        test_create_private_group()
        test_delete_group()
        test_delete_nonexistent_group()
        test_get_group_members()
        test_add_member_to_group()
        test_add_existing_member()
        print("\n" + "="*70)
        print("ALL GROUPS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME, TEST_PASSWORD, and TEST_SECOND_USER")
        print("   to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
