#!/usr/bin/env python3
"""
Manage elbencho workloads on VMs via SSH.

Start, stop, or change elbencho workloads on VMs across multiple namespaces.

Two workload modes:
1. IOPS mode (--iops): Runs separate read/write processes with rate limiting
   - IOPS is split equally between read and write
   - Uses --limitread and --limitwrite flags

2. rwmixpct mode (--rwmixpct): Runs single process with mixed read/write
   - Uses elbencho's native --rwmixpct flag

Actions:
  run-all        - Full workflow: deploy VMs, start workload, wait, gather results, cleanup (optional)
  deploy         - Deploy VMs using datasource-clone
  change-workload- Start/change elbencho workload on VMs
  gather-results - Stop IO and collect results from all VMs
  cleanup        - Delete VMs and namespaces
  start/stop     - Start/stop the elbencho service
  status         - Check elbencho service status

Usage:
    # Full workflow with run-all (deploy + workload + wait + gather)
    python3 measure-elbencho-performance.py --namespace-prefix perf-test \
        --start 1 --end 10 --action run-all --vm-name rhel-elbencho-1 \
        --vm-template /path/to/vm-template.yaml \
        --iops 100 --duration 300 --save-results

    # run-all with rwmixpct mode
    python3 measure-elbencho-performance.py --namespace-prefix perf-test \
        --start 1 --end 10 --action run-all --vm-name rhel-elbencho-1 \
        --vm-template /path/to/vm-template.yaml \
        --rwmixpct 70 --block-size 32K --duration 600 --storage-driver portworx-3.6

    # Deploy VMs only (requires --vm-template)
    python3 measure-elbencho-performance.py --namespace-prefix perf-test \
        --start 1 --end 10 --action deploy --vm-name rhel-elbencho-1 \
        --vm-template /path/to/vm-template.yaml --concurrency 50

    # IOPS mode: 4 total IOPS (2 read + 2 write) on 3 disks
    python3 measure-elbencho-performance.py --namespace-prefix datasource-clone \
        --start 101 --end 110 --action change-workload --vm-name rhel-elbencho-1 \
        --iops 4 --num-disks 3 --block-size 4K

    # rwmixpct mode: 70% read / 30% write
    python3 measure-elbencho-performance.py --namespace-prefix datasource-clone \
        --start 101 --end 110 --action change-workload --vm-name rhel-elbencho-1 \
        --rwmixpct 70 --block-size 32K --iodepth 2 --threads 12

    # Gather results: stop IO on all VMs and collect aggregated metrics
    python3 measure-elbencho-performance.py --namespace-prefix datasource-clone \
        --start 101 --end 110 --action gather-results --vm-name rhel-elbencho-1 \
        --storage-driver portworx-3.6

    # Cleanup VMs and namespaces
    python3 measure-elbencho-performance.py --namespace-prefix perf-test \
        --start 1 --end 10 --action cleanup --vm-name rhel-elbencho-1
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Optional, Tuple

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from utils.common import (
    setup_logging,
    get_vm_disk_count,
    get_vmi_ip,
    ssh_exec_command,
)


def detect_disk_count_from_template(vm_template_path: str) -> Optional[int]:
    """Return non-cloud-init disk count from a VM template, or None on failure."""
    with open(vm_template_path, 'r') as f:
        docs = list(yaml.safe_load_all(f))

    vm_spec = next((doc for doc in docs if doc and doc.get('kind') == 'VirtualMachine'), None)
    if not vm_spec:
        return None

    volumes = (
        vm_spec.get('spec', {})
        .get('template', {})
        .get('spec', {})
        .get('volumes', [])
    )
    non_cloudinit_volumes = [
        v for v in volumes
        if not any(k in v for k in ['cloudInitNoCloud', 'cloudInitConfigDrive'])
    ]
    return len(non_cloudinit_volumes)


def build_elbencho_output_dir(args, vm_targets: List[Tuple[str, str]],
                              logger: Optional[logging.Logger] = None) -> str:
    """Build and cache the canonical elbencho result directory."""
    if getattr(args, '_output_dir', None):
        return args._output_dir

    disks_per_vm = args.disks_per_vm
    if disks_per_vm == "auto":
        disk_count = None
        if args.action == "run-all" and args.vm_template:
            try:
                disk_count = detect_disk_count_from_template(args.vm_template)
                if disk_count and logger:
                    logger.info(f"Auto-detected {disk_count} disks from VM template")
            except Exception as exc:
                if logger:
                    logger.warning(f"Could not detect disks from VM template: {exc}")

        if not disk_count:
            first_ns, first_vm = vm_targets[0]
            disk_count = get_vm_disk_count(first_vm, first_ns, logger)
            if disk_count and logger:
                logger.info(f"Auto-detected {disk_count} disks from {first_ns}/{first_vm} spec")

        if disk_count and disk_count > 0:
            disks_per_vm = f"{disk_count}-disk"
        else:
            disks_per_vm = "1-disk"
            if logger:
                logger.warning("Could not detect disks, using default: 1-disk")

    vm_count = len(vm_targets)
    if args.run_name:
        run_name = args.run_name
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"{timestamp}_elbencho_{vm_count}vms"

    output_dir = os.path.join(args.results_dir, args.storage_driver, disks_per_vm, run_name)
    os.makedirs(output_dir, exist_ok=True)
    args._output_dir = output_dir
    args._disks_per_vm = disks_per_vm
    return output_dir


def stop_all_elbencho(ip: str, ssh_pod: str, ssh_pod_ns: str,
                      vm_user: str, vm_password: str,
                      logger: logging.Logger, log_prefix: str,
                      wait_for_json: bool = True) -> bool:
    """Stop all elbencho processes and services on a VM.

    Args:
        wait_for_json: If True, wait for JSON files to be written after SIGINT
    """
    # Stop the systemd service first
    ssh_exec_command(
        ip, "systemctl stop elbencho-1iops.service 2>/dev/null || true",
        ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    # Send SIGINT first to allow graceful shutdown and JSON file writing
    ssh_exec_command(
        ip, "pkill -SIGINT elbencho 2>/dev/null || true",
        ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    if wait_for_json:
        # Wait for elbencho to write JSON files (it needs time to finalize)
        logger.debug(f"{log_prefix} Waiting for elbencho to write result files...")
        import time
        time.sleep(10)

        # Check if processes are still running
        rc, stdout, _ = ssh_exec_command(
            ip, "pgrep elbencho || echo 'no_process'",
            ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
        )

        if stdout.strip() and "no_process" not in stdout:
            # Processes still running, wait a bit more then force kill
            logger.debug(f"{log_prefix} Elbencho still running, waiting more...")
            time.sleep(2)
            ssh_exec_command(
                ip, "pkill -9 elbencho 2>/dev/null || true",
                ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
            )
    else:
        # Quick kill without waiting
        ssh_exec_command(
            ip, "sleep 1; pkill -9 elbencho 2>/dev/null || true",
            ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
        )

    logger.debug(f"{log_prefix} Stopped all elbencho processes")
    return True


def get_available_disks(ip: str, ssh_pod: str, ssh_pod_ns: str,
                        vm_user: str, vm_password: str,
                        logger: logging.Logger,
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

    logger.debug(f"get_available_disks: rc={rc}, stdout='{stdout}', stderr='{stderr}'")

    if rc != 0 and not stdout.strip():
        return []

    disks = [d.strip() for d in stdout.strip().split('\n') if d.strip()]
    logger.debug(f"Found disks: {disks}")
    return disks


def start_elbencho_iops_mode(ip: str, ssh_pod: str, ssh_pod_ns: str,
                             vm_user: str, vm_password: str,
                             block_size: str, disks: List[str],
                             iops: int, iodepth: int, threads: int,
                             duration: int,
                             logger: logging.Logger, log_prefix: str) -> bool:
    """Start elbencho in IOPS mode - runs separate read and write processes.

    IOPS is split equally between read and write.
    limitwrite/limitread = (iops / 2) * block_size_bytes

    Args:
        iops: Total IOPS (must be multiple of 2, split between read/write)
        iodepth: IO depth per thread
        threads: Number of threads
        duration: Duration in seconds (0 = infinite)
    """
    if not disks:
        logger.warning(f"{log_prefix} No disks available for IO")
        return False

    # Create directories
    ssh_exec_command(
        ip, "mkdir -p /var/log/elbencho /root/elbencho_results /var/run/elbencho",
        ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    disk_args = ' '.join(disks)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Parse block size to bytes
    block_size_upper = block_size.upper()
    if 'K' in block_size_upper:
        block_bytes = int(block_size_upper.replace('K', '')) * 1024
    elif 'M' in block_size_upper:
        block_bytes = int(block_size_upper.replace('M', '')) * 1024 * 1024
    else:
        block_bytes = int(block_size)

    # Calculate limit for each direction (split IOPS equally)
    # NOTE: elbencho's --limitread/--limitwrite is PER-THREAD, not total
    # So we need to divide by number of threads
    iops_per_direction = iops // 2
    iops_per_thread = iops_per_direction // threads
    limit_bytes = iops_per_thread * block_bytes

    # Time limit or infinite loop
    time_flag = f"--timelimit {duration}" if duration > 0 else "--infloop"

    # Common output args
    output_args = (
        f"--livecsv /root/elbencho_results/{{mode}}_{ts}_live.csv --livecsvex "
        f"--resfile /root/elbencho_results/{{mode}}_{ts}.txt "
        f"--csvfile /root/elbencho_results/{{mode}}_{ts}.csv "
        f"--jsonfile /root/elbencho_results/{{mode}}_{ts}.json"
    )

    # Start write process
    write_cmd = (
        f"nohup /root/elbencho/bin/elbencho -w "
        f"-b {block_size} -t {threads} --iodepth {iodepth} "
        f"--limitwrite {limit_bytes} "
        f"--direct --rand --lat {time_flag} --nolive --liveint 1000 "
        f"{output_args.format(mode='write')} "
        f"{disk_args} >> /var/log/elbencho/write.log 2>&1 & "
        f"echo $! > /var/run/elbencho/write.pid"
    )

    logger.debug(f"{log_prefix} Write command: {write_cmd}")
    logger.debug(f"{log_prefix} Calculated: iops_per_direction={iops_per_direction}, iops_per_thread={iops_per_thread}, "
                 f"block_bytes={block_bytes}, limit_bytes={limit_bytes} (per thread)")

    rc, _, stderr = ssh_exec_command(
        ip, write_cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, timeout=30
    )
    if rc != 0:
        logger.warning(f"{log_prefix} Failed to start write elbencho: {stderr}")
        return False

    # Start read process
    read_cmd = (
        f"nohup /root/elbencho/bin/elbencho -r "
        f"-b {block_size} -t {threads} --iodepth {iodepth} "
        f"--limitread {limit_bytes} "
        f"--direct --rand --lat {time_flag} --nolive --liveint 1000 "
        f"{output_args.format(mode='read')} "
        f"{disk_args} >> /var/log/elbencho/read.log 2>&1 & "
        f"echo $! > /var/run/elbencho/read.pid"
    )

    logger.debug(f"{log_prefix} Read command: {read_cmd}")

    rc, _, stderr = ssh_exec_command(
        ip, read_cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, timeout=30
    )
    if rc != 0:
        logger.warning(f"{log_prefix} Failed to start read elbencho: {stderr}")
        return False

    duration_desc = f"{duration}s" if duration > 0 else "infinite"
    logger.info(f"{log_prefix} Started IOPS mode: {iops} total IOPS ({iops_per_direction} read + {iops_per_direction} write), "
                f"{block_size} block, {threads} threads ({iops_per_thread} IOPS/thread), iodepth {iodepth}, "
                f"{len(disks)} disks, duration: {duration_desc}")
    return True


def start_elbencho_rwmix_mode(ip: str, ssh_pod: str, ssh_pod_ns: str,
                               vm_user: str, vm_password: str,
                               block_size: str, disks: List[str],
                               rwmixpct: int, iodepth: int, threads: int,
                               duration: int,
                               logger: logging.Logger, log_prefix: str) -> bool:
    """Start elbencho in rwmixpct mode - single process with mixed read/write.

    Args:
        rwmixpct: Read percentage (0-100)
        iodepth: IO depth per thread
        threads: Number of threads
        duration: Duration in seconds (0 = infinite)
    """
    if not disks:
        logger.warning(f"{log_prefix} No disks available for IO")
        return False

    disk_args = ' '.join(disks)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Create directories
    ssh_exec_command(
        ip, "mkdir -p /var/log/elbencho /root/elbencho_results /var/run/elbencho",
        ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    # Time limit or infinite loop
    time_flag = f"--timelimit {duration}" if duration > 0 else "--infloop"

    # Output args
    output_args = (
        f"--livecsv /root/elbencho_results/rwmix_{ts}_live.csv --livecsvex "
        f"--resfile /root/elbencho_results/rwmix_{ts}.txt "
        f"--csvfile /root/elbencho_results/rwmix_{ts}.csv "
        f"--jsonfile /root/elbencho_results/rwmix_{ts}.json"
    )

    cmd = (
        f"nohup /root/elbencho/bin/elbencho -r -w --rwmixpct {rwmixpct} "
        f"-b {block_size} -t {threads} --iodepth {iodepth} "
        f"--direct --rand --lat {time_flag} "
        f"{output_args} "
        f"{disk_args} >> /var/log/elbencho/rwmix.log 2>&1 & "
        f"echo $! > /var/run/elbencho/rwmix.pid"
    )

    rc, _, stderr = ssh_exec_command(
        ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, timeout=30
    )
    if rc != 0:
        logger.warning(f"{log_prefix} Failed to start rwmix elbencho: {stderr}")
        return False

    duration_desc = f"{duration}s" if duration > 0 else "infinite"
    logger.info(f"{log_prefix} Started rwmix mode: {rwmixpct}% read / {100-rwmixpct}% write, "
                f"{block_size} block, {threads} threads, iodepth {iodepth}, {len(disks)} disks, duration: {duration_desc}")
    return True


def get_elbencho_result_files(ip: str, ssh_pod: str, ssh_pod_ns: str,
                              vm_user: str, vm_password: str,
                              logger: logging.Logger,
                              log_prefix: str = "") -> List[dict]:
    """Get list of result file sets from running elbencho processes.

    Returns list of dicts with keys: json, txt, csv, live_csv
    """
    # Get command lines of running elbencho processes to find file paths
    cmd = "ps aux | grep elbencho | grep -v grep | grep jsonfile"
    rc, stdout, stderr = ssh_exec_command(
        ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    logger.debug(f"{log_prefix} get_elbencho_result_files: rc={rc}, stdout_len={len(stdout) if stdout else 0}")
    logger.debug(f"{log_prefix} ps output: {stdout[:500] if stdout else 'empty'}")

    result_sets = []
    if rc == 0 and stdout.strip():
        import re
        for line in stdout.strip().split('\n'):
            logger.debug(f"{log_prefix} Processing line: {line[:200]}")
            files = {}
            # Extract --jsonfile path
            json_match = re.search(r'--jsonfile\s+(\S+)', line)
            if json_match:
                files["json"] = json_match.group(1)
                logger.debug(f"{log_prefix} Found json: {files['json']}")
            # Extract --resfile path (txt)
            res_match = re.search(r'--resfile\s+(\S+)', line)
            if res_match:
                files["txt"] = res_match.group(1)
            # Extract --csvfile path
            csv_match = re.search(r'--csvfile\s+(\S+)', line)
            if csv_match:
                files["csv"] = csv_match.group(1)
            # Extract --livecsv path
            live_match = re.search(r'--livecsv\s+(\S+)', line)
            if live_match:
                files["live_csv"] = live_match.group(1)

            if files:
                result_sets.append(files)
                logger.debug(f"{log_prefix} Added file set: {files}")
    else:
        logger.debug(f"{log_prefix} No elbencho processes found or command failed")

    return result_sets


def gather_results_from_vm(namespace: str, vm_name: str,
                           ssh_pod: str, ssh_pod_ns: str,
                           vm_user: str, vm_password: str,
                           output_dir: str,
                           logger: logging.Logger) -> dict:
    """Gather elbencho results from a VM.

    Returns dict with:
    - vm_results: list of parsed JSON results
    - total_iops: aggregated IOPS (read + write)
    - total_throughput_bytes: aggregated throughput in bytes/s
    - avg_latency_us: average latency in microseconds
    """
    log_prefix = f"[{namespace}/{vm_name}]"
    result = {
        "namespace": namespace,
        "vm_name": vm_name,
        "json_files": [],
        "total_read_iops": 0,
        "total_write_iops": 0,
        "total_iops": 0,
        "total_read_throughput_bytes": 0,
        "total_write_throughput_bytes": 0,
        "total_throughput_bytes": 0,
        "latencies": [],
        "avg_latency_us": 0,
        "min_latency_us": 0,
        "max_latency_us": 0,
        "skipped": False,
        "error": None
    }

    # Get VM IP
    ip = get_vmi_ip(vm_name, namespace, logger)
    if not ip:
        result["error"] = "Could not get VM IP"
        result["skipped"] = True
        return result

    # Get result file sets from running processes
    logger.debug(f"{log_prefix} Getting result files from VM at {ip}")
    result_sets = get_elbencho_result_files(ip, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, log_prefix)

    if not result_sets:
        logger.debug(f"{log_prefix} No elbencho processes running, skipping")
        result["skipped"] = True
        return result

    logger.info(f"{log_prefix} Found {len(result_sets)} elbencho result file sets")
    for fs in result_sets:
        logger.debug(f"{log_prefix} File set: {fs}")

    # Stop elbencho processes first to ensure files are complete
    # wait_for_json=True will wait for JSON files to be written
    stop_all_elbencho(ip, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, log_prefix, wait_for_json=True)

    # Create output directory for this VM
    vm_output_dir = f"{output_dir}/{namespace}"
    os.makedirs(vm_output_dir, exist_ok=True)

    # List actual files in results directory for debugging
    cmd = "ls -la /root/elbencho_results/*.json 2>/dev/null || echo 'No JSON files found'"
    rc, stdout, _ = ssh_exec_command(ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger)
    logger.debug(f"{log_prefix} Available JSON files: {stdout.strip()}")

    # Download all result files and parse JSON
    for idx, file_set in enumerate(result_sets):
        logger.debug(f"{log_prefix} Processing file set {idx+1}/{len(result_sets)}: {file_set}")
        json_content = None

        # Download all files in the set (json, txt, csv, live_csv)
        for file_type, remote_path in file_set.items():
            if not remote_path:
                continue

            logger.debug(f"{log_prefix} Downloading {file_type}: {remote_path}")
            cmd = f"cat {remote_path}"
            rc, stdout, stderr = ssh_exec_command(
                ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
            )

            logger.debug(f"{log_prefix} cat {remote_path}: rc={rc}, stdout_len={len(stdout) if stdout else 0}")
            if rc != 0 or not stdout.strip():
                logger.debug(f"{log_prefix} Failed to read {remote_path}: rc={rc}, stderr={stderr[:200] if stderr else 'none'}")
                continue

            # Save file locally
            local_filename = os.path.basename(remote_path)
            local_path = f"{vm_output_dir}/{local_filename}"
            with open(local_path, 'w') as f:
                f.write(stdout)
            logger.debug(f"{log_prefix} Saved {local_filename} ({len(stdout)} bytes)")

            # Store JSON content for parsing
            if file_type == "json":
                json_content = stdout.strip()
                logger.debug(f"{log_prefix} Stored JSON content ({len(json_content)} bytes)")

        # Parse JSON content for metrics
        if not json_content:
            json_file = file_set.get("json", "unknown")
            logger.warning(f"{log_prefix} No JSON content available for {json_file}")
            continue

        try:
            data = json.loads(json_content)
            result["json_files"].append(file_set.get("json", "unknown"))

            # Parse metrics from last_done section
            last_done = data.get("last_done", {})
            phase_type = data.get("phase_type", "").upper()
            json_file_name = file_set.get("json", "unknown")

            # Get IOPS and throughput (main values)
            write_iops = int(last_done.get("iops", 0))
            write_throughput = int(last_done.get("bytes/s", 0))

            # Check if this is a read, write, or mixed workload
            if "RWMIX" in phase_type or "MIX" in phase_type:
                # Mixed workload:
                # - main iops/throughput is WRITE
                # - rwmix_read contains READ metrics
                result["total_write_iops"] += write_iops
                result["total_write_throughput_bytes"] += write_throughput

                rwmix_read = last_done.get("rwmix_read", {})
                read_iops = int(rwmix_read.get("iops", 0))
                read_throughput = int(rwmix_read.get("bytes/s", 0))
                result["total_read_iops"] += read_iops
                result["total_read_throughput_bytes"] += read_throughput

                logger.debug(f"{log_prefix} RWMIX: write_iops={write_iops}, read_iops={read_iops}")
            elif "READ" in phase_type:
                result["total_read_iops"] += write_iops
                result["total_read_throughput_bytes"] += write_throughput
            elif "WRITE" in phase_type:
                result["total_write_iops"] += write_iops
                result["total_write_throughput_bytes"] += write_throughput
            else:
                # Unknown type, treat as combined read+write
                result["total_read_iops"] += write_iops
                result["total_read_throughput_bytes"] += write_throughput

            # Get latency - check both main and rwmix_read
            latency = last_done.get("latency", {}).get("IO", {})
            if latency:
                # For mixed workload, use write latency as main, but also capture read
                avg_lat = int(latency.get("avg_us", 0))
                min_lat = int(latency.get("min_us", 0))
                max_lat = int(latency.get("max_us", 0))

                # Check for read latency in rwmix_read
                rwmix_read_lat = latency.get("rwmix_read", {})
                if rwmix_read_lat:
                    read_avg_lat = int(rwmix_read_lat.get("avg_us", 0))
                    read_min_lat = int(rwmix_read_lat.get("min_us", 0))
                    read_max_lat = int(rwmix_read_lat.get("max_us", 0))
                    # Use average of read and write latency
                    if read_avg_lat > 0 and avg_lat > 0:
                        avg_lat = (avg_lat + read_avg_lat) // 2
                        min_lat = min(min_lat, read_min_lat)
                        max_lat = max(max_lat, read_max_lat)

                if avg_lat > 0:
                    result["latencies"].append({
                        "file": json_file_name,
                        "avg_us": avg_lat,
                        "min_us": min_lat,
                        "max_us": max_lat
                    })

            total_iops = write_iops + (read_iops if "RWMIX" in phase_type or "MIX" in phase_type else 0)
            logger.debug(f"{log_prefix} Parsed {json_file_name}: phase={phase_type}, total_iops={total_iops}")

        except json.JSONDecodeError as e:
            logger.warning(f"{log_prefix} Failed to parse JSON from {json_file}: {e}")
            continue

    # Calculate totals
    result["total_iops"] = result["total_read_iops"] + result["total_write_iops"]
    result["total_throughput_bytes"] = result["total_read_throughput_bytes"] + result["total_write_throughput_bytes"]

    # Calculate average latency
    if result["latencies"]:
        result["avg_latency_us"] = sum(l["avg_us"] for l in result["latencies"]) / len(result["latencies"])
        result["min_latency_us"] = min(l["min_us"] for l in result["latencies"])
        result["max_latency_us"] = max(l["max_us"] for l in result["latencies"])

    logger.info(f"{log_prefix} Total IOPS: {result['total_iops']} (R:{result['total_read_iops']}/W:{result['total_write_iops']}), "
                f"Throughput: {result['total_throughput_bytes']/1024/1024:.2f} MB/s, "
                f"Avg Latency: {result['avg_latency_us']:.0f} us")

    return result


def manage_service_on_vm(namespace: str, vm_name: str, action: str,
                         ssh_pod: str, ssh_pod_ns: str,
                         vm_user: str, vm_password: str,
                         logger: logging.Logger,
                         block_size: str = "4K",
                         num_disks: int = 0,
                         iops: int = 0,
                         rwmixpct: int = 0,
                         iodepth: int = 1,
                         threads: int = 0,
                         duration: int = 0) -> bool:
    """Start, stop, or change elbencho workload on a VM.

    Two modes:
    1. IOPS mode (--iops): Runs separate read/write processes with limitread/limitwrite
    2. rwmixpct mode (--rwmixpct): Runs single process with mixed read/write
    """
    log_prefix = f"[{namespace}/{vm_name}]"

    # Get VM IP
    ip = get_vmi_ip(vm_name, namespace, logger)
    if not ip:
        logger.warning(f"{log_prefix} Could not get VM IP")
        return False

    # Handle change-workload action
    if action == "change-workload":
        # Stop all existing elbencho (no need to wait for JSON since we're restarting)
        stop_all_elbencho(ip, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, log_prefix, wait_for_json=False)

        # Get available disks
        disks = get_available_disks(ip, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger)
        if not disks:
            logger.warning(f"{log_prefix} No data disks found")
            return False

        # Limit to num_disks if specified (0 means use all)
        if num_disks > 0 and len(disks) > num_disks:
            disks = disks[:num_disks]
            logger.debug(f"{log_prefix} Using {num_disks} of available disks: {disks}")
        else:
            logger.debug(f"{log_prefix} Using all {len(disks)} disks: {disks}")

        # Default threads to number of disks if not specified
        actual_threads = threads if threads > 0 else len(disks)

        # Choose mode based on arguments
        if iops > 0:
            # IOPS mode - separate read/write processes
            return start_elbencho_iops_mode(
                ip, ssh_pod, ssh_pod_ns, vm_user, vm_password,
                block_size, disks, iops, iodepth, actual_threads, duration,
                logger, log_prefix
            )
        elif rwmixpct > 0:
            # rwmixpct mode - single mixed process
            return start_elbencho_rwmix_mode(
                ip, ssh_pod, ssh_pod_ns, vm_user, vm_password,
                block_size, disks, rwmixpct, iodepth, actual_threads, duration,
                logger, log_prefix
            )
        else:
            logger.error(f"{log_prefix} Must specify either --iops or --rwmixpct")
            return False

    # Handle stop-all action (stops everything, not just service)
    if action == "stop-all":
        return stop_all_elbencho(ip, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger, log_prefix, wait_for_json=False)

    # Execute systemctl command for service actions
    if action == "start":
        cmd = "systemctl start elbencho-1iops.service"
    elif action == "stop":
        cmd = "systemctl stop elbencho-1iops.service"
    elif action == "restart":
        cmd = "systemctl restart elbencho-1iops.service"
    elif action == "status":
        cmd = "systemctl is-active elbencho-1iops.service"
    else:
        logger.error(f"{log_prefix} Unknown action: {action}")
        return False

    rc, stdout, stderr = ssh_exec_command(
        ip, cmd, ssh_pod, ssh_pod_ns, vm_user, vm_password, logger
    )

    if action == "status":
        status = stdout.strip() if rc == 0 else "inactive"
        logger.info(f"{log_prefix} Service status: {status}")
        return True

    if rc != 0:
        logger.warning(f"{log_prefix} Failed to {action} service: {stderr}")
        return False

    logger.info(f"{log_prefix} Service {action}ed successfully")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Manage elbencho workloads on VMs"
    )
    parser.add_argument("--namespace-prefix", required=True,
                        help="Namespace prefix (e.g., datasource-clone)")
    parser.add_argument("--start", type=int, required=True,
                        help="Start namespace index")
    parser.add_argument("--end", type=int, required=True,
                        help="End namespace index")
    parser.add_argument("--vm-name", required=True,
                        help="VM name in each namespace")
    parser.add_argument("--action", required=True,
                        choices=["run-all", "deploy", "start", "stop", "restart", "status", "stop-all", "change-workload", "gather-results", "cleanup"],
                        help="Action to perform (run-all: full workflow - deploy, workload, wait, gather, cleanup)")

    # Deploy action parameters
    parser.add_argument("--vm-template", type=str, default=None,
                        help="Path to VM template YAML (required for deploy and run-all actions)")
    parser.add_argument("--secret-yaml", type=str, default=None,
                        help="Path to cloudinit secret YAML file (optional, for deploy/run-all action)")
    parser.add_argument("--save-results", action="store_true",
                        help="Save results to JSON/CSV")
    parser.add_argument("--ping-timeout", type=int, default=300,
                        help="Timeout for ping tests in seconds (for deploy/run-all action, default: 300)")

    # Workload parameters for change-workload action
    # Mode 1: IOPS mode (--iops) - runs separate read/write processes
    parser.add_argument("--iops", type=int, default=0,
                        help="IOPS mode: Total IOPS (must be multiple of 2, split equally between read/write)")

    # Mode 2: rwmixpct mode (--rwmixpct) - runs single mixed process
    parser.add_argument("--rwmixpct", type=int, default=0,
                        help="rwmixpct mode: Read percentage (0-100), cannot be used with --iops")

    # Common parameters
    parser.add_argument("--block-size", type=str, default="4K",
                        help="Block size (default: 4K)")
    parser.add_argument("--num-disks", type=int, default=0,
                        help="Number of disks to use (0 = all available, default: 0)")
    parser.add_argument("--iodepth", type=int, default=1,
                        help="IO depth per thread (default: 1)")
    parser.add_argument("--threads", type=int, default=0,
                        help="Number of threads (0 = same as num disks, default: 0)")
    parser.add_argument("--duration", type=int, default=0,
                        help="Duration in seconds (0 = infinite, default: 0)")

    # gather-results parameters
    parser.add_argument("--results-dir", type=str, default="./results",
                        help="Base results directory (default: ./results)")
    parser.add_argument("--storage-driver", type=str, default="Not-Specified",
                        help="Storage driver for results folder (default: Not-Specified)")
    parser.add_argument("--disks-per-vm", type=str, default="auto",
                        help="Disks per VM for results folder name (default: auto-detect from first VM, fallback: 1-disk)")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Custom run name (default: auto-generated with timestamp and VM count)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="DEPRECATED: Use --results-dir instead. Full output directory path (overrides --results-dir/--storage-driver/--disks-per-vm)")

    parser.add_argument("--ssh-pod", default="ssh-test-pod",
                        help="SSH pod name (default: ssh-test-pod)")
    parser.add_argument("--ssh-pod-ns", default="default",
                        help="SSH pod namespace (default: default)")
    parser.add_argument("--vm-user", default="root",
                        help="VM SSH user (default: root)")
    parser.add_argument("--vm-password", default="Password1",
                        help="VM SSH password (default: Password1)")
    parser.add_argument("--concurrency", type=int, default=20,
                        help="Max concurrent operations (default: 20)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", default=None,
                        help="Path to log file. If not specified, uses default based on action.")

    args = parser.parse_args()

    # Build list of namespaces early so saved runs can log into their result directory.
    namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]
    vm_targets = [(ns, args.vm_name) for ns in namespaces]

    if args.save_results and args.action in ("gather-results", "run-all") and not args.log_file:
        output_dir = build_elbencho_output_dir(args, vm_targets)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.log_file = os.path.join(output_dir, f"elbencho_{args.action}_{timestamp}.log")

    logger = setup_logging(log_file=args.log_file, log_level=args.log_level)

    # Validate arguments
    if args.action in ["deploy", "run-all"]:
        if not args.vm_template:
            logger.error(f"--vm-template is required for {args.action} action.")
            sys.exit(1)
        if not os.path.exists(args.vm_template):
            logger.error(f"VM template file not found: {args.vm_template}")
            sys.exit(1)
        if args.secret_yaml and not os.path.exists(args.secret_yaml):
            logger.error(f"Secret YAML file not found: {args.secret_yaml}")
            sys.exit(1)

    if args.action in ["change-workload", "run-all"]:
        if args.iops > 0 and args.rwmixpct > 0:
            logger.error("Cannot use both --iops and --rwmixpct. Choose one mode.")
            sys.exit(1)
        if args.iops == 0 and args.rwmixpct == 0:
            logger.error(f"Must specify either --iops or --rwmixpct for {args.action} action.")
            sys.exit(1)
        if args.iops > 0 and args.iops % 2 != 0:
            logger.error("--iops must be a multiple of 2 (split equally between read/write).")
            sys.exit(1)

    if args.action == "run-all" and args.duration == 0:
        logger.error("--duration is required for run-all action (cannot be infinite).")
        sys.exit(1)

    logger.info(f"Managing elbencho workload on {len(vm_targets)} VMs")
    logger.info(f"Action: {args.action}")
    if args.action == "change-workload":
        disk_info = f"{args.num_disks} disks" if args.num_disks > 0 else "all disks"
        thread_info = f"{args.threads} threads" if args.threads > 0 else "threads=disks"
        duration_info = f"{args.duration}s" if args.duration > 0 else "infinite"
        if args.iops > 0:
            logger.info(f"Mode: IOPS ({args.iops} total = {args.iops//2} read + {args.iops//2} write)")
        else:
            logger.info(f"Mode: rwmixpct ({args.rwmixpct}% read / {100-args.rwmixpct}% write)")
        logger.info(f"Block size: {args.block_size}, Disks: {disk_info}, {thread_info}, iodepth: {args.iodepth}, Duration: {duration_info}")

    start_time = datetime.now()

    # Handle gather-results action separately
    if args.action == "gather-results":
        if args.output_dir:
            # Use legacy --output-dir if provided (for backward compatibility)
            logger.warning("--output-dir is deprecated. Use --results-dir, --storage-driver, --disks-per-vm instead.")
            output_dir = args.output_dir
        else:
            output_dir = build_elbencho_output_dir(args, vm_targets, logger)

        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Output directory: {output_dir}")

        all_results = []
        skipped = 0

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    gather_results_from_vm, ns, vm_name,
                    args.ssh_pod, args.ssh_pod_ns, args.vm_user, args.vm_password,
                    output_dir, logger
                ): (ns, vm_name)
                for ns, vm_name in vm_targets
            }

            for future in as_completed(futures):
                ns, vm_name = futures[future]
                try:
                    result = future.result()
                    if result["skipped"]:
                        skipped += 1
                    else:
                        all_results.append(result)
                except Exception as e:
                    logger.error(f"[{ns}/{vm_name}] Exception: {e}")

        elapsed = (datetime.now() - start_time).total_seconds()

        # Calculate aggregates
        total_read_iops = sum(r["total_read_iops"] for r in all_results)
        total_write_iops = sum(r["total_write_iops"] for r in all_results)
        total_iops = sum(r["total_iops"] for r in all_results)
        total_read_throughput = sum(r["total_read_throughput_bytes"] for r in all_results)
        total_write_throughput = sum(r["total_write_throughput_bytes"] for r in all_results)
        total_throughput = sum(r["total_throughput_bytes"] for r in all_results)

        # Calculate average latency across all VMs
        latencies = [r["avg_latency_us"] for r in all_results if r["avg_latency_us"] > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        min_latencies = [r["min_latency_us"] for r in all_results if r["min_latency_us"] > 0]
        max_latencies = [r["max_latency_us"] for r in all_results if r["max_latency_us"] > 0]

        # Print summary
        logger.info("")
        logger.info("=" * 80)
        logger.info("AGGREGATED RESULTS")
        logger.info("=" * 80)
        logger.info(f"VMs with results: {len(all_results)}")
        logger.info(f"VMs skipped (no IO running): {skipped}")
        logger.info(f"Time: {elapsed:.2f}s")
        logger.info("")
        logger.info("AGGREGATED IOPS:")
        logger.info(f"  Total IOPS:  {total_iops:,}")
        logger.info(f"  Read IOPS:   {total_read_iops:,}")
        logger.info(f"  Write IOPS:  {total_write_iops:,}")
        logger.info("")
        logger.info("AGGREGATED THROUGHPUT:")
        logger.info(f"  Total:  {total_throughput/1024/1024:.2f} MB/s ({total_throughput/1024/1024/1024:.2f} GB/s)")
        logger.info(f"  Read:   {total_read_throughput/1024/1024:.2f} MB/s")
        logger.info(f"  Write:  {total_write_throughput/1024/1024:.2f} MB/s")
        logger.info("")
        logger.info("LATENCY (across all VMs):")
        logger.info(f"  Average: {avg_latency:.0f} us ({avg_latency/1000:.2f} ms)")
        if min_latencies:
            logger.info(f"  Min:     {min(min_latencies)} us")
        if max_latencies:
            logger.info(f"  Max:     {max(max_latencies)} us")
        logger.info("")
        logger.info("PER-VM SUMMARY:")
        logger.info("-" * 80)
        logger.info(f"{'Namespace':<30} {'IOPS':>10} {'Throughput':>15} {'Avg Lat (us)':>15}")
        logger.info("-" * 80)
        for r in sorted(all_results, key=lambda x: x["namespace"]):
            throughput_mb = r["total_throughput_bytes"] / 1024 / 1024
            logger.info(f"{r['namespace']:<30} {r['total_iops']:>10,} {throughput_mb:>12.2f} MB/s {r['avg_latency_us']:>15.0f}")
        logger.info("=" * 80)

        # Save aggregated results to JSON
        summary = {
            "timestamp": datetime.now().isoformat(),
            "vms_with_results": len(all_results),
            "vms_skipped": skipped,
            "elapsed_seconds": elapsed,
            "aggregated": {
                "total_iops": total_iops,
                "read_iops": total_read_iops,
                "write_iops": total_write_iops,
                "total_throughput_bytes": total_throughput,
                "read_throughput_bytes": total_read_throughput,
                "write_throughput_bytes": total_write_throughput,
                "avg_latency_us": avg_latency,
                "min_latency_us": min(min_latencies) if min_latencies else 0,
                "max_latency_us": max(max_latencies) if max_latencies else 0
            },
            "per_vm_results": all_results
        }

        summary_file = f"{output_dir}/aggregated_results.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        logger.info(f"Results saved to: {output_dir}/")
        logger.info(f"Aggregated summary: {summary_file}")

        return

    # Handle deploy action - calls datasource-clone script
    if args.action == "deploy":
        logger.info("=" * 60)
        logger.info("ELBENCHO - DEPLOY VMs")
        logger.info("=" * 60)
        logger.info(f"VM Template: {args.vm_template}")
        if args.secret_yaml:
            logger.info(f"Secret YAML: {args.secret_yaml}")
        logger.info(f"Namespaces: {args.namespace_prefix}-{args.start} to {args.namespace_prefix}-{args.end}")
        logger.info(f"VM Name: {args.vm_name}")
        logger.info(f"Concurrency: {args.concurrency}")
        logger.info("=" * 60)

        # Find the datasource-clone script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.abspath(os.path.join(script_dir, '..', '..'))
        datasource_clone_script = os.path.join(repo_root, 'datasource-clone', 'measure-vm-creation-time.py')

        if not os.path.exists(datasource_clone_script):
            logger.error(f"datasource-clone script not found: {datasource_clone_script}")
            sys.exit(1)

        # Build command to call datasource-clone
        cmd = [
            sys.executable, datasource_clone_script,
            '--start', str(args.start),
            '--end', str(args.end),
            '--vm-name', args.vm_name,
            '--vm-template', args.vm_template,
            '--namespace-prefix', args.namespace_prefix,
            '--concurrency', str(args.concurrency),
            '--ping-timeout', str(args.ping_timeout),
            '--ssh-pod', args.ssh_pod,
            '--ssh-pod-ns', args.ssh_pod_ns,
            '--log-level', args.log_level,
        ]

        if args.secret_yaml:
            cmd.extend(['--secret-yaml', args.secret_yaml])

        if args.save_results:
            cmd.append('--save-results')
            cmd.extend(['--results-folder', args.results_dir])
            if args.storage_driver != "Not-Specified":
                cmd.extend(['--storage-driver', args.storage_driver])

        logger.info(f"Running: {' '.join(cmd[:3])}...")
        logger.debug(f"Full command: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, cwd=repo_root)
            if result.returncode != 0:
                logger.error(f"Deploy failed with return code: {result.returncode}")
                sys.exit(result.returncode)
            logger.info("Deploy completed successfully")
        except KeyboardInterrupt:
            logger.warning("Deploy interrupted by user")
            sys.exit(130)
        except Exception as e:
            logger.error(f"Deploy failed: {e}")
            sys.exit(1)

        return

    # Handle cleanup action
    if args.action == "cleanup":
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
        from utils.common import cleanup_test_namespaces

        logger.info("=" * 60)
        logger.info("ELBENCHO - CLEANUP")
        logger.info("=" * 60)
        logger.info(f"Namespaces: {args.namespace_prefix}-{args.start} to {args.namespace_prefix}-{args.end}")
        logger.info(f"VM Name: {args.vm_name}")
        logger.info("=" * 60)

        cleanup_test_namespaces(
            namespace_prefix=args.namespace_prefix,
            start=args.start,
            end=args.end,
            vm_name=args.vm_name,
            delete_namespaces=True,
            batch_size=20,
            logger=logger
        )

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Cleanup completed in {elapsed:.2f}s")

        return

    # Handle run-all action - full workflow
    if args.action == "run-all":
        import time as time_module

        logger.info("")
        logger.info("=" * 60)
        logger.info("ELBENCHO - FULL RUN")
        logger.info("=" * 60)
        logger.info(f"Namespaces: {args.namespace_prefix}-{args.start} to {args.namespace_prefix}-{args.end} ({len(vm_targets)} VMs)")
        logger.info(f"VM Template: {args.vm_template}")
        disk_info = f"{args.num_disks} disks" if args.num_disks > 0 else "all disks"
        thread_info = f"{args.threads} threads" if args.threads > 0 else "threads=disks"
        if args.iops > 0:
            logger.info(f"Mode: IOPS ({args.iops} total = {args.iops//2} read + {args.iops//2} write)")
        else:
            logger.info(f"Mode: rwmixpct ({args.rwmixpct}% read / {100-args.rwmixpct}% write)")
        logger.info(f"Block size: {args.block_size}, Disks: {disk_info}, {thread_info}, iodepth: {args.iodepth}")
        logger.info(f"Duration: {args.duration}s")
        logger.info("=" * 60)

        # Step 1: Deploy VMs
        logger.info("")
        logger.info("[1/4] Deploying VMs...")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.abspath(os.path.join(script_dir, '..', '..'))
        datasource_clone_script = os.path.join(repo_root, 'datasource-clone', 'measure-vm-creation-time.py')

        if not os.path.exists(datasource_clone_script):
            logger.error(f"datasource-clone script not found: {datasource_clone_script}")
            sys.exit(1)

        deploy_cmd = [
            sys.executable, datasource_clone_script,
            '--start', str(args.start),
            '--end', str(args.end),
            '--vm-name', args.vm_name,
            '--vm-template', args.vm_template,
            '--namespace-prefix', args.namespace_prefix,
            '--concurrency', str(args.concurrency),
            '--ping-timeout', str(args.ping_timeout),
            '--ssh-pod', args.ssh_pod,
            '--ssh-pod-ns', args.ssh_pod_ns,
            '--log-level', args.log_level,
        ]

        if args.secret_yaml:
            deploy_cmd.extend(['--secret-yaml', args.secret_yaml])

        logger.debug(f"Deploy command: {' '.join(deploy_cmd)}")

        try:
            result = subprocess.run(deploy_cmd, cwd=repo_root)
            if result.returncode != 0:
                logger.error(f"Deploy failed with return code: {result.returncode}")
                sys.exit(result.returncode)
            logger.info("Deploy completed successfully")
        except KeyboardInterrupt:
            logger.warning("Deploy interrupted by user")
            sys.exit(130)
        except Exception as e:
            logger.error(f"Deploy failed: {e}")
            sys.exit(1)

        deploy_elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Deploy completed in {deploy_elapsed:.2f}s")

        # Step 2: Start workload
        logger.info("")
        logger.info("[2/4] Starting elbencho workload on all VMs...")
        workload_start = datetime.now()

        workload_success = 0
        workload_failure = 0

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    manage_service_on_vm, ns, vm_name, "change-workload",
                    args.ssh_pod, args.ssh_pod_ns, args.vm_user, args.vm_password, logger,
                    args.block_size, args.num_disks, args.iops, args.rwmixpct,
                    args.iodepth, args.threads, args.duration
                ): (ns, vm_name)
                for ns, vm_name in vm_targets
            }

            for future in as_completed(futures):
                ns, vm_name = futures[future]
                try:
                    if future.result():
                        workload_success += 1
                        logger.info(f"  ✓ {ns}/{vm_name}")
                    else:
                        workload_failure += 1
                        logger.warning(f"  ✗ {ns}/{vm_name}")
                except Exception as e:
                    logger.error(f"  ✗ {ns}/{vm_name}: {e}")
                    workload_failure += 1

        workload_elapsed = (datetime.now() - workload_start).total_seconds()
        logger.info(f"Workload started on {workload_success}/{len(vm_targets)} VMs in {workload_elapsed:.2f}s")

        if workload_failure > 0:
            logger.warning(f"{workload_failure} VMs failed to start workload")

        # Step 3: Wait for duration
        logger.info("")
        logger.info(f"[3/4] Waiting for workload duration ({args.duration}s)...")
        wait_start = datetime.now()

        # Show progress every 30 seconds
        remaining = args.duration
        while remaining > 0:
            sleep_time = min(30, remaining)
            time_module.sleep(sleep_time)
            remaining -= sleep_time
            elapsed_wait = (datetime.now() - wait_start).total_seconds()
            logger.info(f"  Progress: {elapsed_wait:.0f}s / {args.duration}s ({100*elapsed_wait/args.duration:.1f}%)")

        logger.info(f"Workload duration completed")

        # Step 4: Gather results
        logger.info("")
        logger.info("[4/4] Gathering results...")

        output_dir = build_elbencho_output_dir(args, vm_targets, logger)
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Output directory: {output_dir}")

        all_results = []
        skipped = 0

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    gather_results_from_vm, ns, vm_name,
                    args.ssh_pod, args.ssh_pod_ns, args.vm_user, args.vm_password,
                    output_dir, logger
                ): (ns, vm_name)
                for ns, vm_name in vm_targets
            }

            for future in as_completed(futures):
                ns, vm_name = futures[future]
                try:
                    result = future.result()
                    if result["skipped"]:
                        skipped += 1
                    else:
                        all_results.append(result)
                except Exception as e:
                    logger.error(f"[{ns}/{vm_name}] Exception: {e}")

        # Calculate aggregates
        total_read_iops = sum(r["total_read_iops"] for r in all_results)
        total_write_iops = sum(r["total_write_iops"] for r in all_results)
        total_iops = sum(r["total_iops"] for r in all_results)
        total_read_throughput = sum(r["total_read_throughput_bytes"] for r in all_results)
        total_write_throughput = sum(r["total_write_throughput_bytes"] for r in all_results)
        total_throughput = sum(r["total_throughput_bytes"] for r in all_results)

        latencies = [r["avg_latency_us"] for r in all_results if r["avg_latency_us"] > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        min_latencies = [r["min_latency_us"] for r in all_results if r["min_latency_us"] > 0]
        max_latencies = [r["max_latency_us"] for r in all_results if r["max_latency_us"] > 0]

        total_elapsed = (datetime.now() - start_time).total_seconds()

        # Print summary
        logger.info("")
        logger.info("=" * 80)
        logger.info("ELBENCHO RUN-ALL RESULTS")
        logger.info("=" * 80)
        logger.info(f"VMs deployed: {len(vm_targets)}")
        logger.info(f"VMs with results: {len(all_results)}")
        logger.info(f"VMs skipped (no IO running): {skipped}")
        logger.info(f"Total time: {total_elapsed:.2f}s")
        logger.info("")
        logger.info("AGGREGATED IOPS:")
        logger.info(f"  Total IOPS:  {total_iops:,}")
        logger.info(f"  Read IOPS:   {total_read_iops:,}")
        logger.info(f"  Write IOPS:  {total_write_iops:,}")
        logger.info("")
        logger.info("AGGREGATED THROUGHPUT:")
        logger.info(f"  Total:  {total_throughput/1024/1024:.2f} MB/s ({total_throughput/1024/1024/1024:.2f} GB/s)")
        logger.info(f"  Read:   {total_read_throughput/1024/1024:.2f} MB/s")
        logger.info(f"  Write:  {total_write_throughput/1024/1024:.2f} MB/s")
        logger.info("")
        logger.info("LATENCY (across all VMs):")
        logger.info(f"  Average: {avg_latency:.0f} us ({avg_latency/1000:.2f} ms)")
        if min_latencies:
            logger.info(f"  Min:     {min(min_latencies)} us")
        if max_latencies:
            logger.info(f"  Max:     {max(max_latencies)} us")
        logger.info("=" * 80)

        # Save results
        if args.save_results:
            summary = {
                "timestamp": datetime.now().isoformat(),
                "action": "run-all",
                "vms_deployed": len(vm_targets),
                "vms_with_results": len(all_results),
                "vms_skipped": skipped,
                "total_elapsed_seconds": total_elapsed,
                "workload_config": {
                    "iops": args.iops,
                    "rwmixpct": args.rwmixpct,
                    "block_size": args.block_size,
                    "num_disks": args.num_disks,
                    "iodepth": args.iodepth,
                    "threads": args.threads,
                    "duration": args.duration
                },
                "aggregated": {
                    "total_iops": total_iops,
                    "read_iops": total_read_iops,
                    "write_iops": total_write_iops,
                    "total_throughput_bytes": total_throughput,
                    "read_throughput_bytes": total_read_throughput,
                    "write_throughput_bytes": total_write_throughput,
                    "avg_latency_us": avg_latency,
                    "min_latency_us": min(min_latencies) if min_latencies else 0,
                    "max_latency_us": max(max_latencies) if max_latencies else 0
                },
                "per_vm_results": all_results
            }

            summary_file = f"{output_dir}/aggregated_results.json"
            with open(summary_file, 'w') as f:
                json.dump(summary, f, indent=2)
            logger.info(f"Results saved to: {output_dir}/")
            logger.info(f"Aggregated summary: {summary_file}")

        return

    # Handle other actions
    success = 0
    failure = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                manage_service_on_vm, ns, vm_name, args.action,
                args.ssh_pod, args.ssh_pod_ns, args.vm_user, args.vm_password, logger,
                args.block_size, args.num_disks, args.iops, args.rwmixpct,
                args.iodepth, args.threads, args.duration
            ): (ns, vm_name)
            for ns, vm_name in vm_targets
        }

        for future in as_completed(futures):
            ns, vm_name = futures[future]
            try:
                if future.result():
                    success += 1
                else:
                    failure += 1
            except Exception as e:
                logger.error(f"[{ns}/{vm_name}] Exception: {e}")
                failure += 1

    elapsed = (datetime.now() - start_time).total_seconds()

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"SUMMARY - {args.action.upper()}")
    logger.info("=" * 60)
    logger.info(f"Total VMs: {len(vm_targets)}")
    logger.info(f"Success: {success}")
    logger.info(f"Failed: {failure}")
    logger.info(f"Time: {elapsed:.2f}s")
    if args.action == "change-workload":
        if args.iops > 0:
            logger.info(f"Mode: IOPS ({args.iops} total)")
        else:
            logger.info(f"Mode: rwmixpct ({args.rwmixpct}% read)")
        logger.info(f"Block size: {args.block_size}, iodepth: {args.iodepth}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
