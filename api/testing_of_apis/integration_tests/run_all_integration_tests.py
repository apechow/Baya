#!/usr/bin/env python3
"""
Master Integration Test Runner

Runs all integration tests and provides a comprehensive report.

Usage:
    python run_all_integration_tests.py
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime

# Test files in order of execution
TEST_FILES = [
    "test_product_integration.py",
    "test_search_integration.py",
    "test_cart_integration.py",
    "test_compare_integration.py",
    "test_review_integration.py",
    "test_shipping_integration.py",
    "test_login_integration.py",
    "test_wishlist_integration.py",
    "test_account_integration.py",
    "test_order_integration.py",
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
            timeout=120  # 2 minute timeout per test file
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
        print(f"❌ TIMEOUT - Test took longer than 120 seconds")
        return {
            "file": test_file,
            "success": False,
            "output": "",
            "error": "Test timeout (120s)"
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
    print("MAGENTO API - COMPREHENSIVE INTEGRATION TEST SUITE")
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
        print("\n🎉 ALL INTEGRATION TESTS PASSED!")
        print("\nYour Magento API is fully validated and working!")
        print("\nTested modules:")
        print("  ✅ Product - extraction, images, options")
        print("  ✅ Cart - add, view, update, remove")
        print("  ✅ Search - product search functionality")
        print("  ✅ Compare - product comparison")
        print("  ✅ Review - product reviews")
        print("  ✅ Shipping - guest checkout")
        print("  ⏭️  Login/Wishlist/Account/Orders - (requires credentials)")
        print("\n💡 To test logged-in features, add TEST_EMAIL and TEST_PASSWORD")
        print("   in the respective test files")
    else:
        print(f"\n⚠️  {failed} TEST MODULE(S) FAILED")
        print("\nReview the output above to see which tests failed")
        print("Check the error messages and fix the issues")
    
    print("="*70)
    
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
