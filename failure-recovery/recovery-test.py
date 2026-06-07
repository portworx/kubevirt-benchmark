#!/usr/bin/env python3
"""
Node Failure Recovery Test (manual, FAR-operator triggered, or monitor-only)

Three modes:
  manual       - Wait for the operator to power off the node manually (via BMC/IPMI),
                 then measure VM recovery time.
  far-operator - Apply a Fence Agents Remediation (FAR) configuration to trigger
                 node failure, then measure VM recovery time and clean up the FAR
                 configuration when finished.
  monitor      - Do not trigger or wait for node failure. Just discover the VMIs
                 on the target node and measure recovery from "now". Used by
                 `virtbench failure-recovery` and for measuring recovery after a
                 separate failure event has already occurred.

All modes:
  1. Detect VMIs to monitor on the target node
  2. (Optional) Remove nodeSelector from VMs to allow rescheduling
  3. Trigger / wait for node failure (manual + far-operator only)
  4. Wait for the node to become NotReady (manual + far-operator only)
  5. Monitor VM recovery (Running+Ready, optionally ping)
  6. Print summary statistics, optionally save results
  7. (Optional) Cleanup FAR resources, annotations, uncordon nodes, delete VMs

Usage:
    # Manual mode
    python3 recovery-test.py --mode manual --node worker-1 --vm-name rhel-9-vm

    # FAR-operator mode
    python3 recovery-test.py --mode far-operator --node worker-1 --vm-name rhel-9-vm \\
        --far-config far-template.yaml

    # Monitor-only mode (failure already triggered externally)
    python3 recovery-test.py --mode monitor --node worker-1 --vm-name rhel-9-vm

Author: KubeVirt Benchmark Suite Contributors
License: Apache 2.0
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.common import (
    setup_logging,
    run_kubectl_command,
    ping_vm,
    cleanup_test_namespaces,
    confirm_cleanup,
    remove_far_annotation,
    delete_far_resource,
    uncordon_node,
    save_results,
)

# Default values
DEFAULT_VM_NAME = 'rhel-9-vm'
DEFAULT_NAMESPACE_PREFIX = 'perf-test'
DEFAULT_FAR_CONFIG = 'far-template.yaml'
DEFAULT_POLL_INTERVAL = 2
DEFAULT_CONCURRENCY = 128
DEFAULT_NODE_TIMEOUT = 600       # 10 minutes for node to go down
DEFAULT_RECOVERY_TIMEOUT = 600   # 10 minutes for VMs to recover
DEFAULT_SSH_POD = 'ssh-test-pod'
DEFAULT_SSH_POD_NS = 'default'


def run_kubectl(args: List[str], logger: Optional[logging.Logger] = None) -> Tuple[int, str, str]:
    """Run kubectl command and return (returncode, stdout, stderr)."""
    try:
        return run_kubectl_command(args, check=False, timeout=60, logger=logger)
    except Exception as e:
        if logger:
            logger.error(f"kubectl command failed: {e}")
        return 1, '', str(e)


def get_vmis_on_node(node_name: str, vm_name: str, namespace_prefix: str,
                     logger: logging.Logger) -> List[str]:
    """
    Get list of namespaces with VMIs running on the specified node.
    Must be called BEFORE node failure to capture which VMIs are on the node.
    """
    returncode, output, stderr = run_kubectl(
        ['get', 'vmi', '--all-namespaces', '-o', 'json'], logger=logger
    )

    if returncode != 0:
        logger.error(f"Failed to get VMIs: {stderr}")
        return []

    try:
        vmis_data = json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse VMI JSON: {e}")
        return []

    namespaces = []
    for vmi in vmis_data.get('items', []):
        vmi_n = vmi.get('metadata', {}).get('name', '')
        namespace = vmi.get('metadata', {}).get('namespace', '')
        scheduled_node = vmi.get('status', {}).get('nodeName', '')
        phase = vmi.get('status', {}).get('phase', '')

        if vmi_n != vm_name:
            continue
        if namespace_prefix and not namespace.startswith(namespace_prefix):
            continue
        if scheduled_node == node_name:
            namespaces.append(namespace)
            logger.debug(f"Found VMI {vmi_n} in {namespace} on {node_name} (phase: {phase})")

    return namespaces


def remove_node_selector(namespace: str, vm_name: str, logger: logging.Logger) -> bool:
    """Remove nodeSelector from a VM to allow rescheduling."""
    returncode, output, _ = run_kubectl(
        ['get', 'vm', vm_name, '-n', namespace,
         '-o', 'jsonpath={.spec.template.spec.nodeSelector}'],
        logger=logger
    )

    if returncode != 0 or not output.strip():
        logger.debug(f"[{namespace}/{vm_name}] No nodeSelector found, skipping")
        return True

    returncode, _, stderr = run_kubectl(
        ['patch', 'vm', vm_name, '-n', namespace, '--type=json',
         '-p', '[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]'],
        logger=logger
    )

    if returncode == 0:
        logger.debug(f"[{namespace}/{vm_name}] NodeSelector removed")
        return True

    logger.warning(f"[{namespace}/{vm_name}] Failed to remove nodeSelector: {stderr}")
    return False


def remove_node_selectors_parallel(namespaces: List[str], vm_name: str,
                                   concurrency: int, logger: logging.Logger) -> Tuple[int, int]:
    """Remove nodeSelector from all VMs in parallel. Returns (success_count, fail_count)."""
    logger.info(f"Removing nodeSelector from {len(namespaces)} VMs...")

    success = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(remove_node_selector, ns, vm_name, logger): ns
            for ns in namespaces
        }
        for future in as_completed(futures):
            ns = futures[future]
            try:
                if future.result():
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"[{ns}] Error removing nodeSelector: {e}")
                failed += 1

    logger.info(f"NodeSelector removal complete: {success} success, {failed} failed")
    return success, failed


def apply_far_config(far_config: str, logger: logging.Logger) -> bool:
    """Apply FAR configuration to trigger node failure."""
    if not os.path.exists(far_config):
        logger.error(f"FAR config file not found: {far_config}")
        return False

    logger.info(f"Applying FAR configuration from {far_config}")
    returncode, _, stderr = run_kubectl(['apply', '-f', far_config], logger=logger)

    if returncode != 0:
        logger.error(f"Failed to apply FAR config: {stderr}")
        return False

    logger.info("FAR configuration applied successfully")
    return True


def remove_far_config(far_config: str, logger: logging.Logger) -> bool:
    """Remove FAR configuration."""
    if not os.path.exists(far_config):
        logger.warning(f"FAR config file not found: {far_config}, skipping cleanup")
        return False

    logger.info(f"Removing FAR configuration from {far_config}")
    returncode, _, stderr = run_kubectl(['delete', '-f', far_config], logger=logger)

    if returncode != 0:
        logger.warning(f"Failed to remove FAR config: {stderr}")
        return False

    logger.info("FAR configuration removed successfully")
    return True


def wait_for_node_down(node_name: str, timeout: int, mode: str,
                       logger: logging.Logger) -> Optional[datetime]:
    """
    Wait for the target node to become NotReady.
    In manual mode, prompt the operator to power off the node via BMC.
    Returns the timestamp the node was first observed NotReady, or None on timeout.
    """
    if mode == 'manual':
        logger.info("=" * 70)
        logger.info(f"ACTION REQUIRED: Power off node '{node_name}' via BMC/IPMI now")
        logger.info("=" * 70)

    logger.info(f"Waiting for node {node_name} to become NotReady (timeout={timeout}s)...")
    start = time.time()

    while time.time() - start < timeout:
        returncode, output, _ = run_kubectl(
            ['get', 'node', node_name, '-o',
             'jsonpath={.status.conditions[?(@.type=="Ready")].status}'],
            logger=logger
        )

        if returncode == 0 and output.strip() in ('False', 'Unknown'):
            down_ts = datetime.utcnow()
            logger.info(f"Node {node_name} is NotReady at {down_ts.isoformat()}Z "
                        f"(status={output.strip()})")
            return down_ts

        time.sleep(2)

    logger.error(f"Timeout waiting for node {node_name} to become NotReady")
    return None


def get_vmi_status(namespace: str, vmi_name: str,
                   logger: logging.Logger) -> Tuple[str, bool, str]:
    """
    Get VMI phase, ready status, and IP via JSON.
    Returns (phase, ready, ip).
    """
    returncode, output, _ = run_kubectl(
        ['get', 'vmi', vmi_name, '-n', namespace, '-o', 'json'],
        logger=logger
    )

    if returncode != 0:
        return '', False, ''

    try:
        vmi_data = json.loads(output)
    except json.JSONDecodeError:
        return '', False, ''

    phase = vmi_data.get('status', {}).get('phase', '')

    ready = False
    for cond in vmi_data.get('status', {}).get('conditions', []):
        if cond.get('type') == 'Ready' and cond.get('status') == 'True':
            ready = True
            break

    ip = ''
    interfaces = vmi_data.get('status', {}).get('interfaces', [])
    if interfaces:
        ip = interfaces[0].get('ipAddress', '')

    return phase, ready, ip


def wait_for_vmi_running(namespace: str, vmi_name: str, start_ts: datetime,
                         poll_interval: int, timeout: int,
                         logger: logging.Logger) -> Tuple[str, float]:
    """Wait for VMI to be Running and Ready. Returns (final_phase, recovery_seconds)."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        phase, ready, _ = get_vmi_status(namespace, vmi_name, logger)

        if phase == 'Running' and ready:
            elapsed = (datetime.utcnow() - start_ts).total_seconds()
            return phase, elapsed

        time.sleep(poll_interval)

    return 'Timeout', -1.0


def wait_for_ping_recovery(namespace: str, vmi_name: str, ssh_pod: str, ssh_pod_ns: str,
                           start_ts: datetime, poll_interval: int, timeout: int,
                           logger: logging.Logger) -> Tuple[bool, float, str]:
    """
    Wait for the VM to respond to ping. The IP is re-fetched on each iteration in case
    it changes during recovery.
    Returns (ping_success, ping_recovery_seconds, ip).
    """
    deadline = time.time() + timeout
    last_ip = ''

    while time.time() < deadline:
        _, _, ip = get_vmi_status(namespace, vmi_name, logger)

        if ip:
            last_ip = ip
            if ping_vm(ip, ssh_pod, ssh_pod_ns, logger):
                elapsed = (datetime.utcnow() - start_ts).total_seconds()
                return True, elapsed, ip

        time.sleep(poll_interval)

    return False, -1.0, last_ip


def monitor_single_vm(namespace: str, vmi_name: str, node_down_ts: datetime,
                      ssh_pod: str, ssh_pod_ns: str, poll_interval: int,
                      recovery_timeout: int, do_ping: bool,
                      logger: logging.Logger) -> Dict:
    """Monitor recovery of a single VMI. Returns a result dict."""
    result = {
        'namespace': namespace,
        'vmi': vmi_name,
        'phase': '',
        'recovery_seconds': -1.0,
        'ping_success': False,
        'ping_recovery_seconds': -1.0,
        'ip': '',
    }

    phase, recovery_secs = wait_for_vmi_running(
        namespace, vmi_name, node_down_ts, poll_interval, recovery_timeout, logger
    )
    result['phase'] = phase
    result['recovery_seconds'] = recovery_secs

    if phase != 'Running' or recovery_secs < 0:
        logger.warning(f"[{namespace}/{vmi_name}] Did not reach Running+Ready (phase={phase})")
        return result

    logger.info(f"[{namespace}/{vmi_name}] Running+Ready in {recovery_secs:.1f}s")

    if do_ping:
        ping_ok, ping_secs, ip = wait_for_ping_recovery(
            namespace, vmi_name, ssh_pod, ssh_pod_ns,
            node_down_ts, poll_interval, recovery_timeout, logger
        )
        result['ping_success'] = ping_ok
        result['ping_recovery_seconds'] = ping_secs
        result['ip'] = ip

        if ping_ok:
            logger.info(f"[{namespace}/{vmi_name}] Ping recovered in {ping_secs:.1f}s (IP={ip})")
        else:
            logger.warning(f"[{namespace}/{vmi_name}] Ping did not recover within timeout")

    return result


def monitor_vm_recovery(namespaces: List[str], vmi_name: str, node_down_ts: datetime,
                        ssh_pod: str, ssh_pod_ns: str, poll_interval: int,
                        recovery_timeout: int, concurrency: int, do_ping: bool,
                        logger: logging.Logger) -> List[Dict]:
    """Monitor recovery of all VMIs in parallel."""
    logger.info(f"Monitoring recovery of {len(namespaces)} VMIs "
                f"(timeout={recovery_timeout}s, ping={do_ping})...")

    results: List[Dict] = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(monitor_single_vm, ns, vmi_name, node_down_ts,
                            ssh_pod, ssh_pod_ns, poll_interval, recovery_timeout,
                            do_ping, logger): ns
            for ns in namespaces
        }
        for future in as_completed(futures):
            ns = futures[future]
            try:
                results.append(future.result())
            except Exception as e:
                logger.error(f"[{ns}] Error monitoring VM: {e}")
                results.append({
                    'namespace': ns,
                    'vmi': vmi_name,
                    'phase': 'Error',
                    'recovery_seconds': -1.0,
                    'ping_success': False,
                    'ping_recovery_seconds': -1.0,
                    'ip': '',
                })

    return results


def print_summary(results: List[Dict], do_ping: bool, logger: logging.Logger) -> None:
    """Print detailed per-VMI table and summary statistics."""
    total = len(results)
    recovered = [r for r in results if r['phase'] == 'Running' and r['recovery_seconds'] >= 0]
    pinged = [r for r in results if r['ping_success']]

    logger.info("")
    logger.info("=" * 100)
    logger.info(f"{'Namespace':<30}{'Time to Run(s)':<15}{'Time to Ping(s)':<17}{'IP':<20}{'Status':<15}")
    logger.info("-" * 100)

    if not do_ping:
        logger.info("Ping recovery checks were skipped (--skip-ping enabled)")

    for r in sorted(results, key=lambda x: x['namespace']):
        run_str = f"{r['recovery_seconds']:.2f}" if r['recovery_seconds'] >= 0 else 'Failed'
        if not do_ping:
            ping_str = 'Skipped'
        elif r['ping_recovery_seconds'] >= 0:
            ping_str = f"{r['ping_recovery_seconds']:.2f}"
        else:
            ping_str = 'Failed'

        if r['phase'] == 'Running' and (not do_ping or r['ping_success']):
            status = 'OK'
        elif r['phase'] == 'Running':
            status = 'No-Ping'
        else:
            status = r['phase'] or 'Error'

        logger.info(f"{r['namespace']:<30}{run_str:<15}{ping_str:<17}{r['ip']:<20}{status:<15}")

    logger.info("=" * 100)
    logger.info("")
    logger.info("=" * 70)
    logger.info("RECOVERY SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Total VMIs monitored: {total}")
    logger.info(f"Recovered to Running+Ready: {len(recovered)}/{total}")

    if recovered:
        rec_times = [r['recovery_seconds'] for r in recovered]
        logger.info(f"  Recovery time min/avg/max: "
                    f"{min(rec_times):.1f}s / {sum(rec_times)/len(rec_times):.1f}s / "
                    f"{max(rec_times):.1f}s")

    if do_ping:
        logger.info(f"Ping recovered: {len(pinged)}/{total}")
        if pinged:
            ping_times = [r['ping_recovery_seconds'] for r in pinged]
            logger.info(f"  Ping recovery time min/avg/max: "
                        f"{min(ping_times):.1f}s / {sum(ping_times)/len(ping_times):.1f}s / "
                        f"{max(ping_times):.1f}s")

    failed = [r for r in results if r['phase'] != 'Running' or r['recovery_seconds'] < 0]
    if failed:
        logger.info(f"Failed/timeout VMIs: {len(failed)}")
        for r in failed:
            logger.info(f"  - {r['namespace']}/{r['vmi']} (phase={r['phase']})")

    logger.info("=" * 70)


def results_to_tuples(results: List[Dict]) -> List[Tuple]:
    """Convert result dicts to 5-tuples expected by utils.common.save_results."""
    tuples = []
    for r in results:
        run_t = r['recovery_seconds'] if r['recovery_seconds'] >= 0 else None
        ping_t = r['ping_recovery_seconds'] if r['ping_recovery_seconds'] >= 0 else None
        success = r['phase'] == 'Running' and r['recovery_seconds'] >= 0
        tuples.append((r['namespace'], run_t, ping_t, None, success))
    return tuples


def run_cleanup_phase(args: argparse.Namespace, namespaces: List[str],
                      logger: logging.Logger) -> None:
    """Run post-test cleanup of FAR resources, annotations, nodes, and optionally VMs."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("CLEANUP - FAR Resources")
    logger.info("=" * 80)

    if not args.dry_run_cleanup and not confirm_cleanup(len(namespaces), args.yes):
        logger.info("Cleanup cancelled by user")
        return

    prefix = '[DRY RUN] ' if args.dry_run_cleanup else ''
    logger.info(f"{prefix}Cleaning up FAR resources...")

    stats = {'far_deleted': 0, 'annotations_removed': 0, 'nodes_uncordoned': 0, 'errors': 0}

    if args.far_name:
        if args.dry_run_cleanup:
            logger.info(f"[DRY RUN] Would delete FAR resource {args.far_name} "
                        f"in namespace {args.far_namespace}")
        elif delete_far_resource(args.far_name, args.far_namespace, logger):
            stats['far_deleted'] = 1
        else:
            stats['errors'] += 1
    else:
        logger.warning("No --far-name specified, skipping FAR resource deletion")

    logger.info("Removing FAR annotations from VMs...")
    for ns in namespaces:
        if args.dry_run_cleanup:
            logger.info(f"[DRY RUN] Would remove FAR annotation from VM {args.vm_name} in {ns}")
        elif remove_far_annotation(args.vm_name, ns, logger):
            stats['annotations_removed'] += 1
        else:
            stats['errors'] += 1

    failed_node = args.failed_node or args.node
    if failed_node:
        if args.dry_run_cleanup:
            logger.info(f"[DRY RUN] Would uncordon node {failed_node}")
        elif uncordon_node(failed_node, logger):
            stats['nodes_uncordoned'] = 1
        else:
            stats['errors'] += 1
    else:
        logger.warning("No --failed-node or --node specified, skipping node uncordon")

    if args.cleanup_vms:
        logger.info("")
        logger.info("Cleaning up VMs and namespaces...")
        cleanup_start, cleanup_end = _resolve_cleanup_range(args, namespaces, logger)
        if cleanup_start is not None and cleanup_end is not None:
            vm_stats = cleanup_test_namespaces(
                namespace_prefix=args.namespace_prefix,
                start=cleanup_start, end=cleanup_end, vm_name=args.vm_name,
                delete_namespaces=True, dry_run=args.dry_run_cleanup,
                batch_size=args.concurrency, logger=logger,
            )
            stats.update({
                'namespaces_deleted': vm_stats.get('namespaces_deleted', 0),
                'vms_deleted': vm_stats.get('total_vms_deleted', 0),
                'dvs_deleted': vm_stats.get('total_dvs_deleted', 0),
                'pvcs_deleted': vm_stats.get('total_pvcs_deleted', 0),
            })
            stats['errors'] += vm_stats.get('total_errors', 0)

    _log_cleanup_summary(stats, args.cleanup_vms, args.dry_run_cleanup, logger)


def _resolve_cleanup_range(args: argparse.Namespace, namespaces: List[str],
                           logger: logging.Logger) -> Tuple[Optional[int], Optional[int]]:
    """Determine the start/end namespace indices for VM cleanup from discovered namespaces."""
    indices = []
    for ns in namespaces:
        try:
            indices.append(int(ns.split('-')[-1]))
        except ValueError:
            continue
    if indices:
        return min(indices), max(indices)

    logger.warning("Could not determine namespace index range for VM cleanup")
    return None, None


def _log_cleanup_summary(stats: Dict, cleanup_vms: bool, dry_run: bool,
                         logger: logging.Logger) -> None:
    """Log a summary of cleanup actions."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("FAR CLEANUP SUMMARY")
    logger.info("=" * 80)
    logger.info(f"  FAR Resources Deleted:       {stats['far_deleted']}")
    logger.info(f"  Annotations Removed:         {stats['annotations_removed']}")
    logger.info(f"  Nodes Uncordoned:            {stats['nodes_uncordoned']}")
    if cleanup_vms:
        logger.info(f"  Namespaces Deleted:          {stats.get('namespaces_deleted', 0)}")
        logger.info(f"  VMs Deleted:                 {stats.get('vms_deleted', 0)}")
        logger.info(f"  DataVolumes Deleted:         {stats.get('dvs_deleted', 0)}")
        logger.info(f"  PVCs Deleted:                {stats.get('pvcs_deleted', 0)}")
    logger.info(f"  Errors:                      {stats['errors']}")
    logger.info("=" * 80)
    if not dry_run:
        logger.info("Cleanup completed successfully!")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Node failure recovery test (manual, FAR-operator, or monitor-only)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Manual: power off a node yourself and measure VM recovery
  %(prog)s --mode manual --node worker-1 --vm-name rhel-9-vm

  # FAR operator: trigger node failure via Fence Agents Remediation
  %(prog)s --mode far-operator --node worker-1 --vm-name rhel-9-vm \\
      --far-config far-template.yaml

  # Monitor only (failure already triggered externally)
  %(prog)s --mode monitor --node worker-1 --vm-name rhel-9-vm
""",
    )

    parser.add_argument('--mode', required=True,
                        choices=['manual', 'far-operator', 'monitor'],
                        help='Failure trigger mode (manual/far-operator trigger the '
                             'failure; monitor only measures recovery)')
    parser.add_argument('--node', required=True,
                        help='Target node name (the node that will fail / has failed)')
    parser.add_argument('--vm-name', default=DEFAULT_VM_NAME,
                        help=f'VM/VMI name to monitor (default: {DEFAULT_VM_NAME})')
    parser.add_argument('--vm-template', default=None,
                        help='Path to VM template YAML (informational, not used by '
                             'this script directly)')
    parser.add_argument('--namespace-prefix', default=DEFAULT_NAMESPACE_PREFIX,
                        help=f'Only consider namespaces starting with this prefix '
                             f'(default: {DEFAULT_NAMESPACE_PREFIX})')
    parser.add_argument('--storage-class', default=None,
                        help='Storage class name (informational, not used by this '
                             'script directly)')
    parser.add_argument('--far-config', default=DEFAULT_FAR_CONFIG,
                        help=f'FAR YAML manifest (far-operator mode only, '
                             f'default: {DEFAULT_FAR_CONFIG})')
    parser.add_argument('--remove-node-selector', action='store_true',
                        help='Remove nodeSelector from VMs to allow rescheduling')
    parser.add_argument('--ping', dest='ping', action='store_true', default=True,
                        help='Measure ping recovery time (default: enabled)')
    parser.add_argument('--skip-ping', dest='ping', action='store_false',
                        help='Skip ping recovery checks')
    parser.add_argument('--ssh-pod', default=DEFAULT_SSH_POD,
                        help=f'SSH pod name for ping (default: {DEFAULT_SSH_POD})')
    parser.add_argument('--ssh-pod-namespace', default=DEFAULT_SSH_POD_NS,
                        help=f'SSH pod namespace (default: {DEFAULT_SSH_POD_NS})')
    parser.add_argument('--poll-interval', type=int, default=DEFAULT_POLL_INTERVAL,
                        help=f'Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL})')
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY,
                        help=f'Maximum parallel workers (default: {DEFAULT_CONCURRENCY})')
    parser.add_argument('--node-timeout', type=int, default=DEFAULT_NODE_TIMEOUT,
                        help=f'Timeout for node to become NotReady '
                             f'(default: {DEFAULT_NODE_TIMEOUT}s)')
    parser.add_argument('--recovery-timeout', type=int, default=DEFAULT_RECOVERY_TIMEOUT,
                        help=f'Timeout for VM recovery '
                             f'(default: {DEFAULT_RECOVERY_TIMEOUT}s)')

    parser.add_argument('--cleanup', action='store_true',
                        help='After the test, clean up FAR resources, annotations, '
                             'and uncordon the node')
    parser.add_argument('--cleanup-vms', action='store_true',
                        help='Also delete VMs and namespaces during cleanup '
                             '(requires --cleanup)')
    parser.add_argument('--dry-run-cleanup', action='store_true',
                        help='Show what cleanup would do without applying changes')
    parser.add_argument('--far-name', default=None,
                        help='Name of FAR resource to delete during cleanup')
    parser.add_argument('--far-namespace', default='default',
                        help='Namespace of FAR resource (default: default)')
    parser.add_argument('--failed-node', default=None,
                        help='Node to uncordon during cleanup (defaults to --node)')
    parser.add_argument('-y', '--yes', action='store_true',
                        help='Skip confirmation prompt for cleanup')

    parser.add_argument('--save-results', action='store_true',
                        help='Save detailed results to a results folder')
    parser.add_argument('--results-folder', default='../results',
                        help='Base directory for saved results (default: ../results)')
    parser.add_argument('--storage-driver', default=None,
                        help='Storage driver label included in results path (optional)')

    parser.add_argument('--log-file', help='Optional log file path')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Logging level (default: INFO)')

    args = parser.parse_args()

    if args.mode == 'far-operator' and not args.far_config:
        parser.error('--far-config is required when --mode far-operator')

    if args.cleanup_vms and not args.cleanup:
        parser.error('--cleanup-vms requires --cleanup')

    return args


def save_test_results(args: argparse.Namespace, results: List[Dict],
                      logger: logging.Logger) -> None:
    """Save results to disk using utils.common.save_results."""
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    suffix = f"{args.namespace_prefix}_{args.node}"

    if args.storage_driver:
        out_dir = os.path.join(args.results_folder, args.storage_driver,
                               'failure-recovery', f"{timestamp}_{suffix}")
    else:
        out_dir = os.path.join(args.results_folder, 'failure-recovery',
                               f"{timestamp}_{suffix}")
    os.makedirs(out_dir, exist_ok=True)
    logger.info(f"Created results directory: {out_dir}")

    save_results(
        args,
        results_to_tuples(results),
        base_dir=out_dir,
        prefix='failure_recovery_results',
        logger=logger,
        skip_clone=True,
    )
    logger.info(f"Detailed and summary results saved under: {out_dir}")


def main() -> int:
    args = parse_args()
    logger = setup_logging(log_file=args.log_file, log_level=args.log_level)

    logger.info("=" * 70)
    logger.info(f"Node Failure Recovery Test (mode={args.mode})")
    logger.info("=" * 70)
    logger.info(f"Target node: {args.node}")
    logger.info(f"VM name: {args.vm_name}")
    logger.info(f"Namespace prefix: {args.namespace_prefix}")
    if args.mode == 'far-operator':
        logger.info(f"FAR config: {args.far_config}")
    logger.info(f"Remove nodeSelector: {args.remove_node_selector}")
    logger.info(f"Ping recovery check: {args.ping}")

    # 1. Detect VMIs to monitor on the target node
    logger.info(f"Detecting VMIs on node {args.node}...")
    namespaces = get_vmis_on_node(args.node, args.vm_name,
                                  args.namespace_prefix, logger)
    if not namespaces:
        logger.error(f"No VMIs named {args.vm_name} found on node {args.node}")
        return 1
    logger.info(f"Found {len(namespaces)} VMIs on {args.node}")

    # 2. Optionally remove nodeSelector
    if args.remove_node_selector:
        remove_node_selectors_parallel(namespaces, args.vm_name, args.concurrency, logger)

    # 3. Trigger / wait for node failure (manual + far-operator only)
    far_applied = False
    if args.mode == 'far-operator':
        def cleanup_handler(signum, frame):
            logger.warning(f"Received signal {signum}, cleaning up FAR config...")
            if far_applied:
                remove_far_config(args.far_config, logger)
            sys.exit(130)

        signal.signal(signal.SIGINT, cleanup_handler)
        signal.signal(signal.SIGTERM, cleanup_handler)

        if not apply_far_config(args.far_config, logger):
            return 1
        far_applied = True

    rc = 0
    try:
        # 4. Determine the start timestamp for measurement
        if args.mode == 'monitor':
            node_down_ts = datetime.utcnow()
            logger.info(f"Monitor mode: using current time as start "
                        f"({node_down_ts.isoformat()}Z)")
        else:
            node_down_ts = wait_for_node_down(args.node, args.node_timeout,
                                              args.mode, logger)
            if node_down_ts is None:
                return 1

        # 5. Monitor VM recovery
        results = monitor_vm_recovery(
            namespaces, args.vm_name, node_down_ts,
            args.ssh_pod, args.ssh_pod_namespace,
            args.poll_interval, args.recovery_timeout,
            args.concurrency, args.ping, logger,
        )

        # 6. Summary
        print_summary(results, args.ping, logger)

        if args.save_results:
            save_test_results(args, results, logger)

        recovered = sum(1 for r in results
                        if r['phase'] == 'Running' and r['recovery_seconds'] >= 0)
        rc = 0 if recovered == len(results) else 2

    finally:
        if far_applied:
            remove_far_config(args.far_config, logger)

    # 7. Optional cleanup phase
    if args.cleanup:
        try:
            run_cleanup_phase(args, namespaces, logger)
        except Exception as e:
            logger.error(f"Cleanup phase failed: {e}")
            if rc == 0:
                rc = 3

    return rc


if __name__ == '__main__':
    sys.exit(main())
