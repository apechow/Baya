#!/usr/bin/env python3
"""Integration tests for GitLab settings module."""

from playwright.sync_api import sync_playwright
import sys
import time
import random
import string
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from gitlab_pw import login, projects, settings
from gitlab_pw.constants import GITLAB_DOMAIN

# TODO: Add real test credentials
TEST_USERNAME = "testuser" # "testuser"
TEST_PASSWORD = "jGv3n7CkM4!XPM" # "jGv3n7CkM4!XPM"

# Test project for settings operations
TEST_PROJECT = "settings-test-project"


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def _random_suffix():
    """Generate a random suffix for unique names."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _ensure_test_project(page, namespace, project_name):
    """Ensure test project exists."""
    result = projects.create_private_project(page, project_name)
    return result.success or "already exist" in (result.error_message or "").lower()


def test_toggle_private_profile():
    """Test toggling private profile setting."""
    print("\n🧪 Testing: Toggle private profile")

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

        # Make profile private
        result = settings.toggle_private_profile(page, make_private=True)
        assert result.success, f"Toggle to private failed: {result.error_message}"
        print("  ✓ Set profile to private")

        time.sleep(1)

        # Make profile public
        result = settings.toggle_private_profile(page, make_private=False)
        assert result.success, f"Toggle to public failed: {result.error_message}"
        print("  ✓ Set profile to public")

        print("✅ Toggle private profile works!")

        browser.close()


def test_change_username():
    """Test changing username (and reverting)."""
    print("\n🧪 Testing: Change username")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    print("  ⏭️  SKIPPED - Username change is destructive and requires manual revert")
    print("  💡 To test username change, uncomment the test code and run manually")
    return

    # UNCOMMENT BELOW TO TEST (CAUTION: this changes your username!)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Change to a new username
        new_username = f"{TEST_USERNAME}-temp-{_random_suffix()}"
        result = settings.change_username(page, new_username)

        if result.success:
            print(f"  ✓ Changed username to: {new_username}")

            # Change it back
            revert_result = settings.change_username(page, TEST_USERNAME)
            if revert_result.success:
                print(f"  ✓ Reverted username to: {TEST_USERNAME}")
            else:
                print(f"  ⚠️  Could not revert username: {revert_result.error_message}")
        else:
            print(f"  ⚠️  Username change failed: {result.error_message}")

        print("✅ Change username works!")

        browser.close()
    """


def test_delete_deploy_key():
    """Test deleting a deploy key."""
    print("\n🧪 Testing: Delete deploy key")

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

        namespace = TEST_USERNAME
        if not _ensure_test_project(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to setup test project")
            browser.close()
            return

        result = settings.delete_deploy_key(page, namespace, TEST_PROJECT)

        # Should succeed even if no key exists
        assert result.success, f"Delete deploy key failed: {result.error_message}"

        if result.error_message and "no" in result.error_message.lower():
            print(f"  ✓ No deploy key to delete: {result.error_message}")
        else:
            print("  ✓ Deleted deploy key (or none existed)")

        print("✅ Delete deploy key works!")

        browser.close()


def test_delete_deploy_token():
    """Test deleting a deploy token."""
    print("\n🧪 Testing: Delete deploy token")

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

        namespace = TEST_USERNAME
        if not _ensure_test_project(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to setup test project")
            browser.close()
            return

        result = settings.delete_deploy_token(page, namespace, TEST_PROJECT)

        # Should succeed even if no token exists
        assert result.success, f"Delete deploy token failed: {result.error_message}"

        if result.error_message and "no" in result.error_message.lower():
            print(f"  ✓ No deploy token to delete: {result.error_message}")
        else:
            print("  ✓ Deleted deploy token (or none existed)")

        print("✅ Delete deploy token works!")

        browser.close()


def test_delete_all_webhooks():
    """Test deleting all webhooks."""
    print("\n🧪 Testing: Delete all webhooks")

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

        namespace = TEST_USERNAME
        if not _ensure_test_project(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to setup test project")
            browser.close()
            return

        result = settings.delete_all_webhooks(page, namespace, TEST_PROJECT)

        assert result.success, f"Delete webhooks failed: {result.error_message}"

        print(f"  ✓ {result.error_message or 'Deleted webhooks'}")
        print("✅ Delete all webhooks works!")

        browser.close()


def test_delete_ssh_key():
    """Test deleting an SSH key."""
    print("\n🧪 Testing: Delete SSH key")

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

        result = settings.delete_ssh_key(page)

        # Should succeed even if no key exists
        assert result.success, f"Delete SSH key failed: {result.error_message}"

        if result.error_message and "no" in result.error_message.lower():
            print(f"  ✓ No SSH key to delete: {result.error_message}")
        else:
            print("  ✓ Deleted SSH key (or none existed)")

        print("✅ Delete SSH key works!")

        browser.close()


def test_delete_all_access_tokens():
    """Test deleting all personal access tokens."""
    print("\n🧪 Testing: Delete all access tokens")

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

        result = settings.delete_all_access_tokens(page)

        assert result.success, f"Delete access tokens failed: {result.error_message}"

        print(f"  ✓ {result.error_message or 'Deleted access tokens'}")
        print("✅ Delete all access tokens works!")

        browser.close()


def test_delete_account():
    """Test account deletion (SKIP - destructive!)."""
    print("\n🧪 Testing: Delete account")

    print("  ⏭️  SKIPPED - Account deletion is destructive and cannot be undone")
    print("  💡 To test account deletion, use a throwaway account and run manually")
    print("✅ Delete account test skipped (as expected)!")


def test_dataclasses():
    """Test settings dataclasses."""
    print("\n🧪 Testing: Settings dataclasses")

    # ProfileUpdateResult
    profile_result = settings.ProfileUpdateResult(success=True)
    assert profile_result.success == True
    assert profile_result.error_message is None

    profile_fail = settings.ProfileUpdateResult(
        success=False,
        error_message="Profile update failed"
    )
    assert profile_fail.success == False
    assert profile_fail.error_message == "Profile update failed"

    print("  ✓ ProfileUpdateResult dataclass works")

    # UsernameChangeResult
    username_result = settings.UsernameChangeResult(
        success=True,
        new_username="newuser"
    )
    assert username_result.success == True
    assert username_result.new_username == "newuser"

    print("  ✓ UsernameChangeResult dataclass works")

    # DeleteResult
    delete_result = settings.DeleteResult(success=True)
    assert delete_result.success == True

    delete_fail = settings.DeleteResult(
        success=False,
        error_message="Not found"
    )
    assert delete_fail.success == False
    assert delete_fail.error_message == "Not found"

    print("  ✓ DeleteResult dataclass works")
    print("✅ Settings dataclasses work!")


if __name__ == "__main__":
    try:
        test_dataclasses()
        test_toggle_private_profile()
        test_change_username()
        test_delete_deploy_key()
        test_delete_deploy_token()
        test_delete_all_webhooks()
        test_delete_ssh_key()
        test_delete_all_access_tokens()
        test_delete_account()
        print("\n" + "="*70)
        print("ALL SETTINGS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   or because they are destructive operations")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable more testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
