#!/usr/bin/env python3
"""
Create VM snapshots in batches with time intervals.

This script creates VirtualMachineSnapshot resources for VMs across multiple
namespaces in parallel batches.

Usage:
    # Snapshot 300 VMs in batches of 50, every 15 minutes
    python3 snapshot-vms.py --namespace-prefix perf-test \
        --start 1 --end 300 --vm-name rhel-elbencho-1 \
        --batch-size 50 --interval 900

    # Dry run to see what would happen
    python3 snapshot-vms.py --namespace-prefix perf-test \
        --start 1 --end 300 --vm-name rhel-elbencho-1 \
        --batch-size 50 --interval 900 --dry-run

    # Custom snapshot name prefix
    python3 snapshot-vms.py --namespace-prefix perf-test \
        --start 1 --end 100 --vm-name rhel-elbencho-1 \
        --batch-size 25 --interval 600 --snapshot-prefix my-snap
"""

import argparse
import logging
import subprocess
import sys
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Tuple


def setup_logging(level: str = "INFO", log_file: str = None) -> logging.Logger:
    """Configure logging to console and optionally to a file."""
    logger = logging.getLogger("vm-snapshot")
    logger.setLevel(getattr(logging, level.upper()))

    # Clear any existing handlers
    logger.handlers = []

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
        import os
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.FileHandler(log_file, mode='a')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.info(f"Logging to file: {log_file}")

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


def create_snapshot_yaml(namespace: str, vm_name: str, snapshot_name: str) -> dict:
    """Create VirtualMachineSnapshot YAML definition."""
    return {
        "apiVersion": "snapshot.kubevirt.io/v1beta1",
        "kind": "VirtualMachineSnapshot",
        "metadata": {
            "name": snapshot_name,
            "namespace": namespace
        },
        "spec": {
            "source": {
                "apiGroup": "kubevirt.io",
                "kind": "VirtualMachine",
                "name": vm_name
            }
        }
    }


def create_vm_snapshot(namespace: str, vm_name: str, snapshot_prefix: str,
                       logger: logging.Logger, dry_run: bool = False) -> dict:
    """Create a snapshot for a VM."""
    log_prefix = f"[{namespace}/{vm_name}]"
    result = {
        "namespace": namespace,
        "vm_name": vm_name,
        "snapshot_name": None,
        "success": False,
        "error": None
    }

    # Generate snapshot name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_name = f"{snapshot_prefix}-{timestamp}"
    result["snapshot_name"] = snapshot_name

    if dry_run:
        logger.info(f"{log_prefix} DRY-RUN: Would create snapshot '{snapshot_name}'")
        result["success"] = True
        return result

    # Create snapshot YAML
    snapshot_yaml = create_snapshot_yaml(namespace, vm_name, snapshot_name)
    yaml_str = yaml.dump(snapshot_yaml)

    logger.debug(f"{log_prefix} Creating snapshot '{snapshot_name}'")

    # Apply the snapshot using kubectl
    rc, stdout, stderr = run_kubectl(
        ["apply", "-f", "-"],
        timeout=30
    )

    # Pass YAML via stdin
    try:
        result_apply = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=yaml_str,
            capture_output=True,
            text=True,
            timeout=30
        )
        rc, stdout, stderr = result_apply.returncode, result_apply.stdout, result_apply.stderr
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"{log_prefix} Failed to create snapshot: {e}")
        return result

    if rc == 0:
        result["success"] = True
        logger.info(f"{log_prefix} Successfully created snapshot '{snapshot_name}'")
    else:
        result["error"] = stderr
        logger.warning(f"{log_prefix} Failed to create snapshot: {stderr}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Create VM snapshots in batches with time intervals"
    )
    parser.add_argument("--namespace-prefix", required=True,
                        help="Namespace prefix (e.g., perf-test)")
    parser.add_argument("--start", type=int, required=True,
                        help="Start namespace index")
    parser.add_argument("--end", type=int, required=True,
                        help="End namespace index")
    parser.add_argument("--vm-name", required=True,
                        help="VM name in each namespace")

    # Batch options
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Number of VMs to snapshot in each batch (default: 50)")
    parser.add_argument("--interval", type=int, default=900,
                        help="Time interval between batches in seconds (default: 900 = 15 min)")
    parser.add_argument("--concurrency", type=int, default=50,
                        help="Max concurrent snapshot operations within a batch (default: 50)")

    # Snapshot options
    parser.add_argument("--snapshot-prefix", default="vm-snap",
                        help="Snapshot name prefix (default: vm-snap)")

    # Other options
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without doing it")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", default=None,
                        help="Path to log file. If not specified, uses default.")

    args = parser.parse_args()

    # Setup logging
    import os
    from datetime import datetime as dt
    log_file = args.log_file
    if not log_file:
        os.makedirs("logs", exist_ok=True)
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"logs/snapshot_{timestamp}.log"

    logger = setup_logging(args.log_level, log_file)

    # Build list of namespaces
    all_namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]
    total_vms = len(all_namespaces)

    # Split into batches
    batches = []
    for i in range(0, total_vms, args.batch_size):
        batch = all_namespaces[i:i + args.batch_size]
        batches.append(batch)

    logger.info("=" * 80)
    logger.info("VM SNAPSHOT CONFIGURATION")
    logger.info("=" * 80)
    logger.info(f"Total VMs: {total_vms}")
    logger.info(f"Batch size: {args.batch_size}")
    logger.info(f"Number of batches: {len(batches)}")

    # Format interval display
    if args.interval >= 60:
        interval_display = f"{args.interval}s ({args.interval / 60:.1f} min)"
    else:
        interval_display = f"{args.interval}s"
    logger.info(f"Interval between batches: {interval_display}")

    logger.info(f"Concurrency per batch: {args.concurrency}")
    logger.info(f"Snapshot prefix: {args.snapshot_prefix}")
    if args.dry_run:
        logger.info("DRY-RUN MODE - No snapshots will be created")
    logger.info("=" * 80)

    overall_start = datetime.now()
    all_results = []
    batch_times = []  # Track start time of each batch

    # Process each batch
    for batch_num, batch_namespaces in enumerate(batches, 1):
        batch_start = datetime.now()
        batch_times.append({
            "batch_num": batch_num,
            "start_time": batch_start,
            "num_vms": len(batch_namespaces)
        })

        logger.info("")
        logger.info(f"{'=' * 80}")
        logger.info(f"BATCH {batch_num}/{len(batches)}: Processing {len(batch_namespaces)} VMs")
        logger.info(f"{'=' * 80}")

        batch_results = []

        # Create snapshots in parallel within the batch
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    create_vm_snapshot, ns, args.vm_name, args.snapshot_prefix,
                    logger, args.dry_run
                ): ns
                for ns in batch_namespaces
            }

            for future in as_completed(futures):
                ns = futures[future]
                try:
                    result = future.result()
                    batch_results.append(result)
                    all_results.append(result)
                except Exception as e:
                    logger.error(f"[{ns}/{args.vm_name}] Exception: {e}")
                    error_result = {
                        "namespace": ns,
                        "vm_name": args.vm_name,
                        "snapshot_name": None,
                        "success": False,
                        "error": str(e)
                    }
                    batch_results.append(error_result)
                    all_results.append(error_result)

        batch_elapsed = (datetime.now() - batch_start).total_seconds()
        batch_success = sum(1 for r in batch_results if r["success"])
        batch_failed = len(batch_results) - batch_success

        logger.info(f"Batch {batch_num} completed: {batch_success} success, {batch_failed} failed, "
                    f"time: {batch_elapsed:.2f}s")

        # Wait before next batch (except for the last batch)
        if batch_num < len(batches):
            logger.info(f"Waiting {args.interval}s before next batch...")
            time.sleep(args.interval)

    overall_elapsed = (datetime.now() - overall_start).total_seconds()

    # Calculate summary
    total_success = sum(1 for r in all_results if r["success"])
    total_failed = len(all_results) - total_success

    # Print summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("FINAL SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total VMs: {len(all_results)}")
    logger.info(f"Successful snapshots: {total_success}")
    logger.info(f"Failed snapshots: {total_failed}")
    logger.info(f"Total time: {overall_elapsed:.2f}s ({overall_elapsed / 60:.2f} min)")
    logger.info("")
    logger.info("BATCH START TIMES:")
    for bt in batch_times:
        logger.info(f"  Batch {bt['batch_num']}: {bt['start_time'].strftime('%Y-%m-%d %H:%M:%S')} ({bt['num_vms']} VMs)")
    logger.info("=" * 80)

    # Print failures if any
    if total_failed > 0:
        logger.info("")
        logger.info("FAILED SNAPSHOTS:")
        for r in all_results:
            if not r["success"]:
                logger.info(f"  {r['namespace']}/{r['vm_name']}: {r['error']}")

    # Exit with error code if any failures
    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
