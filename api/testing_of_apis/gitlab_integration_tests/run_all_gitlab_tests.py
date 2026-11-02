#!/usr/bin/env python3
"""
GitLab API - Comprehensive Integration Test Runner

Runs all integration tests and provides a comprehensive report.

Usage:
    python run_all_gitlab_tests.py
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime

# Test files in order of execution
TEST_FILES = [
    "test_login_integration.py",
    "test_projects_integration.py",
    "test_groups_integration.py",
    "test_issues_integration.py",
    "test_branches_integration.py",
    "test_files_integration.py",
    "test_merge_requests_integration.py",
    "test_settings_integration.py",
]


def run_test_file(test_file):
    """Run a single test file and return results."""
    print(f"\n{'='*70}")
    print(f"Running: {test_file}")
    print(f"{'='*70}")

    try:
        result = subprocess.run(
            [sys.executable, test_file],
            capture_output=True,
            text=True,
            timeout=180  # 3 minute timeout per test file
        )

        # Print output
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)

        success = result.returncode == 0
        return {
            "file": test_file,
            "success": success,
            "output": result.stdout,
            "error": result.stderr if result.stderr else None
        }

    except subprocess.TimeoutExpired:
        print(f"❌ TIMEOUT - Test took longer than 180 seconds")
        return {
            "file": test_file,
            "success": False,
            "output": "",
            "error": "Test timeout (180s)"
        }
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return {
            "file": test_file,
            "success": False,
            "output": "",
            "error": str(e)
        }


def main():
    """Run all integration tests."""
    print("="*70)
    print("GITLAB API - COMPREHENSIVE INTEGRATION TEST SUITE")
    print("="*70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Running {len(TEST_FILES)} test modules...")
    print("="*70)

    results = []

    for test_file in TEST_FILES:
        result = run_test_file(test_file)
        results.append(result)

    # Print summary
    print("\n" + "="*70)
    print("COMPREHENSIVE TEST SUMMARY")
    print("="*70)

    passed = sum(1 for r in results if r["success"])
    failed = sum(1 for r in results if not r["success"])

    print(f"\nResults: {passed}/{len(results)} test modules passed\n")

    for result in results:
        status = "✅ PASS" if result["success"] else "❌ FAIL"
        print(f"{status} - {result['file']}")
        if result["error"]:
            print(f"         Error: {result['error']}")

    print("\n" + "="*70)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    if failed == 0:
        print("\n🎉 ALL GITLAB INTEGRATION TESTS PASSED!")
        print("\nYour GitLab API is fully validated and working!")
        print("\nTested modules:")
        print("  ✅ Login - user authentication")
        print("  ✅ Projects - create, delete projects")
        print("  ✅ Groups - create, delete, manage members")
        print("  ✅ Issues - CRUD operations")
        print("  ✅ Branches - create, delete, list branches")
        print("  ✅ Files - create, replace files")
        print("  ✅ Merge Requests - create, close MRs")
        print("  ✅ Settings - profile, tokens, webhooks")
    else:
        print(f"\n⚠️  {failed} TEST MODULE(S) FAILED")
        print("\nReview the output above to see which tests failed")
        print("Check the error messages and fix the issues")

    print("="*70)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
