#!/usr/bin/env python3
"""
KubeVirt Chaos Benchmark

This script tests the resilience and capacity of Virtual Machines and Volumes
by running concurrent chaos operations including VM creation, volume resize,
volume clone, VM restart, and VM snapshots.

Each iteration performs (concurrently where possible):
1. Create VMs with multiple data volumes
2. Resize root and data volumes
3. Clone volumes
4. Restart VMs
5. Snapshot VMs

Usage:
    python3 measure-chaos.py --storage-class YOUR-STORAGE-CLASS --vms 5 --concurrency 2
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple


# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.common import (
    Colors,
    create_namespace,
    create_vm_snapshot,
    get_pvc_size,
    get_vm_status,
    get_vm_volume_names,
    namespace_exists,
    resize_pvc,
    restart_vm,
    run_kubectl_command,
    save_capacity_results,
    setup_logging,
    wait_for_pvc_resize,
    wait_for_snapshot_ready,
)


# Default configuration
DEFAULT_NAMESPACE = "virt-chaos-benchmark"
DEFAULT_VM_YAML = "../examples/vm-templates/vm-template.yaml"
DEFAULT_VM_NAME = "rhel-9-vm"
DEFAULT_VMS_PER_ITERATION = 5
DEFAULT_DATA_VOLUME_COUNT = 1  # Reduced from 9 to 1
DEFAULT_MIN_VOL_SIZE = "30Gi"
DEFAULT_MIN_VOL_INC_SIZE = "10Gi"
DEFAULT_CONCURRENCY = 2


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="KubeVirt Chaos Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run chaos test with default settings
  python3 measure-chaos.py --storage-class YOUR-STORAGE-CLASS --concurrency 2

  # Run with custom VM count and data volumes
  python3 measure-chaos.py --storage-class YOUR-STORAGE-CLASS --vms 10 --concurrency 5

  # Run with maximum iterations limit
  python3 measure-chaos.py --storage-class YOUR-STORAGE-CLASS --max-iterations 10 --concurrency 2

  # Skip specific phases
  python3 measure-chaos.py --storage-class YOUR-STORAGE-CLASS --skip-resize --skip-clone --concurrency 2

  # Cleanup only mode
  python3 measure-chaos.py --cleanup-only
        """,
    )

    # Required arguments
    parser.add_argument("--storage-class", type=str, help="Storage class name (comma-separated for multiple)")
    parser.add_argument("--concurrency", type=int, required=True, help="Number of concurrent operations (REQUIRED)")

    # Test configuration
    parser.add_argument(
        "--namespace",
        "-n",
        type=str,
        default=DEFAULT_NAMESPACE,
        help=f"Namespace for test resources (default: {DEFAULT_NAMESPACE})",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=0, help="Maximum number of iterations (0 for infinite, default: 0)"
    )
    parser.add_argument(
        "--vms",
        type=int,
        default=DEFAULT_VMS_PER_ITERATION,
        help=f"Number of VMs per iteration (default: {DEFAULT_VMS_PER_ITERATION})",
    )
    parser.add_argument(
        "--data-volume-count",
        type=int,
        default=DEFAULT_DATA_VOLUME_COUNT,
        help=f"Number of data volumes per VM (default: {DEFAULT_DATA_VOLUME_COUNT})",
    )
    parser.add_argument(
        "--min-vol-size",
        type=str,
        default=DEFAULT_MIN_VOL_SIZE,
        help=f"Minimum volume size, e.g., 30Gi, 100Mi (default: {DEFAULT_MIN_VOL_SIZE})",
    )
    parser.add_argument(
        "--min-vol-inc-size",
        type=str,
        default=DEFAULT_MIN_VOL_INC_SIZE,
        help=f"Volume size increment for resize, e.g., 10Gi, 50Mi (default: {DEFAULT_MIN_VOL_INC_SIZE})",
    )

    # VM template configuration
    parser.add_argument(
        "--vm-yaml", type=str, default=DEFAULT_VM_YAML, help=f"Path to VM YAML template (default: {DEFAULT_VM_YAML})"
    )
    parser.add_argument(
        "--vm-name", type=str, default=DEFAULT_VM_NAME, help=f"Base VM name (default: {DEFAULT_VM_NAME})"
    )
    parser.add_argument("--datasource-name", type=str, default="rhel9", help="DataSource name (default: rhel9)")
    parser.add_argument(
        "--datasource-namespace",
        type=str,
        default="openshift-virtualization-os-images",
        help="DataSource namespace (default: openshift-virtualization-os-images)",
    )
    parser.add_argument("--vm-memory", type=str, default="2048M", help="VM memory (default: 2048M)")
    parser.add_argument("--vm-cpu-cores", type=int, default=1, help="VM CPU cores (default: 1)")

    # Skip options
    parser.add_argument("--skip-resize", action="store_true", help="Skip volume resize phase")
    parser.add_argument("--skip-clone", action="store_true", help="Skip volume clone phase")
    parser.add_argument("--skip-snapshot", action="store_true", help="Skip VM snapshot phase")
    parser.add_argument("--skip-restart", action="store_true", help="Skip VM restart phase")

    # Execution options
    parser.add_argument(
        "--scheduling-timeout",
        type=int,
        default=120,
        help="Seconds to wait in Scheduling/Provisioning state before failing (default: 120)",
    )
    parser.add_argument(
        "--vm-timeout",
        type=int,
        default=1800,
        help="Total timeout for VM to reach Running state in seconds (default: 1800)",
    )
    parser.add_argument(
        "--max-create-retries",
        type=int,
        default=5,
        help="Maximum retries for VM creation on transient errors (default: 5)",
    )

    # Cleanup options
    parser.add_argument("--cleanup", action="store_true", help="Cleanup resources after test completion")
    parser.add_argument("--cleanup-only", action="store_true", help="Only cleanup resources from previous runs")

    # Results options
    parser.add_argument(
        "--save-results", action="store_true", help="Save results to JSON/CSV files in results directory"
    )
    parser.add_argument(
        "--results-dir", type=str, default="results", help="Directory to save results (default: results)"
    )
    parser.add_argument(
        "--storage-driver",
        type=str,
        default=None,
        help="Storage driver for results folder hierarchy (e.g., portworx-3.6)",
    )

    # Logging
    parser.add_argument("--log-file", type=str, help="Log file path (default: auto-generated)")
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.cleanup_only and not args.storage_class:
        parser.error("--storage-class is required (unless using --cleanup-only)")

    return args


def parse_size_to_gi(size_str: str) -> int:
    """Parse size string to GiB integer."""
    size_str = size_str.strip().upper()
    if size_str.endswith("GI"):
        return int(size_str[:-2])
    elif size_str.endswith("G"):
        return int(size_str[:-1])
    else:
        raise ValueError(f"Unsupported size format: {size_str}")


def increment_size(current_size: str, increment: str) -> str:
    """Increment size by specified amount."""
    current_gi = parse_size_to_gi(current_size)
    increment_gi = parse_size_to_gi(increment)
    return f"{current_gi + increment_gi}Gi"


def get_storage_classes(storage_class_arg: str) -> List[str]:
    """Parse storage class argument into list."""
    return [sc.strip() for sc in storage_class_arg.split(",")]


def clone_pvc(source_pvc: str, clone_name: str, namespace: str, storage_class: str, logger) -> bool:
    """Clone a PVC using dataSource. Copies spec from source PVC."""
    try:
        import subprocess

        # Get source PVC spec
        returncode, stdout, stderr = run_kubectl_command(
            ["get", "pvc", source_pvc, "-n", namespace, "-o", "json"], check=False, logger=logger
        )
        if returncode != 0:
            logger.error(f"Failed to get source PVC {source_pvc}: {stderr}")
            return False

        source_pvc_data = json.loads(stdout)
        source_spec = source_pvc_data.get("spec", {})

        # Get properties from source PVC
        size = source_spec.get("resources", {}).get("requests", {}).get("storage")
        access_modes = source_spec.get("accessModes", ["ReadWriteOnce"])
        volume_mode = source_spec.get("volumeMode", "Filesystem")

        if not size:
            logger.error(f"Failed to get size of source PVC {source_pvc}")
            return False

        clone_manifest = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": clone_name, "namespace": namespace},
            "spec": {
                "accessModes": access_modes,
                "storageClassName": storage_class,
                "volumeMode": volume_mode,
                "resources": {"requests": {"storage": size}},
                "dataSource": {"kind": "PersistentVolumeClaim", "name": source_pvc},
            },
        }

        process = subprocess.Popen(
            ["kubectl", "create", "-f", "-", "-n", namespace],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(input=json.dumps(clone_manifest))

        if process.returncode == 0:
            logger.info(f"Clone PVC {clone_name} created from {source_pvc}")
            return True
        logger.error(f"Failed to create clone PVC {clone_name}: {stderr}")
        return False
    except Exception as e:
        logger.error(f"Failed to clone PVC {source_pvc}: {e}")
        return False


def wait_for_pvc_bound(
    pvc_name: str, namespace: str, timeout: int = 600, poll_interval: int = 5, logger=None
) -> Tuple[bool, float]:
    """Wait for PVC to be bound. Returns (success, duration_seconds)."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            returncode, stdout, _ = run_kubectl_command(
                ["get", "pvc", pvc_name, "-n", namespace, "-o", "jsonpath={.status.phase}"], check=False, logger=logger
            )
            if returncode == 0 and stdout.strip() == "Bound":
                duration = time.time() - start_time
                if logger:
                    logger.info(f"PVC {pvc_name} bound after {duration:.2f}s")
                return True, duration
            time.sleep(poll_interval)
        except Exception as e:
            if logger:
                logger.error(f"Error checking PVC status: {e}")
            time.sleep(poll_interval)
    if logger:
        logger.error(f"Timeout waiting for PVC {pvc_name} to be bound")
    return False, time.time() - start_time


def create_vm_with_data_volumes(
    vm_name: str,
    namespace: str,
    vm_yaml: str,
    storage_class: str,
    data_volume_count: int,
    volume_size: str,
    args,
    logger,
    max_retries: int = 5,
) -> bool:
    """Create a VM with multiple data volumes."""
    for attempt in range(max_retries):
        try:
            import subprocess

            import yaml as pyyaml

            # Read VM template as text and replace placeholders
            with open(vm_yaml) as f:
                template_text = f.read()

            # Replace all placeholders with actual values
            template_text = template_text.replace("{{VM_NAME}}", vm_name)
            template_text = template_text.replace("{{STORAGE_CLASS_NAME}}", storage_class)
            template_text = template_text.replace("{{DATASOURCE_NAME}}", args.datasource_name)
            template_text = template_text.replace("{{DATASOURCE_NAMESPACE}}", args.datasource_namespace)
            template_text = template_text.replace("{{STORAGE_SIZE}}", volume_size)
            template_text = template_text.replace("{{VM_MEMORY}}", args.vm_memory)
            template_text = template_text.replace("{{VM_CPU_CORES}}", str(args.vm_cpu_cores))

            # Parse the YAML after placeholder replacement
            vm_template = pyyaml.safe_load(template_text)

            # Update VM metadata
            vm_template["metadata"]["name"] = vm_name
            vm_template["metadata"]["namespace"] = namespace

            # Update spec
            spec = vm_template.get("spec", {})
            template_spec = spec.get("template", {}).get("spec", {})

            # Update memory and CPU
            domain = template_spec.get("domain", {})
            if "resources" in domain:
                domain["resources"]["requests"] = {"memory": args.vm_memory}
            if "cpu" in domain:
                domain["cpu"]["cores"] = args.vm_cpu_cores

            # Update volumes and disks
            volumes = template_spec.get("volumes", [])
            disks = domain.get("devices", {}).get("disks", [])

            # Update root volume storage class
            for vol in volumes:
                if "dataVolume" in vol:
                    dv_template = spec.get("dataVolumeTemplates", [])
                    for dvt in dv_template:
                        if dvt["metadata"]["name"] == vol["dataVolume"]["name"]:
                            dvt["spec"]["storage"]["storageClassName"] = storage_class
                            dvt["spec"]["storage"]["resources"]["requests"]["storage"] = volume_size

            # Add data volumes
            dv_templates = spec.get("dataVolumeTemplates", [])
            for i in range(1, data_volume_count + 1):
                dv_name = f"{vm_name}-data-{i}"
                dv_template = {
                    "metadata": {"name": dv_name},
                    "spec": {
                        "storage": {
                            "storageClassName": storage_class,
                            "accessModes": ["ReadWriteOnce"],
                            "resources": {"requests": {"storage": volume_size}},
                        },
                        "source": {"blank": {}},
                    },
                }
                dv_templates.append(dv_template)
                volumes.append({"dataVolume": {"name": dv_name}, "name": f"data-vol-{i}"})
                disks.append({"disk": {"bus": "virtio"}, "name": f"data-vol-{i}"})

            spec["dataVolumeTemplates"] = dv_templates
            template_spec["volumes"] = volumes
            domain["devices"]["disks"] = disks

            # Create VM
            process = subprocess.Popen(
                ["kubectl", "create", "-f", "-", "-n", namespace],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(input=pyyaml.dump(vm_template))

            if process.returncode == 0:
                logger.info(f"VM {vm_name} created successfully")
                return True

            if "AlreadyExists" in stderr:
                logger.warning(f"VM {vm_name} already exists")
                return True

            logger.error(f"Failed to create VM {vm_name}: {stderr}")
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            return False

        except Exception as e:
            logger.error(f"Error creating VM {vm_name}: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            return False
    return False


def wait_for_vm_running(
    vm_name: str, namespace: str, logger, timeout: int = 1800, scheduling_timeout: int = 120, poll_interval: int = 5
) -> Tuple[bool, str]:
    """
    Wait for a VM to reach Running state.
    Tracks time in BOTH Scheduling AND Provisioning states for timeout.

    Returns:
        Tuple of (success, failure_reason)
    """
    start_time = time.time()
    stuck_state_start = None
    last_status = "Unknown"

    while time.time() - start_time < timeout:
        status = get_vm_status(vm_name, namespace, logger)
        last_status = status

        if status == "Running":
            elapsed = time.time() - start_time
            logger.info(f"VM {vm_name} reached Running state after {elapsed:.2f}s")
            return True, ""

        # Track time in Scheduling OR Provisioning state (both indicate stuck)
        if status in ("Scheduling", "Provisioning", "WaitingForVolumeBinding"):
            if stuck_state_start is None:
                stuck_state_start = time.time()
                logger.debug(f"VM {vm_name} entered {status} state")
            elif time.time() - stuck_state_start > scheduling_timeout:
                logger.warning(f"VM {vm_name} stuck in {status} state for {scheduling_timeout}s")
                return False, "scheduling"
        else:
            stuck_state_start = None

        if status == "ErrorUnschedulable":
            logger.warning(f"VM {vm_name} in ErrorUnschedulable state - capacity reached")
            return False, "capacity"
        elif status in ("CrashLoopBackOff", "ErrImagePull", "Error"):
            logger.error(f"VM {vm_name} in error state: {status}")
            return False, "error"

        time.sleep(poll_interval)

    logger.error(f"Timeout waiting for VM {vm_name} to reach Running state (last status: {last_status})")
    return False, "timeout"


def wait_for_vms_running_concurrent(
    vm_names: List[str],
    namespace: str,
    logger,
    timeout: int = 1800,
    scheduling_timeout: int = 120,
    concurrency: int = 10,
) -> Tuple[List[str], List[str], str]:
    """Wait for multiple VMs concurrently. Returns (successful, failed, failure_reason)."""
    successful = []
    failed = []
    failure_reason = ""

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(wait_for_vm_running, vm, namespace, logger, timeout, scheduling_timeout): vm
            for vm in vm_names
        }

        for future in as_completed(futures):
            vm_name = futures[future]
            try:
                success, reason = future.result()
                if success:
                    successful.append(vm_name)
                else:
                    failed.append(vm_name)
                    if not failure_reason:
                        failure_reason = reason
            except Exception as e:
                logger.error(f"Error waiting for VM {vm_name}: {e}")
                failed.append(vm_name)
                if not failure_reason:
                    failure_reason = "error"

    return successful, failed, failure_reason


def run_iteration(
    iteration: int, namespace: str, storage_class: str, args, logger, phases_executed: List[str]
) -> Tuple[bool, bool, int]:
    """
    Run a single chaos test iteration with concurrent operations.

    Args:
        iteration: Iteration number
        namespace: Namespace
        storage_class: Storage class name
        args: Command line arguments
        logger: Logger instance
        phases_executed: List to track which phases actually executed (modified in place)

    Returns:
        Tuple of (success, capacity_reached, vms_created)
    """
    logger.info("=" * 100)
    logger.info(f"{Colors.BOLD}ITERATION {iteration}{Colors.ENDC}")
    logger.info(f"Storage Class: {storage_class}")
    logger.info(f"VMs to create: {args.vms}, Concurrency: {args.concurrency}")
    logger.info("=" * 100)

    vm_names = [f"{args.vm_name}-{iteration}-{i}" for i in range(1, args.vms + 1)]

    # Phase 1: Create VMs (concurrent)
    logger.info(f"\n{Colors.HEADER}Phase 1: Creating {args.vms} VMs (concurrency: {args.concurrency}){Colors.ENDC}")
    phase_start = time.time()

    created_vms = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                create_vm_with_data_volumes,
                vm_name,
                namespace,
                args.vm_yaml,
                storage_class,
                args.data_volume_count,
                args.min_vol_size,
                args,
                logger,
                args.max_create_retries,
            ): vm_name
            for vm_name in vm_names
        }
        for future in as_completed(futures):
            vm_name = futures[future]
            try:
                if future.result():
                    created_vms.append(vm_name)
                else:
                    logger.error(f"Failed to create VM {vm_name}")
                    return False, False, 0
            except Exception as e:
                logger.error(f"Exception creating VM {vm_name}: {e}")
                return False, False, 0

    # Wait for VMs to be running (concurrent)
    logger.info(
        f"Waiting for {len(created_vms)} VMs to reach Running state (scheduling timeout: {args.scheduling_timeout}s)..."
    )
    successful_vms, failed_vms, failure_reason = wait_for_vms_running_concurrent(
        created_vms, namespace, logger, args.vm_timeout, args.scheduling_timeout, args.concurrency
    )

    if failed_vms:
        if failure_reason in ("scheduling", "capacity"):
            logger.warning(
                f"{Colors.WARNING}CAPACITY REACHED: {len(failed_vms)} VMs could not be scheduled{Colors.ENDC}"
            )
            return False, True, len(successful_vms)
        logger.error(f"Phase 1 FAILED: {len(failed_vms)} VMs failed to start (reason: {failure_reason})")
        return False, False, len(successful_vms)

    phase_duration = time.time() - phase_start
    phases_executed.append("Create VMs")
    logger.info(
        f"{Colors.OKGREEN}Phase 1 COMPLETE: {len(successful_vms)} VMs running (took {phase_duration:.2f}s){Colors.ENDC}"
    )

    # Phase 2: Resize Volumes (concurrent)
    if not args.skip_resize:
        logger.info(f"\n{Colors.HEADER}Phase 2: Resizing Volumes (concurrency: {args.concurrency}){Colors.ENDC}")
        phase_start = time.time()

        def resize_vm_volumes(vm_name):
            pvc_names = get_vm_volume_names(vm_name, namespace, logger)
            for pvc_name in pvc_names:
                current_size = get_pvc_size(pvc_name, namespace, logger)
                if not current_size:
                    return False, f"Failed to get size for PVC {pvc_name}"
                new_size = increment_size(current_size, args.min_vol_inc_size)
                if not resize_pvc(pvc_name, namespace, new_size, logger):
                    return False, f"Failed to resize PVC {pvc_name}"
                if not wait_for_pvc_resize(pvc_name, namespace, new_size, logger=logger):
                    return False, f"PVC {pvc_name} resize did not complete"
            return True, None

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {executor.submit(resize_vm_volumes, vm): vm for vm in successful_vms}
            for future in as_completed(futures):
                vm_name = futures[future]
                try:
                    success, error = future.result()
                    if not success:
                        logger.error(f"Phase 2 FAILED for {vm_name}: {error}")
                        return False, False, len(successful_vms)
                except Exception as e:
                    logger.error(f"Phase 2 FAILED for {vm_name}: {e}")
                    return False, False, len(successful_vms)

        phase_duration = time.time() - phase_start
        phases_executed.append("Resize Volumes")
        logger.info(f"{Colors.OKGREEN}Phase 2 COMPLETE: All volumes resized (took {phase_duration:.2f}s){Colors.ENDC}")
    else:
        logger.info(f"\n{Colors.WARNING}Phase 2: SKIPPED (--skip-resize){Colors.ENDC}")

    # Phase 3: Clone Volumes (concurrent)
    if not args.skip_clone:
        logger.info(f"\n{Colors.HEADER}Phase 3: Cloning Volumes (concurrency: {args.concurrency}){Colors.ENDC}")
        phase_start = time.time()
        clones_created = []

        def clone_vm_volumes(vm_name):
            pvc_names = get_vm_volume_names(vm_name, namespace, logger)
            cloned = []
            for pvc_name in pvc_names:
                clone_name = f"{pvc_name}-clone"
                if not clone_pvc(pvc_name, clone_name, namespace, storage_class, logger):
                    return False, cloned, f"Failed to clone PVC {pvc_name}"
                success, _ = wait_for_pvc_bound(clone_name, namespace, logger=logger)
                if not success:
                    return False, cloned, f"Clone PVC {clone_name} did not become bound"
                cloned.append(clone_name)
            return True, cloned, None

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {executor.submit(clone_vm_volumes, vm): vm for vm in successful_vms}
            for future in as_completed(futures):
                vm_name = futures[future]
                try:
                    success, cloned, error = future.result()
                    clones_created.extend(cloned)
                    if not success:
                        logger.error(f"Phase 3 FAILED for {vm_name}: {error}")
                        return False, False, len(successful_vms)
                except Exception as e:
                    logger.error(f"Phase 3 FAILED for {vm_name}: {e}")
                    return False, False, len(successful_vms)

        phase_duration = time.time() - phase_start
        phases_executed.append("Clone Volumes")
        logger.info(
            f"{Colors.OKGREEN}Phase 3 COMPLETE: {len(clones_created)} clones created (took {phase_duration:.2f}s){Colors.ENDC}"
        )
    else:
        logger.info(f"\n{Colors.WARNING}Phase 3: SKIPPED (--skip-clone){Colors.ENDC}")

    # Phase 4: Restart VMs (concurrent)
    if not args.skip_restart:
        logger.info(f"\n{Colors.HEADER}Phase 4: Restarting VMs (concurrency: {args.concurrency}){Colors.ENDC}")
        phase_start = time.time()

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {executor.submit(restart_vm, vm, namespace, logger): vm for vm in successful_vms}
            for future in as_completed(futures):
                vm_name = futures[future]
                try:
                    if not future.result():
                        logger.error(f"Phase 4 FAILED: Failed to restart VM {vm_name}")
                        return False, False, len(successful_vms)
                except Exception as e:
                    logger.error(f"Phase 4 FAILED for {vm_name}: {e}")
                    return False, False, len(successful_vms)

        # Wait for VMs to be running again
        logger.info("Waiting for VMs to be running after restart...")
        successful_vms, failed_vms, failure_reason = wait_for_vms_running_concurrent(
            successful_vms, namespace, logger, args.vm_timeout, args.scheduling_timeout, args.concurrency
        )
        if failed_vms:
            logger.error(f"Phase 4 FAILED: {len(failed_vms)} VMs failed to restart")
            return False, False, len(successful_vms)

        phase_duration = time.time() - phase_start
        phases_executed.append("Restart VMs")
        logger.info(f"{Colors.OKGREEN}Phase 4 COMPLETE: All VMs restarted (took {phase_duration:.2f}s){Colors.ENDC}")
    else:
        logger.info(f"\n{Colors.WARNING}Phase 4: SKIPPED (--skip-restart){Colors.ENDC}")

    # Phase 5: Snapshot VMs (concurrent)
    if not args.skip_snapshot:
        logger.info(f"\n{Colors.HEADER}Phase 5: Creating VM Snapshots (concurrency: {args.concurrency}){Colors.ENDC}")
        phase_start = time.time()
        snapshots_created = []

        def create_snapshot_for_vm(vm_name):
            snapshot_name = f"{vm_name}-snapshot"
            if not create_vm_snapshot(vm_name, snapshot_name, namespace, logger):
                return False, None, f"Failed to create snapshot for VM {vm_name}"
            if not wait_for_snapshot_ready(snapshot_name, namespace, logger=logger):
                return False, snapshot_name, f"Snapshot {snapshot_name} did not become ready"
            return True, snapshot_name, None

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {executor.submit(create_snapshot_for_vm, vm): vm for vm in successful_vms}
            for future in as_completed(futures):
                vm_name = futures[future]
                try:
                    success, snapshot_name, error = future.result()
                    if snapshot_name:
                        snapshots_created.append(snapshot_name)
                    if not success:
                        logger.error(f"Phase 5 FAILED for {vm_name}: {error}")
                        return False, False, len(successful_vms)
                except Exception as e:
                    logger.error(f"Phase 5 FAILED for {vm_name}: {e}")
                    return False, False, len(successful_vms)

        phase_duration = time.time() - phase_start
        phases_executed.append("Create Snapshots")
        logger.info(
            f"{Colors.OKGREEN}Phase 5 COMPLETE: {len(snapshots_created)} snapshots created (took {phase_duration:.2f}s){Colors.ENDC}"
        )
    else:
        logger.info(f"\n{Colors.WARNING}Phase 5: SKIPPED (--skip-snapshot){Colors.ENDC}")

    logger.info(f"\n{Colors.OKGREEN}{Colors.BOLD}ITERATION {iteration} COMPLETE{Colors.ENDC}")
    return True, False, len(successful_vms)


def cleanup_namespace(namespace: str, logger) -> bool:
    """Cleanup test namespace and all resources."""
    try:
        if not namespace_exists(namespace, logger):
            logger.info(f"Namespace {namespace} does not exist")
            return True

        logger.info(f"Deleting namespace {namespace}...")
        returncode, _, stderr = run_kubectl_command(
            ["delete", "namespace", namespace, "--wait=true", "--timeout=300s"], check=False, logger=logger
        )
        if returncode == 0:
            logger.info(f"Namespace {namespace} deleted successfully")
            return True
        logger.error(f"Failed to delete namespace {namespace}: {stderr}")
        return False
    except Exception as e:
        logger.error(f"Error cleaning up namespace {namespace}: {e}")
        return False


def print_test_summary(results: dict, phases_executed: List[str], logger):
    """Print comprehensive test summary report with only actually executed phases."""
    logger.info("\n" + "=" * 100)
    logger.info(f"{Colors.BOLD}CHAOS BENCHMARK REPORT{Colors.ENDC}")
    logger.info("=" * 100)

    logger.info(f"\n{Colors.HEADER}Test Configuration:{Colors.ENDC}")
    logger.info(f"  Storage Class(es):     {results.get('storage_classes', 'N/A')}")
    logger.info(f"  VMs per iteration:     {results.get('vms_per_iteration', 'N/A')}")
    logger.info(f"  Data volumes per VM:   {results.get('data_volumes_per_vm', 'N/A')}")
    logger.info(f"  Volume size:           {results.get('volume_size', 'N/A')}")
    logger.info(f"  VM Memory:             {results.get('vm_memory', 'N/A')}")
    logger.info(f"  VM CPU Cores:          {results.get('vm_cpu_cores', 'N/A')}")
    logger.info(f"  Concurrency:           {results.get('concurrency', 'N/A')}")

    logger.info(f"\n{Colors.HEADER}Test Results:{Colors.ENDC}")
    logger.info(f"  Iterations completed:  {results.get('iterations_completed', 0)}")
    logger.info(f"  Total VMs created:     {results.get('total_vms', 0)}")
    logger.info(f"  Total PVCs created:    {results.get('total_pvcs', 0)}")
    logger.info(f"  Test duration:         {results.get('duration_str', 'N/A')}")

    capacity_reached = results.get("capacity_reached", False)
    if capacity_reached:
        logger.info(f"\n{Colors.OKGREEN}✓ CAPACITY LIMIT REACHED{Colors.ENDC}")
        logger.info(f"  Maximum VMs that could be scheduled: {results.get('total_vms', 0)}")
    else:
        end_reason = results.get("end_reason", "unknown")
        if end_reason == "max_iterations":
            logger.info(f"\n{Colors.WARNING}⚠ MAX ITERATIONS REACHED{Colors.ENDC}")
        elif end_reason == "interrupted":
            logger.info(f"\n{Colors.WARNING}⚠ TEST INTERRUPTED{Colors.ENDC}")
        elif end_reason == "error":
            logger.info(f"\n{Colors.FAIL}✗ TEST FAILED{Colors.ENDC}")
            logger.info("  Test encountered an error.")
        else:
            logger.info(f"\n{Colors.WARNING}⚠ TEST ENDED{Colors.ENDC}")

    # Only show phases that were ACTUALLY executed (not based on skip flags)
    if phases_executed:
        logger.info(f"\n{Colors.HEADER}Phases Executed:{Colors.ENDC}")
        for phase in phases_executed:
            logger.info(f"  ✓ {phase}")
    else:
        logger.info(f"\n{Colors.WARNING}No phases completed successfully{Colors.ENDC}")

    logger.info("\n" + "=" * 100)


def main():
    """Main function."""
    args = parse_args()
    logger = setup_logging(args.log_file, args.log_level)

    # Handle cleanup-only mode
    if args.cleanup_only:
        logger.info("Running in cleanup-only mode")
        cleanup_namespace(args.namespace, logger)
        return

    # Parse storage classes
    storage_classes = get_storage_classes(args.storage_class)
    logger.info(f"Starting Chaos Benchmark with storage classes: {storage_classes}")
    logger.info(f"Concurrency: {args.concurrency}")

    # Create namespace
    if not namespace_exists(args.namespace, logger):
        if not create_namespace(args.namespace, logger):
            logger.error(f"Failed to create namespace {args.namespace}")
            sys.exit(1)

    # Initialize tracking
    start_time = time.time()
    total_vms = 0
    iterations_completed = 0
    capacity_reached = False
    end_reason = "unknown"
    phases_executed = []  # Track ACTUALLY executed phases

    try:
        iteration = 0
        while True:
            iteration += 1

            # Check max iterations
            if args.max_iterations > 0 and iteration > args.max_iterations:
                logger.info(f"Reached maximum iterations ({args.max_iterations})")
                end_reason = "max_iterations"
                break

            # Cycle through storage classes
            storage_class = storage_classes[(iteration - 1) % len(storage_classes)]

            # Run iteration
            success, cap_reached, vms_created = run_iteration(
                iteration, args.namespace, storage_class, args, logger, phases_executed
            )

            if cap_reached:
                capacity_reached = True
                total_vms += vms_created
                end_reason = "capacity"
                break

            if not success:
                end_reason = "error"
                break

            total_vms += vms_created
            iterations_completed += 1

    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        end_reason = "interrupted"
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        end_reason = "error"

    # Calculate duration
    duration = time.time() - start_time
    duration_str = f"{duration:.2f}s ({duration/60:.2f} minutes)"

    # Build results
    results = {
        "storage_classes": ", ".join(storage_classes),
        "vms_per_iteration": args.vms,
        "data_volumes_per_vm": args.data_volume_count,
        "volume_size": args.min_vol_size,
        "vm_memory": args.vm_memory,
        "vm_cpu_cores": args.vm_cpu_cores,
        "concurrency": args.concurrency,
        "iterations_completed": iterations_completed,
        "total_vms": total_vms,
        "total_pvcs": total_vms * (args.data_volume_count + 1),
        "duration_str": duration_str,
        "capacity_reached": capacity_reached,
        "end_reason": end_reason,
    }

    # Print summary with ONLY actually executed phases
    print_test_summary(results, phases_executed, logger)

    # Save results if requested
    if args.save_results:
        save_capacity_results(results, args.results_dir, args.storage_driver, logger)

    # Cleanup if requested
    if args.cleanup:
        cleanup_namespace(args.namespace, logger)


if __name__ == "__main__":
    main()
