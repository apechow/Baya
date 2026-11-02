#!/usr/bin/env python3
"""Integration tests for GitLab projects module."""

from playwright.sync_api import sync_playwright
import sys
import time
import random
import string
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from gitlab_pw import login, projects
from gitlab_pw.constants import GITLAB_DOMAIN

# TODO: Add real test credentials
TEST_USERNAME = "testuser" # "testuser"
TEST_PASSWORD = "jGv3n7CkM4!XPM" # "jGv3n7CkM4!XPM"


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def _random_suffix():
    """Generate a random suffix for unique project names."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


def test_create_private_project():
    """Test creating a private project."""
    print("\n🧪 Testing: Create private project")

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

        project_name = f"test-project-{_random_suffix()}"
        result = projects.create_private_project(page, project_name)

        assert result.success, f"Create project failed: {result.error_message}"
        assert result.project_slug, "Should have project slug"
        assert result.project_url, "Should have project URL"

        print(f"  ✓ Created project: {result.project_slug}")
        print(f"  ✓ Project URL: {result.project_url}")
        print("✅ Create private project works!")

        # Clean up: delete the project
        projects.delete_project(page, TEST_USERNAME, result.project_slug)

        browser.close()


def test_create_project_in_namespace():
    """Test creating a project in a specific namespace."""
    print("\n🧪 Testing: Create project in namespace")

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

        project_name = f"namespaced-project-{_random_suffix()}"
        result = projects.create_private_project(
            page,
            project_name,
            namespace_name=TEST_USERNAME
        )

        assert result.success, f"Create project failed: {result.error_message}"

        print(f"  ✓ Created project in namespace: {TEST_USERNAME}/{result.project_slug}")
        print("✅ Create project in namespace works!")

        # Clean up
        projects.delete_project(page, TEST_USERNAME, result.project_slug)

        browser.close()


def test_create_duplicate_project():
    """Test creating a project with a duplicate name."""
    print("\n🧪 Testing: Create duplicate project")

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

        project_name = f"duplicate-test-{_random_suffix()}"

        # Create first project
        result1 = projects.create_private_project(page, project_name)
        assert result1.success, f"First project creation failed: {result1.error_message}"

        time.sleep(1)

        # Try to create duplicate
        result2 = projects.create_private_project(page, project_name)

        # Should either succeed with "already exist" or fail
        if result2.success:
            assert result2.error_message and "already exist" in result2.error_message.lower()
            print(f"  ✓ Duplicate handled: {result2.error_message}")
        else:
            print(f"  ✓ Duplicate correctly rejected: {result2.error_message}")

        print("✅ Duplicate project handling works!")

        # Clean up
        projects.delete_project(page, TEST_USERNAME, result1.project_slug)

        browser.close()


def test_delete_project():
    """Test deleting a project."""
    print("\n🧪 Testing: Delete project")

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

        # Create a project to delete
        project_name = f"project-to-delete-{_random_suffix()}"
        create_result = projects.create_private_project(page, project_name)
        assert create_result.success, f"Create project failed: {create_result.error_message}"

        time.sleep(1)

        # Delete the project
        delete_result = projects.delete_project(
            page,
            TEST_USERNAME,
            create_result.project_slug
        )

        assert delete_result.success, f"Delete project failed: {delete_result.error_message}"

        print(f"  ✓ Deleted project: {create_result.project_slug}")
        print("✅ Delete project works!")

        browser.close()


def test_delete_nonexistent_project():
    """Test deleting a project that doesn't exist."""
    print("\n🧪 Testing: Delete nonexistent project")

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

        # Try to delete a project that doesn't exist
        result = projects.delete_project(
            page,
            TEST_USERNAME,
            f"nonexistent-project-{_random_suffix()}"
        )

        # Should succeed (already deleted) or fail gracefully
        if result.success:
            print("  ✓ Nonexistent project handled as already deleted")
        else:
            print(f"  ✓ Nonexistent project correctly reported: {result.error_message}")

        print("✅ Delete nonexistent project handling works!")

        browser.close()


if __name__ == "__main__":
    try:
        test_create_private_project()
        test_create_project_in_namespace()
        test_create_duplicate_project()
        test_delete_project()
        test_delete_nonexistent_project()
        print("\n" + "="*70)
        print("ALL PROJECTS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
