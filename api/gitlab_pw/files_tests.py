"""Checks for the files helper."""

from __future__ import annotations

import sys
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

api_stub = types.ModuleType("api")
api_stub.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules["api"] = api_stub

playwright_stub = types.ModuleType("playwright")
playwright_stub.sync_api = types.SimpleNamespace(Page=object, TimeoutError=TimeoutError, expect=lambda x: x)
sys.modules["playwright"] = playwright_stub
sys.modules["playwright.sync_api"] = playwright_stub.sync_api

from api.gitlab_pw import files  # noqa:E402
from api.gitlab_pw.constants import Selectors, GITLAB_DOMAIN  # noqa:E402
from api.gitlab_pw.test_utils import FakeLocator, FakePage  # noqa:E402


class FileDataclassTests(unittest.TestCase):
    def test_create_file_result_dataclass(self) -> None:
        """Test CreateFileResult dataclass has expected fields."""
        result = files.CreateFileResult(
            success=True,
            file_path="README.md",
            error_message=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.file_path, "README.md")

        failed = files.CreateFileResult(
            success=False,
            file_path=None,
            error_message="A file with this name already exists",
        )
        self.assertFalse(failed.success)
        self.assertIn("already exists", failed.error_message or "")

    def test_replace_file_result_dataclass(self) -> None:
        """Test ReplaceFileResult dataclass."""
        result = files.ReplaceFileResult(success=True)
        self.assertTrue(result.success)

        failed = files.ReplaceFileResult(
            success=False,
            error_message="Upload failed",
        )
        self.assertFalse(failed.success)


class CreateEmptyFileTests(unittest.TestCase):
    def test_create_empty_file_success(self) -> None:
        """Test successful empty file creation."""
        name_input = FakeLocator(count_value=1)
        commit_btn = FakeLocator(count_value=1)

        file_url = f"{GITLAB_DOMAIN}/ns/proj/-/blob/main/newfile.txt"

        page = FakePage(
            locators={
                Selectors.FILE_NAME_INPUT: name_input,
                Selectors.FILE_COMMIT_BUTTON: commit_btn,
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/new/main",
        )

        commit_btn.on_click = lambda: setattr(page, "url", file_url)

        result = files.create_empty_file(page, "ns", "proj", "main", "newfile.txt")

        self.assertTrue(result.success)
        self.assertEqual(result.file_path, "newfile.txt")
        self.assertEqual(name_input.text, "newfile.txt")

    def test_create_empty_file_page_not_found(self) -> None:
        """Test file creation when page not found."""
        page = FakePage(
            locators={
                Selectors.PAGE_NOT_FOUND: FakeLocator(text="Page Not Found", count_value=1),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/new/main",
        )

        result = files.create_empty_file(page, "ns", "proj", "main", "test.txt")

        self.assertFalse(result.success)
        self.assertIn("cannot access", (result.error_message or "").lower())


class CreateFileWithContentTests(unittest.TestCase):
    def test_create_file_with_content_success(self) -> None:
        """Test successful file creation with content."""
        name_input = FakeLocator(count_value=1)
        editor_textarea = FakeLocator(count_value=1)
        commit_btn = FakeLocator(count_value=1)

        file_url = f"{GITLAB_DOMAIN}/ns/proj/-/blob/main/script.py"

        page = FakePage(
            locators={
                Selectors.FILE_NAME_INPUT: name_input,
                ".monaco-editor textarea": editor_textarea,
                Selectors.FILE_COMMIT_BUTTON: commit_btn,
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/new/main",
        )

        commit_btn.on_click = lambda: setattr(page, "url", file_url)

        result = files.create_file_with_content(
            page, "ns", "proj", "main", "script.py", "print('hello')"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.file_path, "script.py")
        self.assertEqual(name_input.text, "script.py")
        self.assertEqual(editor_textarea.text, "print('hello')")

    def test_create_file_with_content_fallback_editor(self) -> None:
        """Test file creation with fallback textarea editor."""
        name_input = FakeLocator(count_value=1)
        fallback_textarea = FakeLocator(count_value=1)
        commit_btn = FakeLocator(count_value=1)

        file_url = f"{GITLAB_DOMAIN}/ns/proj/-/blob/main/notes.txt"

        page = FakePage(
            locators={
                Selectors.FILE_NAME_INPUT: name_input,
                ".monaco-editor textarea": FakeLocator(count_value=0),
                "textarea.file-editor": fallback_textarea,
                Selectors.FILE_COMMIT_BUTTON: commit_btn,
                Selectors.PAGE_NOT_FOUND: FakeLocator(count_value=0),
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/new/main",
        )

        commit_btn.on_click = lambda: setattr(page, "url", file_url)

        result = files.create_file_with_content(
            page, "ns", "proj", "main", "notes.txt", "Some notes"
        )

        self.assertTrue(result.success)
        self.assertEqual(fallback_textarea.text, "Some notes")


class ReplaceFileTests(unittest.TestCase):
    def test_replace_file_success(self) -> None:
        """Test successful file replacement."""
        replace_btn = FakeLocator(count_value=1)
        upload_input = FakeLocator(count_value=1)
        confirm_btn = FakeLocator(count_value=1)
        success_alert = FakeLocator(
            text="Your changes have been successfully committed",
            count_value=1,
        )

        page = FakePage(
            locators={
                Selectors.FILE_REPLACE_BUTTON: replace_btn,
                Selectors.FILE_REPLACE_MODAL: FakeLocator(count_value=1),
                Selectors.FILE_UPLOAD_INPUT: upload_input,
                Selectors.FILE_REPLACE_CONFIRM: confirm_btn,
                'div.gl-alert-body:has-text("Your changes have been successfully committed")': success_alert,
            },
            url=f"{GITLAB_DOMAIN}/ns/proj/-/blob/main/config.txt",
        )

        result = files.replace_file_with_upload(
            page, "ns", "proj", "main", "config.txt", "/path/to/local/file.txt"
        )

        self.assertTrue(result.success)
        # Verify upload was called
        self.assertTrue(
            any(action[0] == "set_input_files" for action in upload_input.actions)
        )

    def test_replace_file_result_failure(self) -> None:
        """Test ReplaceFileResult dataclass for failure case."""
        result = files.ReplaceFileResult(
            success=False,
            error_message="Replace button not found",
        )
        self.assertFalse(result.success)
        self.assertIn("not found", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
