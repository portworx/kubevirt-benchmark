#!/usr/bin/env python3
"""
Disk Operations Benchmark - Hotplug/Coldplug disk performance testing for KubeVirt VMs.

Measures:
- Hotplug: Attach disk to running VM
- Coldplug: Attach disk to stopped VM, then start
- Validation: Verify disk appears inside VM via SSH
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Reuse the shared SSH helper that runs `kubectl exec` into a persistent
# sshpass-equipped pod (same approach as the FIO benchmark).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from utils.common import ssh_exec_command


# Constants
DEFAULT_NAMESPACE_PREFIX = "disk-ops"
DEFAULT_VM_NAME = "disk-ops-vm"
DEFAULT_VM_TEMPLATE = "vm-template.yaml"
DEFAULT_DISK_SIZE = "10Gi"
DEFAULT_CONCURRENCY = 10
DEFAULT_SSH_TIMEOUT = 120
DEFAULT_ATTACH_TIMEOUT = 300
DEFAULT_VM_TIMEOUT = 600
DEFAULT_VM_USER = "cloud-user"
DEFAULT_VM_PASSWORD = "changeme"
DEFAULT_SSH_POD = "ssh-test-pod"
DEFAULT_SSH_POD_NS = "default"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Disk Operations Benchmark - Test hotplug/coldplug performance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Hotplug 3 disks to 10 VMs
  python3 measure-disk-ops.py --start 1 --end 10 --operation hotplug --disks 3

  # Coldplug 1 disk to 5 VMs
  python3 measure-disk-ops.py --start 1 --end 5 --operation coldplug --disks 1

  # Both operations with unplug test
  python3 measure-disk-ops.py --start 1 --end 10 --operation all --disks 2 --test-unplug
        """,
    )

    # Required arguments
    parser.add_argument("--start", "-s", type=int, required=True, help="Start namespace index")
    parser.add_argument("--end", "-e", type=int, required=True, help="End namespace index")
    parser.add_argument("--storage-class", type=str, required=True, help="Storage class for PVCs")

    # Operation settings
    parser.add_argument(
        "--operation",
        type=str,
        default="all",
        choices=["hotplug", "coldplug", "all"],
        help="Operation type (default: all)",
    )
    parser.add_argument("--disks", type=int, default=1, help="Number of disks per VM (default: 1)")
    parser.add_argument("--disk-size", type=str, default=DEFAULT_DISK_SIZE, help="Disk size (default: 10Gi)")
    parser.add_argument(
        "--parallel-attach", action="store_true", help="Attach all disks in parallel (default: sequential)"
    )
    parser.add_argument("--test-unplug", action="store_true", help="Also test disk removal")

    # VM settings
    parser.add_argument("--namespace-prefix", type=str, default=DEFAULT_NAMESPACE_PREFIX)
    parser.add_argument("--vm-name", type=str, default=DEFAULT_VM_NAME)
    parser.add_argument("--vm-template", type=str, default=DEFAULT_VM_TEMPLATE, help="VM template YAML")
    parser.add_argument("--vm-user", type=str, default=DEFAULT_VM_USER, help="VM SSH user (default: cloud-user)")
    parser.add_argument(
        "--vm-password",
        type=str,
        default=DEFAULT_VM_PASSWORD,
        help="VM SSH password for in-VM validation (default: changeme)",
    )
    parser.add_argument(
        "--ssh-pod",
        type=str,
        default=DEFAULT_SSH_POD,
        help="Persistent SSH helper pod with sshpass (auto-created if missing)",
    )
    parser.add_argument("--ssh-pod-ns", type=str, default=DEFAULT_SSH_POD_NS, help="SSH helper pod namespace")
    parser.add_argument("--create-vms", action="store_true", help="Create VMs before testing")

    # Execution settings
    parser.add_argument("--concurrency", "-c", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument(
        "--attach-timeout", type=int, default=DEFAULT_ATTACH_TIMEOUT, help="Timeout for disk attach (default: 300s)"
    )
    parser.add_argument(
        "--vm-timeout",
        type=int,
        default=DEFAULT_VM_TIMEOUT,
        help="Timeout for created VMs to reach Running, incl. image import (default: 600s)",
    )
    parser.add_argument("--skip-validation", action="store_true", help="Skip in-VM validation")

    # Output settings
    parser.add_argument("--results-dir", type=str, default="results")
    parser.add_argument("--save-results", action="store_true")
    parser.add_argument("--px-version", type=str, default="px-unknown", help="Portworx version for results grouping")
    parser.add_argument(
        "--disk-type", type=str, default=None, help="Disk type label for results grouping (default: <disks>-disk)"
    )
    parser.add_argument("--cleanup", action="store_true", help="Remove hotplugged disks after test")

    # Logging
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", type=str, default=None, help="Log file path (logs also go to the console)")

    return parser.parse_args()


def setup_logging(level: str, log_file: Optional[str] = None) -> logging.Logger:
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger(__name__)


def ensure_ssh_pod(ssh_pod: str, ssh_pod_ns: str, logger, timeout: int = 180) -> Tuple[bool, bool]:
    """
    Ensure a persistent SSH helper pod (with sshpass) is running and usable.

    Reuses the pod if it already exists; otherwise creates it and waits until
    sshpass is installed. Returns (ready, created_by_us).
    """

    def sshpass_ready() -> bool:
        rc, _, _ = run_cmd(f"kubectl exec -n {ssh_pod_ns} {ssh_pod} -- which sshpass", timeout=15)
        return rc == 0

    if sshpass_ready():
        logger.info(f"Using existing SSH helper pod {ssh_pod_ns}/{ssh_pod}")
        return True, False

    created = False
    rc, _, _ = run_cmd(f"kubectl get pod {ssh_pod} -n {ssh_pod_ns}", timeout=15)
    if rc != 0:
        logger.info(f"Creating SSH helper pod {ssh_pod_ns}/{ssh_pod}...")
        run_cmd(f"kubectl create namespace {ssh_pod_ns} --dry-run=client -o yaml | kubectl apply -f -", timeout=20)
        manifest = f"""apiVersion: v1
kind: Pod
metadata:
  name: {ssh_pod}
  namespace: {ssh_pod_ns}
  labels:
    app: kubevirt-perf-test
spec:
  containers:
  - name: ssh-client
    image: alpine:latest
    command: ["/bin/sh", "-c", "apk add --no-cache bash openssh-client sshpass iputils && tail -f /dev/null"]
    resources:
      requests:
        memory: "128Mi"
        cpu: "100m"
      limits:
        memory: "256Mi"
        cpu: "200m"
  restartPolicy: Always
"""
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"], input=manifest.encode(), capture_output=True, check=False
        )
        if result.returncode != 0:
            logger.error(f"Failed to create SSH helper pod: {result.stderr.decode().strip()}")
            return False, False
        created = True

    # Pod runs `apk add` on startup; wait until sshpass is actually available.
    start = time.time()
    while time.time() - start < timeout:
        if sshpass_ready():
            logger.info(f"SSH helper pod {ssh_pod_ns}/{ssh_pod} is ready")
            return True, created
        time.sleep(5)

    logger.error(f"SSH helper pod {ssh_pod_ns}/{ssh_pod} not ready after {timeout}s")
    return False, created


def prepare_vm_yaml(template_path: str, vm_name: str, storage_class: str, vm_password: str) -> str:
    """Prepare VM YAML with substituted values."""
    with open(template_path) as f:
        content = f.read()

    replacements = {
        "{{VM_NAME}}": vm_name,
        "{{STORAGE_CLASS}}": storage_class,
        "{{STORAGE_CLASS_NAME}}": storage_class,  # alias used by other repo templates
        "{{VM_PASSWORD}}": vm_password,
    }

    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)

    return content


def create_namespace(namespace: str) -> bool:
    """Create a namespace if it doesn't exist."""
    cmd = f"kubectl create namespace {namespace} --dry-run=client -o yaml | kubectl apply -f -"
    rc, _, _ = run_cmd(cmd)
    return rc == 0


def deploy_vm(namespace: str, vm_yaml: str, logger) -> bool:
    """Deploy a VM to a namespace."""
    result = subprocess.run(
        f"kubectl apply -n {namespace} -f -", shell=True, input=vm_yaml.encode(), capture_output=True, check=False
    )
    if result.returncode == 0:
        logger.info(f"[{namespace}] VM deployed")
        return True
    else:
        logger.error(f"[{namespace}] VM deploy failed: {result.stderr.decode()}")
        return False


# VM states that indicate a stuck/failed VM unlikely to recover on its own.
_VM_PROBLEM_HINTS = ("err", "fail", "crashloop", "unschedulable", "backoff", "pull")


def wait_for_vms_running(
    namespaces: List[str], vm_name: str, timeout: int, logger, poll_interval: int = 10
) -> List[str]:
    """
    Wait for VMs to reach Running state, reporting live progress.

    Emits a per-cycle breakdown of what the not-yet-running VMs are doing (e.g.
    Provisioning while the disk image imports) so the wait never looks hung, and
    flags VMs stuck in error states. Returns the list of running namespaces.
    """
    running = set()
    warned = set()
    start = time.time()

    logger.info(f"Waiting for {len(namespaces)} VMs to be Running (timeout: {timeout}s)...")

    while time.time() - start < timeout and len(running) < len(namespaces):
        state_counts = {}
        for ns in namespaces:
            if ns in running:
                continue
            vm_status, vmi_phase = get_vm_status(ns, vm_name)
            if vmi_phase == "Running":
                running.add(ns)
                logger.info(f"[{ns}] VM is Running ({len(running)}/{len(namespaces)})")
                continue
            # Prefer the VM's printableStatus (e.g. "Provisioning", "Starting",
            # "WaitingForVolumeBinding") — far more telling than the VMI phase.
            state = vm_status if vm_status not in ("", "Unknown") else (vmi_phase or "Pending")
            state_counts[state] = state_counts.get(state, 0) + 1
            # Surface a likely-stuck VM once, so the user isn't blocked blindly.
            if ns not in warned and any(h in state.lower() for h in _VM_PROBLEM_HINTS):
                logger.warning(
                    f"[{ns}] VM in problem state '{state}' — " f"inspect with: kubectl describe vm {vm_name} -n {ns}"
                )
                warned.add(ns)

        if len(running) < len(namespaces):
            elapsed = int(time.time() - start)
            breakdown = ", ".join(f"{s}={n}" for s, n in sorted(state_counts.items())) or "pending"
            logger.info(
                f"  ... {len(running)}/{len(namespaces)} Running after {elapsed}s "
                f"(timeout {timeout}s) | remaining: {breakdown}"
            )
            time.sleep(poll_interval)

    not_running = [ns for ns in namespaces if ns not in running]
    if not_running:
        shown = ", ".join(not_running[:10]) + (" ..." if len(not_running) > 10 else "")
        logger.warning(f"{len(not_running)}/{len(namespaces)} VM(s) not Running after {timeout}s: {shown}")
    return list(running)


def run_cmd(cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Run shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


def get_vm_status(namespace: str, vm_name: str) -> Tuple[str, str]:
    """Get VM and VMI status. Returns (vm_status, vmi_phase)."""
    rc, out, _ = run_cmd(f"kubectl get vm {vm_name} -n {namespace} -o jsonpath='{{.status.printableStatus}}'")
    vm_status = out if rc == 0 else "Unknown"

    rc, out, _ = run_cmd(f"kubectl get vmi {vm_name} -n {namespace} -o jsonpath='{{.status.phase}}'")
    vmi_phase = out if rc == 0 else "Unknown"

    return vm_status, vmi_phase


def discover_vm_name(namespace: str) -> Optional[str]:
    """Return the name of the (first) VirtualMachine in a namespace, if any.

    Templates may hardcode a VM name instead of honoring {{VM_NAME}}, so the
    deployed name can differ from --vm-name. This lets the caller adopt the real
    name rather than waiting for a VM that doesn't exist.
    """
    rc, out, _ = run_cmd(f"kubectl get vm -n {namespace} -o jsonpath='{{.items[0].metadata.name}}'")
    return out if rc == 0 and out else None


def get_vm_ip(namespace: str, vm_name: str) -> Optional[str]:
    """Get VM IP address."""
    cmd = f"kubectl get vmi {vm_name} -n {namespace} -o jsonpath='{{.status.interfaces[0].ipAddress}}'"
    rc, out, _ = run_cmd(cmd)
    return out if rc == 0 and out else None


def run_ssh_command(vm_ip: str, command: str, ssh_config: Dict, timeout: int = 60) -> Optional[str]:
    """
    Run a command on the VM via the persistent SSH helper pod (password auth).

    Uses the shared ssh_exec_command helper (kubectl exec into the sshpass pod),
    which is far more reliable than spawning a throwaway pod per call.
    Returns stripped stdout, or None if the command produced no usable output.
    """
    rc, stdout, stderr = ssh_exec_command(
        vm_ip,
        command,
        ssh_config["pod"],
        ssh_config["pod_ns"],
        ssh_config["user"],
        ssh_config["password"],
        logger=logging.getLogger(__name__),
        timeout=timeout,
    )
    output = (stdout or "").strip()
    if rc != 0 and not output:
        logging.getLogger(__name__).debug(f"SSH to {vm_ip} rc={rc}, stderr={(stderr or '').strip()!r}")
        return None
    return output if output else None


def create_pvc(namespace: str, pvc_name: str, size: str, storage_class: str) -> bool:
    """Create a PVC for hotplug/coldplug."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "pvc-template.yaml")

    with open(template_path) as f:
        yaml_content = f.read()

    yaml_content = yaml_content.replace("DISK_NAME", pvc_name)
    yaml_content = yaml_content.replace("NAMESPACE", namespace)
    yaml_content = yaml_content.replace("DISK_SIZE", size)
    yaml_content = yaml_content.replace("STORAGE_CLASS", storage_class)

    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=yaml_content.encode(), capture_output=True, check=False
    )
    return result.returncode == 0


def delete_pvc(namespace: str, pvc_name: str) -> bool:
    """Delete a PVC."""
    rc, _, _ = run_cmd(f"kubectl delete pvc {pvc_name} -n {namespace} --ignore-not-found")
    return rc == 0


def add_volume(namespace: str, vm_name: str, volume_name: str, pvc_name: str, logger=None) -> Tuple[bool, float, str]:
    """Add volume to VM using virtctl. Returns (success, time_taken, error_msg)."""
    start = time.time()
    # virtctl addvolume expects --volume-name to be the PVC name
    cmd = f"virtctl addvolume {vm_name} --volume-name={pvc_name} --persist -n {namespace}"
    rc, out, err = run_cmd(cmd, timeout=60)
    elapsed = time.time() - start
    if rc != 0 and logger:
        logger.error(f"[{namespace}] addvolume failed: {err}")
    return rc == 0, elapsed, err


def remove_volume(namespace: str, vm_name: str, pvc_name: str) -> Tuple[bool, float]:
    """Remove volume from VM using virtctl. Returns (success, time_taken)."""
    start = time.time()
    cmd = f"virtctl removevolume {vm_name} --volume-name={pvc_name} --persist -n {namespace}"
    rc, _, _ = run_cmd(cmd, timeout=60)
    elapsed = time.time() - start
    return rc == 0, elapsed


def wait_for_volume_attached(namespace: str, vm_name: str, pvc_name: str, timeout: int = 300) -> Tuple[bool, float]:
    """Wait for volume to appear in VMI status. Returns (success, time_taken)."""
    start = time.time()
    while time.time() - start < timeout:
        cmd = f"kubectl get vmi {vm_name} -n {namespace} -o jsonpath='{{.status.volumeStatus[*].name}}'"
        rc, out, _ = run_cmd(cmd)
        if rc == 0 and pvc_name in out:
            return True, time.time() - start
        time.sleep(2)
    return False, time.time() - start


# Block devices that report TYPE=disk but are not attachable storage
# (e.g. Fedora's zram swap, loopback mounts, optical drives).
_PSEUDO_DISK_PREFIXES = ("zram", "loop", "sr", "fd")


def get_disk_devices(vm_ip: str, ssh_config: Dict) -> Optional[set]:
    """
    Return the set of real disk device names in the VM.

    Excludes pseudo devices (zram/loop/sr/fd) so swap and similar don't get
    miscounted as attached storage. Returns None if the VM couldn't be queried.
    """
    output = run_ssh_command(vm_ip, "lsblk -d -n -o NAME,TYPE", ssh_config)
    if output is None:
        return None
    disks = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "disk" and not parts[0].startswith(_PSEUDO_DISK_PREFIXES):
            disks.add(parts[0])
    return disks


def get_baseline_disks(vm_ip: str, ssh_config: Dict, logger=None, settle_timeout: int = 60) -> set:
    """
    Capture the VM's disk set after it has stabilized.

    Device enumeration (cloud-init disk, zram, etc.) can lag early in boot, so we
    wait until two consecutive reads agree before trusting the baseline.
    """
    start = time.time()
    previous = None
    while time.time() - start < settle_timeout:
        current = get_disk_devices(vm_ip, ssh_config)
        if current and current == previous:
            return current
        previous = current
        time.sleep(3)
    return previous or set()


def validate_disk_in_vm(
    vm_ip: str,
    expected_disks: int,
    ssh_config: Dict,
    baseline_disks: Optional[set] = None,
    timeout: int = 120,
    logger=None,
) -> Tuple[bool, int, float]:
    """
    Validate that the expected number of newly attached disks are visible in the VM.

    Compares the current disk set against the baseline captured before attaching,
    so only genuinely new devices are counted.
    Returns (success, new_disk_count, time_taken).
    """
    start = time.time()
    baseline = baseline_disks or set()

    current = get_disk_devices(vm_ip, ssh_config)
    if current is None:
        if logger:
            logger.warning(f"SSH command returned no output for {vm_ip}")
        return False, 0, time.time() - start

    new_disks = current - baseline
    if logger:
        logger.debug(
            f"Found disks {sorted(current)}, baseline {sorted(baseline)}, "
            f"{len(new_disks)} new {sorted(new_disks)} (expected {expected_disks})"
        )
    success = len(new_disks) >= expected_disks
    return success, len(new_disks), time.time() - start


def stop_vm(namespace: str, vm_name: str, timeout: int = 300) -> bool:
    """Stop VM and wait for it to be stopped."""
    run_cmd(f"virtctl stop {vm_name} -n {namespace}")
    start = time.time()
    while time.time() - start < timeout:
        _, vmi_phase = get_vm_status(namespace, vm_name)
        if vmi_phase in ("Unknown", ""):
            return True
        time.sleep(5)
    return False


def start_vm(namespace: str, vm_name: str, timeout: int = 300) -> Tuple[bool, float]:
    """Start VM and wait for it to be running. Returns (success, boot_time)."""
    start = time.time()
    run_cmd(f"virtctl start {vm_name} -n {namespace}")

    while time.time() - start < timeout:
        _, vmi_phase = get_vm_status(namespace, vm_name)
        if vmi_phase == "Running":
            return True, time.time() - start
        time.sleep(5)
    return False, time.time() - start


def hotplug_disks(
    namespace: str,
    vm_name: str,
    num_disks: int,
    disk_size: str,
    storage_class: str,
    vm_ip: str,
    ssh_config: Optional[Dict],
    skip_validation: bool,
    attach_timeout: int,
    logger,
    parallel: bool = False,
) -> Dict:
    """Perform hotplug operation on a single VM. If parallel=True, attach all disks concurrently."""
    result = {
        "namespace": namespace,
        "operation": "hotplug",
        "disks_requested": num_disks,
        "disks_attached": 0,
        "disks_validated": 0,
        "api_attach_time": 0,
        "volume_ready_time": 0,
        "validation_time": 0,
        "total_time": 0,
        "success": False,
        "errors": [],
    }

    start_total = time.time()
    attached_volumes = []

    # Capture the baseline disk set before hotplug (for set-difference validation)
    baseline_disks = set()
    if not skip_validation and vm_ip and ssh_config:
        baseline_disks = get_baseline_disks(vm_ip, ssh_config, logger)
        logger.debug(f"[{namespace}] Baseline disks: {sorted(baseline_disks)}")

    if parallel:
        # Parallel hotplug - create all PVCs and add all volumes concurrently
        import concurrent.futures

        disk_info = []
        for i in range(num_disks):
            disk_id = f"{uuid.uuid4().hex[:6]}"
            disk_info.append({"pvc_name": f"hotplug-disk-{disk_id}", "volume_name": f"hotplug-vol-{disk_id}"})

        # Step 1: Create all PVCs in parallel
        def create_pvc_task(info):
            return create_pvc(namespace, info["pvc_name"], disk_size, storage_class), info

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, num_disks)) as executor:
            pvc_futures = [executor.submit(create_pvc_task, info) for info in disk_info]
            valid_disks = []
            for future in concurrent.futures.as_completed(pvc_futures):
                success, info = future.result()
                if success:
                    valid_disks.append(info)
                else:
                    result["errors"].append(f"Failed to create PVC {info['pvc_name']}")

        # Step 2: Add all volumes in parallel
        def add_volume_task(info):
            return add_volume(namespace, vm_name, info["volume_name"], info["pvc_name"], logger), info

        api_start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(valid_disks))) as executor:
            vol_futures = [executor.submit(add_volume_task, info) for info in valid_disks]
            pending_disks = []
            for future in concurrent.futures.as_completed(vol_futures):
                (success, api_time, err), info = future.result()
                if success:
                    pending_disks.append(info)
                else:
                    result["errors"].append(f"Failed to add volume {info['volume_name']}: {err}")
        result["api_attach_time"] = time.time() - api_start

        # Step 3: Wait for all volumes to be attached in parallel
        def wait_volume_task(info):
            return wait_for_volume_attached(namespace, vm_name, info["pvc_name"], attach_timeout), info

        ready_start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(pending_disks))) as executor:
            wait_futures = [executor.submit(wait_volume_task, info) for info in pending_disks]
            for future in concurrent.futures.as_completed(wait_futures):
                (success, ready_time), info = future.result()
                if success:
                    result["disks_attached"] += 1
                    attached_volumes.append((info["pvc_name"], info["volume_name"]))
                else:
                    result["errors"].append(f"Volume {info['volume_name']} not ready in time")
        result["volume_ready_time"] = time.time() - ready_start
    else:
        # Sequential hotplug - one disk at a time
        for i in range(num_disks):
            disk_id = f"{uuid.uuid4().hex[:6]}"
            pvc_name = f"hotplug-disk-{disk_id}"
            volume_name = f"hotplug-vol-{disk_id}"

            # Create PVC
            if not create_pvc(namespace, pvc_name, disk_size, storage_class):
                result["errors"].append(f"Failed to create PVC {pvc_name}")
                continue

            # Add volume
            success, api_time, err = add_volume(namespace, vm_name, volume_name, pvc_name, logger)
            result["api_attach_time"] += api_time

            if not success:
                result["errors"].append(f"Failed to add volume {volume_name}: {err}")
                continue

            # Wait for volume to be attached
            success, ready_time = wait_for_volume_attached(namespace, vm_name, pvc_name, attach_timeout)
            result["volume_ready_time"] += ready_time

            if success:
                result["disks_attached"] += 1
                attached_volumes.append((pvc_name, volume_name))
            else:
                result["errors"].append(f"Volume {volume_name} not ready in time")

    # Validate inside VM (retry to allow disks to appear)
    if not skip_validation and vm_ip and ssh_config and result["disks_attached"] > 0:
        max_retries = 3
        wait_between = 10
        for attempt in range(1, max_retries + 1):
            logger.info(
                f"[{namespace}] Validating {result['disks_attached']} disk(s) inside VM (attempt {attempt}/{max_retries})..."
            )
            success, disk_count, val_time = validate_disk_in_vm(
                vm_ip, result["disks_attached"], ssh_config, baseline_disks=baseline_disks, logger=logger
            )
            result["validation_time"] += val_time
            if success:
                result["disks_validated"] = disk_count
                break
            if attempt < max_retries:
                logger.info(
                    f"[{namespace}] Found {disk_count}/{result['disks_attached']} disks, retrying in {wait_between}s..."
                )
                time.sleep(wait_between)

        if not success:
            result["disks_validated"] = 0
            result["errors"].append(f"Validation failed: expected {result['disks_attached']}, found {disk_count}")

    result["total_time"] = time.time() - start_total
    result["success"] = result["disks_attached"] == num_disks and (
        skip_validation or result["disks_validated"] >= num_disks
    )
    result["attached_volumes"] = attached_volumes

    return result


def coldplug_disks(
    namespace: str,
    vm_name: str,
    num_disks: int,
    disk_size: str,
    storage_class: str,
    ssh_config: Optional[Dict],
    skip_validation: bool,
    attach_timeout: int,
    logger,
    baseline_disks: Optional[set] = None,
    parallel: bool = False,
) -> Dict:
    """Perform coldplug operation on a single VM. If parallel=True, attach all disks concurrently."""
    result = {
        "namespace": namespace,
        "operation": "coldplug",
        "disks_requested": num_disks,
        "disks_attached": 0,
        "disks_validated": 0,
        "api_attach_time": 0,
        "vm_boot_time": 0,
        "validation_time": 0,
        "total_time": 0,
        "success": False,
        "errors": [],
    }

    start_total = time.time()
    attached_volumes = []

    # Capture the baseline disk set before stopping (if not already provided)
    if not skip_validation and ssh_config and baseline_disks is None:
        vm_ip = get_vm_ip(namespace, vm_name)
        if vm_ip:
            baseline_disks = get_baseline_disks(vm_ip, ssh_config, logger)
            logger.debug(f"[{namespace}] Baseline disks before coldplug: {sorted(baseline_disks)}")

    # Stop VM first
    logger.info(f"[{namespace}] Stopping VM for coldplug...")
    if not stop_vm(namespace, vm_name):
        result["errors"].append("Failed to stop VM")
        result["total_time"] = time.time() - start_total
        return result

    # Attach disks while VM is stopped
    if parallel:
        import concurrent.futures

        disk_info = []
        for i in range(num_disks):
            disk_id = f"{uuid.uuid4().hex[:6]}"
            disk_info.append({"pvc_name": f"coldplug-disk-{disk_id}", "volume_name": f"coldplug-vol-{disk_id}"})

        # Create all PVCs in parallel
        def create_pvc_task(info):
            return create_pvc(namespace, info["pvc_name"], disk_size, storage_class), info

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, num_disks)) as executor:
            pvc_futures = [executor.submit(create_pvc_task, info) for info in disk_info]
            valid_disks = []
            for future in concurrent.futures.as_completed(pvc_futures):
                success, info = future.result()
                if success:
                    valid_disks.append(info)
                else:
                    result["errors"].append(f"Failed to create PVC {info['pvc_name']}")

        # Add all volumes in parallel
        def add_volume_task(info):
            return add_volume(namespace, vm_name, info["volume_name"], info["pvc_name"], logger), info

        api_start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(valid_disks))) as executor:
            vol_futures = [executor.submit(add_volume_task, info) for info in valid_disks]
            for future in concurrent.futures.as_completed(vol_futures):
                (success, api_time, err), info = future.result()
                if success:
                    result["disks_attached"] += 1
                    attached_volumes.append((info["pvc_name"], info["volume_name"]))
                else:
                    result["errors"].append(f"Failed to add volume {info['volume_name']}: {err}")
        result["api_attach_time"] = time.time() - api_start
    else:
        # Sequential
        for i in range(num_disks):
            disk_id = f"{uuid.uuid4().hex[:6]}"
            pvc_name = f"coldplug-disk-{disk_id}"
            volume_name = f"coldplug-vol-{disk_id}"

            if not create_pvc(namespace, pvc_name, disk_size, storage_class):
                result["errors"].append(f"Failed to create PVC {pvc_name}")
                continue

            success, api_time, err = add_volume(namespace, vm_name, volume_name, pvc_name, logger)
            result["api_attach_time"] += api_time

            if success:
                result["disks_attached"] += 1
                attached_volumes.append((pvc_name, volume_name))
            else:
                result["errors"].append(f"Failed to add volume {volume_name}: {err}")

    # Start VM
    logger.info(f"[{namespace}] Starting VM with {result['disks_attached']} new disks...")
    success, boot_time = start_vm(namespace, vm_name)
    result["vm_boot_time"] = boot_time

    if not success:
        result["errors"].append("Failed to start VM")
        result["total_time"] = time.time() - start_total
        return result

    # Validate inside VM (retry to allow disks to appear)
    if not skip_validation and ssh_config and result["disks_attached"] > 0:
        vm_ip = wait_for_vm_ip(namespace, vm_name, timeout=120, logger=logger)
        if vm_ip:
            logger.info(f"[{namespace}] Waiting for VM to be ready for validation...")
            time.sleep(10)
            max_retries = 3
            wait_between = 10
            for attempt in range(1, max_retries + 1):
                logger.info(
                    f"[{namespace}] Validating {result['disks_attached']} disk(s) inside VM (attempt {attempt}/{max_retries})..."
                )
                success, disk_count, val_time = validate_disk_in_vm(
                    vm_ip, result["disks_attached"], ssh_config, baseline_disks=baseline_disks, logger=logger
                )
                result["validation_time"] += val_time
                if success:
                    result["disks_validated"] = disk_count
                    break
                if attempt < max_retries:
                    logger.info(
                        f"[{namespace}] Found {disk_count}/{result['disks_attached']} disks, retrying in {wait_between}s..."
                    )
                    time.sleep(wait_between)

            if not success:
                result["disks_validated"] = 0

    result["total_time"] = time.time() - start_total
    result["success"] = result["disks_attached"] == num_disks and (
        skip_validation or result["disks_validated"] >= num_disks
    )
    result["attached_volumes"] = attached_volumes

    return result


def unplug_disks(namespace: str, vm_name: str, volumes: List[Tuple[str, str]], logger) -> Dict:
    """Remove hotplugged disks. volumes is list of (pvc_name, volume_name)."""
    result = {
        "namespace": namespace,
        "operation": "unplug",
        "disks_removed": 0,
        "total_time": 0,
        "success": False,
        "errors": [],
    }

    start = time.time()

    for pvc_name, volume_name in volumes:
        success, _ = remove_volume(namespace, vm_name, pvc_name)
        if success:
            result["disks_removed"] += 1
            # Delete PVC
            delete_pvc(namespace, pvc_name)
        else:
            result["errors"].append(f"Failed to remove {volume_name}")

    result["total_time"] = time.time() - start
    result["success"] = result["disks_removed"] == len(volumes)

    return result


def wait_for_vm_ip(namespace: str, vm_name: str, timeout: int = 120, logger=None) -> Optional[str]:
    """Wait for VM to get an IP address."""
    start = time.time()
    while time.time() - start < timeout:
        vm_ip = get_vm_ip(namespace, vm_name)
        if vm_ip:
            return vm_ip
        time.sleep(5)
    return None


def process_vm(namespace: str, vm_name: str, args, logger) -> List[Dict]:
    """Process a single VM for all requested operations."""
    results = []

    # Get VM status
    vm_status, vmi_phase = get_vm_status(namespace, vm_name)

    # Wait for VM IP (may take a moment after VM reports Running)
    vm_ip = None
    if vmi_phase == "Running":
        vm_ip = wait_for_vm_ip(namespace, vm_name, timeout=120, logger=logger)
        if not vm_ip:
            logger.warning(f"[{namespace}] Could not get VM IP after 120s")

    ssh_config = getattr(args, "ssh_config", None)
    logger.info(
        f"[{namespace}] VM status: {vm_status}, VMI phase: {vmi_phase}, IP: {vm_ip}, validation: {ssh_config is not None}"
    )

    # Hotplug
    if args.operation in ["hotplug", "all"]:
        if vmi_phase != "Running":
            logger.warning(f"[{namespace}] VM not running, skipping hotplug")
        else:
            mode = "parallel" if args.parallel_attach else "sequential"
            logger.info(f"[{namespace}] Starting hotplug of {args.disks} disk(s) ({mode})...")
            result = hotplug_disks(
                namespace,
                vm_name,
                args.disks,
                args.disk_size,
                args.storage_class,
                vm_ip,
                ssh_config,
                args.skip_validation,
                args.attach_timeout,
                logger,
                parallel=args.parallel_attach,
            )
            results.append(result)

            status = "✓" if result["success"] else "✗"
            logger.info(
                f"[{namespace}] Hotplug {status}: {result['disks_attached']}/{args.disks} attached, "
                f"time={result['total_time']:.2f}s"
            )

            # Unplug if requested
            if args.test_unplug and result.get("attached_volumes"):
                logger.info(f"[{namespace}] Testing unplug...")
                unplug_result = unplug_disks(namespace, vm_name, result["attached_volumes"], logger)
                results.append(unplug_result)

    # Coldplug
    if args.operation in ["coldplug", "all"]:
        mode = "parallel" if args.parallel_attach else "sequential"
        logger.info(f"[{namespace}] Starting coldplug of {args.disks} disk(s) ({mode})...")
        result = coldplug_disks(
            namespace,
            vm_name,
            args.disks,
            args.disk_size,
            args.storage_class,
            ssh_config,
            args.skip_validation,
            args.attach_timeout,
            logger,
            parallel=args.parallel_attach,
        )
        results.append(result)

        status = "✓" if result["success"] else "✗"
        logger.info(
            f"[{namespace}] Coldplug {status}: {result['disks_attached']}/{args.disks} attached, "
            f"boot={result['vm_boot_time']:.2f}s, total={result['total_time']:.2f}s"
        )

        # Unplug if requested
        if args.test_unplug and result.get("attached_volumes"):
            logger.info(f"[{namespace}] Testing unplug...")
            unplug_result = unplug_disks(namespace, vm_name, result["attached_volumes"], logger)
            results.append(unplug_result)

    return results


def aggregate_results(all_results: List[Dict], config: Dict) -> Dict:
    """Aggregate results from all VMs."""
    hotplug_results = [r for r in all_results if r.get("operation") == "hotplug"]
    coldplug_results = [r for r in all_results if r.get("operation") == "coldplug"]
    unplug_results = [r for r in all_results if r.get("operation") == "unplug"]

    def avg(lst, key):
        vals = [r.get(key, 0) for r in lst if r.get(key)]
        return round(sum(vals) / len(vals), 3) if vals else 0

    summary = {
        "test_name": "Disk Operations Benchmark",
        "timestamp": datetime.now().isoformat(),
        "config": config,
        "summary": {
            "total_vms": len({r["namespace"] for r in all_results}),
            "total_operations": len(all_results),
        },
        "hotplug": {
            "count": len(hotplug_results),
            "success_count": sum(1 for r in hotplug_results if r["success"]),
            "total_disks_attached": sum(r.get("disks_attached", 0) for r in hotplug_results),
            "total_disks_validated": sum(r.get("disks_validated", 0) for r in hotplug_results),
            "avg_api_attach_time": avg(hotplug_results, "api_attach_time"),
            "avg_volume_ready_time": avg(hotplug_results, "volume_ready_time"),
            "avg_validation_time": avg(hotplug_results, "validation_time"),
            "avg_total_time": avg(hotplug_results, "total_time"),
        }
        if hotplug_results
        else None,
        "coldplug": {
            "count": len(coldplug_results),
            "success_count": sum(1 for r in coldplug_results if r["success"]),
            "total_disks_attached": sum(r.get("disks_attached", 0) for r in coldplug_results),
            "total_disks_validated": sum(r.get("disks_validated", 0) for r in coldplug_results),
            "avg_api_attach_time": avg(coldplug_results, "api_attach_time"),
            "avg_vm_boot_time": avg(coldplug_results, "vm_boot_time"),
            "avg_validation_time": avg(coldplug_results, "validation_time"),
            "avg_total_time": avg(coldplug_results, "total_time"),
        }
        if coldplug_results
        else None,
        "unplug": {
            "count": len(unplug_results),
            "success_count": sum(1 for r in unplug_results if r["success"]),
            "total_disks_removed": sum(r.get("disks_removed", 0) for r in unplug_results),
            "avg_total_time": avg(unplug_results, "total_time"),
        }
        if unplug_results
        else None,
        "per_vm_results": all_results,
    }

    return summary


def save_results(results: Dict, results_dir: str, px_version: str, disk_type: str, vm_start: int, vm_end: int, logger):
    """Save results to JSON and CSV in the dashboard-compatible folder structure."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_name = f"{timestamp}_disk_ops_benchmark_{vm_start}-{vm_end}"
    folder = os.path.join(results_dir, px_version, disk_type, run_name)
    os.makedirs(folder, exist_ok=True)

    # Save JSON
    json_path = os.path.join(folder, "disk_ops_results.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Results saved to: {json_path}")

    # Save CSV summary
    csv_path = os.path.join(folder, "disk_ops_summary.csv")
    with open(csv_path, "w") as f:
        f.write("namespace,operation,disks_requested,disks_attached,disks_validated,total_time,success\n")
        for r in results.get("per_vm_results", []):
            f.write(
                f"{r.get('namespace')},{r.get('operation')},{r.get('disks_requested', 0)},"
                f"{r.get('disks_attached', 0)},{r.get('disks_validated', 0)},"
                f"{r.get('total_time', 0):.2f},{r.get('success')}\n"
            )
    logger.info(f"CSV saved to: {csv_path}")

    return folder


def print_summary(results: Dict, logger):
    """Print summary table."""
    print("\n" + "=" * 70)
    print("DISK OPERATIONS BENCHMARK RESULTS")
    print("=" * 70)

    if results.get("hotplug"):
        hp = results["hotplug"]
        print("\nHOTPLUG:")
        print(f"  Success Rate:      {hp['success_count']}/{hp['count']} ({100*hp['success_count']/hp['count']:.1f}%)")
        print(f"  Disks Attached:    {hp['total_disks_attached']}")
        print(f"  Disks Validated:   {hp['total_disks_validated']}")
        print(f"  Avg API Time:      {hp['avg_api_attach_time']:.2f}s")
        print(f"  Avg Volume Ready:  {hp['avg_volume_ready_time']:.2f}s")
        print(f"  Avg Total Time:    {hp['avg_total_time']:.2f}s")

    if results.get("coldplug"):
        cp = results["coldplug"]
        print("\nCOLDPLUG:")
        print(f"  Success Rate:      {cp['success_count']}/{cp['count']} ({100*cp['success_count']/cp['count']:.1f}%)")
        print(f"  Disks Attached:    {cp['total_disks_attached']}")
        print(f"  Disks Validated:   {cp['total_disks_validated']}")
        print(f"  Avg API Time:      {cp['avg_api_attach_time']:.2f}s")
        print(f"  Avg VM Boot Time:  {cp['avg_vm_boot_time']:.2f}s")
        print(f"  Avg Total Time:    {cp['avg_total_time']:.2f}s")

    if results.get("unplug"):
        up = results["unplug"]
        print("\nUNPLUG:")
        print(f"  Success Rate:      {up['success_count']}/{up['count']} ({100*up['success_count']/up['count']:.1f}%)")
        print(f"  Disks Removed:     {up['total_disks_removed']}")
        print(f"  Avg Total Time:    {up['avg_total_time']:.2f}s")

    print("=" * 70)


def main():
    args = parse_args()
    logger = setup_logging(args.log_level, args.log_file)

    created_ssh_pod = False
    validate = not args.skip_validation
    # SSH config for the persistent helper pod (only used when validating).
    ssh_config = (
        {
            "pod": args.ssh_pod,
            "pod_ns": args.ssh_pod_ns,
            "user": args.vm_user,
            "password": args.vm_password,
        }
        if validate
        else None
    )
    args.ssh_config = ssh_config

    print("\n" + "=" * 70)
    print("  DISK OPERATIONS BENCHMARK")
    print("=" * 70)
    print(f"  Operation:      {args.operation}")
    print(f"  Namespaces:     {args.namespace_prefix}-{args.start} to {args.namespace_prefix}-{args.end}")
    print(f"  Disks per VM:   {args.disks}")
    print(f"  Disk Size:      {args.disk_size}")
    print(f"  Storage Class:  {args.storage_class}")
    print(f"  Create VMs:     {args.create_vms}")
    print(f"  Concurrency:    {args.concurrency}")
    print(f"  VM User:        {args.vm_user}")
    print(
        f"  Validation:     {'Enabled (SSH via ' + args.ssh_pod_ns + '/' + args.ssh_pod + ')' if validate else 'Skip'}"
    )
    print(f"  Test Unplug:    {'Yes' if args.test_unplug else 'No'}")
    print("=" * 70 + "\n")

    # Generate namespace list
    namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]

    try:
        # Ensure the persistent SSH helper pod exists when validation is enabled.
        if validate:
            ready, created_ssh_pod = ensure_ssh_pod(args.ssh_pod, args.ssh_pod_ns, logger)
            if not ready:
                logger.error(
                    "SSH helper pod unavailable; re-run with --skip-validation " "to proceed without in-VM checks"
                )
                sys.exit(1)

        if args.create_vms:
            # Step 1: Create namespaces
            print("[1/3] Creating namespaces...")
            for ns in namespaces:
                create_namespace(ns)

            # Step 2: Deploy VMs
            print("[2/3] Deploying VMs...")
            template_path = args.vm_template
            if not os.path.isabs(template_path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                template_path = os.path.join(script_dir, template_path)
            if not os.path.exists(template_path):
                logger.error(f"VM template not found: {template_path}")
                sys.exit(1)
            vm_yaml = prepare_vm_yaml(template_path, args.vm_name, args.storage_class, args.vm_password)

            # Fail fast on placeholders we couldn't fill, instead of shipping broken
            # YAML to every namespace and then waiting for VMs that never deploy.
            leftover = sorted(set(re.findall(r"\{\{[^}]+\}\}", vm_yaml)))
            if leftover:
                logger.error(f"VM template '{template_path}' has unsubstituted placeholders: {', '.join(leftover)}")
                logger.error(
                    "Supported: {{VM_NAME}}, {{STORAGE_CLASS}} (or {{STORAGE_CLASS_NAME}}), {{VM_PASSWORD}}. "
                    "Use a disk-ops-compatible template (see disk-ops-benchmark/vm-template.yaml)."
                )
                sys.exit(1)

            deployed = []
            with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
                futures = {executor.submit(deploy_vm, ns, vm_yaml, logger): ns for ns in namespaces}
                for future in as_completed(futures):
                    ns = futures[future]
                    if future.result():
                        deployed.append(ns)

            # Don't proceed to the wait/operations if deploys failed.
            if not deployed:
                logger.error(
                    f"All {len(namespaces)} VM deploy(s) failed; aborting. "
                    "Check the template, storage class, and cluster state above."
                )
                sys.exit(1)
            if len(deployed) < len(namespaces):
                logger.warning(
                    f"{len(namespaces) - len(deployed)}/{len(namespaces)} VM deploy(s) failed; "
                    f"continuing with the {len(deployed)} that deployed."
                )
            namespaces = deployed

            # A template may hardcode a VM name instead of honoring {{VM_NAME}}
            # (e.g. rhel9-vm-datasource.yaml names its VM 'rhel-9-vm'). Adopt the
            # actual deployed name so we don't wait for a VM that was never created.
            actual_vm = discover_vm_name(deployed[0])
            if not actual_vm:
                logger.error(f"No VirtualMachine found in '{deployed[0]}' after deploy; aborting.")
                sys.exit(1)
            if actual_vm != args.vm_name:
                logger.warning(
                    f"Template created VM named '{actual_vm}', not --vm-name '{args.vm_name}'. "
                    f"Using '{actual_vm}' (tip: give the template a {{{{VM_NAME}}}} "
                    f"placeholder to honor --vm-name)."
                )
                args.vm_name = actual_vm

            # Step 3: Wait for VMs to be Running
            print("[3/3] Waiting for VMs to be Running...")
            running_namespaces = wait_for_vms_running(namespaces, args.vm_name, args.vm_timeout, logger)

            if len(running_namespaces) < len(namespaces):
                logger.warning(f"Only {len(running_namespaces)}/{len(namespaces)} VMs are running")

            namespaces = running_namespaces

        # Process VMs in parallel
        print("\nRunning disk operations...")
        all_results = []

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {executor.submit(process_vm, ns, args.vm_name, args, logger): ns for ns in namespaces}

            for future in as_completed(futures):
                ns = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)
                except Exception as e:
                    logger.error(f"[{ns}] Error processing VM: {e}")

        # Aggregate results
        config = {
            "operation": args.operation,
            "disks_per_vm": args.disks,
            "disk_size": args.disk_size,
            "storage_class": args.storage_class,
            "test_unplug": args.test_unplug,
            "skip_validation": args.skip_validation,
            "create_vms": args.create_vms,
        }

        aggregated = aggregate_results(all_results, config)

        # Print summary
        print_summary(aggregated, logger)

        # Save results
        if args.save_results:
            disk_type = args.disk_type or f"{args.disks}-disk"
            save_results(aggregated, args.results_dir, args.px_version, disk_type, args.start, args.end, logger)

        # Cleanup if requested
        if args.cleanup:
            logger.info("\nCleaning up hotplugged disks...")
            for result in all_results:
                if result.get("attached_volumes"):
                    for pvc_name, volume_name in result["attached_volumes"]:
                        ns = result["namespace"]
                        remove_volume(ns, args.vm_name, pvc_name)
                        delete_pvc(ns, pvc_name)

            # Cleanup VMs and namespaces if we created them
            if args.create_vms:
                logger.info("Cleaning up VMs and namespaces...")
                for ns in namespaces:
                    run_cmd(f"kubectl delete vm {args.vm_name} -n {ns} --ignore-not-found")
                    run_cmd(f"kubectl delete namespace {ns} --ignore-not-found")

            logger.info("Cleanup complete")

    finally:
        # Remove the SSH helper pod only if we created it and cleanup was requested.
        if created_ssh_pod and args.cleanup:
            logger.info(f"Removing SSH helper pod {args.ssh_pod_ns}/{args.ssh_pod}")
            run_cmd(f"kubectl delete pod {args.ssh_pod} -n {args.ssh_pod_ns} --ignore-not-found")


if __name__ == "__main__":
    main()
