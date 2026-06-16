#!/usr/bin/env python3
"""
KubeVirt FIO Benchmark - Storage I/O Performance Test

This script runs FIO benchmarks across multiple VMs to measure storage performance.
Results include IOPS, bandwidth, and latency metrics aggregated across all VMs.
SSH access uses password authentication via an existing SSH helper pod.

Supports multiple actions:
  deploy         - Deploy VMs with FIO pre-configured (FIO runs automatically on boot)
  status         - Check VM and FIO status
  gather-results - Collect FIO results from VMs
  cleanup        - Delete VMs and namespaces
  run-all        - Full workflow: deploy, wait for FIO, gather results

Usage:
    # Full workflow (deploy + wait + gather)
    python3 measure-fio-performance.py --action run-all --start 1 --end 10 --storage-class px-csi

    # Step-by-step workflow
    python3 measure-fio-performance.py --action deploy --start 1 --end 10 --storage-class px-csi
    python3 measure-fio-performance.py --action status --start 1 --end 10
    python3 measure-fio-performance.py --action gather-results --start 1 --end 10 --storage-driver portworx-3.6
    python3 measure-fio-performance.py --action cleanup --start 1 --end 10

Author: KubeVirt Benchmark Suite Contributors
License: Apache 2.0
"""

import argparse
import os
import sys
import signal
import subprocess
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Optional, Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from utils.common import (
    setup_logging, run_kubectl_command, create_namespace, create_namespaces_parallel,
    delete_namespace, cleanup_test_namespaces, confirm_cleanup,
    print_cleanup_summary, get_vm_disk_count, get_vmi_ip, get_pvc_status,
    ssh_exec_command,
)

# Defaults
DEFAULT_VM_NAME = 'fio-vm'
DEFAULT_VM_TEMPLATE = '../examples/vm-templates/fio-vm-template.yaml'
DEFAULT_NAMESPACE_PREFIX = 'fio-benchmark'
DEFAULT_CONCURRENCY = 20
DEFAULT_FIO_RUNTIME = 300
DEFAULT_FIO_BS = '4k'
DEFAULT_FIO_RW = 'randwrite'
DEFAULT_FIO_IODEPTH = 64
DEFAULT_FIO_NUMJOBS = 4
DEFAULT_FIO_SIZE = '10G'


def parse_args():
    parser = argparse.ArgumentParser(
        description='Run FIO benchmarks across multiple KubeVirt VMs.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Actions:
  deploy         Deploy VMs with FIO pre-configured (FIO runs automatically on boot)
  status         Check VM and FIO status across namespaces
  gather-results Collect FIO results from VMs (requires --ssh-pod)
  cleanup        Delete VMs and namespaces
  run-all        Full workflow: deploy, wait for FIO, gather results (requires --ssh-pod)

Examples:
  # Full workflow (deploy + wait + gather)
  %(prog)s --action run-all --start 1 --end 10 --storage-class px-csi --save-results

  # Step-by-step workflow
  %(prog)s --action deploy --start 1 --end 10 --storage-class px-csi
  %(prog)s --action status --start 1 --end 10
  %(prog)s --action gather-results --start 1 --end 10 --storage-driver portworx-3.6
  %(prog)s --action cleanup --start 1 --end 10

  # Custom FIO parameters (for deploy or run-all)
  %(prog)s --action deploy --start 1 --end 50 --storage-class px-csi \\
      --fio-runtime 600 --fio-rw randrw --fio-bs 8k
        """
    )

    # Action
    parser.add_argument('--action', '-a', type=str, default='run-all',
                        choices=['deploy', 'status', 'gather-results', 'cleanup', 'run-all'],
                        help='Action to perform (default: run-all)')

    # Required for most actions
    parser.add_argument('-s', '--start', type=int, required=True, help='Start namespace index')
    parser.add_argument('-e', '--end', type=int, required=True, help='End namespace index')
    parser.add_argument('--storage-class', type=str, help='Storage class name (required for deploy/run-all)')

    # VM config
    parser.add_argument('--vm-name', type=str, default=DEFAULT_VM_NAME)
    parser.add_argument('--vm-template', type=str, default=DEFAULT_VM_TEMPLATE)
    parser.add_argument('--namespace-prefix', type=str, default=DEFAULT_NAMESPACE_PREFIX)
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY)

    # FIO config
    parser.add_argument('--fio-runtime', type=int, default=DEFAULT_FIO_RUNTIME, help='FIO runtime (seconds)')
    parser.add_argument('--fio-bs', type=str, default=DEFAULT_FIO_BS, help='Block size')
    parser.add_argument('--fio-rw', type=str, default=DEFAULT_FIO_RW,
                        choices=['read', 'write', 'randread', 'randwrite', 'randrw', 'rw'])
    parser.add_argument('--fio-iodepth', type=int, default=DEFAULT_FIO_IODEPTH)
    parser.add_argument('--fio-numjobs', type=int, default=DEFAULT_FIO_NUMJOBS)
    parser.add_argument('--fio-size', type=str, default=DEFAULT_FIO_SIZE, help='Test file size')

    # Output
    parser.add_argument('--results-dir', type=str, default='results', help='Base results directory')
    parser.add_argument('--storage-driver', type=str, default='Not-Specified',
                        help='Storage driver for results folder (default: Not-Specified)')
    parser.add_argument('--disks-per-vm', type=str, default='auto',
                        help='Disks per VM for results folder name (default: auto-detect from first VM, fallback: 1-disk)')
    parser.add_argument('--save-results', action='store_true', help='Save results to JSON/CSV')
    parser.add_argument('--cleanup', action='store_true', help='Delete VMs after test (for run-all action)')

    # Collection settings
    parser.add_argument('--collect-retries', type=int, default=8, help='Max retries for collecting results')
    parser.add_argument('--collect-retry-delay', type=int, default=20, help='Delay (seconds) between retries')
    parser.add_argument('--collect-concurrency', type=int, default=5, help='Max concurrent result collections')

    # SSH settings (password-based via existing pod)
    parser.add_argument('--ssh-pod', default='ssh-test-pod', help='SSH helper pod name (must have sshpass installed)')
    parser.add_argument('--ssh-pod-ns', default='default', help='SSH helper pod namespace')
    parser.add_argument('--vm-user', default='cloud-user', help='VM SSH user')
    parser.add_argument('--vm-password', default='changeme', help='VM SSH password')

    # Logging
    parser.add_argument('--log-file', type=str, help='Log file path')
    parser.add_argument('--log-level', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])

    args = parser.parse_args()

    # Validate required args based on action
    if args.action in ['deploy', 'run-all'] and not args.storage_class:
        parser.error(f"--storage-class is required for action '{args.action}'")

    return args


def prepare_vm_yaml(template_path: str, vm_name: str, storage_class: str,
                    fio_config: Dict, vm_password: str, logger) -> str:
    """Prepare VM YAML with substituted values. Returns YAML content."""
    with open(template_path) as f:
        content = f.read()

    replacements = {
        '{{VM_NAME}}': vm_name,
        '{{STORAGE_CLASS_NAME}}': storage_class,
        '{{VM_PASSWORD}}': vm_password,
        '{{FIO_RUNTIME}}': str(fio_config['runtime']),
        '{{FIO_BS}}': fio_config['bs'],
        '{{FIO_RW}}': fio_config['rw'],
        '{{FIO_IODEPTH}}': str(fio_config['iodepth']),
        '{{FIO_NUMJOBS}}': str(fio_config['numjobs']),
        '{{FIO_SIZE}}': fio_config['size'],
    }

    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)

    return content


def deploy_vm(namespace: str, vm_yaml: str, logger) -> bool:
    """Deploy VM in namespace. Returns success status."""
    try:
        proc = subprocess.run(
            ['kubectl', 'apply', '-f', '-', '-n', namespace],
            input=vm_yaml, capture_output=True, text=True, check=True
        )
        logger.debug(f"[{namespace}] VM deployed")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"[{namespace}] Failed to deploy VM: {e.stderr}")
        return False


def wait_for_vm_running(namespace: str, vm_name: str, timeout: int, logger) -> bool:
    """Wait for VM to reach Running state."""
    start = time.time()
    while time.time() - start < timeout:
        vm_status, vmi_phase = get_vm_and_vmi_status(namespace, vm_name)
        if vmi_phase == 'Running':
            return True
        time.sleep(5)
    return False


def get_vm_and_vmi_status(namespace: str, vm_name: str) -> Tuple[str, str]:
    """Get VM and VMI status. Returns (vm_status, vmi_phase)."""
    vm_status = "Unknown"
    vmi_phase = "NotFound"
    try:
        result = subprocess.run([
            'kubectl', 'get', 'vm', '-n', namespace, vm_name,
            '-o', 'jsonpath={.status.printableStatus}'
        ], capture_output=True, text=True)
        vm_status = result.stdout.strip() or "Unknown"
    except subprocess.CalledProcessError:
        pass
    try:
        result = subprocess.run([
            'kubectl', 'get', 'vmi', '-n', namespace, vm_name,
            '-o', 'jsonpath={.status.phase}'
        ], capture_output=True, text=True)
        vmi_phase = result.stdout.strip() or "NotFound"
    except subprocess.CalledProcessError:
        pass
    return vm_status, vmi_phase


def run_ssh_command(vm_ip: str, command: str, ssh_pod: str, ssh_pod_ns: str,
                    vm_user: str, vm_password: str, timeout: int = 60) -> Optional[str]:
    """Run SSH command on VM via the shared helper. Returns stdout, or None on exec failure."""
    try:
        _, stdout, _ = ssh_exec_command(
            vm_ip, command, ssh_pod, ssh_pod_ns, vm_user, vm_password,
            timeout=timeout
        )
        return stdout
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None


def wait_for_fio_complete(namespace: str, vm_name: str, ssh_config: Dict,
                          timeout: int, logger) -> bool:
    """Wait for FIO to complete by checking for completion marker."""
    start = time.time()

    # First wait for VM to be Running
    while time.time() - start < 300:
        vm_status, vmi_phase = get_vm_and_vmi_status(namespace, vm_name)
        if vmi_phase == "Running":
            break
        if vmi_phase in ["Failed", "Unknown"] or "Error" in vm_status:
            pvc_status = get_pvc_status(namespace, logger)
            logger.error(f"[{namespace}] VM failed to start. VM={vm_status}, VMI={vmi_phase}, PVCs={pvc_status}")
            return False
        time.sleep(15)
    else:
        vm_status, vmi_phase = get_vm_and_vmi_status(namespace, vm_name)
        pvc_status = get_pvc_status(namespace, logger)
        logger.warning(f"[{namespace}] VM not running after 5 min. VM={vm_status}, VMI={vmi_phase}, PVCs={pvc_status}")
        return False

    # Wait for VM to get an IP
    vm_ip = None
    while time.time() - start < 360 and not vm_ip:
        vm_ip = get_vmi_ip(vm_name, namespace, logger)
        if not vm_ip:
            time.sleep(10)

    if not vm_ip:
        logger.warning(f"[{namespace}] VM running but no IP after 6 minutes")
        return False

    logger.info(f"[{namespace}] VM running with IP {vm_ip}, waiting for FIO...")

    # Wait for FIO to complete
    while time.time() - start < timeout:
        output = run_ssh_command(
            vm_ip, 'cat /tmp/fio_complete 2>/dev/null',
            ssh_config['pod'], ssh_config['pod_ns'],
            ssh_config['user'], ssh_config['password'],
            timeout=90
        )
        if output and 'completed' in output:
            return True
        time.sleep(30)

    logger.warning(f"[{namespace}] FIO did not complete within timeout")
    return False


def extract_json_object(text: str) -> Optional[str]:
    """Extract complete JSON object from text with potential noise before/after."""
    start = text.find('{')
    if start == -1:
        return None

    # Find matching closing brace
    depth = 0
    for i, char in enumerate(text[start:], start):
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    return None


def collect_fio_results(namespace: str, vm_name: str, ssh_config: Dict,
                        output_dir: str, logger,
                        max_retries: int = 8, retry_delay: int = 20) -> Optional[Dict]:
    """Collect FIO results from VM via SSH with retries."""
    vm_results_dir = os.path.join(output_dir, "per-vm-results", namespace)
    os.makedirs(vm_results_dir, exist_ok=True)
    local_path = os.path.join(vm_results_dir, "fio_raw.json")

    vm_ip = get_vmi_ip(vm_name, namespace, logger)
    if not vm_ip:
        logger.error(f"[{namespace}] Could not get VM IP")
        return None

    for attempt in range(max_retries):
        try:
            output = run_ssh_command(
                vm_ip, 'cat /tmp/fio_results.json',
                ssh_config['pod'], ssh_config['pod_ns'],
                ssh_config['user'], ssh_config['password'],
                timeout=120
            )
            if not output:
                logger.warning(f"[{namespace}] Retry {attempt+1}/{max_retries}: No output")
                time.sleep(retry_delay)
                continue

            json_content = extract_json_object(output)
            if not json_content:
                logger.warning(f"[{namespace}] Retry {attempt+1}/{max_retries}: No valid JSON")
                time.sleep(retry_delay)
                continue

            with open(local_path, 'w') as f:
                f.write(json_content)

            return json.loads(json_content)
        except Exception as e:
            logger.warning(f"[{namespace}] Retry {attempt+1}/{max_retries}: {e}")
            time.sleep(retry_delay)

    logger.error(f"[{namespace}] Failed to collect results after {max_retries} attempts")
    return None


def parse_fio_results(namespace: str, raw_data: Dict) -> Dict:
    """Parse raw FIO JSON into summary metrics."""
    ri = wi = rb = wb = rl = wl = 0

    for job in raw_data.get("jobs", []):
        r, w = job.get("read", {}), job.get("write", {})
        ri += r.get("iops", 0)
        wi += w.get("iops", 0)
        rb += r.get("bw_bytes", 0)
        wb += w.get("bw_bytes", 0)
        if r.get("lat_ns"):
            rl = r["lat_ns"].get("mean", 0)
        if w.get("lat_ns"):
            wl = w["lat_ns"].get("mean", 0)

    return {
        "namespace": namespace,
        "read_iops": round(ri, 2),
        "write_iops": round(wi, 2),
        "read_bw_mibps": round(rb / 1024 / 1024, 2),
        "write_bw_mibps": round(wb / 1024 / 1024, 2),
        "read_lat_ms": round(rl / 1e6, 3),
        "write_lat_ms": round(wl / 1e6, 3),
        "success": True
    }


def aggregate_results(results: List[Dict], fio_config: Dict,
                      total_duration: float) -> Dict:
    """Aggregate results from all VMs into summary."""
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    def calc_stats(key):
        values = [r[key] for r in successful if r.get(key, 0) > 0]
        if not values:
            return {"avg": 0, "max": 0, "min": 0}
        return {
            "avg": round(sum(values) / len(values), 2),
            "max": round(max(values), 2),
            "min": round(min(values), 2)
        }

    return {
        "test_type": "fio_benchmark",
        "timestamp": datetime.now().isoformat(),
        "total_vms": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "total_test_duration_sec": round(total_duration, 2),
        "config": fio_config,
        "metrics": [
            {"metric": "read_iops", **calc_stats("read_iops")},
            {"metric": "write_iops", **calc_stats("write_iops")},
            {"metric": "read_bw_mibps", **calc_stats("read_bw_mibps")},
            {"metric": "write_bw_mibps", **calc_stats("write_bw_mibps")},
            {"metric": "read_lat_ms", **calc_stats("read_lat_ms")},
            {"metric": "write_lat_ms", **calc_stats("write_lat_ms")},
        ]
    }


def save_results_to_files(output_dir: str, summary: Dict, all_results: List[Dict], logger):
    """Save results to JSON and CSV files."""
    # Save summary
    summary_path = os.path.join(output_dir, "summary_fio_benchmark.json")
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    # Save all results
    results_path = os.path.join(output_dir, "fio_benchmark_results.json")
    with open(results_path, 'w') as f:
        json.dump(all_results, f, indent=2)

    # Save CSV
    csv_path = os.path.join(output_dir, "fio_benchmark_results.csv")
    with open(csv_path, 'w') as f:
        headers = ["namespace", "read_iops", "write_iops", "read_bw_mibps",
                   "write_bw_mibps", "read_lat_ms", "write_lat_ms", "success"]
        f.write(",".join(headers) + "\n")
        for r in all_results:
            f.write(",".join(str(r.get(h, "")) for h in headers) + "\n")

    logger.info(f"Results saved to {output_dir}")


def print_results_table(summary: Dict):
    """Print results summary table."""
    print("\n" + "=" * 60)
    print("FIO BENCHMARK RESULTS")
    print("=" * 60)
    print(f"Total VMs: {summary['total_vms']} | Successful: {summary['successful']} | Failed: {summary['failed']}")
    print(f"Test Duration: {summary['total_test_duration_sec']:.1f}s")
    print(f"\nFIO Config: {summary['config']['rw']} | bs={summary['config']['bs']} | "
          f"iodepth={summary['config']['iodepth']} | numjobs={summary['config']['numjobs']}")
    print("-" * 60)
    print(f"{'Metric':<20} {'Avg':>12} {'Max':>12} {'Min':>12}")
    print("-" * 60)

    for m in summary['metrics']:
        metric_name = m['metric'].replace('_', ' ').title()
        print(f"{metric_name:<20} {m['avg']:>12,.2f} {m['max']:>12,.2f} {m['min']:>12,.2f}")
    print("=" * 60 + "\n")


def get_output_dir(args, namespaces, logger) -> str:
    """Determine and create output directory."""
    if getattr(args, '_output_dir', None):
        return args._output_dir

    disks_per_vm = args.disks_per_vm
    if disks_per_vm == "auto":
        first_ns = namespaces[0]
        disk_count = get_vm_disk_count(args.vm_name, first_ns, logger)
        if disk_count > 0:
            disks_per_vm = f"{disk_count}-disk"
            if logger:
                logger.info(f"Auto-detected {disk_count} disks from {first_ns}/{args.vm_name} spec")
        else:
            disks_per_vm = "1-disk"
            if logger:
                logger.warning(f"Could not detect disks from {first_ns}/{args.vm_name}, using default: 1-disk")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    num_vms = len(namespaces)
    run_name = f"{timestamp}_fio_benchmark_{num_vms}vms"

    output_dir = os.path.join(
        args.results_dir,
        args.storage_driver,
        disks_per_vm,
        run_name
    )
    os.makedirs(output_dir, exist_ok=True)
    args._output_dir = output_dir
    return output_dir


def action_deploy(args, namespaces, fio_config, logger):
    """Deploy VMs with FIO pre-configured."""
    print("\n" + "=" * 60)
    print("FIO BENCHMARK - DEPLOY")
    print("=" * 60)
    print(f"Namespaces: {namespaces[0]} to {namespaces[-1]} ({len(namespaces)} VMs)")
    print(f"Storage Class: {args.storage_class}")
    print(f"FIO Config: {fio_config['rw']} | bs={fio_config['bs']} | iodepth={fio_config['iodepth']}")
    print("=" * 60 + "\n")

    # Create namespaces
    print("[1/2] Creating namespaces...")
    create_namespaces_parallel(namespaces, batch_size=20, logger=logger)

    # Deploy VMs
    print("[2/2] Deploying FIO VMs...")
    template_path = os.path.join(os.path.dirname(__file__), args.vm_template)
    vm_yaml = prepare_vm_yaml(
        template_path, args.vm_name, args.storage_class,
        fio_config, args.vm_password, logger
    )

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(deploy_vm, ns, vm_yaml, logger): ns
            for ns in namespaces
        }
        for future in as_completed(futures):
            ns = futures[future]
            try:
                future.result()
                print(f"  ✓ {ns}")
            except Exception as e:
                print(f"  ✗ {ns}: {e}")

    print(f"\nDeployment complete.")
    print("VMs will start FIO automatically on boot.")
    print(f"Use --action status to check progress.")
    print(f"Use --action gather-results to collect results when complete.")


def check_fio_status_in_vm(vm_ip: str, ssh_config: Dict, logger) -> str:
    """Check FIO status inside the VM. Returns: 'running', 'completed', 'not-started', or 'unknown'."""
    if not vm_ip:
        return "no-ip"

    # Check if FIO is currently running
    fio_running = run_ssh_command(
        vm_ip, 'pgrep -x fio >/dev/null 2>&1 && echo "YES" || echo "NO"',
        ssh_config['pod'], ssh_config['pod_ns'],
        ssh_config['user'], ssh_config['password'],
        timeout=30
    )

    if fio_running and 'YES' in fio_running:
        return "running"

    # Check if FIO completed
    fio_complete = run_ssh_command(
        vm_ip, 'cat /tmp/fio_complete 2>/dev/null',
        ssh_config['pod'], ssh_config['pod_ns'],
        ssh_config['user'], ssh_config['password'],
        timeout=30
    )

    if fio_complete and 'completed' in fio_complete:
        return "completed"

    # Check if FIO results exist (in case completion marker wasn't written)
    fio_results = run_ssh_command(
        vm_ip, 'test -f /tmp/fio_results.json && echo "YES" || echo "NO"',
        ssh_config['pod'], ssh_config['pod_ns'],
        ssh_config['user'], ssh_config['password'],
        timeout=30
    )

    if fio_results and 'YES' in fio_results:
        return "completed"

    if fio_running and 'NO' in fio_running:
        return "not-started"

    return "unknown"


def action_status(args, namespaces, ssh_config, logger):
    """Check VM and FIO status."""
    print("\n" + "=" * 70)
    print("FIO BENCHMARK - STATUS")
    print("=" * 70)
    print(f"Checking {len(namespaces)} VMs...")
    print("-" * 70)
    print(f"{'Namespace':<25} {'VM Status':<12} {'VMI Phase':<10} {'VM IP':<16} {'FIO Status':<12}")
    print("-" * 70)

    summary = {'running': 0, 'completed': 0, 'not-started': 0, 'not-running': 0, 'unknown': 0}

    for ns in namespaces:
        vm_status, vmi_phase = get_vm_and_vmi_status(ns, args.vm_name)
        vm_ip = get_vmi_ip(args.vm_name, ns, logger) if vmi_phase == "Running" else None

        if vmi_phase == "Running" and vm_ip:
            fio_status = check_fio_status_in_vm(vm_ip, ssh_config, logger)
        elif vmi_phase == "Running":
            fio_status = "no-ip"
        else:
            fio_status = "-"

        # Update summary
        if fio_status in summary:
            summary[fio_status] += 1
        elif vmi_phase != "Running":
            summary['not-running'] += 1

        # Color coding for display
        ip_display = vm_ip if vm_ip else "-"
        print(f"{ns:<25} {vm_status:<12} {vmi_phase:<10} {ip_display:<16} {fio_status:<12}")

    print("-" * 70)
    print(f"Summary: {summary['completed']} completed, {summary['running']} running, "
          f"{summary['not-started']} not-started, {summary['not-running']} VMs not running")
    print("=" * 70 + "\n")


def action_gather_results(args, namespaces, fio_config, ssh_config, logger):
    """Collect FIO results from existing VMs."""
    print("\n" + "=" * 60)
    print("FIO BENCHMARK - GATHER RESULTS")
    print("=" * 60)
    print(f"Collecting from {len(namespaces)} VMs...")
    print("=" * 60 + "\n")

    test_start = time.time()
    output_dir = get_output_dir(args, namespaces, logger)
    print(f"Output directory: {output_dir}\n")

    all_results = []

    # Collect from all VMs concurrently (honors --collect-concurrency), reusing the
    # same retrying collector as the run-all workflow.
    with ThreadPoolExecutor(max_workers=max(1, args.collect_concurrency)) as executor:
        futures = {
            executor.submit(
                collect_fio_results, ns, args.vm_name, ssh_config, output_dir, logger,
                args.collect_retries, args.collect_retry_delay
            ): ns for ns in namespaces
        }
        for future in as_completed(futures):
            ns = futures[future]
            raw_data = future.result()
            if raw_data:
                all_results.append(parse_fio_results(ns, raw_data))
                print(f"  ✓ {ns}")
            else:
                all_results.append({"namespace": ns, "success": False})
                print(f"  ✗ {ns}")

    # Aggregate and save
    test_duration = time.time() - test_start
    summary = aggregate_results(all_results, fio_config, test_duration)

    if args.save_results:
        save_results_to_files(output_dir, summary, all_results, logger)

    print_results_table(summary)

    if args.save_results:
        print(f"Results saved to: {output_dir}/")


def action_cleanup(args, namespaces, logger):
    """Delete VMs and namespaces."""
    print("\n" + "=" * 60)
    print("FIO BENCHMARK - CLEANUP")
    print("=" * 60)
    print(f"Cleaning up {len(namespaces)} namespaces...")
    print("=" * 60 + "\n")

    cleanup_test_namespaces(
        namespace_prefix=args.namespace_prefix,
        start=args.start,
        end=args.end,
        vm_name=args.vm_name,
        delete_namespaces=True,
        batch_size=20,
        logger=logger
    )
    print(f"Cleaned up {len(namespaces)} namespaces")


def action_run_all(args, namespaces, fio_config, ssh_config, logger):
    """Full workflow: deploy, wait for FIO, gather results."""
    print("\n" + "=" * 60)
    print("FIO BENCHMARK - FULL RUN")
    print("=" * 60)
    print(f"Namespaces: {namespaces[0]} to {namespaces[-1]} ({len(namespaces)} VMs)")
    print(f"Storage Class: {args.storage_class}")
    print(f"FIO Config: {fio_config['rw']} | bs={fio_config['bs']} | iodepth={fio_config['iodepth']} | "
          f"numjobs={fio_config['numjobs']} | runtime={fio_config['runtime']}s")
    print("=" * 60 + "\n")

    test_start = time.time()

    # Step 1: Create namespaces
    print("[1/4] Creating namespaces...")
    create_namespaces_parallel(namespaces, batch_size=20, logger=logger)

    # Step 2: Deploy VMs
    print("[2/4] Deploying FIO VMs...")
    template_path = os.path.join(os.path.dirname(__file__), args.vm_template)
    vm_yaml = prepare_vm_yaml(
        template_path, args.vm_name, args.storage_class,
        fio_config, ssh_config['password'], logger
    )

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(deploy_vm, ns, vm_yaml, logger): ns
            for ns in namespaces
        }
        for future in as_completed(futures):
            ns = futures[future]
            try:
                future.result()
                print(f"  ✓ {ns}")
            except Exception as e:
                print(f"  ✗ {ns}: {e}")

    # Step 3: Wait for VMs and FIO to complete
    print(f"[3/4] Waiting for VMs to boot and FIO to complete (polling every 30s)...")
    fio_timeout = fio_config['runtime'] + 600  # FIO runtime + boot time + buffer

    completed = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                wait_for_fio_complete, ns, args.vm_name, ssh_config, fio_timeout, logger
            ): ns for ns in namespaces
        }
        for future in as_completed(futures):
            ns = futures[future]
            success = future.result()
            status = "✓" if success else "✗"
            print(f"  {status} {ns}")
            completed.append((ns, success))

    # Step 4: Collect results
    print("[4/4] Collecting results...")
    output_dir = get_output_dir(args, namespaces, logger)
    print(f"Output directory: {output_dir}")

    all_results = []
    with ThreadPoolExecutor(max_workers=args.collect_concurrency) as executor:
        futures = {
            executor.submit(
                collect_fio_results, ns, args.vm_name, ssh_config, output_dir, logger,
                args.collect_retries, args.collect_retry_delay
            ): ns for ns in namespaces
        }
        for future in as_completed(futures):
            ns = futures[future]
            raw_data = future.result()
            if raw_data:
                parsed = parse_fio_results(ns, raw_data)
                all_results.append(parsed)
                print(f"  ✓ {ns}")
            else:
                all_results.append({"namespace": ns, "success": False})
                print(f"  ✗ {ns}")

    # Aggregate and save
    test_duration = time.time() - test_start
    summary = aggregate_results(all_results, fio_config, test_duration)

    if args.save_results:
        save_results_to_files(output_dir, summary, all_results, logger)

    print_results_table(summary)

    if args.save_results:
        print(f"Results saved to: {output_dir}/")

        # Cleanup VMs if requested
        if args.cleanup:
            print("\nCleaning up resources...")
            cleanup_test_namespaces(
                namespace_prefix=args.namespace_prefix,
                start=args.start,
                end=args.end,
                vm_name=args.vm_name,
                delete_namespaces=True,
                batch_size=20,
                logger=logger
            )
            print(f"Cleaned up {len(namespaces)} namespaces")


def main():
    args = parse_args()
    namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]

    if args.save_results and args.action in ['gather-results', 'run-all'] and not args.log_file:
        output_dir = get_output_dir(args, namespaces, logger=None)
        args.log_file = os.path.join(output_dir, "fio-benchmark.log")

    logger = setup_logging(args.log_file, args.log_level)

    fio_config = {
        'runtime': args.fio_runtime,
        'bs': args.fio_bs,
        'rw': args.fio_rw,
        'iodepth': args.fio_iodepth,
        'numjobs': args.fio_numjobs,
        'size': args.fio_size
    }

    ssh_config = {
        'pod': args.ssh_pod,
        'pod_ns': args.ssh_pod_ns,
        'user': args.vm_user,
        'password': args.vm_password
    }

    if args.action == 'deploy':
        action_deploy(args, namespaces, fio_config, logger)
    elif args.action == 'status':
        action_status(args, namespaces, ssh_config, logger)
    elif args.action == 'gather-results':
        action_gather_results(args, namespaces, fio_config, ssh_config, logger)
    elif args.action == 'cleanup':
        action_cleanup(args, namespaces, logger)
    elif args.action == 'run-all':
        action_run_all(args, namespaces, fio_config, ssh_config, logger)
    else:
        print(f"Unknown action: {args.action}")
        sys.exit(1)


if __name__ == '__main__':
    main()
