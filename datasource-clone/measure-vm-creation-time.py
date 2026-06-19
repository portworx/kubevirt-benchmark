#!/usr/bin/env python3
"""
KubeVirt VM Creation Performance Test - DataSource Clone Method

This script measures VM creation and boot performance when cloning from
KubeVirt DataSource objects. Works with any CSI-compatible storage backend.

Usage:
    python3 measure-vm-creation-time.py --start 1 --end 100 --vm-name rhel-9-vm

Author: KubeVirt Benchmark Suite Contributors
License: Apache 2.0
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import yaml


# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.common import (
    add_node_selector_to_vm_yaml,
    cleanup_test_namespaces,
    confirm_cleanup,
    create_namespaces_parallel,
    get_vm_status,
    get_vmi_ip,
    ping_vm,
    print_cleanup_summary,
    print_summary_table,
    run_kubectl_command,
    save_results,
    select_random_node,
    setup_logging,
    start_vm,
    stop_vm,
    validate_prerequisites,
    wait_for_vm_stopped,
)


# Default configuration
DEFAULT_VM_YAML = "../examples/vm-templates/rhel9-vm-datasource.yaml"
DEFAULT_VM_NAME = "rhel-9-vm"
DEFAULT_SSH_POD = "ssh-test-pod"
DEFAULT_SSH_POD_NS = "default"
DEFAULT_POLL_INTERVAL = 1
DEFAULT_CONCURRENCY = 50
DEFAULT_PING_TIMEOUT = 600  # 10 minutes
DEFAULT_NAMESPACE_PREFIX = "kubevirt-perf-test"


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Measure KubeVirt VM creation and boot performance using DataSource clone method.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test 10 VMs with default settings
  %(prog)s --start 1 --end 10

  # Test 100 VMs with custom concurrency
  %(prog)s --start 1 --end 100 --concurrency 100

  # Test with custom VM template
  %(prog)s --start 1 --end 50 --vm-template my-vm.yaml

  # Test with cleanup after completion
  %(prog)s --start 1 --end 20 --cleanup
        """,
    )

    # Test range
    parser.add_argument("-s", "--start", type=int, default=1, help="Start index for test namespaces (default: 1)")
    parser.add_argument(
        "-e", "--end", type=int, default=10, help="End index for test namespaces, inclusive (default: 10)"
    )

    # VM configuration
    parser.add_argument(
        "-n", "--vm-name", type=str, default=DEFAULT_VM_NAME, help=f"VM resource name (default: {DEFAULT_VM_NAME})"
    )
    parser.add_argument(
        "--vm-template",
        type=str,
        default=DEFAULT_VM_YAML,
        help=f"Path to VM template YAML (default: {DEFAULT_VM_YAML})",
    )
    parser.add_argument("--secret-yaml", type=str, default=None, help="Path to cloudinit secret YAML file (optional)")
    parser.add_argument(
        "--namespace-prefix",
        type=str,
        default=DEFAULT_NAMESPACE_PREFIX,
        help=f"Prefix for test namespaces (default: {DEFAULT_NAMESPACE_PREFIX})",
    )

    # Performance tuning
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max parallel threads for monitoring (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Seconds between status checks (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--ping-timeout",
        type=int,
        default=DEFAULT_PING_TIMEOUT,
        help=f"Ping timeout in seconds (default: {DEFAULT_PING_TIMEOUT})",
    )

    # SSH pod for ping tests
    parser.add_argument(
        "--ssh-pod", type=str, default=DEFAULT_SSH_POD, help=f"Pod name for ping tests (default: {DEFAULT_SSH_POD})"
    )
    parser.add_argument(
        "--ssh-pod-ns",
        type=str,
        default=DEFAULT_SSH_POD_NS,
        help=f"Namespace of SSH pod (default: {DEFAULT_SSH_POD_NS})",
    )

    # Logging
    parser.add_argument("--log-file", type=str, help="Path to log file (default: stdout only)")
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    # Cleanup
    parser.add_argument("--cleanup", action="store_true", help="Delete test resources and namespaces after completion")
    parser.add_argument("--cleanup-on-failure", action="store_true", help="Clean up resources even if tests fail")
    parser.add_argument(
        "--dry-run-cleanup", action="store_true", help="Show what would be deleted without actually deleting"
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt for cleanup (use with caution)")
    parser.add_argument(
        "--skip-namespace-creation", action="store_true", help="Skip namespace creation (use existing namespaces)"
    )

    # Boot storm testing
    parser.add_argument(
        "--boot-storm",
        action="store_true",
        help="After initial test, shutdown all VMs and test boot storm (power on all together)",
    )
    parser.add_argument(
        "--skip-vm-creation",
        action="store_true",
        help="Skip VM creation phase (use with --boot-storm to test existing VMs)",
    )
    parser.add_argument(
        "--num-disks",
        type=int,
        default=None,
        help="Number of disks per VM (auto-detected from template or existing VM if not specified)",
    )
    parser.add_argument(
        "--namespace-batch-size", type=int, default=20, help="Number of namespaces to create in parallel (default: 20)"
    )

    # Single node testing
    parser.add_argument(
        "--single-node",
        action="store_true",
        help="Run all VMs on a single node (useful for node-level boot storm testing)",
    )
    parser.add_argument(
        "--node-name",
        type=str,
        default=None,
        help="Specific node name to use (if not provided, a random worker node will be selected)",
    )

    # Save results
    parser.add_argument(
        "--save-results",
        action="store_true",
        help="Save detailed results (JSON and CSV) inside a timestamped folder under results/.",
    )

    # Base folder for results
    parser.add_argument(
        "--results-folder", type=str, default="results", help="Base directory to store test results (default: results)"
    )

    parser.add_argument(
        "--storage-driver",
        dest="storage_driver",
        type=str,
        default=None,
        help="Storage driver label to include in results path (for example: portworx-3.6, ceph)",
    )

    args = parser.parse_args()

    # Validation
    if args.start < 1:
        parser.error("--start must be >= 1")
    if args.end < args.start:
        parser.error("--end must be >= --start")
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    if not os.path.exists(args.vm_template):
        parser.error(f"VM template file not found: {args.vm_template}")
    if args.secret_yaml and not os.path.exists(args.secret_yaml):
        parser.error(f"Secret YAML file not found: {args.secret_yaml}")

    return args


def detect_disk_count_from_template(vm_template_path: str) -> Optional[int]:
    """Return non-cloud-init disk count from a VM template, or None on failure."""
    with open(vm_template_path) as f:
        docs = list(yaml.safe_load_all(f))

    vm_spec = next((doc for doc in docs if doc and doc.get("kind") == "VirtualMachine"), None)
    if not vm_spec:
        return None

    volumes = vm_spec.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])
    non_cloudinit_volumes = [
        v for v in volumes if not any(k in v for k in ["cloudInitNoCloud", "cloudInitConfigDrive"])
    ]
    return len(non_cloudinit_volumes)


def build_results_dir(args, num_disks_per_vm: int, timestamp: Optional[str] = None) -> str:
    """Build the canonical results directory path for a datasource-clone run."""
    timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"{args.namespace_prefix}_{args.start}-{args.end}"
    disk_dir = f"{num_disks_per_vm}-disk" if num_disks_per_vm else "unknown-disk"
    if args.storage_driver:
        return os.path.join(args.results_folder, args.storage_driver, disk_dir, f"{timestamp}_{suffix}")
    return os.path.join(args.results_folder, disk_dir, f"{timestamp}_{suffix}")


def ensure_namespaces(start: int, end: int, prefix: str, batch_size: int, logger) -> List[str]:
    """
    Create test namespaces in parallel batches.

    Args:
        start: Start index
        end: End index
        prefix: Namespace prefix
        batch_size: Number of namespaces to create in parallel
        logger: Logger instance

    Returns:
        List of namespace names
    """
    logger.info(f"Creating namespaces {prefix}-{start} to {prefix}-{end} in batches of {batch_size}...")

    # Generate namespace names
    namespaces = [f"{prefix}-{i}" for i in range(start, end + 1)]

    # Create namespaces in parallel
    successful = create_namespaces_parallel(namespaces, batch_size, logger)

    if len(successful) != len(namespaces):
        failed = set(namespaces) - set(successful)
        logger.error(f"Failed to create {len(failed)} namespaces: {failed}")
        raise RuntimeError("Failed to create some namespaces")

    logger.info(f"All {len(namespaces)} namespaces ready")
    return namespaces


def create_secret(ns: str, secret_yaml: str, logger, max_retries: int = 3, initial_delay: float = 1.0) -> bool:
    """
    Create a Kubernetes secret in the specified namespace with retry logic.

    Args:
        ns: Namespace name
        secret_yaml: Path to secret YAML file
        logger: Logger instance
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay between retries in seconds (default: 1.0)

    Returns:
        True if secret created successfully, False otherwise
    """
    logger.info(f"[{ns}] Creating secret from {secret_yaml}")

    # List of retryable error patterns
    retryable_errors = [
        "context deadline exceeded",
        "webhook",
        "Internal error",
        "InternalError",
        "connection refused",
        "timeout",
        "temporarily unavailable",
    ]

    for attempt in range(1, max_retries + 1):
        try:
            returncode, stdout, stderr = run_kubectl_command(
                ["create", "-f", secret_yaml, "-n", ns], check=False, logger=logger
            )

            if returncode == 0:
                logger.info(f"[{ns}] Secret created successfully")
                return True
            else:
                if "AlreadyExists" in stderr:
                    logger.warning(f"[{ns}] Secret already exists, continuing")
                    return True

                # Check if it's a retryable error
                is_retryable = any(err in stderr for err in retryable_errors)

                if is_retryable and attempt < max_retries:
                    delay = initial_delay * (2 ** (attempt - 1))  # Exponential backoff
                    logger.warning(
                        f"[{ns}] Retryable error creating secret (attempt {attempt}/{max_retries}): {stderr.strip()}"
                    )
                    logger.info(f"[{ns}] Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"[{ns}] Failed to create secret: {stderr}")
                    return False

        except Exception as e:
            if attempt < max_retries:
                delay = initial_delay * (2 ** (attempt - 1))
                logger.warning(f"[{ns}] Exception creating secret (attempt {attempt}/{max_retries}): {e}")
                logger.info(f"[{ns}] Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                logger.error(f"[{ns}] Exception after {max_retries} attempts: {e}")
                return False

    return False


def create_vm(
    ns: str,
    vm_yaml: str,
    node_name: Optional[str],
    logger,
    secret_yaml: Optional[str] = None,
    max_retries: int = 5,
    initial_delay: float = 2.0,
) -> Tuple[str, datetime]:
    """
    Create a VM in the specified namespace with retry logic.

    Args:
        ns: Namespace name
        vm_yaml: Path to VM YAML file
        node_name: Optional node name to pin VM to
        logger: Logger instance
        secret_yaml: Optional path to secret YAML file to create before VM
        max_retries: Maximum number of retry attempts (default: 5)
        initial_delay: Initial delay between retries in seconds (default: 2.0)
                      Uses exponential backoff: delay * 2^attempt

    Returns:
        Tuple of (namespace, creation_timestamp)
    """
    # Create secret first if provided
    if secret_yaml:
        if not create_secret(ns, secret_yaml, logger):
            logger.error(f"[{ns}] Failed to create secret, aborting VM creation")
            raise RuntimeError(f"Failed to create secret in {ns}")

    logger.info(f"[{ns}] Creating VM from {vm_yaml}")
    start_ts = datetime.now()

    # List of retryable error patterns
    retryable_errors = [
        "context deadline exceeded",
        "webhook",
        "Internal error",
        "InternalError",
        "connection refused",
        "timeout",
        "temporarily unavailable",
    ]

    for attempt in range(1, max_retries + 1):
        try:
            # If node_name is specified, modify YAML to add nodeSelector
            if node_name:
                logger.debug(f"[{ns}] Adding nodeSelector for node: {node_name}")
                modified_yaml = add_node_selector_to_vm_yaml(vm_yaml, node_name, logger)

                if modified_yaml:
                    # Create VM using modified YAML via stdin
                    process = subprocess.Popen(
                        ["kubectl", "create", "-f", "-", "-n", ns],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    stdout, stderr = process.communicate(input=modified_yaml)
                    returncode = process.returncode
                else:
                    logger.warning(f"[{ns}] Failed to modify YAML, creating without nodeSelector")
                    returncode, stdout, stderr = run_kubectl_command(
                        ["create", "-f", vm_yaml, "-n", ns], check=False, logger=logger
                    )
            else:
                # Create VM normally without nodeSelector
                returncode, stdout, stderr = run_kubectl_command(
                    ["create", "-f", vm_yaml, "-n", ns], check=False, logger=logger
                )

            if returncode == 0:
                logger.info(f"[{ns}] VM creation API call completed")
                return ns, start_ts
            else:
                if "AlreadyExists" in stderr:
                    logger.warning(f"[{ns}] VM already exists, continuing with existing VM")
                    return ns, start_ts

                # Check if it's a retryable error
                is_retryable = any(err in stderr for err in retryable_errors)

                if is_retryable and attempt < max_retries:
                    delay = initial_delay * (2 ** (attempt - 1))  # Exponential backoff
                    logger.warning(f"[{ns}] Retryable error (attempt {attempt}/{max_retries}): {stderr.strip()}")
                    logger.info(f"[{ns}] Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    continue
                elif is_retryable:
                    logger.error(f"[{ns}] VM creation failed after {max_retries} attempts: {stderr}")
                    raise RuntimeError(f"Failed to create VM in {ns} after {max_retries} attempts: {stderr}")
                else:
                    # Non-retryable error
                    logger.error(f"[{ns}] VM creation failed: {stderr}")
                    raise RuntimeError(f"Failed to create VM in {ns}: {stderr}")

        except RuntimeError:
            raise
        except Exception as e:
            if attempt < max_retries:
                delay = initial_delay * (2 ** (attempt - 1))
                logger.warning(f"[{ns}] Exception (attempt {attempt}/{max_retries}): {e}")
                logger.info(f"[{ns}] Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                logger.error(f"[{ns}] Exception after {max_retries} attempts: {e}")
                raise

    # Should not reach here, but just in case
    raise RuntimeError(f"Failed to create VM in {ns} after {max_retries} attempts")


def get_vm_disk_count(ns: str, vm_name: str, logger) -> int:
    """
    Get the number of disks (excluding cloud-init) from an existing VM.

    Args:
        ns: Namespace
        vm_name: VM name
        logger: Logger instance

    Returns:
        Number of disks (excluding cloud-init volumes)
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "vm", vm_name, "-n", ns, "-o", "json"], capture_output=True, text=True, check=True
        )
        vm_spec = json.loads(result.stdout)

        # Get list of volumes under spec.template.spec.volumes
        volumes = vm_spec.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])

        # Exclude cloudInit volumes
        non_cloudinit_volumes = [
            v for v in volumes if not any(k in v for k in ["cloudInitNoCloud", "cloudInitConfigDrive"])
        ]

        disk_count = len(non_cloudinit_volumes)
        logger.info(f"[{ns}] Detected {disk_count} disks (excluding cloud-init) from existing VM")
        return disk_count

    except subprocess.CalledProcessError as e:
        logger.warning(f"[{ns}] Failed to get VM spec: {e.stderr}")
        return 1
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"[{ns}] Failed to parse VM spec: {e}")
        return 1


def wait_for_vm_running(ns: str, vm_name: str, start_ts: datetime, poll_interval: int, logger) -> Tuple[str, float]:
    """
    Wait for VM to reach Running state.

    Args:
        ns: Namespace
        vm_name: VM name
        start_ts: Creation timestamp
        poll_interval: Polling interval in seconds
        logger: Logger instance

    Returns:
        Tuple of (namespace, elapsed_seconds)
    """
    logger.info(f"[{ns}] Waiting for VM {vm_name} to reach Running state...")

    while True:
        status = get_vm_status(vm_name, ns, logger)

        if status == "Running":
            elapsed = (datetime.now() - start_ts).total_seconds()
            logger.info(f"[{ns}] VM Running after {elapsed:.2f}s")
            return ns, elapsed

        time.sleep(poll_interval)


def wait_for_vmi_ip(ns: str, vm_name: str, poll_interval: int, logger) -> str:
    """
    Wait for VMI to have an IP address.

    Args:
        ns: Namespace
        vm_name: VM name (same as VMI name)
        poll_interval: Polling interval in seconds
        logger: Logger instance

    Returns:
        IP address
    """
    logger.info(f"[{ns}] Waiting for VMI to get IP address...")

    while True:
        ip = get_vmi_ip(vm_name, ns, logger)

        if ip:
            logger.info(f"[{ns}] VMI IP: {ip}")
            return ip

        time.sleep(poll_interval)


def wait_for_ping(
    ns: str, ip: str, start_ts: datetime, ssh_pod: str, ssh_pod_ns: str, poll_interval: int, timeout: int, logger
) -> Tuple[str, float, bool]:
    """
    Wait for VM to respond to ping.

    Args:
        ns: Namespace
        ip: VM IP address
        start_ts: Creation timestamp
        ssh_pod: SSH pod name
        ssh_pod_ns: SSH pod namespace
        poll_interval: Polling interval in seconds
        timeout: Timeout in seconds
        logger: Logger instance

    Returns:
        Tuple of (namespace, elapsed_seconds, success)
    """
    logger.info(f"[{ns}] Pinging {ip} (timeout: {timeout}s)...")
    ping_start = datetime.now()

    while True:
        elapsed_ping = (datetime.now() - ping_start).total_seconds()

        if elapsed_ping > timeout:
            logger.warning(f"[{ns}] Ping timeout after {timeout}s")
            return ns, None, False

        if ping_vm(ip, ssh_pod, ssh_pod_ns, logger):
            elapsed_total = (datetime.now() - start_ts).total_seconds()
            logger.info(f"[{ns}] Ping successful after {elapsed_total:.2f}s")
            return ns, elapsed_total, True

        time.sleep(poll_interval)


def monitor_vm(
    ns: str,
    vm_name: str,
    start_ts: datetime,
    ssh_pod: str,
    ssh_pod_ns: str,
    poll_interval: int,
    ping_timeout: int,
    logger,
    skip_dv_clone_tracking=False,
    vm_template_path: Optional[str] = None,
) -> Tuple[str, float, float, float, bool]:
    """
    Monitor a single VM through its lifecycle and record clone timing.

    Args:
        ns: Namespace
        vm_name: VM name
        start_ts: Creation timestamp
        ssh_pod: SSH pod name
        ssh_pod_ns: SSH pod namespace
        poll_interval: Polling interval
        ping_timeout: Ping timeout
        logger: Logger instance
        skip_dv_clone_tracking: Flag to control DataVolume Clone
        vm_template_path: Path to VM template YAML (optional, for DV name extraction)
    Returns:
        Tuple of (namespace, running_time, ping_time, clone_duration, success)
    """
    try:
        # Track clone timing
        if not skip_dv_clone_tracking:
            clone_start, clone_end, clone_duration = track_clone_progress(
                ns, vm_name, start_ts, poll_interval, logger, vm_template_path=vm_template_path
            )
        else:
            clone_duration = None
        # Wait for VM to become Running
        _, running_time = wait_for_vm_running(ns, vm_name, start_ts, poll_interval, logger)

        # Wait for VMI IP
        ip = wait_for_vmi_ip(ns, vm_name, poll_interval, logger)

        # Wait until ping works
        _, ping_time, success = wait_for_ping(
            ns, ip, start_ts, ssh_pod, ssh_pod_ns, poll_interval, ping_timeout, logger
        )

        return ns, running_time, ping_time, clone_duration, success

    except Exception as e:
        logger.error(f"[{ns}] Error monitoring VM: {e}")
        return ns, None, None, None, False


def extract_datavolume_name_from_yaml(vm_template_path: str, logger) -> Optional[str]:
    """
    Extract the boot disk DataVolume name from the VM template YAML.

    Follows the reference chain:
    1. Find disk with bootOrder: 1 -> get its name (e.g., 'rootdisk')
    2. Find matching volume with that name -> get dataVolume.name (e.g., 'rhel-root-disk-1')

    Args:
        vm_template_path: Path to the VM template YAML file
        logger: Logger instance

    Returns:
        DataVolume name if found, None otherwise
    """
    try:
        with open(vm_template_path) as f:
            docs = list(yaml.safe_load_all(f))

        for doc in docs:
            if not doc:
                continue

            if doc.get("kind") == "VirtualMachine":
                template_spec = doc.get("spec", {}).get("template", {}).get("spec", {})

                # Step 1: Find disk with bootOrder: 1, or use first disk as fallback
                disks = template_spec.get("domain", {}).get("devices", {}).get("disks", [])
                boot_disk_name = None
                for disk in disks:
                    if disk.get("bootOrder") == 1:
                        boot_disk_name = disk.get("name")
                        break

                if not boot_disk_name and disks:
                    # Fallback to first disk if no bootOrder: 1 found
                    boot_disk_name = disks[0].get("name")
                    logger.debug(f"No bootOrder: 1 found, using first disk: {boot_disk_name}")

                if not boot_disk_name:
                    logger.debug("No disks found in template")
                    continue

                # Step 2: Find volume with matching name and get dataVolume.name or PVC claimName
                # Prefer dataVolume, fallback to persistentVolumeClaim
                volumes = template_spec.get("volumes", [])
                for volume in volumes:
                    if volume.get("name") == boot_disk_name:
                        if "dataVolume" in volume:
                            dv_name = volume.get("dataVolume", {}).get("name")
                            if dv_name:
                                logger.debug(f"Found boot disk DataVolume: {dv_name}")
                                return dv_name
                        elif "persistentVolumeClaim" in volume:
                            pvc_name = volume.get("persistentVolumeClaim", {}).get("claimName")
                            if pvc_name:
                                logger.debug(f"Found boot disk PVC: {pvc_name}")
                                return pvc_name

        logger.debug(f"No boot disk DataVolume found in template: {vm_template_path}")
        return None
    except Exception as e:
        logger.debug(f"Error parsing VM template YAML: {e}")
        return None


def track_clone_progress(
    ns: str,
    vm_name: str,
    start_ts: datetime,
    poll_interval: int,
    logger,
    vm_template_path: Optional[str] = None,
    timeout: int = 1800,
):
    """
    Track DataVolume/PVC clone timing for a given VM, including inferred clone start logic.

    Args:
        ns: Namespace
        vm_name: VM name (used to derive DV name)
        start_ts: Time when VM creation was initiated
        poll_interval: Poll interval in seconds
        logger: Logger instance
        vm_template_path: Path to VM template YAML (optional, for boot disk name extraction)
        timeout: Timeout in seconds

    Returns:
        Tuple (clone_start_time, clone_end_time, clone_duration_seconds)
        or (None, None, None) if not detected
    """

    # Try to get boot disk name from the VM template YAML
    dv_name = None
    if vm_template_path:
        dv_name = extract_datavolume_name_from_yaml(vm_template_path, logger)
        if dv_name:
            logger.info(f"[{ns}] Extracted boot disk name from template: {dv_name}")

    # Fallback to standard naming pattern if not found in template
    if not dv_name:
        dv_name = f"{vm_name}-volume"
        logger.debug(f"[{ns}] Using standard naming pattern: {dv_name}")

    # Check if it's a DataVolume or PVC
    # First try DataVolume, then fall back to PVC
    result = subprocess.run(
        ["kubectl", "get", "dv", dv_name, "-n", ns, "--no-headers"], capture_output=True, text=True, check=False
    )
    is_datavolume = result.returncode == 0

    if is_datavolume:
        logger.info(f"[{ns}] Tracking DataVolume clone progress for {dv_name}")
    else:
        logger.info(f"[{ns}] Tracking PVC clone progress for {dv_name}")

    clone_start = None
    clone_end = None
    clone_inferred = False
    elapsed = 0

    # Use appropriate resource type for kubectl commands
    resource_type = "dv" if is_datavolume else "pvc"

    while elapsed < timeout:
        try:
            result = subprocess.run(
                ["kubectl", "get", resource_type, dv_name, "-n", ns, "-o", "json"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0 or not result.stdout:
                time.sleep(poll_interval)
                elapsed = (datetime.now() - start_ts).total_seconds()
                continue

            data = json.loads(result.stdout)

            # Get phase - DataVolume uses status.phase, PVC uses annotations
            if is_datavolume:
                phase = data.get("status", {}).get("phase", "").lower()
            else:
                # For PVC, check if it's bound (clone complete)
                pvc_phase = data.get("status", {}).get("phase", "").lower()
                if pvc_phase == "bound":
                    phase = "succeeded"
                else:
                    phase = pvc_phase

            # CloneScheduled observed
            if phase == "clonescheduled" and not clone_start:
                clone_start = datetime.now()
                logger.info(
                    f"[{ns}] {dv_name} entered CloneScheduled ({(clone_start - start_ts).total_seconds():.2f}s after VM creation)"
                )

            # Clone in progress but no CloneScheduled observed (inferred)
            elif phase == "csicloneinprogress" and not clone_start:
                # Infer clone started shortly after VM creation
                current_time = datetime.now()
                elapsed_now = (current_time - start_ts).total_seconds()
                if elapsed_now > poll_interval:
                    # Infer it started one poll interval ago
                    clone_start = current_time - timedelta(seconds=poll_interval)
                else:
                    # Started right after VM creation
                    clone_start = start_ts
                clone_inferred = True
                clone_start_delta = (clone_start - start_ts).total_seconds()
                logger.info(
                    f"[{ns}] {dv_name} in CSICloneInProgress (inferred clone start: {clone_start_delta:.2f}s after VM creation)"
                )

            # Clone succeeded (DataVolume) or Bound (PVC)
            elif phase == "succeeded":
                clone_end = datetime.now()
                if not clone_start:
                    # Infer clone started one poll interval ago, but not before VM creation
                    inferred_start = clone_end - timedelta(seconds=poll_interval)
                    if inferred_start < start_ts:
                        clone_start = start_ts
                    else:
                        clone_start = inferred_start
                    clone_inferred = True
                    logger.info(
                        f"[{ns}] {dv_name} clone was fast (inferred clone start: {(clone_start - start_ts).total_seconds():.2f}s after VM creation)"
                    )
                logger.info(
                    f"[{ns}] {dv_name} clone succeeded ({(clone_end - start_ts).total_seconds():.2f}s after VM creation)"
                )
                break

            elif phase == "failed":
                logger.error(f"[{ns}] {dv_name} entered Failed state")
                return None, None, None

        except Exception as e:
            logger.error(f"[{ns}] Error tracking clone progress: {e}")
            return None, None, None

        time.sleep(poll_interval)
        elapsed = (datetime.now() - start_ts).total_seconds()

    if clone_start and clone_end:
        duration = round((clone_end - clone_start).total_seconds(), 2)
        inferred_text = " (inferred)" if clone_inferred else ""
        logger.info(f"[{ns}] {dv_name} CloneScheduled to Succeeded duration: {duration} seconds{inferred_text}")
        return clone_start, clone_end, duration
    else:
        logger.warning(f"[{ns}] Clone tracking incomplete or timed out")
        return None, None, None


def main():
    """Main execution function."""
    args = parse_args()
    args._results_dir = None
    args._precomputed_disk_count = None

    if args.save_results:
        if args.num_disks:
            args._precomputed_disk_count = args.num_disks
        else:
            try:
                args._precomputed_disk_count = detect_disk_count_from_template(args.vm_template)
            except Exception:
                args._precomputed_disk_count = None

        args._results_dir = build_results_dir(args, args._precomputed_disk_count or 0)
        os.makedirs(args._results_dir, exist_ok=True)

        if args.log_file:
            log_name = os.path.basename(args.log_file)
            args.log_file = os.path.join(args._results_dir, log_name)
        else:
            args.log_file = os.path.join(args._results_dir, "datasource-clone.log")

    # Setup logging
    logger = setup_logging(args.log_file, args.log_level)

    # Global variables for signal handler
    namespaces_created = []
    cleanup_on_interrupt = args.cleanup or args.cleanup_on_failure

    def signal_handler(signum, frame):
        """Handle Ctrl+C gracefully with optional cleanup."""
        logger.warning("\n\nInterrupt received (Ctrl+C)")
        if cleanup_on_interrupt and namespaces_created:
            logger.info("Cleaning up resources before exit...")
            try:
                stats = cleanup_test_namespaces(
                    namespace_prefix=args.namespace_prefix,
                    start=args.start,
                    end=args.end,
                    vm_name=args.vm_name,
                    delete_namespaces=True,
                    dry_run=False,
                    batch_size=args.namespace_batch_size,
                    logger=logger,
                )
                print_cleanup_summary(stats, logger)
            except Exception as e:
                logger.error(f"Error during interrupt cleanup: {e}")
        sys.exit(1)

    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)

    logger.info("=" * 80)
    logger.info("KubeVirt VM Creation Performance Test - DataSource Clone Method")
    logger.info("=" * 80)
    logger.info(f"Test range: {args.start} to {args.end} ({args.end - args.start + 1} VMs)")
    logger.info(f"Namespace prefix: {args.namespace_prefix}")
    logger.info(f"VM name: {args.vm_name}")
    logger.info(f"VM template: {args.vm_template}")
    logger.info(f"Concurrency: {args.concurrency}")
    logger.info(f"Poll interval: {args.poll_interval}s")
    logger.info(f"Ping timeout: {args.ping_timeout}s")
    logger.info("=" * 80)
    num_disks_per_vm = 1

    if args.storage_driver:
        logger.info(f"Using storage driver label: {args.storage_driver}")
    if args.save_results:
        logger.info(f"Results and log files will be saved under: {args._results_dir}")

    # Determine number of disks per VM
    if args.num_disks:
        # Use explicitly provided disk count
        num_disks_per_vm = args.num_disks
        logger.info(f"Using provided disk count: {num_disks_per_vm}")
    elif args._precomputed_disk_count:
        num_disks_per_vm = args._precomputed_disk_count
        logger.info(f"Detected {num_disks_per_vm} disks (excluding cloud-init) in VM template")
    elif not args.skip_vm_creation:
        # Parse VM template to get disk count
        try:
            num_disks_per_vm = detect_disk_count_from_template(args.vm_template)
            logger.info(f"Detected {num_disks_per_vm} disks (excluding cloud-init) in VM template")

        except Exception as e:
            logger.warning(f"Failed to parse {args.vm_template}: {e}")
            logger.info("Will attempt to detect disk count from existing VM later")
    else:
        # skip_vm_creation without num_disks - will detect from existing VM after namespaces are set
        logger.info("Disk count will be detected from existing VM")

    # Validate prerequisites
    if not validate_prerequisites(args.ssh_pod, args.ssh_pod_ns, logger):
        logger.error("Prerequisites validation failed")
        sys.exit(1)

    # Handle single-node testing
    target_node = None
    if args.single_node:
        logger.info("\n" + "=" * 80)
        logger.info("SINGLE NODE MODE ENABLED")
        logger.info("=" * 80)

        if args.node_name:
            # Use explicitly provided node
            target_node = args.node_name
            logger.info(f"Using explicitly specified node: {target_node}")
        else:
            # Select a random worker node
            logger.info("No node specified, selecting a random worker node...")
            target_node = select_random_node(logger)

            if not target_node:
                logger.error("Failed to select a node. Please specify --node-name explicitly.")
                sys.exit(1)

        logger.info(f"All VMs will be scheduled on node: {target_node}")
        logger.info("=" * 80)
    else:
        logger.info("Multi-node mode: VMs will be distributed across all available nodes")

    # Create namespaces
    if not args.skip_namespace_creation:
        try:
            namespaces = ensure_namespaces(
                args.start, args.end, args.namespace_prefix, args.namespace_batch_size, logger
            )
            namespaces_created.extend(namespaces)  # Track for cleanup on interrupt
        except Exception as e:
            logger.error(f"Failed to create namespaces: {e}")
            sys.exit(1)
    else:
        namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]
        logger.info(f"Using existing namespaces: {namespaces[0]} to {namespaces[-1]}")

    # Initialize variables for results
    results = []
    out_dir = None

    # Skip VM creation if requested (for boot-storm only tests)
    if args.skip_vm_creation:
        logger.info("\n" + "=" * 80)
        logger.info("SKIPPING VM CREATION (--skip-vm-creation)")
        logger.info("=" * 80)
        logger.info(f"Assuming {len(namespaces)} VMs already exist")

        if not args.boot_storm:
            logger.warning("--skip-vm-creation is typically used with --boot-storm")

        # Detect disk count from existing VM if not provided
        if not args.num_disks:
            first_ns = namespaces[0]
            logger.info(f"Detecting disk count from existing VM in {first_ns}...")
            num_disks_per_vm = get_vm_disk_count(first_ns, args.vm_name, logger)

        # Create output directory for results if saving
        if args.save_results:
            out_dir = args._results_dir
            logger.info(f"Using results directory: {out_dir}")
    else:
        # Phase 1: Create all VMs in parallel
        logger.info(f"\nPhase 1: Creating {len(namespaces)} VMs in parallel...")
        if target_node:
            logger.info(f"Target node: {target_node}")
        if args.secret_yaml:
            logger.info(f"Using secret YAML: {args.secret_yaml}")
        create_start = datetime.now()
        start_times = {}

        with ThreadPoolExecutor(max_workers=len(namespaces)) as executor:
            futures = {
                executor.submit(create_vm, ns, args.vm_template, target_node, logger, args.secret_yaml): ns
                for ns in namespaces
            }

            for future in as_completed(futures):
                try:
                    ns, ts = future.result()
                    start_times[ns] = ts
                except Exception as e:
                    ns = futures[future]
                    logger.error(f"[{ns}] Failed to create VM: {e}")

        create_elapsed = (datetime.now() - create_start).total_seconds()
        logger.info(f"Phase 1 completed in {create_elapsed:.2f}s")

        # Phase 2: Monitor VMs
        logger.info(f"\nPhase 2: Monitoring {len(start_times)} VMs (concurrency={args.concurrency})...")
        monitor_start = datetime.now()

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    monitor_vm,
                    ns,
                    args.vm_name,
                    ts,
                    args.ssh_pod,
                    args.ssh_pod_ns,
                    args.poll_interval,
                    args.ping_timeout,
                    logger,
                    False,
                    args.vm_template,
                ): ns
                for ns, ts in start_times.items()
            }

            for future in as_completed(futures):
                ns = futures[future]
                try:
                    result = future.result()  # now returns (ns, run_time, ping_time, clone_time, success)
                    results.append(result)
                except Exception as e:
                    logger.error(f"[{ns}] Monitoring failed: {e}")
                    results.append((ns, None, None, None, False))

        monitor_elapsed = (datetime.now() - monitor_start).total_seconds()
        total_elapsed = (datetime.now() - create_start).total_seconds()

        logger.info(f"Phase 2 completed in {monitor_elapsed:.2f}s")
        logger.info(f"Total test duration: {total_elapsed:.2f}s")

        # Print summary
        print_summary_table(results, "VM Creation Performance Test Results", logger=logger)

        # Save structured results if requested
        if args.save_results:
            out_dir = args._results_dir
            logger.info(f"Using results directory: {out_dir}")

            # Save initial creation test results
            save_results(
                args, results, base_dir=out_dir, prefix="vm_creation_results", logger=logger, total_time=total_elapsed
            )
            logger.info(f"Detailed and summary results saved under: {out_dir}")
        else:
            logger.info("VM Creation Performance Test Results not saved (use --save-results to enable).")

    # Boot storm testing if requested
    boot_storm_results = []
    if args.boot_storm:
        logger.info("\n" + "=" * 80)
        logger.info("BOOT STORM TEST - Shutdown and Power On All VMs")
        logger.info("=" * 80)

        # Phase 1: Stop all VMs
        logger.info("\nPhase 1: Stopping all VMs...")
        stop_start = datetime.now()

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            stop_futures = {executor.submit(stop_vm, args.vm_name, ns, logger): ns for ns in namespaces}

            for future in as_completed(stop_futures):
                ns = stop_futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"[{ns}] Failed to stop VM: {e}")

        stop_elapsed = (datetime.now() - stop_start).total_seconds()
        logger.info(f"Stop commands issued in {stop_elapsed:.2f}s")

        # Phase 2: Wait for all VMs to be fully stopped
        logger.info("\nPhase 2: Waiting for all VMs to be fully stopped...")
        wait_start = datetime.now()

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            wait_futures = {
                executor.submit(wait_for_vm_stopped, args.vm_name, ns, 300, logger): ns for ns in namespaces
            }

            stopped_count = 0
            for future in as_completed(wait_futures):
                ns = wait_futures[future]
                try:
                    if future.result():
                        stopped_count += 1
                        logger.debug(f"[{ns}] VM stopped ({stopped_count}/{len(namespaces)})")
                except Exception as e:
                    logger.error(f"[{ns}] Error waiting for VM to stop: {e}")

        wait_elapsed = (datetime.now() - wait_start).total_seconds()
        logger.info(f"All VMs stopped in {wait_elapsed:.2f}s")
        logger.info(f"Successfully stopped: {stopped_count}/{len(namespaces)} VMs")

        # Phase 3: Start all VMs simultaneously (BOOT STORM)
        logger.info("\nPhase 3: Starting all VMs simultaneously (BOOT STORM)...")
        boot_start = datetime.now()
        boot_start_times = {}

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            start_futures = {executor.submit(start_vm, args.vm_name, ns, logger): ns for ns in namespaces}

            for future in as_completed(start_futures):
                ns = start_futures[future]
                try:
                    future.result()
                    boot_start_times[ns] = datetime.now()
                except Exception as e:
                    logger.error(f"[{ns}] Failed to start VM: {e}")

        boot_issue_elapsed = (datetime.now() - boot_start).total_seconds()
        logger.info(f"All start commands issued in {boot_issue_elapsed:.2f}s")

        # Phase 4: Monitor boot storm - wait for Running and Ping
        logger.info(f"\nPhase 4: Monitoring boot storm (concurrency: {args.concurrency})...")
        monitor_start = datetime.now()

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            boot_futures = {
                executor.submit(
                    monitor_vm,
                    ns,
                    args.vm_name,
                    ts,
                    args.ssh_pod,
                    args.ssh_pod_ns,
                    args.poll_interval,
                    args.ping_timeout,
                    logger,
                    skip_dv_clone_tracking=True,
                    vm_template_path=args.vm_template,
                ): ns
                for ns, ts in boot_start_times.items()
            }

            for future in as_completed(boot_futures):
                try:
                    result = future.result()
                    boot_storm_results.append(result)
                except Exception as e:
                    ns = boot_futures[future]
                    logger.error(f"[{ns}] Boot storm monitoring failed: {e}")
                    boot_storm_results.append((ns, None, None, False))

        boot_monitor_elapsed = (datetime.now() - monitor_start).total_seconds()
        boot_total_elapsed = (datetime.now() - boot_start).total_seconds()

        logger.info(f"Boot storm monitoring completed in {boot_monitor_elapsed:.2f}s")
        logger.info(f"Total boot storm duration: {boot_total_elapsed:.2f}s")

        # Print boot storm summary
        print_summary_table(boot_storm_results, "Boot Storm Performance Test Results", skip_clone=True, logger=logger)
        if args.save_results:
            save_results(
                args,
                boot_storm_results,
                base_dir=out_dir,
                prefix="boot_storm_results",
                logger=logger,
                skip_clone=True,
                total_time=boot_total_elapsed,
            )

    failed_count = sum(1 for r in results if len(r) > 4 and not r[4]) if results else 0
    should_cleanup = args.cleanup or (args.cleanup_on_failure and failed_count > 0)

    # Cleanup if requested
    if should_cleanup or args.dry_run_cleanup:
        logger.info("\n" + "=" * 80)
        logger.info("CLEANUP")
        logger.info("=" * 80)

        # Confirm cleanup if needed
        if not args.dry_run_cleanup and not confirm_cleanup(len(namespaces), args.yes):
            logger.info("Cleanup cancelled by user")
        else:
            logger.info(f"\n{'[DRY RUN] ' if args.dry_run_cleanup else ''}Cleaning up test resources...")

            try:
                stats = cleanup_test_namespaces(
                    namespace_prefix=args.namespace_prefix,
                    start=args.start,
                    end=args.end,
                    vm_name=args.vm_name,
                    delete_namespaces=True,
                    dry_run=args.dry_run_cleanup,
                    batch_size=args.namespace_batch_size,
                    logger=logger,
                )

                print_cleanup_summary(stats, logger)

                if not args.dry_run_cleanup:
                    logger.info("Cleanup completed successfully!")

            except Exception as e:
                logger.error(f"Error during cleanup: {e}")
                logger.warning("Some resources may not have been cleaned up")

    logger.info("\nTest completed successfully!")

    # Exit with error code if any VMs failed
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
