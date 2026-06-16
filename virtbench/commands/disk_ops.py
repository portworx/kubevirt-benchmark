#!/usr/bin/env python3
"""
Disk Operations Benchmark command - Hotplug/Coldplug disk performance testing
"""
import click
import subprocess
import sys
from pathlib import Path
from rich.console import Console

from virtbench.common import print_banner, build_python_command, generate_log_filename

console = Console()


@click.command('disk-ops')
@click.option('--start', '-s', required=True, type=int, help='Start namespace index')
@click.option('--end', '-e', required=True, type=int, help='End namespace index')
@click.option('--storage-class', required=True, help='Storage class for PVCs')
@click.option('--operation', default='all', type=click.Choice(['hotplug', 'coldplug', 'all']),
              help='Operation type (default: all)')
@click.option('--disks', default=1, type=int, help='Number of disks per VM')
@click.option('--disk-size', default='10Gi', help='Disk size')
@click.option('--parallel-attach', is_flag=True,
              help='Attach all disks in parallel (default: sequential)')
@click.option('--namespace-prefix', default='disk-ops', help='Namespace prefix')
@click.option('--vm-name', default='disk-ops-vm', help='VM name')
@click.option('--vm-template', default='disk-ops-benchmark/vm-template.yaml',
              help='Path to VM template YAML (used with --create-vms)')
@click.option('--vm-user', default='cloud-user', help='VM SSH user for in-VM validation')
@click.option('--vm-password', default='changeme', help='VM SSH password for in-VM validation')
@click.option('--ssh-pod', default='ssh-test-pod',
              help='Persistent SSH helper pod with sshpass (auto-created if missing)')
@click.option('--ssh-pod-ns', default='default', help='SSH helper pod namespace')
@click.option('--create-vms', is_flag=True, help='Create VMs before testing')
@click.option('--concurrency', '-c', default=10, type=int, help='Max concurrent operations')
@click.option('--attach-timeout', default=300, type=int, help='Disk attach timeout (seconds)')
@click.option('--vm-timeout', default=600, type=int,
              help='Timeout for created VMs to reach Running, incl. image import (seconds)')
@click.option('--test-unplug', is_flag=True, help='Also test disk removal')
@click.option('--skip-validation', is_flag=True, help='Skip in-VM validation')
@click.option('--results-dir', default='results', help='Base directory for results')
@click.option('--save-results', is_flag=True, help='Save results to JSON/CSV files')
@click.option('--px-version', default='px-unknown', help='Storage driver/version label for results folder')
@click.option('--disk-type', default=None, help='Disk type label for results folder (default: <disks>-disk)')
@click.option('--cleanup', is_flag=True, help='Remove hotplugged disks (and created VMs) after test')
@click.option('--log-file', type=click.Path(), help='Log file path (auto-generated if not specified)')
@click.option('--log-level', default='INFO',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']),
              help='Logging level')
@click.pass_context
def disk_ops(ctx, **kwargs):
    """
    Run disk operations benchmark (hotplug/coldplug)

    Measures the time to attach disks to running VMs (hotplug) or stopped VMs
    (coldplug), with optional in-VM validation to verify disks appear correctly.

    \b
    Operations:
      hotplug  - Attach disk to running VM
      coldplug - Attach disk to stopped VM, then start
      all      - Run both hotplug and coldplug tests (default)

    \b
    Validation:
      In-VM validation runs lsblk over SSH through a persistent helper pod
      (--ssh-pod, auto-created with sshpass if missing) using password auth
      (--vm-user/--vm-password). With --create-vms the VMs are provisioned with
      that password baked in. Use --skip-validation to skip the SSH checks.

    \b
    Examples:
      # Create VMs and hotplug 3 disks to each
      virtbench disk-ops --start 1 --end 10 --storage-class px-csi --disks 3 \\
          --create-vms --save-results
    \b
      # Coldplug against existing VMs (must already trust --vm-password)
      virtbench disk-ops --start 1 --end 5 --storage-class px-csi \\
          --operation coldplug --vm-password mypass --save-results
    \b
      # All operations with unplug test and cleanup
      virtbench disk-ops --start 1 --end 10 --storage-class px-csi \\
          --operation all --create-vms --test-unplug --cleanup
    """
    print_banner("Disk Operations Benchmark")

    repo_root = ctx.obj.repo_root
    script_path = repo_root / 'disk-ops-benchmark' / 'measure-disk-ops.py'

    if not script_path.exists():
        console.print(f"[red]Error:[/red] Script not found: {script_path}")
        sys.exit(1)

    # Resolve VM template path relative to repo root (only needed for --create-vms)
    vm_template_path = Path(kwargs['vm_template']).expanduser()
    if not vm_template_path.is_absolute():
        vm_template_path = repo_root / vm_template_path

    if kwargs['create_vms'] and not vm_template_path.exists():
        console.print(f"[red]Error:[/red] VM template not found: {vm_template_path}")
        sys.exit(1)

    console.print(f"[cyan]Operation:[/cyan] {kwargs['operation']}  "
                  f"[cyan]Storage class:[/cyan] {kwargs['storage_class']}  "
                  f"[cyan]Disks/VM:[/cyan] {kwargs['disks']}")

    # Map CLI args to the benchmark script's arguments
    python_args = {
        'start': kwargs['start'],
        'end': kwargs['end'],
        'storage-class': kwargs['storage_class'],
        'operation': kwargs['operation'],
        'disks': kwargs['disks'],
        'disk-size': kwargs['disk_size'],
        'namespace-prefix': kwargs['namespace_prefix'],
        'vm-name': kwargs['vm_name'],
        'vm-template': str(vm_template_path),
        'vm-user': kwargs['vm_user'],
        'vm-password': kwargs['vm_password'],
        'ssh-pod': kwargs['ssh_pod'],
        'ssh-pod-ns': kwargs['ssh_pod_ns'],
        'concurrency': kwargs['concurrency'],
        'attach-timeout': kwargs['attach_timeout'],
        'vm-timeout': kwargs['vm_timeout'],
        'results-dir': kwargs['results_dir'],
        'px-version': kwargs['px_version'],
        'disk-type': kwargs['disk_type'],
        'log-level': kwargs['log_level'],
    }

    # Log file: explicit > global context > auto-generated timestamped file
    if kwargs.get('log_file'):
        python_args['log-file'] = kwargs['log_file']
    elif ctx.obj.log_file:
        python_args['log-file'] = ctx.obj.log_file
    else:
        python_args['log-file'] = generate_log_filename('disk-ops-benchmark')

    # Flags
    if kwargs['parallel_attach']:
        python_args['parallel-attach'] = True
    if kwargs['create_vms']:
        python_args['create-vms'] = True
    if kwargs['test_unplug']:
        python_args['test-unplug'] = True
    if kwargs['skip_validation']:
        python_args['skip-validation'] = True
    if kwargs['save_results']:
        python_args['save-results'] = True
    if kwargs['cleanup']:
        python_args['cleanup'] = True

    cmd = build_python_command(script_path, python_args)

    console.print(f"[dim]Running: {' '.join(cmd[:2])} ...[/dim]")
    console.print()

    try:
        result = subprocess.run(cmd, cwd=repo_root)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
