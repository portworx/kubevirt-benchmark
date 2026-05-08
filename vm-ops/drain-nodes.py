#!/usr/bin/env python3
"""
Drain Kubernetes nodes and measure drain time.

This script drains one or more nodes and tracks how long each drain takes.
Can simulate OCP upgrade by draining nodes sequentially with uncordon.

Usage:
    # Drain a single node
    python3 drain-nodes.py --nodes worker-1

    # Drain multiple nodes sequentially
    python3 drain-nodes.py --nodes worker-1 worker-2 worker-3

    # Drain multiple nodes in parallel
    python3 drain-nodes.py --nodes worker-1 worker-2 worker-3 --parallel

    # Drain with custom timeout
    python3 drain-nodes.py --nodes worker-1 --timeout 600

    # Simulate OCP upgrade (drain -> wait -> uncordon -> next node)
    python3 drain-nodes.py --nodes worker-1 worker-2 worker-3 \
        --timeout 1800 --uncordon-after --wait-between 120

    # Dry run
    python3 drain-nodes.py --nodes worker-1 --dry-run
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Tuple, Dict


def setup_logging(level: str = "INFO", log_file: str = None) -> logging.Logger:
    """Configure logging to console and optionally to a file."""
    logger = logging.getLogger("drain-nodes")
    logger.setLevel(getattr(logging, level.upper()))

    logger.handlers = []

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        import os
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.FileHandler(log_file, mode='a')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.info(f"Logging to file: {log_file}")

    return logger


def get_node_pod_count(node_name: str) -> int:
    """Get the number of pods running on a node."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "--all-namespaces", "-o", "wide",
             "--field-selector", f"spec.nodeName={node_name}"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            # Count lines minus header
            lines = result.stdout.strip().split('\n')
            return max(0, len(lines) - 1)
    except Exception:
        pass
    return -1


def get_node_status(node_name: str) -> str:
    """Get node scheduling status."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "node", node_name, "-o",
             "jsonpath={.spec.unschedulable}"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return "SchedulingDisabled" if result.stdout.strip() == "true" else "Ready"
    except Exception:
        pass
    return "Unknown"


def get_pods_on_node(node_name: str) -> List[Dict]:
    """Get all pods on a node with their owner info."""
    pods = []
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "--all-namespaces",
             "--field-selector", f"spec.nodeName={node_name}",
             "-o", "json"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for item in data.get("items", []):
                namespace = item.get("metadata", {}).get("namespace", "")
                name = item.get("metadata", {}).get("name", "")
                status = item.get("status", {}).get("phase", "Unknown")
                owner_refs = item.get("metadata", {}).get("ownerReferences", [])
                owner_kind = owner_refs[0].get("kind", "Unknown") if owner_refs else "Unknown"
                pods.append({
                    "name": f"{namespace}/{name}",
                    "owner": owner_kind,
                    "status": status
                })
    except Exception:
        pass
    return pods


def get_daemonset_pods(node_name: str) -> List[Dict]:
    """Get list of DaemonSet pods on a node."""
    all_pods = get_pods_on_node(node_name)
    return [p for p in all_pods if p["owner"] == "DaemonSet"]


def get_remaining_pods(node_name: str) -> List[Dict]:
    """Get all remaining pods on a node with their owner info."""
    return get_pods_on_node(node_name)


def get_vmi_count_per_node() -> Dict[str, int]:
    """Get VMI count per node across the cluster."""
    vmi_counts = {}
    try:
        result = subprocess.run(
            ["kubectl", "get", "vmi", "-A", "-o", "json"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for item in data.get("items", []):
                node_name = item.get("status", {}).get("nodeName", "")
                if node_name:
                    vmi_counts[node_name] = vmi_counts.get(node_name, 0) + 1
    except Exception:
        pass
    return vmi_counts


def print_vmi_distribution(logger: logging.Logger, title: str = "VMI Distribution"):
    """Print VMI count per node."""
    vmi_counts = get_vmi_count_per_node()
    total_vmis = sum(vmi_counts.values())

    logger.info("")
    logger.info(f"{title} (Total VMIs: {total_vmis})")
    logger.info("-" * 60)
    logger.info(f"{'Node':<45} {'VMI Count':<10}")
    logger.info("-" * 60)

    for node in sorted(vmi_counts.keys()):
        logger.info(f"{node:<45} {vmi_counts[node]:<10}")

    logger.info("-" * 60)
    return vmi_counts


def drain_node(node_name: str, timeout: int, grace_period: int,
               ignore_daemonsets: bool, delete_emptydir: bool,
               force: bool, logger: logging.Logger,
               dry_run: bool = False) -> Dict:
    """Drain a single node and measure time."""
    result = {
        "node": node_name,
        "success": False,
        "start_time": None,
        "end_time": None,
        "duration_seconds": 0,
        "pods_before": 0,
        "pods_after": 0,
        "daemonset_pods": [],
        "remaining_pods": [],
        "error": None
    }

    # Get initial pod count and DaemonSet pods
    result["pods_before"] = get_node_pod_count(node_name)
    result["daemonset_pods"] = get_daemonset_pods(node_name)
    logger.info(f"[{node_name}] Starting drain (pods: {result['pods_before']}, daemonsets: {len(result['daemonset_pods'])})")

    # Build drain command
    cmd = ["kubectl", "drain", node_name, f"--timeout={timeout}s"]

    if grace_period > 0:
        cmd.append(f"--grace-period={grace_period}")
    if ignore_daemonsets:
        cmd.append("--ignore-daemonsets")
    if delete_emptydir:
        cmd.append("--delete-emptydir-data")
    if force:
        cmd.append("--force")
    if dry_run:
        cmd.append("--dry-run=client")

    logger.debug(f"[{node_name}] Command: {' '.join(cmd)}")

    result["start_time"] = datetime.now()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 60  # Add buffer to kubectl timeout
        )

        result["end_time"] = datetime.now()
        result["duration_seconds"] = (result["end_time"] - result["start_time"]).total_seconds()

        if proc.returncode == 0:
            result["success"] = True
            result["pods_after"] = get_node_pod_count(node_name)
            result["remaining_pods"] = get_remaining_pods(node_name)
            logger.info(f"[{node_name}] Drain completed in {result['duration_seconds']:.2f}s "
                       f"(pods: {result['pods_before']} -> {result['pods_after']})")
        else:
            result["error"] = proc.stderr.strip()
            result["remaining_pods"] = get_remaining_pods(node_name)
            logger.error(f"[{node_name}] Drain failed: {result['error']}")

    except subprocess.TimeoutExpired:
        result["end_time"] = datetime.now()
        result["duration_seconds"] = (result["end_time"] - result["start_time"]).total_seconds()
        result["error"] = f"Timeout after {timeout}s"
        logger.error(f"[{node_name}] Drain timed out after {result['duration_seconds']:.2f}s")
    except Exception as e:
        result["end_time"] = datetime.now()
        result["duration_seconds"] = (result["end_time"] - result["start_time"]).total_seconds()
        result["error"] = str(e)
        logger.error(f"[{node_name}] Drain exception: {e}")

    return result


def uncordon_node(node_name: str, logger: logging.Logger) -> bool:
    """Uncordon a node to allow scheduling again."""
    try:
        result = subprocess.run(
            ["kubectl", "uncordon", node_name],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info(f"[{node_name}] Uncordoned successfully")
            return True
        else:
            logger.error(f"[{node_name}] Failed to uncordon: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"[{node_name}] Exception uncordoning: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Drain Kubernetes nodes and measure drain time"
    )
    parser.add_argument("--nodes", nargs="+", required=True,
                        help="Node names to drain")
    parser.add_argument("--parallel", action="store_true",
                        help="Drain nodes in parallel (default: sequential)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Drain timeout in seconds (default: 300)")
    parser.add_argument("--grace-period", type=int, default=30,
                        help="Pod termination grace period (default: 30)")
    parser.add_argument("--ignore-daemonsets", action="store_true", default=False,
                        help="Ignore DaemonSet pods (default: False)")
    parser.add_argument("--delete-emptydir", action="store_true", default=True,
                        help="Delete pods with emptyDir (default: True)")
    parser.add_argument("--force", action="store_true",
                        help="Force drain even if pods don't have controllers")
    parser.add_argument("--uncordon-after", action="store_true", default=True,
                        help="Uncordon node after drain completes")
    parser.add_argument("--wait-between", type=int, default=0,
                        help="Seconds to wait between nodes (simulates reboot time, default: 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without draining")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", default=None,
                        help="Path to log file (auto-generated if not specified)")

    args = parser.parse_args()

    # Always log to a file
    import os
    log_file = args.log_file
    if not log_file:
        os.makedirs("logs", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"logs/drain_{timestamp}.log"

    logger = setup_logging(args.log_level, log_file)

    logger.info("=" * 80)
    logger.info("NODE DRAIN CONFIGURATION")
    logger.info("=" * 80)
    logger.info(f"Nodes: {', '.join(args.nodes)}")
    logger.info(f"Mode: {'Parallel' if args.parallel else 'Sequential'}")
    logger.info(f"Timeout: {args.timeout}s")
    logger.info(f"Grace period: {args.grace_period}s")
    logger.info(f"Ignore DaemonSets: {args.ignore_daemonsets}")
    logger.info(f"Delete emptyDir: {args.delete_emptydir}")
    logger.info(f"Force: {args.force}")
    logger.info(f"Uncordon after: {args.uncordon_after}")
    if args.wait_between > 0:
        logger.info(f"Wait between nodes: {args.wait_between}s ({args.wait_between / 60:.1f} min)")
    if args.uncordon_after and not args.parallel:
        logger.info("Mode: OCP UPGRADE SIMULATION (drain -> uncordon -> next)")
    if args.dry_run:
        logger.info("DRY-RUN MODE - No actual drain will be performed")
    logger.info("=" * 80)

    # Print VMI distribution BEFORE drain
    logger.info("")
    logger.info("=" * 80)
    vmi_before = print_vmi_distribution(logger, "VMI DISTRIBUTION BEFORE DRAIN")
    logger.info("=" * 80)

    overall_start = datetime.now()
    all_results = []

    if args.parallel:
        # Drain nodes in parallel
        with ThreadPoolExecutor(max_workers=len(args.nodes)) as executor:
            futures = {
                executor.submit(
                    drain_node, node, args.timeout, args.grace_period,
                    args.ignore_daemonsets, args.delete_emptydir,
                    args.force, logger, args.dry_run
                ): node
                for node in args.nodes
            }

            for future in as_completed(futures):
                node = futures[future]
                try:
                    result = future.result()
                    all_results.append(result)
                except Exception as e:
                    logger.error(f"[{node}] Exception: {e}")
                    all_results.append({
                        "node": node,
                        "success": False,
                        "duration_seconds": 0,
                        "error": str(e)
                    })
    else:
        # Drain nodes sequentially (simulates OCP upgrade behavior)
        for i, node in enumerate(args.nodes):
            result = drain_node(
                node, args.timeout, args.grace_period,
                args.ignore_daemonsets, args.delete_emptydir,
                args.force, logger, args.dry_run
            )
            all_results.append(result)

            # Uncordon immediately after this node (OCP upgrade simulation)
            if args.uncordon_after and not args.dry_run and result["success"]:
                if args.wait_between > 0:
                    logger.info(f"[{node}] Simulating reboot/update, waiting {args.wait_between}s...")
                    time.sleep(args.wait_between)
                uncordon_node(node, logger)

            # Wait before next node
            if i < len(args.nodes) - 1 and args.wait_between > 0 and not args.uncordon_after:
                logger.info(f"Waiting {args.wait_between}s before next node...")
                time.sleep(args.wait_between)

    # Uncordon all at end if not done per-node
    if args.uncordon_after and args.parallel and not args.dry_run:
        logger.info("")
        logger.info("Uncordoning nodes...")
        for result in all_results:
            if result["success"]:
                uncordon_node(result["node"], logger)

    overall_elapsed = (datetime.now() - overall_start).total_seconds()

    # Print VMI distribution AFTER drain
    logger.info("")
    logger.info("=" * 80)
    vmi_after = print_vmi_distribution(logger, "VMI DISTRIBUTION AFTER DRAIN")
    logger.info("=" * 80)

    # Print summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("DRAIN SUMMARY")
    logger.info("=" * 80)

    success_count = sum(1 for r in all_results if r["success"])
    failed_count = len(all_results) - success_count

    logger.info(f"Total nodes: {len(all_results)}")
    logger.info(f"Successful: {success_count}")
    logger.info(f"Failed: {failed_count}")
    logger.info(f"Total time: {overall_elapsed:.2f}s ({overall_elapsed / 60:.2f} min)")
    logger.info("")
    logger.info("PER-NODE TIMING:")
    logger.info("-" * 80)
    logger.info(f"{'Node':<40} {'Status':<10} {'Duration':<15} {'Pods':<15}")
    logger.info("-" * 80)

    for r in sorted(all_results, key=lambda x: x.get("duration_seconds", 0), reverse=True):
        status = "SUCCESS" if r["success"] else "FAILED"
        duration = f"{r.get('duration_seconds', 0):.2f}s"
        pods_before = r.get("pods_before", "?")
        pods_after = r.get("pods_after", "?")
        pods_info = f"{pods_before} -> {pods_after}"
        logger.info(f"{r['node']:<40} {status:<10} {duration:<15} {pods_info:<15}")

    logger.info("-" * 80)

    # Statistics
    if all_results:
        durations = [r.get("duration_seconds", 0) for r in all_results if r["success"]]
        if durations:
            avg_duration = sum(durations) / len(durations)
            min_duration = min(durations)
            max_duration = max(durations)

            logger.info("")
            logger.info("STATISTICS (successful drains):")
            logger.info(f"  Average: {avg_duration:.2f}s ({avg_duration / 60:.2f} min)")
            logger.info(f"  Min:     {min_duration:.2f}s ({min_duration / 60:.2f} min)")
            logger.info(f"  Max:     {max_duration:.2f}s ({max_duration / 60:.2f} min)")

    logger.info("=" * 80)

    # Print failures
    if failed_count > 0:
        logger.info("")
        logger.info("FAILED NODES:")
        for r in all_results:
            if not r["success"]:
                logger.info(f"  {r['node']}: {r.get('error', 'Unknown error')}")

    # Print pods NOT evacuated (DaemonSets and others remaining)
    logger.info("")
    logger.info("=" * 80)
    logger.info("PODS NOT EVACUATED (remained on nodes after drain)")
    logger.info("=" * 80)

    # Known non-evictable owner kinds
    NON_EVICTABLE_OWNERS = {"DaemonSet", "Node", "StorageCluster"}

    for r in all_results:
        remaining = r.get("remaining_pods", [])
        daemonset_pods = [p for p in remaining if p["owner"] == "DaemonSet"]
        static_pods = [p for p in remaining if p["owner"] == "Node"]
        storage_pods = [p for p in remaining if p["owner"] == "StorageCluster"]
        other_pods = [p for p in remaining if p["owner"] not in NON_EVICTABLE_OWNERS]

        logger.info("")
        logger.info(f"[{r['node']}] - {len(remaining)} pods NOT evacuated")
        logger.info("-" * 60)

        if daemonset_pods:
            logger.info(f"  DaemonSet pods ({len(daemonset_pods)}) - cannot be evicted:")
            for pod in daemonset_pods:
                logger.info(f"    - {pod['name']} ({pod['status']})")

        if static_pods:
            logger.info(f"  Static/Node pods ({len(static_pods)}) - managed by kubelet:")
            for pod in static_pods:
                logger.info(f"    - {pod['name']} ({pod['status']})")

        if storage_pods:
            logger.info(f"  StorageCluster pods ({len(storage_pods)}) - managed by storage operator:")
            for pod in storage_pods:
                logger.info(f"    - {pod['name']} ({pod['status']})")

        if other_pods:
            logger.info(f"  Other pods ({len(other_pods)}) - UNEXPECTED, should have been evicted:")
            for pod in other_pods:
                logger.info(f"    - {pod['name']} [owner: {pod['owner']}] ({pod['status']})")

    logger.info("")
    logger.info("=" * 80)

    # Exit with error if any failed
    if failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
