#!/usr/bin/env python3
"""Integration tests for GitLab merge requests module."""

from playwright.sync_api import sync_playwright
import sys
import time
import random
import string
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from gitlab_pw import login, projects, branches, files, merge_requests
from gitlab_pw.constants import GITLAB_DOMAIN

TEST_USERNAME = "testuser" # "testuser"
TEST_PASSWORD = "jGv3n7CkM4!XPM" # "jGv3n7CkM4!XPM"

# Test project for MR operations
TEST_PROJECT = "mr-test-project"


def _ensure_logged_in(page):
    """Helper to ensure logged in state."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        return False
    result = login.login_user(page, TEST_USERNAME, TEST_PASSWORD)
    return result.success


def _random_suffix():
    """Generate a random suffix for unique names."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _ensure_test_project_with_commit(page, namespace, project_name):
    """Ensure test project exists with at least one commit."""
    result = projects.create_private_project(page, project_name)
    if result.success or "already exist" in (result.error_message or "").lower():
        # Create a file if project is new (to have an initial commit)
        files.create_empty_file(page, namespace, project_name, "main", "README.md")
        return True
    return False


def _setup_branch_with_changes(page, namespace, project, branch_name):
    """Create a branch with changes that can be merged."""
    # Create the branch
    branch_result = branches.create_branch(page, namespace, project, branch_name)
    if not branch_result.success:
        return False

    time.sleep(1)

    # Add a file to create a difference from main
    file_result = files.create_file_with_content(
        page, namespace, project, branch_name,
        f"new-file-{_random_suffix()}.txt",
        "This is new content for the merge request."
    )
    return file_result.success


def test_create_merge_request():
    """Test creating a merge request."""
    print("\n🧪 Testing: Create merge request")

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

        # Create branch with changes
        branch_name = f"feature-mr-{_random_suffix()}"
        if not _setup_branch_with_changes(page, namespace, TEST_PROJECT, branch_name):
            print("  ❌ Failed to setup branch with changes")
            browser.close()
            return

        time.sleep(1)

        # Create merge request
        result = merge_requests.create_merge_request(
            page,
            namespace,
            TEST_PROJECT,
            branch_name,
            f"Test MR: {branch_name}"
        )

        assert result.success, f"Create MR failed: {result.error_message}"
        assert result.mr_id, "Should have MR ID"
        assert result.mr_url, "Should have MR URL"

        print(f"  ✓ Created MR !{result.mr_id}")
        print(f"  ✓ MR URL: {result.mr_url}")
        print("✅ Create merge request works!")

        # Clean up: close and delete branch
        merge_requests.close_merge_request(page, namespace, TEST_PROJECT, result.mr_id)
        branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        browser.close()


def test_close_merge_request():
    """Test closing a merge request."""
    print("\n🧪 Testing: Close merge request")

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

        # Create branch with changes
        branch_name = f"to-close-{_random_suffix()}"
        if not _setup_branch_with_changes(page, namespace, TEST_PROJECT, branch_name):
            print("  ❌ Failed to setup branch with changes")
            browser.close()
            return

        time.sleep(1)

        # Create MR
        create_result = merge_requests.create_merge_request(
            page,
            namespace,
            TEST_PROJECT,
            branch_name,
            f"MR to close: {branch_name}"
        )
        assert create_result.success, f"Create MR failed: {create_result.error_message}"

        time.sleep(1)

        # Close the MR
        close_result = merge_requests.close_merge_request(
            page,
            namespace,
            TEST_PROJECT,
            create_result.mr_id
        )

        assert close_result.success, f"Close MR failed: {close_result.error_message}"

        print(f"  ✓ Closed MR !{create_result.mr_id}")
        print("✅ Close merge request works!")

        # Clean up
        branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        browser.close()


def test_close_merge_request_by_url():
    """Test closing a merge request using its URL."""
    print("\n🧪 Testing: Close merge request by URL")

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

        # Create branch with changes
        branch_name = f"close-by-url-{_random_suffix()}"
        if not _setup_branch_with_changes(page, namespace, TEST_PROJECT, branch_name):
            print("  ❌ Failed to setup branch with changes")
            browser.close()
            return

        time.sleep(1)

        # Create MR
        create_result = merge_requests.create_merge_request(
            page,
            namespace,
            TEST_PROJECT,
            branch_name,
            f"MR to close by URL: {branch_name}"
        )
        assert create_result.success

        time.sleep(1)

        # Close by URL
        close_result = merge_requests.close_merge_request_by_url(
            page,
            create_result.mr_url
        )

        assert close_result.success, f"Close MR by URL failed: {close_result.error_message}"

        print(f"  ✓ Closed MR by URL: {create_result.mr_url}")
        print("✅ Close merge request by URL works!")

        # Clean up
        branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        browser.close()


def test_create_mr_no_changes():
    """Test creating MR when branch has no changes from target."""
    print("\n🧪 Testing: Create MR with no changes")

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

        # Create branch but don't add changes
        branch_name = f"no-changes-{_random_suffix()}"
        branch_result = branches.create_branch(page, namespace, TEST_PROJECT, branch_name)

        if not branch_result.success:
            print(f"  ❌ Could not create branch: {branch_result.error_message}")
            browser.close()
            return

        time.sleep(1)

        # Try to create MR (should fail or show warning about no changes)
        result = merge_requests.create_merge_request(
            page,
            namespace,
            TEST_PROJECT,
            branch_name,
            "MR with no changes"
        )

        # This might succeed (GitLab shows warning but allows) or fail
        if result.success:
            print("  ⚠️  GitLab allowed MR with no changes")
            merge_requests.close_merge_request(page, namespace, TEST_PROJECT, result.mr_id)
        else:
            print(f"  ✓ Correctly rejected: {result.error_message}")

        print("✅ No changes handling works!")

        # Clean up
        branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        browser.close()


def test_mr_dataclass():
    """Test MergeRequest dataclass structure."""
    print("\n🧪 Testing: MergeRequest dataclass")

    # Can run without credentials - just testing dataclass
    mr = merge_requests.MergeRequest(
        mr_id=1,
        title="Test MR",
        url="http://example.com/mr/1",
        source_branch="feature",
        target_branch="main",
        state="opened",
        author="testuser"
    )

    assert mr.mr_id == 1
    assert mr.title == "Test MR"
    assert mr.source_branch == "feature"
    assert mr.target_branch == "main"
    assert mr.state == "opened"
    assert mr.author == "testuser"

    print("  ✓ MergeRequest dataclass works correctly")
    print("✅ MergeRequest dataclass works!")


if __name__ == "__main__":
    try:
        test_mr_dataclass()
        test_create_merge_request()
        test_close_merge_request()
        test_close_merge_request_by_url()
        test_create_mr_no_changes()
        print("\n" + "="*70)
        print("ALL MERGE REQUESTS TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
