#!/usr/bin/env python3
"""
Power VMs on or off.

Two modes are supported via --action:

  --action off   Find running VMs on a node, select a percentage of them,
                 and power them off in parallel. The selected VMs are saved
                 to a list file so the same set can later be powered back on.

  --action on    Power on a range of VMs by namespace (default), or restore
                 a previously saved list, or filter by the node a VM was
                 last scheduled on. Powered-off VMs are not bound to a node,
                 so node-only discovery is not possible — a namespace range
                 (or list file) is required.

Usage:
    # Power off 50% of running VMs on a node
    python3 power-toggle-vms.py --action off --node worker-1 --percentage 50

    # Power on VMs in a namespace range (default mode)
    python3 power-toggle-vms.py --action on --namespace-prefix migration --start 1 --end 50

    # Power on VMs in a range, but only those whose last-known node matches
    python3 power-toggle-vms.py --action on --namespace-prefix migration \\
        --start 1 --end 50 --node worker-1

    # Power on VMs from a saved list file
    python3 power-toggle-vms.py --action on --vm-list-file powered_off_vms_worker-1_*.txt

    # Dry run
    python3 power-toggle-vms.py --action off --node worker-1 --percentage 50 --dry-run
"""

import argparse
import json
import logging
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Tuple, Dict, Optional


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging."""
    logger = logging.getLogger("power-toggle-vms")
    logger.setLevel(getattr(logging, level.upper()))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    return logger


def run_kubectl(args: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    """Run kubectl command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["kubectl"] + args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


def get_running_vms_on_node(node_name: str, logger: logging.Logger) -> List[Dict]:
    """Return running VMIs on *node_name* (used by --action off)."""
    rc, stdout, stderr = run_kubectl(["get", "vmi", "-A", "-o", "json"])
    if rc != 0:
        logger.error(f"Failed to get VMIs: {stderr}")
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse VMI JSON: {e}")
        return []

    vms = []
    for item in data.get("items", []):
        status = item.get("status", {})
        if status.get("nodeName") == node_name and status.get("phase") == "Running":
            vms.append({
                "namespace": item["metadata"]["namespace"],
                "name": item["metadata"]["name"],
                "node": node_name,
            })
    return vms


def get_vm_node_selector(namespace: str, vm_name: str,
                         logger: logging.Logger) -> Optional[str]:
    """Return the kubernetes.io/hostname nodeSelector for a VM, or None."""
    rc, stdout, stderr = run_kubectl([
        "get", "vm", vm_name, "-n", namespace,
        "-o", "jsonpath={.spec.template.spec.nodeSelector.kubernetes\\.io/hostname}",
    ], timeout=15)
    if rc != 0:
        return None
    val = stdout.strip()
    return val or None


def discover_vms_in_range(namespace_prefix: str, start: int, end: int,
                          vm_name: str, node_filter: Optional[str],
                          logger: logging.Logger) -> List[Dict]:
    """
    Enumerate ``{namespace_prefix}-{i}`` for i in [start, end] and return the
    VMs that exist (regardless of running state). When *node_filter* is set,
    only VMs whose nodeSelector matches *node_filter* are returned.
    """
    vms: List[Dict] = []
    skipped_missing = 0
    skipped_node = 0

    for i in range(start, end + 1):
        ns = f"{namespace_prefix}-{i}"
        rc, _, stderr = run_kubectl(
            ["get", "vm", vm_name, "-n", ns, "-o", "name"], timeout=10
        )
        if rc != 0:
            skipped_missing += 1
            continue

        if node_filter:
            ns_node = get_vm_node_selector(ns, vm_name, logger)
            if ns_node != node_filter:
                skipped_node += 1
                continue
            vms.append({"namespace": ns, "name": vm_name, "node": ns_node})
        else:
            vms.append({"namespace": ns, "name": vm_name, "node": ""})

    if skipped_missing:
        logger.info(f"Skipped {skipped_missing} namespace(s) where VM '{vm_name}' was not found")
    if skipped_node:
        logger.info(f"Skipped {skipped_node} VM(s) not matching --node {node_filter}")
    return vms


def _virtctl_action(action: str, namespace: str, vm_name: str,
                    logger: logging.Logger,
                    dry_run: bool = False) -> Tuple[str, str, bool, float]:
    """Send `virtctl {start|stop} <vm>`. Returns (ns, name, success, duration)."""
    log_prefix = f"[{namespace}/{vm_name}]"
    started = time.time()
    verb = "stop" if action == "off" else "start"

    if dry_run:
        logger.info(f"{log_prefix} DRY-RUN: Would {verb} VM")
        return namespace, vm_name, True, 0.0

    # Prefer `kubectl virt <verb>` (krew plugin) and fall back to `virtctl`.
    rc, _, stderr = run_kubectl(["virt", verb, vm_name, "-n", namespace], timeout=30)
    if rc != 0:
        try:
            result = subprocess.run(
                ["virtctl", verb, vm_name, "-n", namespace],
                capture_output=True, text=True, timeout=30
            )
            rc, stderr = result.returncode, result.stderr
        except Exception as e:
            logger.warning(f"{log_prefix} Failed to {verb} VM: {e}")
            return namespace, vm_name, False, time.time() - started

    if rc != 0:
        logger.warning(f"{log_prefix} Failed to {verb} VM: {stderr}")
        return namespace, vm_name, False, time.time() - started

    logger.debug(f"{log_prefix} {verb} command sent")
    return namespace, vm_name, True, time.time() - started


def wait_for_vm_phase(namespace: str, vm_name: str, target: str,
                      logger: logging.Logger, timeout: int = 300) -> bool:
    """
    Wait for a VM to reach a target state.

    target == "stopped":  VMI deleted / Succeeded / Failed
    target == "running":  VMI phase == Running
    """
    log_prefix = f"[{namespace}/{vm_name}]"
    started = time.time()
    while time.time() - started < timeout:
        rc, stdout, stderr = run_kubectl([
            "get", "vmi", vm_name, "-n", namespace,
            "-o", "jsonpath={.status.phase}"
        ], timeout=10)

        if target == "stopped":
            if rc != 0 or "not found" in stderr.lower():
                return True
            phase = stdout.strip()
            if phase in ("Succeeded", "Failed"):
                return True
        elif target == "running":
            if rc == 0 and stdout.strip() == "Running":
                return True

        time.sleep(2)

    logger.warning(f"{log_prefix} Timeout waiting for VM to be {target}")
    return False


def load_vm_list_file(path: str, logger: logging.Logger) -> List[Dict]:
    """Read 'namespace/name' lines from *path* into VM dicts."""
    vms: List[Dict] = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and "/" in line:
                    ns, name = line.split("/", 1)
                    vms.append({"namespace": ns, "name": name})
    except Exception as e:
        logger.error(f"Failed to read VM list file '{path}': {e}")
        sys.exit(1)
    if not vms:
        logger.error(f"No VMs found in list file '{path}'")
        sys.exit(1)
    return vms


def save_vm_list_file(vms: List[Dict], node: str, logger: logging.Logger) -> str:
    """Write a `powered_off_vms_<node>_<ts>.txt` snapshot for later restore."""
    fname = f"powered_off_vms_{node}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(fname, "w") as f:
        for vm in vms:
            f.write(f"{vm['namespace']}/{vm['name']}\n")
    logger.info(f"Saved VM list to: {fname}")
    return fname


def _run_action_in_parallel(action: str, vms: List[Dict], concurrency: int,
                            wait_timeout: int, dry_run: bool,
                            logger: logging.Logger) -> None:
    """Send the start/stop command to every VM and wait for the target phase."""
    verb = "stop" if action == "off" else "start"
    target_phase = "stopped" if action == "off" else "running"

    logger.info("=" * 60)
    logger.info(f"POWERING {action.upper()} {len(vms)} VM(s)")
    logger.info("=" * 60)

    cmd_start = datetime.now()
    results: List[Tuple[str, str, bool]] = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_virtctl_action, action, vm["namespace"],
                            vm["name"], logger, dry_run): vm
            for vm in vms
        }
        for future in as_completed(futures):
            ns, name, success, duration = future.result()
            results.append((ns, name, success))
            if success:
                logger.info(f"[{ns}/{name}] {verb} command sent ({duration:.2f}s)")

    cmd_elapsed = (datetime.now() - cmd_start).total_seconds()
    logger.info(f"All {verb} commands sent in {cmd_elapsed:.2f}s")

    if dry_run:
        return

    logger.info("")
    logger.info(f"Waiting for VMs to be {target_phase}...")
    wait_start = datetime.now()
    reached = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(wait_for_vm_phase, ns, name, target_phase,
                            logger, wait_timeout): (ns, name)
            for ns, name, ok in results if ok
        }
        for future in as_completed(futures):
            if future.result():
                reached += 1

    total = (datetime.now() - cmd_start).total_seconds()
    wait_elapsed = (datetime.now() - wait_start).total_seconds()
    success_count = sum(1 for _, _, ok in results if ok)
    failure_count = len(results) - success_count

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"POWER {action.upper()} SUMMARY")
    logger.info("=" * 60)
    logger.info(f"VMs targeted:           {len(vms)}")
    logger.info(f"{verb.capitalize()} commands sent:    {success_count}")
    logger.info(f"{verb.capitalize()} commands failed:  {failure_count}")
    logger.info(f"VMs reached '{target_phase}':  {reached}")
    logger.info("-" * 60)
    logger.info(f"Time to send commands:  {cmd_elapsed:.2f}s")
    logger.info(f"Time to reach target:   {wait_elapsed:.2f}s")
    logger.info(f"Total time:             {total:.2f}s")
    logger.info("=" * 60)


def _do_power_off(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Discover, sample, save, then power off VMs."""
    if args.vm_list_file:
        vms = load_vm_list_file(args.vm_list_file, logger)
        logger.info(f"Powering OFF {len(vms)} VM(s) from {args.vm_list_file}")
        if args.dry_run:
            for vm in vms:
                logger.info(f"  Would stop: {vm['namespace']}/{vm['name']}")
            return
        _run_action_in_parallel("off", vms, args.concurrency,
                                args.wait_timeout, args.dry_run, logger)
        return

    if not args.node:
        logger.error("--action off requires --node (or --vm-list-file)")
        sys.exit(1)

    logger.info(f"Finding running VMs on node: {args.node}")
    all_vms = get_running_vms_on_node(args.node, logger)
    if not all_vms:
        logger.error(f"No running VMs found on node {args.node}")
        sys.exit(1)
    logger.info(f"Found {len(all_vms)} running VM(s) on {args.node}")

    num_to_stop = max(1, int(len(all_vms) * args.percentage / 100))
    vms_to_stop = random.sample(all_vms, num_to_stop)
    logger.info(f"Selected {len(vms_to_stop)} VM(s) ({args.percentage}%) to power off")
    save_vm_list_file(vms_to_stop, args.node, logger)

    if args.dry_run:
        logger.info("DRY-RUN MODE - No changes will be made")
        for vm in vms_to_stop:
            logger.info(f"  Would stop: {vm['namespace']}/{vm['name']}")
        return

    _run_action_in_parallel("off", vms_to_stop, args.concurrency,
                            args.wait_timeout, args.dry_run, logger)


def _do_power_on(args: argparse.Namespace, logger: logging.Logger) -> None:
    """
    Discover and power on VMs. Default is range-based — powered-off VMs are
    not bound to any node so a namespace range (or list file) is required.
    """
    if args.vm_list_file:
        vms = load_vm_list_file(args.vm_list_file, logger)
        logger.info(f"Powering ON {len(vms)} VM(s) from {args.vm_list_file}")
    else:
        missing = [k for k in ("namespace_prefix", "start", "end", "vm_name")
                   if getattr(args, k) is None]
        if missing:
            logger.error("--action on requires --namespace-prefix, --start, --end, "
                         "and --vm-name (or --vm-list-file). Missing: "
                         f"{', '.join('--' + m.replace('_', '-') for m in missing)}")
            sys.exit(1)
        logger.info(f"Discovering VMs in {args.namespace_prefix}-{args.start}..{args.end}"
                    + (f" filtered to node '{args.node}'" if args.node else ""))
        vms = discover_vms_in_range(args.namespace_prefix, args.start, args.end,
                                    args.vm_name, args.node, logger)
        if not vms:
            logger.error("No VMs matched the discovery criteria")
            sys.exit(1)
        logger.info(f"Found {len(vms)} VM(s) to power on")

    if args.dry_run:
        logger.info("DRY-RUN MODE - No changes will be made")
        for vm in vms:
            logger.info(f"  Would start: {vm['namespace']}/{vm['name']}")
        return

    _run_action_in_parallel("on", vms, args.concurrency,
                            args.wait_timeout, args.dry_run, logger)



def main():
    parser = argparse.ArgumentParser(
        description="Power VMs on or off (--action {on,off})",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--action", required=True, choices=["on", "off"],
                        help="Whether to power VMs on or off")

    # Selection: --action off uses --node + --percentage. --action on uses
    # --namespace-prefix/--start/--end (default), with optional --node filter,
    # OR --vm-list-file.
    parser.add_argument("--node", default=None,
                        help="Node name. Required for --action off. Optional "
                             "filter for --action on (matches VM nodeSelector).")
    parser.add_argument("--percentage", type=int, default=50,
                        help="Percentage of VMs to power off (default: 50, --action off only)")

    parser.add_argument("--namespace-prefix", default=None,
                        help="Namespace prefix for range-based discovery (--action on)")
    parser.add_argument("--start", type=int, default=None,
                        help="Start index for namespace range (--action on)")
    parser.add_argument("--end", type=int, default=None,
                        help="End index for namespace range (--action on)")
    parser.add_argument("--vm-name", default=None,
                        help="VM resource name to look for in each namespace (--action on)")

    parser.add_argument("--vm-list-file", default=None,
                        help="File of 'namespace/name' lines to act on. Bypasses "
                             "node/range discovery for both --action on and --action off.")

    parser.add_argument("--concurrency", type=int, default=50,
                        help="Max concurrent operations (default: 50)")
    parser.add_argument("--wait-timeout", type=int, default=300,
                        help="Timeout waiting for VMs to reach target phase (default: 300s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without doing it")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()
    logger = setup_logging(args.log_level)

    if args.action == "off":
        _do_power_off(args, logger)
    else:
        _do_power_on(args, logger)


if __name__ == "__main__":
    main()
