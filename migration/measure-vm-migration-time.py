#!/usr/bin/env python3
"""
KubeVirt VM Live Migration Performance Test

This script measures VM live migration performance across different scenarios:
- Sequential migration (one by one)
- Parallel migration (multiple VMs simultaneously)
- Evacuation scenario (all VMs from one node)
- Round-robin migration (distribute across multiple nodes)
- Multi-source-node migration (VMs discovered directly from a list of nodes,
  interleaved across nodes so the load is spread evenly from the start)

Usage:
    # Sequential migration
    python3 measure-vm-migration-time.py --start 1 --end 10 --source-node worker-1 --target-node worker-2

    # Parallel migration
    python3 measure-vm-migration-time.py --start 1 --end 50 --source-node worker-1 --target-node worker-2 --parallel --concurrency 10

    # Evacuation scenario
    python3 measure-vm-migration-time.py --start 1 --end 100 --source-node worker-1 --evacuate

    # Round-robin migration
    python3 measure-vm-migration-time.py --start 1 --end 100 --round-robin

    # Multi-source-node migration (no range needed, VMs auto-discovered per node)
    python3 measure-vm-migration-time.py --source-nodes worker-1 worker-2 worker-3 --concurrency 20

    # Multi-source-node migration pinned to a single target node
    python3 measure-vm-migration-time.py --source-nodes worker-1 worker-2 --target-node worker-5 --concurrency 15

Author: KubeVirt Benchmark Suite Contributors
License: Apache 2.0
"""

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import yaml


# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.common import (
    add_node_selector_to_vm_yaml,
    cleanup_test_namespaces,
    confirm_cleanup,
    create_namespaces_parallel,
    delete_vmim,
    find_busiest_node,
    get_available_nodes,
    get_command_for_logging,
    get_vm_node,
    get_vm_status,
    get_vmi_ip,
    get_vms_on_node,
    get_worker_nodes,
    list_resources_in_namespace,
    migrate_vm,
    ping_vm,
    print_cleanup_summary,
    remove_node_selectors,
    run_kubectl_command,
    save_migration_results,
    select_random_node,
    setup_logging,
    validate_prerequisites,
    wait_for_migration_complete,
)


# Default configuration
DEFAULT_VM_NAME = "rhel-9-vm"
DEFAULT_NAMESPACE_PREFIX = "kubevirt-perf-test"
DEFAULT_VM_YAML = "../examples/vm-templates/rhel9-vm-datasource.yaml"


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Measure KubeVirt VM live migration performance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Sequential migration from node-1 to node-2
  python3 measure-vm-migration-time.py --start 1 --end 10 --source-node worker-1 --target-node worker-2

  # Parallel migration with 10 concurrent migrations
  python3 measure-vm-migration-time.py --start 1 --end 50 --source-node worker-1 --target-node worker-2 --parallel --concurrency 10

  # Evacuate all VMs from node-1
  python3 measure-vm-migration-time.py --start 1 --end 100 --source-node worker-1 --evacuate

  # Round-robin migration across all nodes
  python3 measure-vm-migration-time.py --start 1 --end 100 --round-robin

  # Create VMs first, then migrate
  python3 measure-vm-migration-time.py --start 1 --end 10 --create-vms --source-node worker-1 --target-node worker-2

  # Multi-source-node migration: discover VMs on listed nodes and migrate them
  # in an interleaved order (VM1 from node1, VM1 from node2, VM1 from node3,
  # VM2 from node1, VM2 from node2, ...). Useful when VM numbering may have
  # changed after multiple prior live migrations.
  python3 measure-vm-migration-time.py --source-nodes worker-1 worker-2 worker-3 --concurrency 20

  # Multi-source-node with all migrations pinned to a single target node
  python3 measure-vm-migration-time.py --source-nodes worker-1 worker-2 --target-node worker-5 --concurrency 15
        """,
    )

    # VM range
    parser.add_argument("-s", "--start", type=int, default=1, help="Start index for test namespaces (default: 1)")
    parser.add_argument("-e", "--end", type=int, default=10, help="End index for test namespaces (default: 10)")
    parser.add_argument(
        "-n", "--vm-name", type=str, default=DEFAULT_VM_NAME, help=f"VM name (default: {DEFAULT_VM_NAME})"
    )

    # Namespace configuration
    parser.add_argument(
        "--namespace-prefix",
        type=str,
        default=DEFAULT_NAMESPACE_PREFIX,
        help=f"Prefix for test namespaces (default: {DEFAULT_NAMESPACE_PREFIX})",
    )

    # VM creation
    parser.add_argument(
        "--create-vms", action="store_true", help="Create VMs before migration (default: use existing VMs)"
    )
    parser.add_argument(
        "--vm-template", type=str, default=DEFAULT_VM_YAML, help=f"VM template YAML file (default: {DEFAULT_VM_YAML})"
    )
    parser.add_argument(
        "--single-node", action="store_true", help="Create all VMs on a single node (requires --create-vms)"
    )
    parser.add_argument(
        "--node-name",
        type=str,
        default=None,
        help="Specific node to create VMs on (requires --single-node and --create-vms)",
    )

    # Migration scenarios
    parser.add_argument(
        "--source-node", type=str, default=None, help="Source node name (required for sequential/parallel/evacuate)"
    )
    parser.add_argument(
        "--source-nodes",
        type=str,
        nargs="+",
        default=None,
        help="List of source nodes whose VMs will all be migrated in parallel. "
        "Discovers VMs directly from each node via kubectl (no --start/--end "
        "range needed) and submits them concurrently to the thread pool in an "
        "interleaved order (VM1 from node1, VM1 from node2, ..., VM2 from node1, "
        'VM2 from node2, ...). Pass "all" as the sole value to target every '
        "worker node in the cluster.",
    )
    parser.add_argument(
        "--target-node", type=str, default=None, help="Target node name (optional, auto-select if not specified)"
    )
    parser.add_argument("--parallel", action="store_true", help="Migrate VMs in parallel (default: sequential)")
    parser.add_argument(
        "--evacuate", action="store_true", help="Evacuate all VMs from source node to any available nodes"
    )
    parser.add_argument(
        "--auto-select-busiest",
        action="store_true",
        help="Auto-select the node with most VMs for evacuation (requires --evacuate)",
    )
    parser.add_argument(
        "--round-robin", action="store_true", help="Migrate VMs in round-robin fashion across all nodes"
    )

    # Performance options
    parser.add_argument(
        "-c", "--concurrency", type=int, default=50, help="Number of concurrent migrations (default: 10)"
    )
    parser.add_argument("--poll-interval", type=int, default=2, help="Seconds between status checks (default: 5)")
    parser.add_argument(
        "--migration-timeout", type=int, default=600, help="Timeout for each migration in seconds (default: 600)"
    )
    parser.add_argument(
        "--vm-startup-timeout",
        type=int,
        default=3600,
        help="Timeout waiting for VMs to reach Running state in seconds (default: 3600 = 1 hour)",
    )
    parser.add_argument(
        "--max-migration-retries", type=int, default=3, help="Maximum retries for failed migrations (default: 3)"
    )

    # Validation options
    parser.add_argument(
        "--ssh-pod", type=str, default="ssh-test-pod", help="SSH test pod name for ping tests (default: ssh-test-pod)"
    )
    parser.add_argument("--ssh-pod-ns", type=str, default="default", help="SSH test pod namespace (default: default)")
    parser.add_argument(
        "--ping-timeout", type=int, default=3600, help="Timeout for ping validation in seconds (default: 3600 = 1 hour)"
    )
    parser.add_argument("--skip-ping", action="store_true", help="Skip ping validation after migration")

    # Logging options
    parser.add_argument("--log-file", type=str, default=None, help="Log file path (default: console only)")
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    # Cleanup options
    parser.add_argument("--cleanup", action="store_true", help="Delete VMs, VMIMs, and namespaces after test")
    parser.add_argument("--cleanup-on-failure", action="store_true", help="Clean up resources even if tests fail")
    parser.add_argument(
        "--dry-run-cleanup", action="store_true", help="Show what would be deleted without actually deleting"
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt for cleanup (use with caution)")
    parser.add_argument("--skip-checks", action="store_true", help="Skip VM verifications before migration")
    parser.add_argument(
        "--save-results", action="store_true", help="Save detailed migration results (JSON and CSV) under results/."
    )

    parser.add_argument(
        "--storage-driver", type=str, default=None, help="Storage driver to include in results path (optional)"
    )

    parser.add_argument(
        "--results-folder",
        type=str,
        default=os.path.join(os.path.dirname(os.getcwd()), "results"),
        help="Base directory to store test results (default: ../results)",
    )

    parser.add_argument(
        "--interleaved-scheduling",
        action="store_true",
        help="Distribute parallel migration threads in interleaved pattern across nodes. "
        "Instead of sequential (1,2,3,...), distributes VMs evenly across nodes first. "
        "Example: With 400 VMs and 5 nodes, processes VMs in order: 1,81,161,241,321,2,82,162,... "
        "This ensures even load distribution across all nodes from the start, preventing "
        "hotspots and improving overall migration performance.",
    )

    return parser.parse_args()


def validate_migration_args(args, logger):
    """Validate migration-specific arguments."""
    # Validate --single-node usage
    if args.single_node and not args.create_vms:
        logger.error("--single-node requires --create-vms")
        return False

    if args.node_name and not args.single_node:
        logger.error("--node-name requires --single-node")
        return False

    # --source-nodes: multi-node parallel evacuation (new scenario)
    if args.source_nodes:
        if args.source_node:
            logger.warning(
                "Both --source-node and --source-nodes specified; "
                "--source-nodes takes precedence for multi-node mode"
            )
        if args.evacuate or args.round_robin or args.parallel:
            logger.error("--source-nodes cannot be combined with --evacuate, --round-robin, or --parallel")
            return False
        logger.info(f"Multi-node evacuation mode: will migrate VMs from nodes: {args.source_nodes}")
        return True

    if args.round_robin:
        # Round-robin doesn't need source/target nodes
        logger.info("Round-robin mode: will distribute VMs across all available nodes")
        return True

    if args.evacuate:
        # Evacuation can use either --source-node or --auto-select-busiest
        if args.auto_select_busiest:
            logger.info("Evacuation mode: will auto-select busiest node")
            if args.source_node:
                logger.warning("Both --source-node and --auto-select-busiest specified, will use --source-node")
            return True
        elif args.source_node:
            logger.info(f"Evacuation mode: will migrate all VMs from {args.source_node}")
            return True
        else:
            logger.error("--evacuate requires either --source-node or --auto-select-busiest")
            return False

    # Check if --auto-select-busiest is used without --evacuate
    if args.auto_select_busiest and not args.evacuate:
        logger.error("--auto-select-busiest can only be used with --evacuate")
        return False

    return True


def create_vms_on_node(
    namespaces: List[str],
    vm_yaml: str,
    node_name: str,
    vm_name: str,
    logger,
    max_retries: int = 5,
    initial_delay: float = 2.0,
) -> Dict[str, bool]:
    """
    Create VMs on a specific node with retry logic.

    Args:
        namespaces: List of namespaces to create VMs in
        vm_yaml: Path to VM YAML template
        node_name: Node to create VMs on (can be None for no node selector)
        vm_name: VM resource name
        logger: Logger instance
        max_retries: Maximum number of retry attempts (default: 5)
        initial_delay: Initial delay between retries in seconds (default: 2.0)
                      Uses exponential backoff: delay * 2^attempt

    Returns:
        Dictionary mapping namespace to success status
    """
    if node_name:
        logger.info(f"\nCreating {len(namespaces)} VMs on node {node_name}...")
    else:
        logger.info(f"\nCreating {len(namespaces)} VMs (no node selector)...")

    import subprocess

    results = {}

    for ns in namespaces:
        success = False
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                # Modify VM YAML to add nodeSelector if node_name is specified
                if node_name:
                    modified_yaml = add_node_selector_to_vm_yaml(vm_yaml, node_name, logger)
                    if not modified_yaml:
                        logger.error(f"[{ns}] Failed to modify VM YAML")
                        last_error = "Failed to modify VM YAML"
                        break  # Don't retry YAML modification failures
                else:
                    # Read the YAML file directly without node selector
                    with open(vm_yaml) as f:
                        modified_yaml = f.read()

                # Create VM
                result = subprocess.run(
                    f"kubectl create -f - -n {ns}",
                    shell=True,
                    input=modified_yaml.encode(),
                    capture_output=True,
                    check=False,
                )

                if result.returncode == 0:
                    logger.info(f"[{ns}] VM created successfully")
                    success = True
                    break
                else:
                    error_msg = result.stderr.decode().strip()
                    last_error = error_msg

                    # Check if it's a retryable error (webhook timeout, internal error)
                    retryable_errors = [
                        "context deadline exceeded",
                        "webhook",
                        "Internal error",
                        "InternalError",
                        "connection refused",
                        "timeout",
                        "temporarily unavailable",
                    ]

                    is_retryable = any(err in error_msg for err in retryable_errors)

                    if is_retryable and attempt < max_retries:
                        delay = initial_delay * (2 ** (attempt - 1))  # Exponential backoff
                        logger.warning(f"[{ns}] Retryable error (attempt {attempt}/{max_retries}): {error_msg}")
                        logger.info(f"[{ns}] Retrying in {delay:.1f}s...")
                        time.sleep(delay)
                    elif is_retryable:
                        logger.error(f"[{ns}] Failed after {max_retries} attempts: {error_msg}")
                    else:
                        # Non-retryable error, fail immediately
                        logger.error(f"[{ns}] Failed to create VM: {error_msg}")
                        break

            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    delay = initial_delay * (2 ** (attempt - 1))
                    logger.warning(f"[{ns}] Exception (attempt {attempt}/{max_retries}): {e}")
                    logger.info(f"[{ns}] Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"[{ns}] Exception after {max_retries} attempts: {e}")

        results[ns] = success
        if not success and last_error:
            logger.debug(f"[{ns}] Final error: {last_error}")

    # Log summary
    successful = sum(1 for v in results.values() if v)
    failed = len(results) - successful
    logger.info(f"\nVM creation complete: {successful} successful, {failed} failed")

    return results


def wait_for_vms_running(
    namespaces: List[str], vm_name: str, timeout: int, logger, poll_interval: int = 10
) -> Dict[str, bool]:
    """
    Wait for all VMs to reach Running state with polling.

    Args:
        namespaces: List of namespaces containing VMs
        vm_name: VM resource name
        timeout: Maximum time to wait in seconds (default should be 3600 = 1 hour)
        logger: Logger instance
        poll_interval: Seconds between status checks (default: 10)

    Returns:
        Dictionary mapping namespace to success status (True if Running)
    """
    logger.info(f"\nWaiting for {len(namespaces)} VMs to reach Running state (timeout: {timeout}s)...")

    # Track which VMs are still pending
    pending = set(namespaces)
    results = {ns: False for ns in namespaces}
    start_time = time.time()

    while pending and (time.time() - start_time) < timeout:
        elapsed = time.time() - start_time

        # Check status of all pending VMs
        still_pending = set()
        for ns in pending:
            status = get_vm_status(vm_name, ns, logger)

            if status == "Running":
                logger.info(f"[{ns}] VM is now Running")
                results[ns] = True
            elif status in [
                "Provisioning",
                "Starting",
                "Stopped",
                "WaitingForVolumeBinding",
                "Scheduling",
                "Scheduled",
                "DataVolumeError",
                None,
            ]:
                # VM is still starting up - keep waiting
                still_pending.add(ns)
            else:
                # Unexpected status - could be an error
                logger.warning(f"[{ns}] VM has unexpected status: {status}")
                still_pending.add(ns)

        pending = still_pending

        if pending:
            running_count = len(namespaces) - len(pending)
            remaining_time = timeout - elapsed
            logger.info(
                f"VMs running: {running_count}/{len(namespaces)} | "
                f"Pending: {len(pending)} | "
                f"Elapsed: {elapsed:.0f}s | "
                f"Remaining: {remaining_time:.0f}s"
            )

            # Log which VMs are still pending (only first few to avoid spam)
            if len(pending) <= 5:
                for ns in pending:
                    status = get_vm_status(vm_name, ns, logger)
                    logger.debug(f"  [{ns}] status: {status}")

            time.sleep(poll_interval)

    # Final status check for any remaining pending VMs
    if pending:
        logger.warning(f"\nTimeout reached. {len(pending)} VMs did not reach Running state:")
        for ns in pending:
            status = get_vm_status(vm_name, ns, logger)
            logger.warning(f"  [{ns}] final status: {status}")
            results[ns] = False

    # Summary
    running_count = sum(1 for v in results.values() if v)
    logger.info(f"\nVM startup complete: {running_count}/{len(namespaces)} VMs running")

    return results


def migrate_vm_sequential(
    ns: str,
    vm_name: str,
    target_node: Optional[str],
    migration_timeout: int,
    logger,
    poll_interval: int = 2,
    max_vmim_retries: int = 10,
    max_migration_retries: int = 3,
    retry_delay: int = 2,
) -> Tuple[str, bool, float, Optional[str], Optional[str], Optional[float]]:
    """
    Migrate a single VM and measure time.

    Retries VMIM creation up to `max_vmim_retries` times if webhook/internal errors occur.
    Retries the entire migration up to `max_migration_retries` times if migration fails.
    """

    try:
        # Get source node
        source_node = get_vm_node(vm_name, ns, logger)
        if not source_node:
            logger.error(f"[{ns}] Could not determine source node for VM {vm_name}")
            return ns, False, 0.0, None, None, None

        logger.info(f"[{ns}] Starting migration from {source_node}")

        # Retry the entire migration process if it fails
        for migration_attempt in range(1, max_migration_retries + 1):
            vmim_name = f"migration-{vm_name}"

            # --- Retry VMIM creation only ---
            vmim_created = False
            for attempt in range(1, max_vmim_retries + 1):
                try:
                    if migrate_vm(vm_name, ns, target_node, logger):
                        vmim_created = True
                        break
                    else:
                        logger.warning(f"[{ns}] Failed to trigger migration (attempt {attempt}/{max_vmim_retries})")
                except Exception as e:
                    err_str = str(e)
                    logger.warning(f"[{ns}] Exception creating VMIM (attempt {attempt}/{max_vmim_retries}): {err_str}")

                # backoff before next retry
                if attempt < max_vmim_retries:
                    logger.info(f"[{ns}] Retrying VMIM creation in {retry_delay}s...")
                    time.sleep(retry_delay)

            if not vmim_created:
                logger.error(f"[{ns}] Failed to create VMIM after {max_vmim_retries} attempts")
                return ns, False, 0.0, source_node, None, None

            # Wait for migration to complete
            success, observed_duration, actual_target, vmim_duration = wait_for_migration_complete(
                vm_name, ns, migration_timeout, poll_interval, logger
            )

            if success:
                return ns, success, observed_duration, source_node, actual_target, vmim_duration

            # Migration failed - check if we should retry
            if migration_attempt < max_migration_retries:
                logger.warning(f"[{ns}] Migration failed (attempt {migration_attempt}/{max_migration_retries})")

                # Delete the failed VMIM before retrying
                logger.info(f"[{ns}] Deleting failed VMIM '{vmim_name}' before retry...")
                delete_vmim(vmim_name, ns, logger)

                # Wait a bit for cleanup
                time.sleep(retry_delay)

                # Update source node in case VM moved partially
                new_source = get_vm_node(vm_name, ns, logger)
                if new_source and new_source != source_node:
                    logger.info(f"[{ns}] VM is now on {new_source} (was {source_node})")
                    source_node = new_source

                logger.info(f"[{ns}] Retrying migration (attempt {migration_attempt + 1}/{max_migration_retries})...")
            else:
                logger.error(f"[{ns}] Migration failed after {max_migration_retries} attempts")
                return ns, False, observed_duration, source_node, None, None

        # Should not reach here, but just in case
        return ns, False, 0.0, source_node, None, None

    except Exception as e:
        logger.error(f"[{ns}] Exception during migration: {e}")
        return ns, False, 0.0, None, None, None


_ALL_VMIS_CACHE: dict = {}  # node-independent cache so we fetch only once per run


def _fetch_all_vmis(logger) -> list:
    """
    Fetch all VMIs across all namespaces once and cache the result.

    Uses ``kubectl get vmi -A -o json`` and returns the list of item dicts.
    ``spec.nodeName`` field selectors are not supported by the KubeVirt CRD,
    so we retrieve everything and filter in Python.
    """
    if "items" in _ALL_VMIS_CACHE:
        return _ALL_VMIS_CACHE["items"]

    returncode, stdout, stderr = run_kubectl_command(
        ["get", "vmi", "-A", "-o", "json"],
        check=False,
        logger=logger,
    )
    if returncode != 0:
        logger.error(f"Failed to list all VMIs: {stderr}")
        _ALL_VMIS_CACHE["items"] = []
        return []

    try:
        data = json.loads(stdout)
        items = data.get("items", [])
        _ALL_VMIS_CACHE["items"] = items
        logger.info(f"Fetched {len(items)} total VMI(s) from cluster.")
        return items
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse VMI JSON: {e}")
        _ALL_VMIS_CACHE["items"] = []
        return []


def discover_vms_on_node(node_name: str, vm_name: str, namespace_prefix: str, logger) -> List[str]:
    """
    Discover all namespaces that have a VMI named *vm_name* running on *node_name*.

    Fetches all VMIs cluster-wide (cached) and filters by ``.status.nodeName`` in
    Python — ``spec.nodeName`` is not a supported field selector for the KubeVirt
    VMI CRD.

    Returns a sorted list of namespace names whose VMI matches *vm_name* on
    *node_name*.
    """
    logger.info(
        f"Discovering VMIs named '{vm_name}' on node '{node_name}' "
        f"(prefix filter: '{namespace_prefix or '<none>'}')..."
    )
    try:
        all_items = _fetch_all_vmis(logger)
        namespaces: List[str] = []
        for item in all_items:
            meta = item.get("metadata", {})
            status = item.get("status", {})
            ns = meta.get("namespace", "")
            name = meta.get("name", "")
            node = status.get("nodeName", "")

            if name != vm_name:
                continue
            if node != node_name:
                continue
            if namespace_prefix and not ns.startswith(namespace_prefix):
                continue
            namespaces.append(ns)

        namespaces.sort()
        logger.info(f"  Found {len(namespaces)} VMI(s) on {node_name}")
        return namespaces

    except Exception as e:
        logger.error(f"Error discovering VMIs on node {node_name}: {e}")
        return []


def interleave_vms_across_nodes(per_node_vms: Dict[str, List[str]], node_order: List[str]) -> List[str]:
    """
    Interleave per-node VM lists into a single migration order.

    Order: VM1 from node1, VM1 from node2, VM1 from node3,
           VM2 from node1, VM2 from node2, VM2 from node3, ...

    Lists of unequal length are zipped using a round-robin walk so that no
    node is starved at the end. Duplicate namespaces (a VM may appear under
    more than one node if it migrated mid-discovery) are deduplicated while
    preserving first-seen order.
    """
    seen: set = set()
    ordered: List[str] = []
    max_len = max((len(per_node_vms[n]) for n in node_order), default=0)
    for i in range(max_len):
        for n in node_order:
            lst = per_node_vms.get(n, [])
            if i < len(lst):
                ns = lst[i]
                if ns not in seen:
                    seen.add(ns)
                    ordered.append(ns)
    return ordered


def build_results_dir(args, num_disks: int, timestamp: Optional[str] = None) -> str:
    """Build the canonical migration results directory."""
    timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.source_nodes:
        source_label = (
            "all-workers"
            if len(args.source_nodes) == 1 and args.source_nodes[0] == "all"
            else f"{len(args.source_nodes)}-source-nodes"
        )
        suffix = f"{args.namespace_prefix}_{source_label}"
    else:
        suffix = f"{args.namespace_prefix}_{args.start}-{args.end}"
    disk_dir = f"{num_disks}-disk"
    run_dir = f"{timestamp}_live_migration_{suffix}"
    if args.storage_driver:
        return os.path.join(args.results_folder, args.storage_driver, disk_dir, run_dir)
    return os.path.join(args.results_folder, disk_dir, run_dir)


def attach_file_logging(logger, log_file: str) -> None:
    """Attach file logging after the migration result directory is known."""
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.info(f"Logging to file: {log_file}")
    logger.info(f"Command: {get_command_for_logging()}")


def main():
    """Main function."""
    args = parse_arguments()

    # Setup logging
    logger = setup_logging(args.log_file, args.log_level)

    # Print configuration
    logger.info("=" * 80)
    logger.info("KubeVirt VM Live Migration Performance Test")
    logger.info("=" * 80)

    # Catch the common mistake of omitting a space before the line-continuation
    # backslash, e.g. "--source-nodes all\" -> "all--concurrency".
    if args.source_nodes:
        for bad in args.source_nodes:
            if bad.startswith("all-") or (
                bad != "all" and bad.lower().startswith("all") and not bad.startswith("all.")
            ):
                logger.error(
                    f"Unexpected --source-nodes value: '{bad}'. "
                    f"Did you forget a space before the backslash in your command? "
                    f"Use '--source-nodes all' (with a space before '\\') to target every node."
                )
                sys.exit(1)

    # Expand the magic value "all" into the full list of worker nodes.
    if args.source_nodes and len(args.source_nodes) == 1 and args.source_nodes[0].lower() == "all":
        logger.info("--source-nodes all: discovering every worker node in the cluster...")
        all_nodes = get_worker_nodes(logger)
        if not all_nodes:
            logger.error("No worker nodes found in the cluster.")
            sys.exit(1)
        args.source_nodes = all_nodes
        logger.info(f"Expanded 'all' to {len(args.source_nodes)} node(s): {', '.join(args.source_nodes)}")

    if not args.source_nodes:
        logger.info(f"VM range: {args.start} to {args.end}")
    logger.info(f"VM name: {args.vm_name}")
    logger.info(f"Namespace prefix: {args.namespace_prefix}")
    logger.info(f"Create VMs: {args.create_vms}")

    if args.storage_driver:
        logger.info(f"Using provided storage driver: {args.storage_driver}")

    if args.source_nodes:
        logger.info(f"Migration mode: Multi-node evacuation from {len(args.source_nodes)} nodes")
        logger.info(f"  Source nodes: {', '.join(args.source_nodes)}")
    elif args.round_robin:
        logger.info("Migration mode: Round-robin")
    elif args.evacuate:
        if args.auto_select_busiest:
            logger.info("Migration mode: Evacuation (auto-select busiest node)")
        else:
            logger.info(f"Migration mode: Evacuation from {args.source_node}")
    elif args.parallel:
        logger.info(f"Migration mode: Parallel (concurrency: {args.concurrency})")
    else:
        logger.info("Migration mode: Sequential")

    if args.source_node and not args.evacuate and not args.source_nodes:
        logger.info(f"Source node: {args.source_node}")
    if args.target_node:
        logger.info(f"Target node: {args.target_node}")

    logger.info("=" * 80)

    # Validate arguments
    if not validate_migration_args(args, logger):
        sys.exit(1)

    # Validate prerequisites (SSH pod for ping tests)
    if not args.skip_ping:
        if not validate_prerequisites(args.ssh_pod, args.ssh_pod_ns, logger):
            logger.warning("SSH pod not available, will skip ping tests")
            args.skip_ping = True

    # Prepare namespaces
    if args.source_nodes:
        # Namespaces are discovered per-node in Scenario 5; nothing to build here.
        namespaces: List[str] = []
        logger.info("\nNamespace discovery will be performed per source node.")
    else:
        namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]
        logger.info(f"\nTarget namespaces: {namespaces[0]} to {namespaces[-1]} ({len(namespaces)} total)")

    # Phase 1: Create VMs if requested
    if args.create_vms:
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 1: Creating VMs")
        logger.info("=" * 80)

        # Determine node for VM creation
        creation_node = None

        if args.single_node:
            # Single-node mode: create all VMs on one node
            if args.node_name:
                creation_node = args.node_name
                logger.info(f"Single-node mode: Creating all VMs on {creation_node}")
            else:
                # Auto-select a node
                creation_node = select_random_node(logger)
                if not creation_node:
                    logger.error("Failed to select a node for single-node mode")
                    sys.exit(1)
                logger.info(f"Single-node mode: Auto-selected node {creation_node}")
        elif args.source_node:
            creation_node = args.source_node
            logger.info(f"Creating VMs on source node: {creation_node}")
        elif args.round_robin:
            # For round-robin, create VMs distributed across nodes
            creation_node = None
            logger.info("Round-robin mode: VMs will be created across all nodes")
        else:
            # Auto-select a source node
            creation_node = select_random_node(logger)
            if not creation_node:
                logger.error("Failed to select a source node")
                sys.exit(1)
            logger.info(f"Auto-selected source node: {creation_node}")

        # Create namespaces
        logger.info(f"\nCreating {len(namespaces)} namespaces...")
        successful_ns = create_namespaces_parallel(namespaces, 20, logger)

        if len(successful_ns) < len(namespaces):
            logger.error(f"Failed to create all namespaces. Created: {len(successful_ns)}/{len(namespaces)}")
            sys.exit(1)

        # Create VMs
        if creation_node:
            create_vms_on_node(namespaces, args.vm_template, creation_node, args.vm_name, logger)
        else:
            # For round-robin, create VMs without node selector
            logger.info("Creating VMs without node selector (will be distributed)")
            create_vms_on_node(namespaces, args.vm_template, None, args.vm_name, logger)

        # Wait for VMs to be running (default: 1 hour timeout)
        logger.info("\nWaiting for VMs to reach Running state...")
        running_results = wait_for_vms_running(
            namespaces, args.vm_name, args.vm_startup_timeout, logger, poll_interval=args.poll_interval
        )

        successful_vms = sum(1 for success in running_results.values() if success)

        if successful_vms == 0:
            logger.error("No VMs are running. Cannot proceed with migration.")
            sys.exit(1)
        elif successful_vms < len(namespaces):
            logger.warning(f"Only {successful_vms}/{len(namespaces)} VMs are running. Proceeding with available VMs.")

        # Remove nodeSelectors to allow migration
        if creation_node:
            logger.info("\n" + "=" * 80)
            logger.info("REMOVING NODE SELECTORS FOR MIGRATION")
            logger.info("=" * 80)
            logger.info(f"\nVMs were created with nodeSelector on {creation_node}")
            logger.info("Removing nodeSelector from VM and VMI objects to allow live migration...")

            removal_success = 0
            removal_failed = 0

            for ns in namespaces:
                if remove_node_selectors(args.vm_name, ns, logger):
                    removal_success += 1
                    logger.info(f"[{ns}] Removed nodeSelector")
                else:
                    removal_failed += 1
                    logger.warning(f"[{ns}] Failed to remove nodeSelector")

            logger.info(f"\nNodeSelector removal: {removal_success} successful, {removal_failed} failed")

            if removal_success == 0:
                logger.error("Failed to remove nodeSelectors. VMs cannot be migrated.")
                sys.exit(1)

            logger.info("VMs are now ready for live migration!")
            logger.info("=" * 80)

    # Phase 2: Verify VMs exist and are running
    else:
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 1: Verifying Existing VMs")
        logger.info("=" * 80)
        if args.source_nodes:
            # Namespace discovery hasn't run yet — VMs will be verified during
            # the per-node discover_vms_on_node() calls in Scenario 5.
            logger.info(
                "Skipping pre-flight VM check for --source-nodes mode; "
                "VMs will be discovered per node during migration."
            )
        elif not args.skip_checks:
            logger.info(f"\nChecking {len(namespaces)} VMs...")
            running_count = 0

            for ns in namespaces:
                status = get_vm_status(args.vm_name, ns, logger)
                if status == "Running":
                    running_count += 1
                else:
                    logger.warning(f"[{ns}] VM not running (status: {status})")

            logger.info(f"\nFound {running_count}/{len(namespaces)} running VMs")

            if running_count == 0:
                logger.error("No running VMs found. Use --create-vms to create VMs first.")
                sys.exit(1)

            # Check if VMs have nodeSelectors and remove them
            logger.info("\n" + "=" * 80)
            logger.info("CHECKING FOR NODE SELECTORS")
            logger.info("=" * 80)
            logger.info("Checking if VMs have nodeSelector that would prevent migration...")

            # Remove nodeSelectors from all VMs to ensure they can be migrated
            removal_success = 0
            removal_failed = 0

            for ns in namespaces:
                if remove_node_selectors(args.vm_name, ns, logger):
                    removal_success += 1
                else:
                    removal_failed += 1

            if removal_success > 0:
                logger.info(f"\nRemoved nodeSelector from {removal_success} VMs")
            if removal_failed > 0:
                logger.warning(f"Failed to remove nodeSelector from {removal_failed} VMs")
        else:
            logger.info("Skipping VM Verifications...")
        logger.info("VMs are ready for live migration!")
        logger.info("=" * 80)

    logger.info("Detecting disk count from existing VM spec...")
    try:
        # For --source-nodes mode `namespaces` is empty here; pick any namespace
        # that currently exists by probing the first source node.
        if args.source_nodes:
            _probe_ns_list = discover_vms_on_node(args.source_nodes[0], args.vm_name, args.namespace_prefix, logger)
            sample_ns = _probe_ns_list[0] if _probe_ns_list else None
        else:
            sample_ns = f"{args.namespace_prefix}-{args.start}"

        if not sample_ns:
            logger.warning("No sample namespace available for disk detection; defaulting to 1 disk")
            num_disks = 1
        else:
            vm_yaml_cmd = ["kubectl", "get", "vm", args.vm_name, "-n", sample_ns, "-o", "yaml"]
            result = subprocess.run(vm_yaml_cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0 and result.stdout:
                vm_spec = yaml.safe_load(result.stdout)
                volumes = vm_spec.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])
                non_cloudinit = [
                    v for v in volumes if not any(k in v for k in ["cloudInitNoCloud", "cloudInitConfigDrive"])
                ]
                num_disks = len(non_cloudinit)
                logger.info(f"Detected {num_disks} disks (excluding cloud-init volumes)")
            else:
                logger.warning("Could not retrieve VM spec; defaulting to 1 disk")
                num_disks = 1
    except Exception as e:
        logger.error(f"Error detecting disks: {e}")
        num_disks = 1

    out_dir = None
    if args.save_results:
        out_dir = build_results_dir(args, num_disks)
        os.makedirs(out_dir, exist_ok=True)
        logger.info(f"Results and log files will be saved under: {out_dir}")
        if not args.log_file:
            attach_file_logging(logger, os.path.join(out_dir, "migration.log"))

    # Phase 2: Perform Migration
    logger.info("\n" + "=" * 80)
    logger.info("PHASE 2: Live Migration")
    logger.info("=" * 80)

    migration_results = []
    migration_phase_start = datetime.now()

    # Scenario 1: Sequential Migration
    if not args.parallel and not args.evacuate and not args.round_robin and not args.source_nodes:
        logger.info(
            f"\nSequential migration from {args.source_node or 'auto-selected node'} to {args.target_node or 'auto-selected node'}"
        )

        for ns in namespaces:
            result = migrate_vm_sequential(
                ns,
                args.vm_name,
                args.target_node,
                args.migration_timeout,
                logger,
                poll_interval=args.poll_interval,
                max_migration_retries=args.max_migration_retries,
            )
            migration_results.append(result)

            # Small delay between migrations
            time.sleep(1)

    # Scenario 2: Parallel Migration
    elif args.parallel and not args.evacuate and not args.round_robin and not args.source_nodes:
        logger.info(
            f"\nParallel migration from {args.source_node or 'auto-selected node'} "
            f"to {args.target_node or 'auto-selected node'}"
        )
        logger.info(f"Concurrency: {args.concurrency}")

        # Detect available nodes
        available_nodes = get_worker_nodes(logger)
        num_nodes = len(available_nodes) if available_nodes else 1
        logger.info(f"Found {num_nodes} worker nodes: {', '.join(available_nodes) if available_nodes else 'N/A'}")

        # Default: sequential namespace order
        reordered_namespaces = namespaces

        # --- Interleaved scheduling ---
        if args.interleaved_scheduling:
            total_namespaces = len(namespaces)
            group_size = total_namespaces // num_nodes or 1

            reordered_namespaces = []
            for offset in range(group_size):
                for i in range(offset, total_namespaces, group_size):
                    reordered_namespaces.append(namespaces[i])

            logger.info(f"Detected {num_nodes} available nodes for interleaved scheduling")
            logger.info(
                f"Reordered namespaces for interleaved scheduling (stride={group_size}). "
                f"First 10: {reordered_namespaces[:10]}"
            )
        else:
            logger.info("Using default sequential namespace order for parallel scheduling")

        # --- Parallel migration execution ---
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    migrate_vm_sequential,
                    ns,
                    args.vm_name,
                    args.target_node,
                    args.migration_timeout,
                    logger,
                    args.poll_interval,
                    10,  # max_vmim_retries
                    args.max_migration_retries,
                ): ns
                for ns in reordered_namespaces
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    migration_results.append(result)
                except Exception as e:
                    ns = futures[future]
                    logger.error(f"[{ns}] Exception during migration: {e}")
                    migration_results.append((ns, False, 0.0, None, None, None))

    # Scenario 3: Evacuation
    elif args.evacuate:
        # Determine source node
        if args.auto_select_busiest and not args.source_node:
            logger.info("\n" + "=" * 80)
            logger.info("AUTO-SELECTING BUSIEST NODE")
            logger.info("=" * 80)

            source_node = find_busiest_node(namespaces, args.vm_name, logger)

            if not source_node:
                logger.error("Could not find any VMs to determine busiest node")
                sys.exit(1)

            logger.info(f"\nSelected source node for evacuation: {source_node}")
            logger.info("=" * 80)
        else:
            source_node = args.source_node

        logger.info(f"\nEvacuation: migrating all VMs from {source_node}")
        logger.info(f"Concurrency: {args.concurrency}")

        # Find VMs actually running on the source node
        logger.info("\n" + "=" * 80)
        logger.info("IDENTIFYING VMs ON SOURCE NODE")
        logger.info("=" * 80)

        vms_to_evacuate = get_vms_on_node(namespaces, args.vm_name, source_node, logger)

        if not vms_to_evacuate:
            logger.error(f"No VMs found on {source_node} within the specified namespace range")
            logger.info(f"Checked namespaces: {namespaces[0]} to {namespaces[-1]}")
            sys.exit(1)

        logger.info(f"\nVMs to evacuate from {source_node}:")
        for ns in vms_to_evacuate:
            logger.info(f"  - {ns}")

        # Get available target nodes (excluding source)
        available_nodes = get_available_nodes([source_node], logger)

        if not available_nodes:
            logger.error(f"No available nodes to evacuate to (excluding {source_node})")
            sys.exit(1)

        logger.info(f"\nAvailable target nodes: {available_nodes}")
        logger.info("=" * 80)

        # Migrate only the VMs that are on the source node
        logger.info(f"\nStarting evacuation of {len(vms_to_evacuate)} VMs...")

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    migrate_vm_sequential,
                    ns,
                    args.vm_name,
                    None,
                    args.migration_timeout,
                    logger,
                    args.poll_interval,
                    10,
                    args.max_migration_retries,
                ): ns
                for ns in vms_to_evacuate  # Only migrate VMs on source node
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    migration_results.append(result)
                except Exception as e:
                    ns = futures[future]
                    logger.error(f"[{ns}] Exception during migration: {e}")
                    migration_results.append((ns, False, 0.0, None, None, None))

    # Scenario 4: Round-Robin
    elif args.round_robin:
        logger.info("\nRound-robin migration across all nodes")
        logger.info(f"Concurrency: {args.concurrency}")

        # Get all worker nodes
        all_nodes = get_worker_nodes(logger)

        if len(all_nodes) < 2:
            logger.error("Need at least 2 nodes for round-robin migration")
            sys.exit(1)

        logger.info(f"Available nodes: {all_nodes}")

        # For each VM, select a target node different from current node
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {}

            for ns in namespaces:
                # Get current node
                current_node = get_vm_node(args.vm_name, ns, logger)

                if current_node:
                    # Select a different node
                    available = [n for n in all_nodes if n != current_node]
                    target = random.choice(available) if available else None
                else:
                    target = None

                future = executor.submit(
                    migrate_vm_sequential,
                    ns,
                    args.vm_name,
                    target,
                    args.migration_timeout,
                    logger,
                    args.poll_interval,
                    10,
                    args.max_migration_retries,
                )
                futures[future] = ns

            for future in as_completed(futures):
                try:
                    result = future.result()
                    migration_results.append(result)
                except Exception as e:
                    ns = futures[future]
                    logger.error(f"[{ns}] Exception during migration: {e}")
                    migration_results.append((ns, False, 0.0, None, None, None))

    # Scenario 5: Multi-source-node parallel migration (interleaved across nodes)
    elif args.source_nodes:
        logger.info("\n" + "=" * 80)
        logger.info("IDENTIFYING VMs ON SOURCE NODES")
        logger.info("=" * 80)
        logger.info(f"Collecting VMs from {len(args.source_nodes)} source node(s): " f"{', '.join(args.source_nodes)}")

        # Discover VMIs directly from each node — no namespace range required.
        per_node_vms: Dict[str, List[str]] = {}
        for source_node in args.source_nodes:
            vms_on_node = discover_vms_on_node(source_node, args.vm_name, args.namespace_prefix, logger)
            per_node_vms[source_node] = vms_on_node
            if vms_on_node:
                logger.info(f"  {source_node}: {len(vms_on_node)} VM(s) found")
            else:
                logger.warning(f"  {source_node}: no VMs found (check node name and namespace prefix)")

        # Interleave across nodes so the migration order is:
        # VM1 from node1, VM1 from node2, VM1 from node3, VM2 from node1, ...
        all_vms_to_migrate = interleave_vms_across_nodes(per_node_vms, args.source_nodes)

        if not all_vms_to_migrate:
            logger.error(
                "No VMs found on any of the specified source nodes. " "Check node names and --namespace-prefix."
            )
            sys.exit(1)

        logger.info(f"\nTotal unique VMs to migrate: {len(all_vms_to_migrate)}")
        logger.info(f"Interleaved migration order (first 10): {all_vms_to_migrate[:10]}")
        logger.info(f"Target node: {args.target_node or '(auto-selected per VM)'}")
        logger.info(f"Concurrency: {args.concurrency}")
        logger.info("=" * 80)

        logger.info("\n" + "=" * 80)
        logger.info("REMOVING NODE SELECTORS FOR MIGRATION")
        logger.info("=" * 80)
        logger.info("Removing nodeSelector from discovered VMs to allow live migration...")

        removal_success = 0
        removal_failed = 0

        for ns in all_vms_to_migrate:
            if remove_node_selectors(args.vm_name, ns, logger):
                removal_success += 1
            else:
                removal_failed += 1
                logger.warning(f"[{ns}] Failed to remove nodeSelector")

        logger.info(f"\nNodeSelector removal: {removal_success} successful, {removal_failed} failed")

        if removal_success != len(all_vms_to_migrate):
            logger.error(
                "Failed to remove nodeSelectors from all discovered VMs. "
                "Aborting before migration so target pods do not get stuck unschedulable."
            )
            sys.exit(1)

        # Determine available target nodes.
        # When a specific --target-node was given, pin to that node.
        # Otherwise try to exclude source nodes so KubeVirt does not land a
        # migrated VM back on a node being drained. If every worker is a
        # source node (e.g. --source-nodes all) there are no non-source nodes,
        # so we fall back to allowing all workers and rely on KubeVirt's own
        # scheduler to avoid migrating a VM to its current node.
        if args.target_node:
            logger.info(f"Pinning all migrations to target node: {args.target_node}")
        else:
            available_targets = get_available_nodes(args.source_nodes, logger)
            if available_targets:
                logger.info(f"Available target nodes (excluding sources): {available_targets}")
            else:
                available_targets = get_available_nodes([], logger)
                logger.warning(
                    "All worker nodes are listed as source nodes — no non-source nodes "
                    "available as targets. Falling back to all worker nodes as potential "
                    "targets; KubeVirt will avoid migrating each VM back to its current node."
                )
                logger.info(f"Effective target pool: {available_targets}")

        logger.info(f"\nStarting parallel migration of {len(all_vms_to_migrate)} VMs...")

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    migrate_vm_sequential,
                    ns,
                    args.vm_name,
                    args.target_node,  # None -> KubeVirt auto-selects from available nodes
                    args.migration_timeout,
                    logger,
                    args.poll_interval,
                    10,  # max_vmim_retries
                    args.max_migration_retries,
                ): ns
                for ns in all_vms_to_migrate
            }

            completed = 0
            for future in as_completed(futures):
                ns = futures[future]
                try:
                    result = future.result()
                    migration_results.append(result)
                    completed += 1
                    _, success, duration, src, tgt, _ = result
                    status_str = "✓" if success else "✗"
                    if success:
                        logger.info(
                            f"[{completed}/{len(all_vms_to_migrate)}] {status_str} "
                            f"{ns}: {src} → {tgt or 'unknown'} ({duration:.1f}s)"
                        )
                    else:
                        logger.info(f"[{completed}/{len(all_vms_to_migrate)}] {status_str} {ns}: FAILED")
                except Exception as e:
                    completed += 1
                    logger.error(f"[{ns}] Exception during migration: {e}")
                    migration_results.append((ns, False, 0.0, None, None, None))

        # Expose discovered namespaces to the ping / cleanup phases below.
        namespaces = all_vms_to_migrate

    total_migration_time = (datetime.now() - migration_phase_start).total_seconds()
    # Phase 4: Validation (Ping Test)
    if not args.skip_ping:
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 3: Network Validation")
        logger.info("=" * 80)

        logger.info(f"\nTesting network connectivity for {len(namespaces)} VMs...")
        logger.info(f"Timeout: {args.ping_timeout}s (will poll until all VMs respond or timeout)")

        # Track which VMs still need ping validation
        pending = set(namespaces)
        ping_results = {ns: False for ns in namespaces}
        vm_ips = {}  # Cache VM IPs
        start_time = time.time()
        poll_interval = 10  # Check every 10 seconds

        while pending and (time.time() - start_time) < args.ping_timeout:
            elapsed = time.time() - start_time
            still_pending = set()

            for ns in pending:
                # Get VM IP (may not be available immediately after migration)
                if ns not in vm_ips or vm_ips[ns] is None:
                    vm_ips[ns] = get_vmi_ip(args.vm_name, ns, logger)

                vm_ip = vm_ips[ns]
                if vm_ip:
                    ping_success = ping_vm(vm_ip, args.ssh_pod, args.ssh_pod_ns, logger)
                    if ping_success:
                        logger.info(f"[{ns}] Ping successful to {vm_ip}")
                        ping_results[ns] = True
                    else:
                        # Keep trying
                        still_pending.add(ns)
                else:
                    # No IP yet, keep trying
                    still_pending.add(ns)

            pending = still_pending

            if pending:
                successful_count = sum(1 for v in ping_results.values() if v)
                remaining_time = args.ping_timeout - elapsed
                logger.info(
                    f"Ping status: {successful_count}/{len(namespaces)} successful | "
                    f"Pending: {len(pending)} | "
                    f"Elapsed: {elapsed:.0f}s | "
                    f"Remaining: {remaining_time:.0f}s"
                )
                time.sleep(poll_interval)

        # Final status for any remaining pending VMs
        if pending:
            logger.warning(f"\nTimeout reached. {len(pending)} VMs did not respond to ping:")
            for ns in pending:
                vm_ip = vm_ips.get(ns, "No IP")
                logger.warning(f"  [{ns}] IP: {vm_ip}")

        successful_pings = sum(1 for success in ping_results.values() if success)
        logger.info(f"\nNetwork validation complete: {successful_pings}/{len(namespaces)} VMs reachable")

    # Phase 5: Display Results
    logger.info("\n" + "=" * 80)
    logger.info("MIGRATION RESULTS")
    logger.info("=" * 80)

    # Prepare results table
    table_data = []
    for ns, success, observed_duration, source, target, vmim_duration in migration_results:
        status = "Success" if success else "Failed"
        table_data.append(
            {
                "namespace": ns,
                "source_node": source or "Unknown",
                "target_node": target or "Unknown",
                "observed_duration": f"{observed_duration:.2f}s" if success else "N/A",
                "vmim_duration": f"{vmim_duration:.2f}s" if (success and vmim_duration) else "N/A",
                "status": status,
            }
        )

    # Print table
    if table_data:
        logger.info(f"Total migration time for {len(migration_results)} VMs: {total_migration_time:.2f}s")
        logger.info("\n" + "=" * 150)
        logger.info(
            f"{'Namespace':<25} {'Source Node':<30} {'Target Node':<30} {'Observed Time':<15} {'VMIM Time':<15} {'Status':<10}"
        )
        logger.info("=" * 150)

        for row in table_data:
            logger.info(
                f"{row['namespace']:<25} {row['source_node']:<30} {row['target_node']:<30} "
                f"{row['observed_duration']:<15} {row['vmim_duration']:<15} {row['status']:<10}"
            )

        logger.info("=" * 150)

    # Statistics
    successful_migrations = sum(1 for _, success, _, _, _, _ in migration_results if success)
    failed_migrations = len(migration_results) - successful_migrations

    if successful_migrations > 0:
        # Observed durations (node change detection)
        observed_durations = [
            observed_duration for _, success, observed_duration, _, _, _ in migration_results if success
        ]
        avg_observed = sum(observed_durations) / len(observed_durations)
        min_observed = min(observed_durations)
        max_observed = max(observed_durations)

        # VMIM durations (official KubeVirt timestamps)
        vmim_durations = [
            vmim_duration
            for _, success, _, _, _, vmim_duration in migration_results
            if success and vmim_duration is not None
        ]

        logger.info("\n" + "=" * 80)
        logger.info("MIGRATION STATISTICS")
        logger.info("=" * 80)
        logger.info(f"\n  Total VMs:              {len(migration_results)}")
        logger.info(f"  Successful Migrations:  {successful_migrations}")
        logger.info(f"  Failed Migrations:      {failed_migrations}")

        logger.info("\n  Observed Time (Node Change Detection):")
        logger.info(f"    Average:              {avg_observed:.2f}s")
        logger.info(f"    Minimum:              {min_observed:.2f}s")
        logger.info(f"    Maximum:              {max_observed:.2f}s")

        if vmim_durations:
            avg_vmim = sum(vmim_durations) / len(vmim_durations)
            min_vmim = min(vmim_durations)
            max_vmim = max(vmim_durations)

            logger.info("\n  VMIM Time (Official KubeVirt Timestamps):")
            logger.info(f"    Average:              {avg_vmim:.2f}s")
            logger.info(f"    Minimum:              {min_vmim:.2f}s")
            logger.info(f"    Maximum:              {max_vmim:.2f}s")

            # Calculate difference
            avg_diff = avg_observed - avg_vmim
            logger.info("\n  Difference (Observed - VMIM):")
            logger.info(f"    Average:              {avg_diff:.2f}s")
            logger.info("    Note: Difference includes polling overhead (~2s) and status update delays")
        else:
            logger.info("\n  VMIM Time: Not available (timestamps not found)")

        logger.info("=" * 80)

    # --- Save structured migration results if requested ---
    if args.save_results:
        logger.info(f"Using results directory: {out_dir}")

        # Save detailed and summary results in the correct folder
        save_migration_results(
            args, migration_results, base_dir=out_dir, logger=logger, total_time=total_migration_time
        )

        logger.info(f"Migration results saved under: {out_dir}")
    else:
        logger.info("Migration results not saved (use --save-results to enable).")

    # Determine if cleanup should run
    should_cleanup = args.cleanup or (args.cleanup_on_failure and failed_migrations > 0)

    # Cleanup
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
                # Clean up VMIMs first
                logger.info("Cleaning up VirtualMachineInstanceMigration objects...")
                vmim_count = 0
                for ns in namespaces:
                    vmims = list_resources_in_namespace(ns, "virtualmachineinstancemigration", logger)
                    for vmim in vmims:
                        if args.dry_run_cleanup:
                            logger.info(f"[DRY RUN] Would delete VMIM: {vmim} in {ns}")
                        elif delete_vmim(vmim, ns, logger):
                            vmim_count += 1

                logger.info(
                    f"{'[DRY RUN] Would delete' if args.dry_run_cleanup else 'Deleted'} {vmim_count} VMIM objects"
                )

                # Clean up VMs and namespaces if they were created by this test
                if args.create_vms:
                    stats = cleanup_test_namespaces(
                        namespace_prefix=args.namespace_prefix,
                        start=args.start,
                        end=args.end,
                        vm_name=args.vm_name,
                        delete_namespaces=True,
                        dry_run=args.dry_run_cleanup,
                        batch_size=args.concurrency,
                        logger=logger,
                    )
                    print_cleanup_summary(stats, logger)
                else:
                    logger.info("VMs were not created by this test, skipping VM/namespace deletion")
                    logger.info("Only VMIM objects were cleaned up")

                if not args.dry_run_cleanup:
                    logger.info("Cleanup completed successfully!")

            except Exception as e:
                logger.error(f"Error during cleanup: {e}")
                logger.warning("Some resources may not have been cleaned up")

    logger.info("\nMigration test complete!")


if __name__ == "__main__":
    main()
