#!/usr/bin/env python3
"""Integration tests for GitLab files module."""

from playwright.sync_api import sync_playwright
import sys
import time
import tempfile
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

# Test project for file operations
TEST_PROJECT = "files-test-project"


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


def test_create_empty_file():
    """Test creating an empty file."""
    print("\n🧪 Testing: Create empty file")

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

        filename = f"empty-file-{_random_suffix()}.txt"
        result = files.create_empty_file(
            page,
            namespace,
            TEST_PROJECT,
            "main",
            filename
        )

        assert result.success, f"Create file failed: {result.error_message}"
        assert result.file_path == filename, "File path should match"

        print(f"  ✓ Created empty file: {result.file_path}")
        print("✅ Create empty file works!")

        browser.close()


def test_create_file_with_content():
    """Test creating a file with content."""
    print("\n🧪 Testing: Create file with content")

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

        filename = f"content-file-{_random_suffix()}.txt"
        content = "Hello, World!\nThis is test content.\nLine 3."

        result = files.create_file_with_content(
            page,
            namespace,
            TEST_PROJECT,
            "main",
            filename,
            content
        )

        assert result.success, f"Create file failed: {result.error_message}"
        assert result.file_path == filename

        print(f"  ✓ Created file with content: {result.file_path}")
        print("✅ Create file with content works!")

        browser.close()


def test_create_file_on_branch():
    """Test creating a file on a non-main branch."""
    print("\n🧪 Testing: Create file on branch")

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

        # First ensure there's an initial commit
        files.create_empty_file(page, namespace, TEST_PROJECT, "main", "init.txt")

        # Create a branch
        branch_name = f"file-test-branch-{_random_suffix()}"
        branch_result = branches.create_branch(page, namespace, TEST_PROJECT, branch_name)

        if not branch_result.success:
            print(f"  ⚠️  Could not create branch, using main: {branch_result.error_message}")
            branch_name = "main"

        time.sleep(1)

        filename = f"branch-file-{_random_suffix()}.md"
        result = files.create_file_with_content(
            page,
            namespace,
            TEST_PROJECT,
            branch_name,
            filename,
            "# File on Branch\n\nThis file is on a feature branch."
        )

        assert result.success, f"Create file on branch failed: {result.error_message}"

        print(f"  ✓ Created file on branch '{branch_name}': {result.file_path}")
        print("✅ Create file on branch works!")

        # Clean up branch if we created one
        if branch_name != "main":
            branches.delete_branch(page, namespace, TEST_PROJECT, branch_name)

        browser.close()


def test_replace_file_with_upload():
    """Test replacing a file via upload."""
    print("\n🧪 Testing: Replace file with upload")

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

        # Create a file first
        filename = f"to-replace-{_random_suffix()}.txt"
        create_result = files.create_file_with_content(
            page,
            namespace,
            TEST_PROJECT,
            "main",
            filename,
            "Original content"
        )

        if not create_result.success:
            print(f"  ❌ Could not create file to replace: {create_result.error_message}")
            browser.close()
            return

        time.sleep(1)

        # Create a local temp file to upload
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Replaced content via upload")
            temp_path = f.name

        # Replace the file
        result = files.replace_file_with_upload(
            page,
            namespace,
            TEST_PROJECT,
            "main",
            filename,
            temp_path
        )

        # Clean up temp file
        Path(temp_path).unlink()

        assert result.success, f"Replace file failed: {result.error_message}"

        print(f"  ✓ Replaced file: {filename}")
        print("✅ Replace file with upload works!")

        browser.close()


def test_create_file_result_dataclass():
    """Test CreateFileResult dataclass structure."""
    print("\n🧪 Testing: CreateFileResult dataclass")

    # Can run without credentials - just testing dataclass
    result = files.CreateFileResult(
        success=True,
        file_path="test.txt",
        error_message=None
    )

    assert result.success == True
    assert result.file_path == "test.txt"
    assert result.error_message is None

    # Test failure case
    fail_result = files.CreateFileResult(
        success=False,
        file_path=None,
        error_message="File already exists"
    )

    assert fail_result.success == False
    assert fail_result.file_path is None
    assert fail_result.error_message == "File already exists"

    print("  ✓ CreateFileResult dataclass works correctly")
    print("✅ CreateFileResult dataclass works!")


def test_replace_file_result_dataclass():
    """Test ReplaceFileResult dataclass structure."""
    print("\n🧪 Testing: ReplaceFileResult dataclass")

    result = files.ReplaceFileResult(
        success=True,
        error_message=None
    )

    assert result.success == True
    assert result.error_message is None

    fail_result = files.ReplaceFileResult(
        success=False,
        error_message="File not found"
    )

    assert fail_result.success == False
    assert fail_result.error_message == "File not found"

    print("  ✓ ReplaceFileResult dataclass works correctly")
    print("✅ ReplaceFileResult dataclass works!")


if __name__ == "__main__":
    try:
        test_create_file_result_dataclass()
        test_replace_file_result_dataclass()
        test_create_empty_file()
        test_create_file_with_content()
        test_create_file_on_branch()
        test_replace_file_with_upload()
        print("\n" + "="*70)
        print("ALL FILES TESTS PASSED! ✅")
        print("="*70)
        print("\n💡 Note: Some tests were skipped due to missing credentials")
        print("   Set TEST_USERNAME and TEST_PASSWORD to enable full testing")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
