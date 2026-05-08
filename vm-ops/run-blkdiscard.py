#!/usr/bin/env python3
"""
Run blkdiscard on data disks inside VMs.

This script runs blkdiscard on specified data disks (vdb, vdc, etc.) inside VMs
across multiple namespaces in parallel.

Usage:
    # Run blkdiscard on all data disks in VMs
    python3 run-blkdiscard.py --namespace-prefix rhel-eb-filler \
        --start 2001 --end 2010 --vm-name rhel-elbencho-1

    # Run on specific disks only
    python3 run-blkdiscard.py --namespace-prefix rhel-eb-filler \
        --start 2001 --end 2010 --vm-name rhel-elbencho-1 \
        --disks vdb vdc

    # Dry run
    python3 run-blkdiscard.py --namespace-prefix rhel-eb-filler \
        --start 2001 --end 2010 --vm-name rhel-elbencho-1 --dry-run
"""

import argparse
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Tuple


def setup_logging(level: str = "INFO", log_file: str = None) -> logging.Logger:
    """Configure logging to console and optionally to a file."""
    logger = logging.getLogger("blkdiscard")
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


def get_vmi_ip(namespace: str, vm_name: str, logger: logging.Logger) -> str:
    """Get VMI IP address."""
    rc, stdout, stderr = run_kubectl([
        "get", "vmi", vm_name, "-n", namespace,
        "-o", "jsonpath={.status.interfaces[0].ipAddress}"
    ])
    if rc != 0:
        logger.debug(f"[{namespace}/{vm_name}] Failed to get IP: {stderr}")
        return ""
    return stdout.strip()


def ssh_exec_command(ip: str, command: str, ssh_pod: str, ssh_pod_ns: str,
                     vm_user: str, vm_password: str,
                     logger: logging.Logger) -> Tuple[int, str, str]:
    """Execute command on VM via SSH pod."""
    ssh_cmd = (
        f"sshpass -p '{vm_password}' ssh -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "
        f"{vm_user}@{ip} '{command}'"
    )

    rc, stdout, stderr = run_kubectl([
        "exec", ssh_pod, "-n", ssh_pod_ns, "--",
        "sh", "-c", ssh_cmd
    ], timeout=300)

    return rc, stdout, stderr


def get_available_disks(ip: str, ssh_pod: str, ssh_pod_ns: str,
                        vm_user: str, vm_password: str,
                        logger: logging.Logger,
                        log_prefix: str = "",
                        min_size_mb: int = 10) -> List[str]:
    """Get list of available data disks (vdb, vdc, vdd, sda, sdb, etc.).

    Args:
        min_size_mb: Minimum disk size in MB to include (default: 10MB to filter tiny disks)
    """
    # Get all block devices except vda (root disk), with size > min_size_mb
    # Use simple grep/sed instead of awk to avoid escaping issues
    min_size_bytes = min_size_mb * 1024 * 1024
    cmd = f"lsblk -d -n -o NAME,TYPE,SIZE -b | grep disk | grep -v vda | while read name type size; do [ \"$size\" -gt {min_size_bytes} ] && echo /dev/$name; done"
    rc, stdout, stderr = ssh_exec_command(
        ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    logger.debug(f"{log_prefix} get_available_disks: rc={rc}, stdout='{stdout}', stderr='{stderr}'")

    if rc != 0 and not stdout.strip():
        logger.debug(f"{log_prefix} No disks found matching criteria")
        return []

    disks = [d.strip() for d in stdout.strip().split('\n') if d.strip()]
    logger.debug(f"{log_prefix} Found disks: {disks}")
    return disks


def run_blkdiscard_on_vm(namespace: str, vm_name: str,
                         ssh_pod: str, ssh_pod_ns: str,
                         vm_user: str, vm_password: str,
                         disks: List[str],
                         logger: logging.Logger,
                         dry_run: bool = False) -> dict:
    """Run blkdiscard on specified disks in a VM."""
    log_prefix = f"[{namespace}/{vm_name}]"
    result = {
        "namespace": namespace,
        "vm_name": vm_name,
        "success": False,
        "disks_processed": [],
        "disks_failed": [],
        "error": None
    }

    # Get VM IP
    ip = get_vmi_ip(namespace, vm_name, logger)
    if not ip:
        result["error"] = "Could not get VM IP"
        logger.warning(f"{log_prefix} Could not get VM IP")
        return result

    # Get available disks if not specified
    if not disks:
        logger.debug(f"{log_prefix} Auto-detecting data disks")
        disks = get_available_disks(ip, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, log_prefix)
        if not disks:
            result["error"] = "No data disks found"
            logger.warning(f"{log_prefix} No data disks found")
            return result

    logger.info(f"{log_prefix} Running blkdiscard on {len(disks)} disks: {', '.join(disks)}")

    if dry_run:
        logger.info(f"{log_prefix} DRY-RUN: Would run blkdiscard on: {', '.join(disks)}")
        result["success"] = True
        result["disks_processed"] = disks
        return result

    # Run blkdiscard on each disk
    for disk in disks:
        cmd = f"blkdiscard {disk}"
        logger.debug(f"{log_prefix} Running: {cmd}")

        rc, stdout, stderr = ssh_exec_command(
            ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
        )

        if rc == 0:
            result["disks_processed"].append(disk)
            logger.info(f"{log_prefix} Successfully ran blkdiscard on {disk}")
        else:
            result["disks_failed"].append(disk)
            logger.warning(f"{log_prefix} Failed to run blkdiscard on {disk}: {stderr}")

    result["success"] = len(result["disks_failed"]) == 0
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run blkdiscard on data disks inside VMs"
    )
    parser.add_argument("--namespace-prefix", required=True,
                        help="Namespace prefix (e.g., rhel-eb-filler)")
    parser.add_argument("--start", type=int, required=True,
                        help="Start namespace index")
    parser.add_argument("--end", type=int, required=True,
                        help="End namespace index")
    parser.add_argument("--vm-name", required=True,
                        help="VM name in each namespace")

    # Disk options
    parser.add_argument("--disks", nargs="+", default=None,
                        help="Specific disks to run blkdiscard on (e.g., vdb vdc). "
                             "If not specified, auto-detects all data disks")

    # SSH options
    parser.add_argument("--ssh-pod", default="ssh-test-pod",
                        help="SSH pod name (default: ssh-test-pod)")
    parser.add_argument("--ssh-pod-ns", default="default",
                        help="SSH pod namespace (default: default)")
    parser.add_argument("--vm-user", default="root",
                        help="VM SSH user (default: root)")
    parser.add_argument("--vm-password", default="Password1",
                        help="VM SSH password (default: Password1)")
    parser.add_argument("--concurrency", type=int, default=10,
                        help="Max concurrent operations (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without doing it")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", default=None,
                        help="Path to log file. If not specified, uses default.")

    args = parser.parse_args()

    # Always log to a file
    import os
    from datetime import datetime as dt
    log_file = args.log_file
    if not log_file:
        os.makedirs("logs", exist_ok=True)
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"logs/blkdiscard_{timestamp}.log"

    logger = setup_logging(args.log_level, log_file)

    # Build list of namespaces
    namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]

    # Convert disk names to full paths if needed
    disks = None
    if args.disks:
        disks = [f"/dev/{d}" if not d.startswith("/dev/") else d for d in args.disks]

    logger.info(f"Running blkdiscard on {len(namespaces)} VMs")
    logger.info(f"Namespaces: {args.namespace_prefix}-{args.start} to {args.namespace_prefix}-{args.end}")
    if disks:
        logger.info(f"Target disks: {', '.join(disks)}")
    else:
        logger.info(f"Target disks: auto-detect all data disks")
    if args.dry_run:
        logger.info("DRY-RUN MODE - No changes will be made")

    start_time = datetime.now()
    all_results = []

    # Run blkdiscard on all VMs in parallel
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                run_blkdiscard_on_vm, ns, args.vm_name,
                args.ssh_pod, args.ssh_pod_ns, args.vm_user, args.vm_password,
                disks, logger, args.dry_run
            ): ns
            for ns in namespaces
        }

        for future in as_completed(futures):
            ns = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                logger.error(f"[{ns}/{args.vm_name}] Exception: {e}")
                all_results.append({
                    "namespace": ns,
                    "vm_name": args.vm_name,
                    "success": False,
                    "disks_processed": [],
                    "disks_failed": [],
                    "error": str(e)
                })

    elapsed = (datetime.now() - start_time).total_seconds()

    # Calculate summary
    success_count = sum(1 for r in all_results if r["success"])
    failure_count = len(all_results) - success_count
    total_disks_processed = sum(len(r["disks_processed"]) for r in all_results)
    total_disks_failed = sum(len(r["disks_failed"]) for r in all_results)

    # Print summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total VMs: {len(all_results)}")
    logger.info(f"Success: {success_count}")
    logger.info(f"Failed: {failure_count}")
    logger.info(f"Total disks processed: {total_disks_processed}")
    logger.info(f"Total disks failed: {total_disks_failed}")
    logger.info(f"Time: {elapsed:.2f}s")
    logger.info("=" * 80)

    # Print failures if any
    if failure_count > 0:
        logger.info("")
        logger.info("FAILED VMs:")
        for r in all_results:
            if not r["success"]:
                logger.info(f"  {r['namespace']}/{r['vm_name']}: {r['error']}")
                if r["disks_failed"]:
                    logger.info(f"    Failed disks: {', '.join(r['disks_failed'])}")


if __name__ == "__main__":
    main()
