#!/usr/bin/env python3
"""
KubeVirt FIO Benchmark - Storage I/O Performance Test

This script runs FIO benchmarks across multiple VMs to measure storage performance.
Results include IOPS, bandwidth, and latency metrics aggregated across all VMs.

Usage:
    python3 measure-fio-performance.py --start 1 --end 50 --storage-class px-csi

Author: KubeVirt Benchmark Suite Contributors
License: Apache 2.0
"""

import argparse
import os
import sys
import signal
import tempfile
import shutil
import subprocess
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Optional, Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.common import (
    setup_logging, run_kubectl_command, create_namespace, create_namespaces_parallel,
    delete_namespace, get_vm_status, cleanup_test_namespaces, confirm_cleanup,
    print_cleanup_summary
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
Examples:
  # Run FIO on 10 VMs
  %(prog)s --start 1 --end 10 --storage-class px-csi

  # Custom FIO parameters
  %(prog)s --start 1 --end 50 --storage-class px-csi \\
      --fio-runtime 600 --fio-rw randrw --fio-bs 8k
        """
    )

    # Required
    parser.add_argument('-s', '--start', type=int, required=True, help='Start namespace index')
    parser.add_argument('-e', '--end', type=int, required=True, help='End namespace index')
    parser.add_argument('--storage-class', type=str, required=True, help='Storage class name')

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
    parser.add_argument('--save-results', action='store_true', help='Save results to JSON/CSV')
    parser.add_argument('--cleanup', action='store_true', help='Delete VMs after test')

    # Collection settings
    parser.add_argument('--collect-retries', type=int, default=8, help='Max retries for collecting results')
    parser.add_argument('--collect-retry-delay', type=int, default=20, help='Delay (seconds) between retries')
    parser.add_argument('--collect-concurrency', type=int, default=5, help='Max concurrent result collections')

    # Logging
    parser.add_argument('--log-file', type=str, help='Log file path')
    parser.add_argument('--log-level', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])

    return parser.parse_args()


def generate_ssh_keypair(logger) -> Tuple[str, str, str]:
    """Generate temporary SSH keypair. Returns (private_key_path, public_key_content, key_dir)."""
    key_dir = tempfile.mkdtemp(prefix="fio-benchmark-keys-")
    private_key = os.path.join(key_dir, "id_rsa")
    public_key = os.path.join(key_dir, "id_rsa.pub")

    subprocess.run([
        "ssh-keygen", "-t", "rsa", "-b", "2048",
        "-f", private_key, "-N", "", "-q"
    ], check=True)

    os.chmod(private_key, 0o600)

    with open(public_key) as f:
        public_key_content = f.read().strip()

    logger.info(f"Generated SSH keypair in {key_dir}")
    return private_key, public_key_content, key_dir


def prepare_vm_yaml(template_path: str, vm_name: str, storage_class: str,
                    ssh_pubkey: str, fio_config: Dict, logger) -> str:
    """Prepare VM YAML with substituted values. Returns YAML content."""
    with open(template_path) as f:
        content = f.read()

    replacements = {
        '{{VM_NAME}}': vm_name,
        '{{STORAGE_CLASS_NAME}}': storage_class,
        '{{SSH_PUBLIC_KEY}}': ssh_pubkey,
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
        status = get_vm_status(namespace, vm_name, logger)
        if status == 'Running':
            return True
        time.sleep(5)
    return False


def get_vm_status(namespace: str, vm_name: str) -> Tuple[str, str]:
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


def get_pvc_status(namespace: str) -> str:
    """Get PVC status summary for namespace."""
    try:
        result = subprocess.run([
            'kubectl', 'get', 'pvc', '-n', namespace,
            '-o', 'jsonpath={range .items[*]}{.metadata.name}={.status.phase} {end}'
        ], capture_output=True, text=True)
        return result.stdout.strip() or "No PVCs"
    except subprocess.CalledProcessError:
        return "Error"


def get_vm_ip(namespace: str, vm_name: str) -> Optional[str]:
    """Get VM's pod network IP address."""
    try:
        result = subprocess.run([
            'kubectl', 'get', 'vmi', '-n', namespace, vm_name,
            '-o', 'jsonpath={.status.interfaces[0].ipAddress}'
        ], capture_output=True, text=True, check=True)
        return result.stdout.strip() if result.stdout.strip() else None
    except subprocess.CalledProcessError:
        return None


def run_ssh_command_via_pod(vm_ip: str, command: str, private_key_path: str, timeout: int = 60) -> Optional[str]:
    """Run SSH command on VM using a helper pod with SSH key."""
    import uuid
    pod_name = f"ssh-helper-{uuid.uuid4().hex[:8]}"

    with open(private_key_path) as f:
        key_content = f.read()

    ssh_cmd = f"""
mkdir -p ~/.ssh && echo '{key_content}' > ~/.ssh/id_rsa && chmod 600 ~/.ssh/id_rsa && \
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -i ~/.ssh/id_rsa cloud-user@{vm_ip} '{command}'
"""

    try:
        result = subprocess.run([
            'kubectl', 'run', pod_name, '--rm', '-i', '--restart=Never',
            '--image=alpine:latest', '--',
            'sh', '-c', f'apk add --no-cache -q openssh-client >/dev/null 2>&1 && {ssh_cmd}'
        ], capture_output=True, text=True, timeout=timeout)
        return result.stdout
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        # Cleanup pod if it wasn't deleted
        subprocess.run(['kubectl', 'delete', 'pod', pod_name, '--ignore-not-found'],
                      capture_output=True, timeout=10)
        return None


def wait_for_fio_complete(namespace: str, vm_name: str, private_key: str,
                          timeout: int, logger) -> bool:
    """Wait for FIO to complete by checking for completion marker."""
    start = time.time()

    # First wait for VM to be Running
    while time.time() - start < 300:
        vm_status, vmi_phase = get_vm_status(namespace, vm_name)
        if vmi_phase == "Running":
            break
        if vmi_phase in ["Failed", "Unknown"] or "Error" in vm_status:
            pvc_status = get_pvc_status(namespace)
            logger.error(f"[{namespace}] VM failed to start. VM={vm_status}, VMI={vmi_phase}, PVCs={pvc_status}")
            return False
        time.sleep(15)
    else:
        vm_status, vmi_phase = get_vm_status(namespace, vm_name)
        pvc_status = get_pvc_status(namespace)
        logger.warning(f"[{namespace}] VM not running after 5 min. VM={vm_status}, VMI={vmi_phase}, PVCs={pvc_status}")
        return False

    # Wait for VM to get an IP
    vm_ip = None
    while time.time() - start < 360 and not vm_ip:
        vm_ip = get_vm_ip(namespace, vm_name)
        if not vm_ip:
            time.sleep(10)

    if not vm_ip:
        logger.warning(f"[{namespace}] VM running but no IP after 6 minutes")
        return False

    logger.info(f"[{namespace}] VM running with IP {vm_ip}, waiting for FIO...")

    # Wait for FIO to complete
    while time.time() - start < timeout:
        output = run_ssh_command_via_pod(vm_ip, 'cat /tmp/fio_complete 2>/dev/null', private_key, timeout=90)
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


def collect_fio_results(namespace: str, vm_name: str, private_key: str,
                        output_dir: str, logger,
                        max_retries: int = 8, retry_delay: int = 20) -> Optional[Dict]:
    """Collect FIO results from VM via helper pod SSH with retries."""
    vm_results_dir = os.path.join(output_dir, "per-vm-results", namespace)
    os.makedirs(vm_results_dir, exist_ok=True)
    local_path = os.path.join(vm_results_dir, "fio_raw.json")

    vm_ip = get_vm_ip(namespace, vm_name)
    if not vm_ip:
        logger.error(f"[{namespace}] Could not get VM IP")
        return None

    for attempt in range(max_retries):
        try:
            output = run_ssh_command_via_pod(vm_ip, 'cat /tmp/fio_results.json', private_key, timeout=120)
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


def main():
    args = parse_args()
    logger = setup_logging(args.log_file, args.log_level)

    namespaces = [f"{args.namespace_prefix}-{i}" for i in range(args.start, args.end + 1)]
    num_vms = len(namespaces)

    fio_config = {
        'runtime': args.fio_runtime,
        'bs': args.fio_bs,
        'rw': args.fio_rw,
        'iodepth': args.fio_iodepth,
        'numjobs': args.fio_numjobs,
        'size': args.fio_size
    }

    print("\n" + "=" * 60)
    print("FIO BENCHMARK")
    print("=" * 60)
    print(f"Namespaces: {namespaces[0]} to {namespaces[-1]} ({num_vms} VMs)")
    print(f"Storage Class: {args.storage_class}")
    print(f"FIO Config: {args.fio_rw} | bs={args.fio_bs} | iodepth={args.fio_iodepth} | "
          f"numjobs={args.fio_numjobs} | runtime={args.fio_runtime}s")
    print("=" * 60 + "\n")

    test_start = time.time()
    key_dir = None

    try:
        # Step 1: Generate SSH keypair
        print("[1/5] Generating SSH keypair...")
        private_key, public_key, key_dir = generate_ssh_keypair(logger)

        # Step 2: Create namespaces
        print("[2/5] Creating namespaces...")
        create_namespaces_parallel(namespaces, batch_size=20, logger=logger)

        # Step 3: Deploy VMs
        print("[3/5] Deploying FIO VMs...")
        template_path = os.path.join(os.path.dirname(__file__), args.vm_template)
        vm_yaml = prepare_vm_yaml(
            template_path, args.vm_name, args.storage_class,
            public_key, fio_config, logger
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

        # Step 4: Wait for VMs and FIO to complete
        print(f"[4/5] Waiting for VMs to boot and FIO to complete (polling every 30s)...")
        fio_timeout = args.fio_runtime + 600  # FIO runtime + boot time + buffer

        completed = []
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    wait_for_fio_complete, ns, args.vm_name, private_key, fio_timeout, logger
                ): ns for ns in namespaces
            }
            for future in as_completed(futures):
                ns = futures[future]
                success = future.result()
                status = "✓" if success else "✗"
                print(f"  {status} {ns}")
                completed.append((ns, success))

        # Step 5: Collect results
        print("[5/5] Collecting results...")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = os.path.join(
            args.results_dir,
            f"{timestamp}_fio_benchmark_{args.start}-{args.end}"
        )
        os.makedirs(output_dir, exist_ok=True)

        all_results = []
        with ThreadPoolExecutor(max_workers=args.collect_concurrency) as executor:
            futures = {
                executor.submit(
                    collect_fio_results, ns, args.vm_name, private_key, output_dir, logger,
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

    finally:
        # Cleanup SSH keys
        if key_dir and os.path.exists(key_dir):
            shutil.rmtree(key_dir)
            logger.debug(f"Cleaned up SSH keys from {key_dir}")

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


if __name__ == '__main__':
    main()

