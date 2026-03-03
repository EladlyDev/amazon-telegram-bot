#!/usr/bin/env python3
"""Master test runner — executes all test suites and reports overall results.

Usage::

    python -m tests.run_all
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

# Test modules to run in order
TEST_MODULES = [
    ("Integration Tests", "tests.integration_test"),
    ("Security Tests", "tests.test_security"),
]


def run_module(label: str, module: str) -> tuple[bool, float]:
    """Run a test module as a subprocess and return (success, elapsed)."""
    print(f"\n{'═' * 60}")
    print(f"  ▶ {label}")
    print(f"{'═' * 60}")

    start = time.time()
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=120,
    )
    elapsed = time.time() - start
    return result.returncode == 0, elapsed


def main() -> None:
    results: list[tuple[str, bool, float]] = []

    for label, module in TEST_MODULES:
        try:
            success, elapsed = run_module(label, module)
        except subprocess.TimeoutExpired:
            print(f"\n  ⏰ {label} TIMED OUT (>120s)")
            success, elapsed = False, 120.0
        except Exception as exc:
            print(f"\n  ❌ {label} ERROR: {exc}")
            success, elapsed = False, 0.0
        results.append((label, success, elapsed))

    # ── Summary ────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print("  ALL TEST RESULTS")
    print(f"{'═' * 60}")

    total_time = 0.0
    all_passed = True
    for label, success, elapsed in results:
        icon = "✅" if success else "❌"
        print(f"  {icon} {label:<35} ({elapsed:.1f}s)")
        total_time += elapsed
        if not success:
            all_passed = False

    print(f"{'─' * 60}")
    overall = "✅ ALL PASSED" if all_passed else "❌ SOME FAILED"
    print(f"  {overall}  (total: {total_time:.1f}s)")
    print(f"{'═' * 60}\n")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
