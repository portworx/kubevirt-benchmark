#!/usr/bin/env python3
"""
Hotplug Portworx DataVolumes to KubeVirt VMs created by datasource-clone tests.

This script creates blank DataVolumes (Block mode, ReadWriteMany) and hotplugs
them to running VMs using virtctl addvolume.

Two modes of operation:
1. Namespace range: --start and --end to target VMs in numbered namespaces
2. Node-based: --node and --num-vms to target VMs running on a specific node

Usage:
    # Mode 1: Namespace range
    python3 hotplug-disks.py --start 1 --end 10 --vm-name rhel-9-vm \
        --disk-count 2 --disk-size 10Gi --storage-class portworx-raw-sc

    # Mode 2: Node-based
    python3 hotplug-disks.py --node worker-1 --num-vms 5 \
        --disk-count 2 --disk-size 10Gi --storage-class portworx-raw-sc
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Tuple, Optional, List

# Default configuration
DEFAULT_VM_NAME = 'rhel-9-vm'
DEFAULT_NAMESPACE_PREFIX = 'datasource-clone'
DEFAULT_DISK_COUNT = 1
DEFAULT_DISK_SIZE = '10Gi'
DEFAULT_CONCURRENCY = 10
DEFAULT_VOLUME_MODE = 'Block'
DEFAULT_SSH_POD = 'ssh-test-pod'
DEFAULT_SSH_POD_NS = 'default'
DEFAULT_VM_USER = 'root'
DEFAULT_VM_PASSWORD = 'Password1'


def setup_logging(log_level: str = 'INFO', log_file: str = None) -> logging.Logger:
    """Setup logging configuration."""
    logger = logging.getLogger('hotplug-disks')
    logger.setLevel(getattr(logging, log_level.upper()))

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (if log_file specified)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Hotplug Portworx DataVolumes to KubeVirt VMs using virtctl',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Mode 1: Hotplug to VMs in namespaces 1-10
  python3 hotplug-disks.py -s 1 -e 10 --disk-count 2 --disk-size 10Gi \\
      --storage-class portworx-raw-sc

  # Mode 2: Hotplug to 5 VMs running on a specific node
  python3 hotplug-disks.py --node worker-1 --num-vms 5 --disk-count 2 \\
      --storage-class portworx-raw-sc

  # Hotplug and start elbencho IO on the disks
  python3 hotplug-disks.py -s 1 -e 10 --disk-count 2 --storage-class sc \\
      --start-io

  # Dry run to see what would happen
  python3 hotplug-disks.py --node worker-1 --num-vms 10 --storage-class sc --dry-run

  # Use filesystem mode instead of block
  python3 hotplug-disks.py -s 1 -e 10 --disk-count 1 --storage-class sc \\
      --volume-mode Filesystem
        """
    )

    # Mode 1: Namespace range
    mode1 = parser.add_argument_group('Mode 1: Namespace range')
    mode1.add_argument('-s', '--start', type=int,
                       help='Start namespace index (e.g., 1 for datasource-clone-1)')
    mode1.add_argument('-e', '--end', type=int,
                       help='End namespace index (e.g., 10 for datasource-clone-10)')
    mode1.add_argument('--vm-name', type=str, default=DEFAULT_VM_NAME,
                       help=f'VM name in each namespace (default: {DEFAULT_VM_NAME})')
    mode1.add_argument('--namespace-prefix', type=str, default=DEFAULT_NAMESPACE_PREFIX,
                       help=f'Namespace prefix (default: {DEFAULT_NAMESPACE_PREFIX})')

    # Mode 2: Node-based
    mode2 = parser.add_argument_group('Mode 2: Node-based')
    mode2.add_argument('--node', type=str,
                       help='Node name to find VMs on')
    mode2.add_argument('--num-vms', type=int,
                       help='Number of VMs to hotplug disks to (from the node)')

    # Common options
    parser.add_argument('--disk-count', type=int, default=DEFAULT_DISK_COUNT,
                        help=f'Number of disks to hotplug per VM (default: {DEFAULT_DISK_COUNT})')
    parser.add_argument('--disk-size', type=str, default=DEFAULT_DISK_SIZE,
                        help=f'Size of each disk (default: {DEFAULT_DISK_SIZE})')
    parser.add_argument('--storage-class', type=str, required=True,
                        help='Storage class for the DataVolumes (REQUIRED)')
    parser.add_argument('--volume-mode', type=str, default=DEFAULT_VOLUME_MODE,
                        choices=['Block', 'Filesystem'],
                        help=f'Volume mode for DataVolumes (default: {DEFAULT_VOLUME_MODE})')
    parser.add_argument('--persist', action='store_true', default=True,
                        help='Persist hotplug volumes to VM spec (default: True)')

    parser.add_argument('-c', '--concurrency', type=int, default=DEFAULT_CONCURRENCY,
                        help=f'Max parallel operations (default: {DEFAULT_CONCURRENCY})')
    parser.add_argument('--log-level', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Logging level (default: INFO)')
    parser.add_argument('--log-file', type=str, default=None,
                        help='Log file path (default: auto-generated hotplug_<timestamp>.log)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')

    # IO options
    io_group = parser.add_argument_group('IO options (run elbencho on hotplugged disks)')
    io_group.add_argument('--start-io', action='store_true',
                          help='Start elbencho IO on hotplugged disks after hotplug completes')
    io_group.add_argument('--ssh-pod', type=str, default=DEFAULT_SSH_POD,
                          help=f'SSH pod name for running commands (default: {DEFAULT_SSH_POD})')
    io_group.add_argument('--ssh-pod-ns', type=str, default=DEFAULT_SSH_POD_NS,
                          help=f'SSH pod namespace (default: {DEFAULT_SSH_POD_NS})')
    io_group.add_argument('--vm-user', type=str, default=DEFAULT_VM_USER,
                          help=f'VM SSH username (default: {DEFAULT_VM_USER})')
    io_group.add_argument('--vm-password', type=str, default=DEFAULT_VM_PASSWORD,
                          help=f'VM SSH password (default: {DEFAULT_VM_PASSWORD})')

    args = parser.parse_args()

    # Validate: either (start and end) or (node and num-vms) must be provided
    has_range = args.start is not None and args.end is not None
    has_node = args.node is not None and args.num_vms is not None

    if not has_range and not has_node:
        parser.error("Must provide either (--start and --end) or (--node and --num-vms)")
    if has_range and has_node:
        parser.error("Cannot use both namespace range and node-based modes together")
    if (args.start is not None) != (args.end is not None):
        parser.error("--start and --end must be used together")
    if (args.node is not None) != (args.num_vms is not None):
        parser.error("--node and --num-vms must be used together")

    return args


def get_vms_on_node(node: str, num_vms: int, logger: logging.Logger) -> List[Tuple[str, str]]:
    """
    Get VMs running on a specific node.

    Args:
        node: Node name
        num_vms: Maximum number of VMs to return
        logger: Logger instance

    Returns:
        List of (namespace, vm_name) tuples
    """
    try:
        # Get all VMIs running on the specified node
        result = subprocess.run(
            ["kubectl", "get", "vmi", "-A", "-o", "json"],
            capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            logger.error(f"Failed to get VMIs: {result.stderr}")
            return []

        vmis = json.loads(result.stdout)
        vms_on_node = []

        for vmi in vmis.get("items", []):
            vmi_node = vmi.get("status", {}).get("nodeName", "")
            if vmi_node == node:
                ns = vmi.get("metadata", {}).get("namespace", "")
                name = vmi.get("metadata", {}).get("name", "")
                phase = vmi.get("status", {}).get("phase", "")
                if ns and name and phase.lower() == "running":
                    vms_on_node.append((ns, name))

        logger.info(f"Found {len(vms_on_node)} running VMs on node {node}")

        # Return up to num_vms
        return vms_on_node[:num_vms]

    except Exception as e:
        logger.error(f"Exception getting VMs on node {node}: {e}")
        return []


def get_vmi_ip(namespace: str, vm_name: str, logger: logging.Logger) -> Optional[str]:
    """Get the IP address of a VMI."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "vmi", vm_name, "-n", namespace, "-o",
             "jsonpath={.status.interfaces[0].ipAddress}"],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception as e:
        logger.debug(f"Error getting VMI IP for {namespace}/{vm_name}: {e}")
        return None


def ssh_exec_command(ip: str, command: str, ssh_pod: str, ssh_pod_ns: str,
                     vm_user: str, vm_password: str, logger: logging.Logger,
                     timeout: int = 60) -> Tuple[int, str, str]:
    """
    Execute a command on a VM via SSH through the ssh-pod.

    Args:
        ip: VM IP address
        command: Command to execute
        ssh_pod: SSH pod name
        ssh_pod_ns: SSH pod namespace
        vm_user: VM username
        vm_password: VM password
        logger: Logger instance
        timeout: Command timeout in seconds

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    # Use sshpass for password-based SSH
    # PreferredAuthentications=password forces password auth (skips pubkey)
    ssh_cmd = (
        f"sshpass -p '{vm_password}' ssh -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 "
        f"-o PreferredAuthentications=password -o PubkeyAuthentication=no "
        f"{vm_user}@{ip} '{command}'"
    )

    try:
        result = subprocess.run(
            ["kubectl", "exec", "-n", ssh_pod_ns, ssh_pod, "--", "sh", "-c", ssh_cmd],
            capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        logger.warning(f"SSH command timed out after {timeout}s")
        return -1, "", "Timeout"
    except Exception as e:
        logger.debug(f"SSH exec error: {e}")
        return -1, "", str(e)


def get_scsi_disks(ip: str, ssh_pod: str, ssh_pod_ns: str,
                   vm_user: str, vm_password: str,
                   logger: logging.Logger) -> List[str]:
    """
    Get list of current SCSI disks (/dev/sd*) in the VM.

    Returns:
        List of disk device paths (e.g., ['/dev/sda', '/dev/sdb'])
    """
    cmd = "ls -1 /dev/sd[a-z] 2>/dev/null | sort"
    rc, stdout, _ = ssh_exec_command(
        ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    if rc != 0 or not stdout.strip():
        return []

    disks = [d.strip() for d in stdout.strip().split('\n') if d.strip()]
    return disks


def start_io_on_vm(namespace: str, vm_name: str, ip: str, disk_count: int,
                   ssh_pod: str, ssh_pod_ns: str, vm_user: str, vm_password: str,
                   logger: logging.Logger, dry_run: bool = False,
                   disks_before: List[str] = None) -> bool:
    """
    Start elbencho IO on hotplugged disks in a VM.

    Args:
        namespace: VM namespace
        vm_name: VM name
        ip: VM IP address
        disk_count: Expected number of hotplugged disks
        ssh_pod: SSH pod name
        ssh_pod_ns: SSH pod namespace
        vm_user: VM username
        vm_password: VM password
        logger: Logger instance
        dry_run: If True, only show what would be done
        disks_before: List of SCSI disks before hotplug (to find new ones)

    Returns:
        True if IO started successfully, False otherwise
    """
    log_prefix = f"[{namespace}/{vm_name}]"

    # Test SSH connectivity first
    rc, stdout, stderr = ssh_exec_command(
        ip, "echo SSH_OK", ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )
    if rc != 0 or "SSH_OK" not in stdout:
        logger.warning(f"{log_prefix} SSH connection failed (rc={rc}): {stderr}")
        return False
    logger.debug(f"{log_prefix} SSH connection successful to {ip}")

    # Get current SCSI disks
    disks_after = get_scsi_disks(ip, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger)
    logger.debug(f"{log_prefix} SCSI disks after hotplug: {disks_after}")

    # Find new disks (difference between after and before)
    if disks_before is None:
        disks_before = []

    disks_before_set = set(disks_before)
    new_disks = [d for d in disks_after if d not in disks_before_set]

    if not new_disks:
        logger.warning(f"{log_prefix} No new hotplugged disks detected. Before: {disks_before}, After: {disks_after}")
        return False

    logger.info(f"{log_prefix} Detected {len(new_disks)} new hotplugged disks: {', '.join(new_disks)}")

    if dry_run:
        logger.info(f"{log_prefix} DRY-RUN: Would start elbencho IO on {', '.join(new_disks)}")
        return True

    # Build elbencho command - run in background with nohup
    disk_args = ' '.join(new_disks)
    num_disks = len(new_disks)

    # Create directories and start elbencho
    setup_cmd = "mkdir -p /var/log/elbencho /root/elbencho_results /var/run/elbencho"

    rc, _, stderr = ssh_exec_command(
        ip, setup_cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )
    if rc != 0:
        logger.warning(f"{log_prefix} Failed to create directories: {stderr}")

    # Start write process (1 IOPS per disk, runs in background)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    write_cmd = (
        f"nohup /root/elbencho/bin/elbencho -w "
        f"-b 4K -t {num_disks} --iodepth 1 --limitwrite 4096 "
        f"--direct --rand --lat --infloop --nolive --liveint 1000 "
        f"--livecsv /root/elbencho_results/write_hp_{ts}_live.csv --livecsvex "
        f"--resfile /root/elbencho_results/write_hp_{ts}.txt "
        f"{disk_args} > /var/log/elbencho/write_hp.log 2>&1 & "
        f"echo $! > /var/run/elbencho/write_hp.pid"
    )

    rc, stdout, stderr = ssh_exec_command(
        ip, write_cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, timeout=30
    )
    if rc != 0:
        logger.warning(f"{log_prefix} Failed to start write IO: {stderr}")
        return False

    logger.info(f"{log_prefix} Started elbencho write process (hotplug)")

    # Start read process (1 IOPS per disk, runs in background)
    read_cmd = (
        f"nohup /root/elbencho/bin/elbencho -r "
        f"-b 4K -t {num_disks} --iodepth 1 --limitread 4096 "
        f"--direct --rand --lat --infloop --nolive --liveint 1000 "
        f"--livecsv /root/elbencho_results/read_hp_{ts}_live.csv --livecsvex "
        f"--resfile /root/elbencho_results/read_hp_{ts}.txt "
        f"{disk_args} > /var/log/elbencho/read_hp.log 2>&1 & "
        f"echo $! > /var/run/elbencho/read_hp.pid"
    )

    rc, stdout, stderr = ssh_exec_command(
        ip, read_cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, timeout=30
    )
    if rc != 0:
        logger.warning(f"{log_prefix} Failed to start read IO: {stderr}")
        return False

    logger.info(f"{log_prefix} Started elbencho read process")
    return True


def create_datavolume(ns: str, dv_name: str, size: str, storage_class: str,
                      volume_mode: str, logger: logging.Logger,
                      dry_run: bool = False) -> bool:
    """
    Create a blank DataVolume in the specified namespace.

    Args:
        ns: Namespace
        dv_name: DataVolume name
        size: Storage size (e.g., '10Gi')
        storage_class: Storage class name
        volume_mode: 'Block' or 'Filesystem'
        logger: Logger instance
        dry_run: If True, only log what would be done
    """
    dv_manifest = {
        "apiVersion": "cdi.kubevirt.io/v1beta1",
        "kind": "DataVolume",
        "metadata": {"name": dv_name},
        "spec": {
            "source": {"blank": {}},
            "storage": {
                "storageClassName": storage_class,
                "accessModes": ["ReadWriteMany"],
                "resources": {"requests": {"storage": size}},
                "volumeMode": volume_mode
            }
        }
    }

    if dry_run:
        logger.info(f"[{ns}] DRY-RUN: Would create DataVolume {dv_name} ({size}, {volume_mode})")
        return True

    try:
        result = subprocess.run(
            ["kubectl", "apply", "-n", ns, "-f", "-"],
            input=json.dumps(dv_manifest),
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            logger.debug(f"[{ns}] Created DataVolume {dv_name}")
            return True
        else:
            logger.error(f"[{ns}] Failed to create DataVolume {dv_name}: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"[{ns}] Exception creating DataVolume {dv_name}: {e}")
        return False


def wait_for_dv_ready(ns: str, dv_name: str, logger: logging.Logger, 
                      timeout: int = 300) -> bool:
    """Wait for DataVolume to be ready (Succeeded phase)."""
    start = datetime.now()
    while (datetime.now() - start).total_seconds() < timeout:
        try:
            result = subprocess.run(
                ["kubectl", "get", "dv", dv_name, "-n", ns, "-o", 
                 "jsonpath={.status.phase}"],
                capture_output=True, text=True, check=False
            )
            phase = result.stdout.strip().lower()
            if phase == "succeeded":
                logger.debug(f"[{ns}] DataVolume {dv_name} is ready")
                return True
            elif phase == "failed":
                logger.error(f"[{ns}] DataVolume {dv_name} failed")
                return False
        except Exception as e:
            logger.error(f"[{ns}] Error checking DV status: {e}")
        time.sleep(2)
    
    logger.error(f"[{ns}] Timeout waiting for DataVolume {dv_name}")
    return False


def hotplug_volume(ns: str, vm_name: str, volume_name: str,
                   logger: logging.Logger, persist: bool = True,
                   dry_run: bool = False) -> bool:
    """
    Hotplug a volume to a VM using virtctl addvolume.

    Args:
        ns: Namespace
        vm_name: VM name
        volume_name: Name of the DataVolume/PVC to hotplug
        logger: Logger instance
        persist: Whether to persist the volume to VM spec
        dry_run: If True, only log what would be done
    """
    if dry_run:
        logger.info(f"[{ns}] DRY-RUN: Would hotplug {volume_name} to {vm_name}")
        return True

    cmd = ["virtctl", "addvolume", vm_name, "--volume-name", volume_name, "-n", ns]
    if persist:
        cmd.append("--persist")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            logger.info(f"[{ns}] Hotplugged {volume_name} to {vm_name}")
            return True
        else:
            logger.error(f"[{ns}] Failed to hotplug {volume_name}: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"[{ns}] Exception hotplugging {volume_name}: {e}")
        return False


def wait_for_hotplug_ready(ns: str, vm_name: str, volume_name: str,
                           logger: logging.Logger, timeout: int = 300) -> bool:
    """
    Wait for hotplugged volume to be ready in the VM.

    Checks VMI status.volumeStatus for the hotplugged volume to reach Ready phase.
    """
    start = datetime.now()
    while (datetime.now() - start).total_seconds() < timeout:
        try:
            result = subprocess.run(
                ["kubectl", "get", "vmi", vm_name, "-n", ns, "-o", "json"],
                capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                time.sleep(5)
                continue

            vmi = json.loads(result.stdout)
            volume_statuses = vmi.get("status", {}).get("volumeStatus", [])

            for vs in volume_statuses:
                if vs.get("name") == volume_name:
                    phase = vs.get("phase", "").lower()
                    if phase == "ready":
                        logger.debug(f"[{ns}] Hotplug volume {volume_name} is ready")
                        return True
                    elif "hotplugvolume" in vs:
                        # Volume is being hotplugged, check message
                        msg = vs.get("message", "")
                        if "successfully" in msg.lower():
                            logger.debug(f"[{ns}] Hotplug volume {volume_name} attached")
                            return True

        except Exception as e:
            logger.debug(f"[{ns}] Error checking hotplug status: {e}")

        time.sleep(5)

    logger.warning(f"[{ns}] Timeout waiting for hotplug volume {volume_name} to be ready")
    return False


def hotplug_disks_to_vm(ns: str, vm_name: str, disk_count: int, disk_size: str,
                        storage_class: str, volume_mode: str,
                        disk_prefix: str, logger: logging.Logger, persist: bool = True,
                        dry_run: bool = False) -> Tuple[str, int, int]:
    """
    Create DataVolumes and hotplug them to a VM using virtctl.

    Returns:
        Tuple of (namespace, success_count, failure_count)
    """
    success = 0
    failure = 0

    for i in range(1, disk_count + 1):
        # Use timestamp to ensure unique DV names
        timestamp = int(datetime.now().timestamp())
        dv_name = f"{disk_prefix}-{timestamp}-{i}"

        # Create DataVolume
        logger.info(f"[{ns}] Creating DataVolume {dv_name} ({disk_size}, {volume_mode})")
        if not create_datavolume(ns, dv_name, disk_size, storage_class, volume_mode, logger, dry_run):
            failure += 1
            continue

        # Wait for DV to be ready (skip for dry-run)
        if not dry_run:
            logger.info(f"[{ns}] Waiting for DataVolume {dv_name} to be ready...")
            if not wait_for_dv_ready(ns, dv_name, logger):
                failure += 1
                continue

        # Hotplug the volume to the VM using virtctl
        logger.info(f"[{ns}] Hotplugging {dv_name} to VM {vm_name}...")
        if not hotplug_volume(ns, vm_name, dv_name, logger, persist, dry_run):
            failure += 1
            continue

        # Wait for hotplug to be ready in VMI
        if not dry_run:
            if wait_for_hotplug_ready(ns, vm_name, dv_name, logger):
                logger.info(f"[{ns}] Successfully hotplugged {dv_name} to {vm_name}")
                success += 1
            else:
                logger.warning(f"[{ns}] Hotplug {dv_name} completed but readiness check timed out")
                success += 1  # virtctl succeeded, just readiness check timed out
        else:
            success += 1

    return ns, success, failure


def main():
    """Main execution function."""
    args = parse_args()

    # Setup log file
    if args.log_file:
        log_file = args.log_file
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = f"hotplug_{timestamp}.log"

    logger = setup_logging(args.log_level, log_file)
    logger.info(f"Logging to: {log_file}")

    # Determine target VMs based on mode
    if args.node:
        # Mode 2: Node-based - get VMs running on the specified node
        vm_targets = get_vms_on_node(args.node, args.num_vms, logger)
        if not vm_targets:
            logger.error(f"No running VMs found on node {args.node}")
            sys.exit(1)
        mode_desc = f"Node: {args.node} ({len(vm_targets)} VMs)"
    else:
        # Mode 1: Namespace range
        vm_targets = [
            (f"{args.namespace_prefix}-{i}", args.vm_name)
            for i in range(args.start, args.end + 1)
        ]
        mode_desc = f"Namespace range: {args.start} to {args.end} ({len(vm_targets)} VMs)"

    logger.info("=" * 80)
    logger.info("HOTPLUG DISKS TO KUBEVIRT VMs")
    logger.info("=" * 80)
    logger.info(mode_desc)
    if args.node:
        logger.info(f"Target VMs:")
        for ns, vm in vm_targets[:10]:  # Show first 10
            logger.info(f"  - {ns}/{vm}")
        if len(vm_targets) > 10:
            logger.info(f"  ... and {len(vm_targets) - 10} more")
    else:
        logger.info(f"Namespace prefix: {args.namespace_prefix}")
        logger.info(f"VM name: {args.vm_name}")
    logger.info(f"Disks per VM: {args.disk_count}")
    logger.info(f"Disk size: {args.disk_size}")
    logger.info(f"Storage class: {args.storage_class}")
    logger.info(f"Volume mode: {args.volume_mode}")
    logger.info(f"Concurrency: {args.concurrency}")
    if args.start_io:
        logger.info(f"Start IO: Yes (elbencho on hotplugged disks)")
        logger.info(f"SSH pod: {args.ssh_pod} (ns: {args.ssh_pod_ns})")
    if args.dry_run:
        logger.info("DRY-RUN MODE - No changes will be made")
    logger.info("=" * 80)

    start_time = datetime.now()
    results = []

    # Use 'hotplug-dv' as disk name prefix (similar to Go code pattern)
    disk_prefix = "hotplug-dv"

    # If starting IO, get disks BEFORE hotplug for each VM
    vm_disks_before = {}
    if args.start_io:
        logger.info("Getting disk list before hotplug for each VM...")
        for ns, vm_name in vm_targets:
            ip = get_vmi_ip(ns, vm_name, logger)
            if ip:
                disks_before = get_scsi_disks(
                    ip, args.ssh_pod, args.ssh_pod_ns,
                    args.vm_user, args.vm_password, logger
                )
                vm_disks_before[(ns, vm_name)] = (ip, disks_before)
                logger.debug(f"[{ns}/{vm_name}] Disks before hotplug: {disks_before}")

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                hotplug_disks_to_vm, ns, vm_name, args.disk_count,
                args.disk_size, args.storage_class, args.volume_mode,
                disk_prefix, logger, True, args.dry_run  # persist=True
            ): (ns, vm_name)
            for ns, vm_name in vm_targets
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                ns, vm_name = futures[future]
                logger.error(f"[{ns}/{vm_name}] Failed: {e}")
                results.append((ns, 0, args.disk_count))

    elapsed = (datetime.now() - start_time).total_seconds()

    # Summary
    total_success = sum(r[1] for r in results)
    total_failure = sum(r[2] for r in results)
    total_disks = args.disk_count * len(vm_targets)

    logger.info("")
    logger.info("=" * 80)
    logger.info("HOTPLUG SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total VMs: {len(vm_targets)}")
    logger.info(f"Total disks attempted: {total_disks}")
    logger.info(f"Successful hotplugs: {total_success}")
    logger.info(f"Failed hotplugs: {total_failure}")
    logger.info(f"Total time: {elapsed:.2f}s")
    logger.info("=" * 80)

    # Start IO on hotplugged disks if requested
    if args.start_io and total_success > 0:
        logger.info("")
        logger.info("=" * 80)
        logger.info("STARTING IO ON HOTPLUGGED DISKS")
        logger.info("=" * 80)

        io_start_time = datetime.now()
        io_success = 0
        io_failure = 0

        # Get successful VMs (those with at least one successful hotplug)
        successful_vms = [(ns, vm_name) for ns, vm_name in vm_targets
                         if any(r[0] == ns and r[1] > 0 for r in results)]

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            io_futures = {}
            for ns, vm_name in successful_vms:
                if (ns, vm_name) not in vm_disks_before:
                    logger.warning(f"[{ns}/{vm_name}] No disk snapshot before hotplug, skipping IO")
                    io_failure += 1
                    continue

                ip, disks_before = vm_disks_before[(ns, vm_name)]

                io_futures[executor.submit(
                    start_io_on_vm, ns, vm_name, ip, args.disk_count,
                    args.ssh_pod, args.ssh_pod_ns, args.vm_user, args.vm_password,
                    logger, args.dry_run, disks_before
                )] = (ns, vm_name)

            for future in as_completed(io_futures):
                ns, vm_name = io_futures[future]
                try:
                    if future.result():
                        io_success += 1
                    else:
                        io_failure += 1
                except Exception as e:
                    logger.error(f"[{ns}/{vm_name}] IO start failed: {e}")
                    io_failure += 1

        io_elapsed = (datetime.now() - io_start_time).total_seconds()

        logger.info("")
        logger.info("=" * 80)
        logger.info("IO SUMMARY")
        logger.info("=" * 80)
        logger.info(f"VMs with IO started: {io_success}")
        logger.info(f"VMs with IO failed: {io_failure}")
        logger.info(f"IO start time: {io_elapsed:.2f}s")
        logger.info("=" * 80)

    if total_failure > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
