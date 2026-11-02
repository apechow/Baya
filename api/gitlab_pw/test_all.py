#!/usr/bin/env python3
"""Run all gitlab_pw tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure the repo root is on the path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_tests() -> bool:
    """Discover and run all tests in the gitlab_pw package."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Discover tests in the current directory
    test_dir = Path(__file__).parent
    discovered = loader.discover(
        start_dir=str(test_dir),
        pattern="*_tests.py",
        top_level_dir=str(ROOT),
    )
    suite.addTests(discovered)

    # Run with verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
