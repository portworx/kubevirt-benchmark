#!/usr/bin/env python3
"""
Elbencho benchmark command for virtbench CLI.

Wraps the measure-elbencho-performance.py script for managing elbencho workloads on VMs.
"""
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from virtbench.common import print_banner

console = Console()


@click.command('elbencho')
@click.option('--namespace-prefix', '-p', required=True, help='Namespace prefix (e.g., datasource-clone)')
@click.option('--start', '-s', type=int, required=True, help='Start namespace index')
@click.option('--end', '-e', type=int, required=True, help='End namespace index')
@click.option('--vm-name', '-n', required=True, help='VM name in each namespace')
@click.option('--action', '-a', required=True,
              type=click.Choice(['run-all', 'deploy', 'start', 'stop', 'restart', 'status',
                                 'stop-all', 'change-workload', 'gather-results', 'cleanup']),
              help='Action to perform')
# Deploy parameters
@click.option('--vm-template', default=None,
              help='Path to VM template YAML (required for deploy and run-all)')
@click.option('--secret-yaml', default=None,
              help='Path to cloudinit secret YAML file (optional, for deploy/run-all)')
@click.option('--ping-timeout', type=int, default=300,
              help='Timeout for ping tests in seconds (deploy/run-all, default: 300)')
# Workload parameters
@click.option('--iops', type=int, default=0,
              help='IOPS mode: Total IOPS (split equally between read/write)')
@click.option('--rwmixpct', type=int, default=0,
              help='rwmixpct mode: Read percentage (0-100)')
@click.option('--block-size', default='4K', help='Block size (default: 4K)')
@click.option('--num-disks', type=int, default=0, help='Number of disks (0 = all available)')
@click.option('--iodepth', type=int, default=1, help='IO depth per thread')
@click.option('--threads', type=int, default=0, help='Number of threads (0 = same as num disks)')
@click.option('--duration', type=int, default=0,
              help='Duration in seconds (0 = infinite, REQUIRED for run-all)')
# Results parameters
@click.option('--save-results', is_flag=True, help='Save results to JSON/CSV')
@click.option('--results-dir', default='./results', help='Base results directory')
@click.option('--storage-driver', default='Not-Specified', help='Storage driver label for results folder')
@click.option('--disks-per-vm', default='auto', help='Disks per VM for results folder (auto-detect)')
@click.option('--run-name', default=None, help='Custom run name')
@click.option('--output-dir', default=None,
              help='DEPRECATED: full output directory path (overrides results-dir/storage-driver/disks-per-vm)')
# SSH parameters
@click.option('--ssh-pod', default='ssh-test-pod', help='SSH pod name')
@click.option('--ssh-pod-ns', default='default', help='SSH pod namespace')
@click.option('--vm-user', default='root', help='VM SSH user')
@click.option('--vm-password', default='Password1', help='VM SSH password')
# Execution parameters
@click.option('--concurrency', '-c', type=int, default=20, help='Max concurrent operations')
@click.option('--log-level', default='INFO',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']))
@click.option('--log-file', type=click.Path(), help='Log file path')
@click.pass_context
def elbencho(ctx, **kwargs):
    """
    Manage elbencho workloads on VMs

    This command wraps the elbencho management script to deploy VMs, control
    elbencho IO workloads, and collect results across many namespaces.

    \b
    Actions:
      run-all          Full workflow: deploy, workload, wait, gather, cleanup
      deploy           Deploy VMs (requires --vm-template)
      start            Start the default 1 IOPS systemd service
      stop             Stop the systemd service
      restart          Restart the systemd service
      status           Check elbencho service status
      stop-all         Stop all elbencho processes (service + manual)
      change-workload  Stop current workload and start a new one
      gather-results   Stop IO and collect results from all VMs
      cleanup          Delete VMs and namespaces

    \b
    Examples:

    \b
      # Full workflow: deploy 10 VMs, run for 5 minutes, gather, save
      virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 -a run-all \\
          --vm-template examples/vm-templates/YOUR-ELBENCHO-VM.yaml \\
          --iops 1000 --block-size 4K --duration 300 --save-results

    \b
      # Deploy VMs only
      virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 -a deploy \\
          --vm-template examples/vm-templates/YOUR-ELBENCHO-VM.yaml

    \b
      # Change workload: 1000 total IOPS (500 read, 500 write)
      virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \\
          -a change-workload --iops 1000 --block-size 4K

    \b
      # Change workload: 70% read at max throughput
      virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \\
          -a change-workload --rwmixpct 70 --block-size 32K

    \b
      # Gather results and save with storage driver label
      virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \\
          -a gather-results --save-results --storage-driver portworx-3.6

    \b
      # Stop all elbencho processes
      virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 -a stop-all

    \b
      # Cleanup VMs and namespaces
      virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 -a cleanup
    """
    print_banner("Elbencho Benchmark")

    repo_root = ctx.obj.repo_root
    script_path = repo_root / 'io-benchmark' / 'elbencho' / 'measure-elbencho-performance.py'

    if not script_path.exists():
        console.print(f"[red]Error:[/red] Script not found: {script_path}")
        sys.exit(1)

    action = kwargs['action']

    # Validate deploy/run-all requirements
    if action in ('deploy', 'run-all') and not kwargs['vm_template']:
        console.print(f"[red]Error:[/red] --vm-template is required for action '{action}'")
        sys.exit(1)
    if action == 'run-all' and kwargs['duration'] == 0:
        console.print("[red]Error:[/red] --duration is required for run-all (cannot be infinite)")
        sys.exit(1)

    # Resolve VM template path relative to repo root (if provided)
    vm_template_path = None
    if kwargs['vm_template']:
        vm_template_path = Path(kwargs['vm_template'])
        if not vm_template_path.is_absolute():
            vm_template_path = repo_root / vm_template_path
        if action in ('deploy', 'run-all') and not vm_template_path.exists():
            console.print(f"[red]Error:[/red] VM template not found: {vm_template_path}")
            sys.exit(1)

    # Build command
    cmd = [sys.executable, str(script_path)]

    # Required arguments
    cmd.extend(['--namespace-prefix', kwargs['namespace_prefix']])
    cmd.extend(['--start', str(kwargs['start'])])
    cmd.extend(['--end', str(kwargs['end'])])
    cmd.extend(['--vm-name', kwargs['vm_name']])
    cmd.extend(['--action', action])

    # Deploy parameters
    if vm_template_path is not None:
        cmd.extend(['--vm-template', str(vm_template_path)])
    if kwargs['secret_yaml']:
        cmd.extend(['--secret-yaml', kwargs['secret_yaml']])
    cmd.extend(['--ping-timeout', str(kwargs['ping_timeout'])])

    # Workload parameters
    if kwargs['iops'] > 0:
        cmd.extend(['--iops', str(kwargs['iops'])])
    if kwargs['rwmixpct'] > 0:
        cmd.extend(['--rwmixpct', str(kwargs['rwmixpct'])])
    cmd.extend(['--block-size', kwargs['block_size']])
    cmd.extend(['--num-disks', str(kwargs['num_disks'])])
    cmd.extend(['--iodepth', str(kwargs['iodepth'])])
    cmd.extend(['--threads', str(kwargs['threads'])])
    cmd.extend(['--duration', str(kwargs['duration'])])

    # Results parameters
    if kwargs['save_results']:
        cmd.append('--save-results')
    cmd.extend(['--results-dir', kwargs['results_dir']])
    cmd.extend(['--storage-driver', kwargs['storage_driver']])
    cmd.extend(['--disks-per-vm', kwargs['disks_per_vm']])
    if kwargs['run_name']:
        cmd.extend(['--run-name', kwargs['run_name']])
    if kwargs['output_dir']:
        cmd.extend(['--output-dir', kwargs['output_dir']])

    # SSH parameters
    cmd.extend(['--ssh-pod', kwargs['ssh_pod']])
    cmd.extend(['--ssh-pod-ns', kwargs['ssh_pod_ns']])
    cmd.extend(['--vm-user', kwargs['vm_user']])
    cmd.extend(['--vm-password', kwargs['vm_password']])

    # Execution parameters
    cmd.extend(['--concurrency', str(kwargs['concurrency'])])
    cmd.extend(['--log-level', kwargs['log_level']])
    if kwargs['log_file']:
        cmd.extend(['--log-file', kwargs['log_file']])

    console.print(f"[cyan]Running:[/cyan] {' '.join(cmd[:3])}...")
    console.print(f"[dim]Full command: {' '.join(cmd)}[/dim]\n")

    try:
        result = subprocess.run(cmd, cwd=str(repo_root))
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
