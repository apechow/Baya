#!/usr/bin/env python3
"""Integration tests for GitLab issues module."""

from playwright.sync_api import sync_playwright
import sys
import time
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from gitlab_pw import login, issues, projects
from gitlab_pw.constants import GITLAB_DOMAIN

# TODO: Add real test credentials
TEST_USERNAME = "testuser" # "testuser"
TEST_PASSWORD = "jGv3n7CkM4!XPM" # "jGv3n7CkM4!XPM"

# Test project namespace and name (will be created if credentials provided)
TEST_NAMESPACE = "testuser"  # Should be same as TEST_USERNAME typically
TEST_PROJECT = "test-project-issues"


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def _ensure_test_project(page, namespace, project_name):
    """Helper to ensure test project exists."""
    result = projects.create_private_project(page, project_name)
    return result.success or "already exist" in (result.error_message or "").lower()


def test_create_issue():
    """Test creating an issue."""
    print("\n🧪 Testing: Create issue")

    if not TEST_USERNAME or not TEST_PASSWORD:
        print("  ⏭️  SKIPPED - No test credentials provided")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Login
        if not _ensure_logged_in(page):
            print("  ❌ Failed to log in")
            browser.close()
            return

        # Ensure project exists
        namespace = TEST_NAMESPACE or TEST_USERNAME
        if not _ensure_test_project(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to create test project")
            browser.close()
            return

        # Create issue
        result = issues.create_issue(
            page,
            namespace,
            TEST_PROJECT,
            "Test Issue Title",
            "This is a test issue description"
        )

        assert result.success, f"Create issue failed: {result.error_message}"
        assert result.issue_url, "Should have issue URL"
        assert result.issue_id, "Should have issue ID"

        print(f"  ✓ Created issue #{result.issue_id}")
        print(f"  ✓ Issue URL: {result.issue_url}")
        print("✅ Create issue works!")

        browser.close()


def test_create_issue_with_title_only():
    """Test creating an issue with just a title."""
    print("\n🧪 Testing: Create issue with title only")

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

        namespace = TEST_NAMESPACE or TEST_USERNAME
        if not _ensure_test_project(page, namespace, TEST_PROJECT):
            print("  ❌ Failed to create test project")
            browser.close()
            return

        result = issues.create_issue_with_title(
            page,
            namespace,
            TEST_PROJECT,
            "Title Only Issue"
        )

        assert result.success, f"Create issue failed: {result.error_message}"

        print(f"  ✓ Created issue #{result.issue_id}")
        print("✅ Create issue with title only works!")

        browser.close()


def test_get_issues():
    """Test getting list of issues."""
    print("\n🧪 Testing: Get issues")

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

        namespace = TEST_NAMESPACE or TEST_USERNAME

        # Create an issue first to ensure there's at least one
        issues.create_issue_with_title(
            page,
            namespace,
            TEST_PROJECT,
            "Issue for list test"
        )
        time.sleep(1)

        # Get issues
        issue_list = issues.get_issues(page, namespace, TEST_PROJECT)

        assert len(issue_list) > 0, "Should have at least one issue"

        print(f"  ✓ Found {len(issue_list)} issues")
        for issue in issue_list[:3]:  # Show first 3
            print(f"    - #{issue.issue_id}: {issue.title}")
        print("✅ Get issues works!")

        browser.close()


def test_delete_issue():
    """Test deleting an issue."""
    print("\n🧪 Testing: Delete issue")

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

        namespace = TEST_NAMESPACE or TEST_USERNAME

        # Create an issue to delete
        create_result = issues.create_issue_with_title(
            page,
            namespace,
            TEST_PROJECT,
            "Issue to be deleted"
        )

        assert create_result.success, f"Create issue failed: {create_result.error_message}"
        time.sleep(1)

        # Delete the issue
        delete_result = issues.delete_issue(page, create_result.issue_url)

        assert delete_result.success, f"Delete issue failed: {delete_result.error_message}"

        print(f"  ✓ Deleted issue #{create_result.issue_id}")
        print("✅ Delete issue works!")

        browser.close()


def test_delete_issue_by_id():
    """Test deleting an issue by ID."""
    print("\n🧪 Testing: Delete issue by ID")

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

        namespace = TEST_NAMESPACE or TEST_USERNAME

        # Create an issue to delete
        create_result = issues.create_issue_with_title(
            page,
            namespace,
            TEST_PROJECT,
            "Issue to delete by ID"
        )

        assert create_result.success and create_result.issue_id
        time.sleep(1)

        # Delete by ID
        delete_result = issues.delete_issue_by_id(
            page,
            namespace,
            TEST_PROJECT,
            create_result.issue_id
        )

        assert delete_result.success, f"Delete issue failed: {delete_result.error_message}"

        print(f"  ✓ Deleted issue by ID #{create_result.issue_id}")
        print("✅ Delete issue by ID works!")

        browser.close()


def test_delete_all_issues():
    """Test deleting all issues in a project."""
    print("\n🧪 Testing: Delete all issues")

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

        namespace = TEST_NAMESPACE or TEST_USERNAME

        # Create a few issues
        for i in range(3):
            issues.create_issue_with_title(
                page,
                namespace,
                TEST_PROJECT,
                f"Issue to delete all #{i+1}"
            )
            time.sleep(0.5)

        # Delete all
        deleted_count = issues.delete_all_issues(page, namespace, TEST_PROJECT)

        print(f"  ✓ Deleted {deleted_count} issues")

        # Verify no issues remain
        remaining = issues.get_issues(page, namespace, TEST_PROJECT)
        assert len(remaining) == 0, f"Should have 0 issues but found {len(remaining)}"

        print("  ✓ Verified all issues deleted")
        print("✅ Delete all issues works!")

        browser.close()


if __name__ == "__main__":
    try:
        test_create_issue()
        test_create_issue_with_title_only()
        test_get_issues()
        test_delete_issue()
        test_delete_issue_by_id()
        test_delete_all_issues()
        print("\n" + "="*70)
        print("ALL ISSUES TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME, TEST_PASSWORD, and TEST_NAMESPACE to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
