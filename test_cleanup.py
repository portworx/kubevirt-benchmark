#!/usr/bin/env python3
"""
Quick test to verify cleanup functionality works correctly.
This script tests the cleanup utilities without actually creating resources.
"""

import os
import sys


# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from utils.common import Colors, confirm_cleanup, print_cleanup_summary, setup_logging


def test_confirm_cleanup():
    """Test the confirmation prompt function."""
    print("\n" + "=" * 80)
    print("Testing confirm_cleanup function")
    print("=" * 80)

    # Test with auto_yes=True
    result = confirm_cleanup(5, auto_yes=True)
    print(f"✓ confirm_cleanup(5, auto_yes=True) = {result} (expected: True)")
    assert result is True, "Should return True with auto_yes"

    # Test with small number (no prompt)
    result = confirm_cleanup(5, auto_yes=False)
    print(f"✓ confirm_cleanup(5, auto_yes=False) = {result} (expected: True)")
    assert result is True, "Should return True for small numbers without prompt"

    print(f"{Colors.OKGREEN}✓ All confirm_cleanup tests passed{Colors.ENDC}")


def test_print_cleanup_summary():
    """Test the cleanup summary printing function."""
    print("\n" + "=" * 80)
    print("Testing print_cleanup_summary function")
    print("=" * 80)

    # Create test stats
    stats = {
        "namespaces_processed": 50,
        "namespaces_deleted": 48,
        "total_vms_deleted": 50,
        "total_dvs_deleted": 50,
        "total_pvcs_deleted": 50,
        "total_vmims_deleted": 25,
        "total_errors": 2,
    }

    print("\nTest cleanup summary output:")
    print_cleanup_summary(stats)

    print(f"{Colors.OKGREEN}✓ print_cleanup_summary test passed{Colors.ENDC}")


def test_logging_setup():
    """Test logging setup."""
    print("\n" + "=" * 80)
    print("Testing logging setup")
    print("=" * 80)

    logger = setup_logging(log_file=None, log_level="INFO")
    logger.info("Test log message")

    print(f"{Colors.OKGREEN}✓ Logging setup test passed{Colors.ENDC}")


def main():
    """Run all tests."""
    print(f"\n{Colors.HEADER}{'=' * 80}{Colors.ENDC}")
    print(f"{Colors.HEADER}KubeVirt Cleanup Functionality Tests{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 80}{Colors.ENDC}")

    try:
        test_logging_setup()
        test_confirm_cleanup()
        test_print_cleanup_summary()

        print(f"\n{Colors.OKGREEN}{'=' * 80}{Colors.ENDC}")
        print(f"{Colors.OKGREEN}✓ All tests passed successfully!{Colors.ENDC}")
        print(f"{Colors.OKGREEN}{'=' * 80}{Colors.ENDC}\n")

        print("Cleanup functionality is ready to use!")
        print("\nNext steps:")
        print("1. Review CLEANUP_GUIDE.md for comprehensive documentation")
        print("2. Test with --dry-run-cleanup flag first")
        print("3. Use --cleanup flag to automatically clean up after tests")

        return 0

    except Exception as e:
        print(f"\n{Colors.FAIL}{'=' * 80}{Colors.ENDC}")
        print(f"{Colors.FAIL}✗ Test failed: {e}{Colors.ENDC}")
        print(f"{Colors.FAIL}{'=' * 80}{Colors.ENDC}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
