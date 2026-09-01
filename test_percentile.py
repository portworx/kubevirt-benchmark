#!/usr/bin/env python3
"""Tests for the nearest-rank percentile helper in utils.common."""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from utils.common import percentile, Colors


def test_edges_and_median():
    """p0 -> min, p100 -> max, p50 -> median (odd n)."""
    values = [1, 2, 3, 4, 5]
    assert percentile(values, 0) == 1, "p0 must be the minimum"
    assert percentile(values, 100) == 5, "p100 must be the maximum"
    assert percentile(values, 50) == 3, "p50 must be the median (odd n)"
    print(f"{Colors.OKGREEN}✓ p0/p100/median{Colors.ENDC}")


def test_high_percentiles():
    """p95/p99 on a 1..100 series by nearest rank."""
    values = list(range(1, 101))  # n=100, sorted 1..100
    assert percentile(values, 50) == 50, "p50 of 1..100"
    assert percentile(values, 95) == 95, "p95 of 1..100"
    assert percentile(values, 99) == 99, "p99 of 1..100"
    print(f"{Colors.OKGREEN}✓ p95/p99{Colors.ENDC}")


def test_unsorted_input():
    """The helper sorts internally, so input order must not matter."""
    assert percentile([5, 3, 1, 4, 2], 50) == 3, "unsorted input"
    assert percentile([5, 3, 1, 4, 2], 100) == 5, "unsorted input p100"
    print(f"{Colors.OKGREEN}✓ unsorted input{Colors.ENDC}")


def test_empty_series():
    """An empty series has no percentile."""
    assert percentile([], 50) is None, "empty -> None"
    print(f"{Colors.OKGREEN}✓ empty series{Colors.ENDC}")


def main():
    print(f"\n{Colors.HEADER}{'=' * 80}{Colors.ENDC}")
    print(f"{Colors.HEADER}Percentile helper tests{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 80}{Colors.ENDC}")
    try:
        test_edges_and_median()
        test_high_percentiles()
        test_unsorted_input()
        test_empty_series()
        print(f"\n{Colors.OKGREEN}✓ All percentile tests passed{Colors.ENDC}\n")
        return 0
    except AssertionError as e:
        print(f"\n{Colors.FAIL}✗ Test failed: {e}{Colors.ENDC}\n")
        return 1


if __name__ == '__main__':
    sys.exit(main())
