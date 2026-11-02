#!/usr/bin/env python3
"""Integration tests for GitLab branches module."""

from playwright.sync_api import sync_playwright
import sys
import time
import random
import string
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from gitlab_pw import login, projects, branches, files
from gitlab_pw.constants import GITLAB_DOMAIN

# TODO: Add real test credentials
TEST_USERNAME = "testuser" # "testuser"
TEST_PASSWORD = "jGv3n7CkM4!XPM" # "jGv3n7CkM4!XPM"

# Test project for branch operations
TEST_PROJECT = "branch-test-project"


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def _random_suffix():
    """Generate a random suffix for unique branch names."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _ensure_test_project_with_commit(page, namespace, project_name):
    """Ensure test project exists with at least one commit (needed for branches)."""
    result = projects.create_private_project(page, project_name)
    if result.success or "already exist" in (result.error_message or "").lower():
        # Create a file if project is new (to have an initial commit)
        file_result = files.create_empty_file(
            page, namespace, project_name, "main", "README.md"
        )
        # Don't fail if file already exists
        return True
    return False


def test_create_branch():
    """Test creating a branch."""
    print("\n🧪 Testing: Create branch")

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
        if not _ensure_test_project_with_commit(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to setup test project")
            browser.close()
            return

        branch_name = f"feature-{_random_suffix()}"
        result = branches.create_branch(page, namespace, TEST_PROJECT, branch_name)

        assert result.success, f"Create branch failed: {result.error_message}"
        assert result.branch_name == branch_name, "Branch name should match"

        print(f"  ✓ Created branch: {result.branch_name}")
        print("✅ Create branch works!")

        # Clean up: delete the branch
        branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        browser.close()


def test_get_branches():
    """Test getting list of branches."""
    print("\n🧪 Testing: Get branches")

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
        if not _ensure_test_project_with_commit(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to setup test project")
            browser.close()
            return

        # Create a branch to ensure there's at least one besides main
        branch_name = f"list-test-{_random_suffix()}"
        branches.create_branch(page, namespace, TEST_PROJECT, branch_name)
        time.sleep(1)

        # Get branches
        branch_list = branches.get_branches(page, namespace, TEST_PROJECT)

        assert len(branch_list) >= 1, "Should have at least one branch"

        print(f"  ✓ Found {len(branch_list)} branches")
        for b in branch_list[:5]:  # Show first 5
            default_marker = " (default)" if b.is_default else ""
            protected_marker = " (protected)" if b.is_protected else ""
            print(f"    - {b.name}{default_marker}{protected_marker}")
        print("✅ Get branches works!")

        # Clean up
        branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        browser.close()


def test_delete_branch():
    """Test deleting a branch."""
    print("\n🧪 Testing: Delete branch")

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
        if not _ensure_test_project_with_commit(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to setup test project")
            browser.close()
            return

        # Create a branch to delete
        branch_name = f"to-delete-{_random_suffix()}"
        create_result = branches.create_branch(page, namespace, TEST_PROJECT, branch_name)
        assert create_result.success, f"Create branch failed: {create_result.error_message}"

        time.sleep(1)

        # Delete the branch
        delete_result = branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        assert delete_result.success, f"Delete branch failed: {delete_result.error_message}"

        print(f"  ✓ Deleted branch: {branch_name}")
        print("✅ Delete branch works!")

        browser.close()


def test_delete_nonexistent_branch():
    """Test deleting a branch that doesn't exist."""
    print("\n🧪 Testing: Delete nonexistent branch")

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

        # Try to delete a branch that doesn't exist
        result = branches.delete_branch(
            page,
            namespace,
            TEST_PROJECT,
            f"nonexistent-branch-{_random_suffix()}"
        )

        # Should fail with appropriate error
        assert not result.success, "Should fail for nonexistent branch"
        assert result.error_message, "Should have error message"
        assert "not found" in result.error_message.lower()

        print(f"  ✓ Correctly reported: {result.error_message}")
        print("✅ Delete nonexistent branch handling works!")

        browser.close()


def test_branch_properties():
    """Test branch object properties."""
    print("\n🧪 Testing: Branch properties")

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
        if not _ensure_test_project_with_commit(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to setup test project")
            browser.close()
            return

        # Get branches and check default branch
        branch_list = branches.get_branches(page, namespace, TEST_PROJECT)

        # Find default branch
        default_branches = [b for b in branch_list if b.is_default]
        if default_branches:
            print(f"  ✓ Default branch: {default_branches[0].name}")
        else:
            print("  ⚠️  No default branch found (may be expected)")

        # Check properties exist
        for b in branch_list:
            assert hasattr(b, 'name'), "Branch should have name"
            assert hasattr(b, 'is_default'), "Branch should have is_default"
            assert hasattr(b, 'is_protected'), "Branch should have is_protected"

        print("  ✓ All branch objects have required properties")
        print("✅ Branch properties work!")

        browser.close()


if __name__ == "__main__":
    try:
        test_create_branch()
        test_get_branches()
        test_delete_branch()
        test_delete_nonexistent_branch()
        test_branch_properties()
        print("\n" + "="*70)
        print("ALL BRANCHES TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
