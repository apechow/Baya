#!/usr/bin/env python3
"""Run all shipping_pw tests and report results."""

import subprocess
import sys
from pathlib import Path

def run_test_file(test_file: Path) -> tuple[bool, str]:
    """Run a test file and return (success, output)."""
    try:
        result = subprocess.run(
            [sys.executable, str(test_file)],
            cwd=test_file.parent,
            capture_output=True,
            text=True,
            timeout=30,
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr
        return success, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (>30s)"
    except Exception as e:
        return False, f"ERROR: {e}"

def main():
    # Find all test files in the current directory
    test_dir = Path(__file__).parent
    
    # Get all *_tests.py files, but exclude this script
    all_test_files = test_dir.glob("*_tests.py")
    test_files = sorted([
        f for f in all_test_files 
        if f.name not in ['run_all_tests.py', 'test_all.py']  # Exclude test runners
    ])
    
    if not test_files:
        print("No test files found!")
        print(f"Searched in: {test_dir}")
        return 1
    
    print("=" * 70)
    print("RUNNING ALL SHIPPING_PW TESTS")
    print("=" * 70)
    print(f"Test directory: {test_dir}")
    print(f"Found {len(test_files)} test file(s)")
    
    results = []
    total_tests = 0
    failed_tests = 0
    
    for test_file in test_files:
        test_name = test_file.stem
        print(f"\n{'=' * 70}")
        print(f"Running: {test_name}")
        print('=' * 70)
        
        success, output = run_test_file(test_file)
        results.append((test_name, success, output))
        
        # Parse output for test count
        if success:
            # Extract test count from output like "Ran 3 tests in 0.001s"
            import re
            match = re.search(r'Ran (\d+) test', output)
            if match:
                count = int(match.group(1))
                total_tests += count
                print(f"✅ PASSED ({count} tests)")
            else:
                print(f"✅ PASSED")
        else:
            print(f"❌ FAILED")
            failed_tests += 1
            # Show error output
            print("\nError output:")
            print(output[:500])  # First 500 chars
            if len(output) > 500:
                print("... (truncated)")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for test_name, success, _ in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal test files: {len(test_files)}")
    print(f"Passed: {len([r for r in results if r[1]])}")
    print(f"Failed: {failed_tests}")
    
    if failed_tests == 0:
        print(f"\n🎉 ALL TESTS PASSED! ({total_tests} total tests)")
        return 0
    else:
        print(f"\n⚠️  {failed_tests} test file(s) failed")
        print("\nTo see full error details, run individual test files:")
        for test_name, success, _ in results:
            if not success:
                print(f"  python {test_name}.py")
        return 1

if __name__ == "__main__":
    sys.exit(main())