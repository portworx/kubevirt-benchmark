#!/usr/bin/env python3
"""
VM operations command group.

Wraps the standalone scripts under ``vm-ops/`` so they can be invoked through
the unified ``virtbench`` CLI:

    virtbench vm-ops <operation> [options...]

Operations:
  drain-nodes        Drain Kubernetes nodes and measure drain time
  rebalance-vms      Rebalance VMs evenly across nodes
  vm-snapshot        Create VirtualMachineSnapshots in batches
  run-blkdiscard     Run blkdiscard on data disks inside VMs
  power-toggle-vms   Power VMs on or off (--action {on,off})
"""
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from virtbench.common import build_python_command, generate_log_filename, print_banner

console = Console()

# Map subcommand -> script filename in <repo>/vm-ops/
_SCRIPTS = {
    'drain-nodes': 'drain-nodes.py',
    'rebalance-vms': 'rebalance-vms.py',
    'vm-snapshot': 'snapshot-vms.py',
    'run-blkdiscard': 'run-blkdiscard.py',
    'power-toggle-vms': 'power-toggle-vms.py',
}


def _run_script(ctx, op_name: str, python_args: dict) -> None:
    """Resolve the script under vm-ops/, build the command, and exec it."""
    repo_root: Path = ctx.obj.repo_root
    script_path = repo_root / 'vm-ops' / _SCRIPTS[op_name]
    if not script_path.exists():
        console.print(f"[red]Error: Script not found: {script_path}[/red]")
        sys.exit(1)

    # Honour the global --log-file when the subcommand didn't set one and the
    # underlying script accepts --log-file (rebalance-vms and power-toggle-vms
    # don't expose one).
    _supports_log_file = {'drain-nodes', 'vm-snapshot', 'run-blkdiscard'}
    if op_name in _supports_log_file and 'log-file' not in python_args:
        if ctx.obj.log_file:
            python_args['log-file'] = ctx.obj.log_file
        else:
            python_args['log-file'] = generate_log_filename(op_name)

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
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@click.group('vm-ops', context_settings={'help_option_names': ['-h', '--help']})
def vm_ops():
    """
    VM operations toolkit.

    \b
    Available operations:
      drain-nodes        Drain Kubernetes nodes and measure drain time
      rebalance-vms      Rebalance VMs evenly across nodes
      vm-snapshot        Create VirtualMachineSnapshots in batches
      run-blkdiscard     Run blkdiscard on data disks inside VMs
      power-toggle-vms   Power VMs on or off (--action {on,off})

    \b
    Examples:
      virtbench vm-ops drain-nodes --nodes worker-1 worker-2 --parallel
      virtbench vm-ops rebalance-vms --vm-name rhel-elbencho-1 --dry-run
      virtbench vm-ops vm-snapshot --namespace-prefix migration --start 1 --end 50 --vm-name rhel-9-vm
      virtbench vm-ops run-blkdiscard --namespace-prefix rhel-eb-filler --start 1 --end 10 --vm-name rhel-elbencho-1
      virtbench vm-ops power-toggle-vms --action off --node worker-1 --percentage 50
      virtbench vm-ops power-toggle-vms --action on --namespace-prefix migration --start 1 --end 50 --vm-name rhel-9-vm
    """


# ----------------------------------------------------------------------------
# Subcommands. Each one mirrors the underlying script's argparse signature
# and forwards everything via build_python_command(). Defaults are intentionally
# left to the underlying script so we don't drift between two sources of truth.
# ----------------------------------------------------------------------------



@vm_ops.command('drain-nodes', context_settings={'help_option_names': ['-h', '--help']})
@click.option('--nodes', required=True, multiple=True,
              help='Node names to drain (repeat the flag or pass space-separated)')
@click.option('--parallel', is_flag=True, help='Drain nodes in parallel (default: sequential)')
@click.option('--timeout', type=int, default=None, help='Drain timeout in seconds (default: 300)')
@click.option('--grace-period', type=int, default=None, help='Pod termination grace period (default: 30)')
@click.option('--ignore-daemonsets', is_flag=True, help='Ignore DaemonSet pods')
@click.option('--delete-emptydir', is_flag=True, help='Delete pods with emptyDir')
@click.option('--force', is_flag=True, help="Force drain even if pods don't have controllers")
@click.option('--uncordon-after', is_flag=True, help='Uncordon node after drain completes')
@click.option('--wait-between', type=int, default=None,
              help='Seconds to wait between nodes (simulates reboot, default: 0)')
@click.option('--dry-run', is_flag=True, help='Show what would be done without draining')
@click.option('--log-file', type=click.Path(), default=None, help='Path to log file')
@click.pass_context
def drain_nodes(ctx, **kwargs):
    """Drain Kubernetes nodes and measure drain time."""
    print_banner("VM-Ops: Drain Nodes")
    args = {'nodes': list(kwargs['nodes']), 'log-level': ctx.obj.log_level.upper()}
    for k in ('timeout', 'grace_period', 'wait_between'):
        if kwargs[k] is not None:
            args[k.replace('_', '-')] = kwargs[k]
    for flag in ('parallel', 'ignore_daemonsets', 'delete_emptydir',
                 'force', 'uncordon_after', 'dry_run'):
        if kwargs[flag]:
            args[flag.replace('_', '-')] = True
    if kwargs['log_file']:
        args['log-file'] = kwargs['log_file']
    _run_script(ctx, 'drain-nodes', args)


@vm_ops.command('rebalance-vms', context_settings={'help_option_names': ['-h', '--help']})
@click.option('--vm-name', default=None, help='VM name to rebalance (default: rhel-elbencho-1)')
@click.option('--target-min', type=int, default=None, help='Minimum VMs per target node (default: auto)')
@click.option('--target-max', type=int, default=None, help='Maximum VMs per target node (default: auto)')
@click.option('--include-master-nodes', is_flag=True,
              help='Include master/control-plane nodes as rebalance targets')
@click.option('--dry-run', is_flag=True, help='Print commands without executing them')
@click.pass_context
def rebalance_vms(ctx, **kwargs):
    """Rebalance KubeVirt VMs evenly across nodes."""
    print_banner("VM-Ops: Rebalance VMs")
    args = {}
    for k in ('vm_name', 'target_min', 'target_max'):
        if kwargs[k] is not None:
            args[k.replace('_', '-')] = kwargs[k]
    if kwargs['include_master_nodes']:
        args['include-master-nodes'] = True
    if kwargs['dry_run']:
        args['dry-run'] = True
    _run_script(ctx, 'rebalance-vms', args)


@vm_ops.command('vm-snapshot', context_settings={'help_option_names': ['-h', '--help']})
@click.option('--namespace-prefix', required=True, help='Namespace prefix (e.g., perf-test)')
@click.option('--start', type=int, required=True, help='Start namespace index')
@click.option('--end', type=int, required=True, help='End namespace index')
@click.option('--vm-name', required=True, help='VM name in each namespace')
@click.option('--batch-size', type=int, default=None, help='VMs per batch (default: 50)')
@click.option('--interval', type=int, default=None,
              help='Seconds between batches (default: 900)')
@click.option('--concurrency', type=int, default=None,
              help='Max concurrent operations within a batch (default: 50)')
@click.option('--snapshot-prefix', default=None, help='Snapshot name prefix (default: vm-snap)')
@click.option('--dry-run', is_flag=True, help='Show what would be done without doing it')
@click.option('--log-file', type=click.Path(), default=None, help='Path to log file')
@click.pass_context
def vm_snapshot(ctx, **kwargs):
    """Create VirtualMachineSnapshots in batches."""
    print_banner("VM-Ops: VM Snapshot")
    args = {
        'namespace-prefix': kwargs['namespace_prefix'],
        'start': kwargs['start'],
        'end': kwargs['end'],
        'vm-name': kwargs['vm_name'],
        'log-level': ctx.obj.log_level.upper(),
    }
    for k in ('batch_size', 'interval', 'concurrency', 'snapshot_prefix'):
        if kwargs[k] is not None:
            args[k.replace('_', '-')] = kwargs[k]
    if kwargs['dry_run']:
        args['dry-run'] = True
    if kwargs['log_file']:
        args['log-file'] = kwargs['log_file']
    _run_script(ctx, 'vm-snapshot', args)


@vm_ops.command('run-blkdiscard', context_settings={'help_option_names': ['-h', '--help']})
@click.option('--namespace-prefix', required=True, help='Namespace prefix (e.g., rhel-eb-filler)')
@click.option('--start', type=int, required=True, help='Start namespace index')
@click.option('--end', type=int, required=True, help='End namespace index')
@click.option('--vm-name', required=True, help='VM name in each namespace')
@click.option('--disks', multiple=True,
              help='Specific disks (e.g., vdb vdc). Auto-detected if omitted.')
@click.option('--ssh-pod', default=None, help='SSH pod name (default: ssh-test-pod)')
@click.option('--ssh-pod-ns', default=None, help='SSH pod namespace (default: default)')
@click.option('--vm-user', default=None, help='VM SSH user (default: root)')
@click.option('--vm-password', default=None, help='VM SSH password (default: Password1)')
@click.option('--concurrency', type=int, default=None, help='Max concurrent operations (default: 10)')
@click.option('--dry-run', is_flag=True, help='Show what would be done without doing it')
@click.option('--log-file', type=click.Path(), default=None, help='Path to log file')
@click.pass_context
def run_blkdiscard(ctx, **kwargs):
    """Run blkdiscard on data disks inside VMs."""
    print_banner("VM-Ops: Run blkdiscard")
    args = {
        'namespace-prefix': kwargs['namespace_prefix'],
        'start': kwargs['start'],
        'end': kwargs['end'],
        'vm-name': kwargs['vm_name'],
        'log-level': ctx.obj.log_level.upper(),
    }
    if kwargs['disks']:
        args['disks'] = list(kwargs['disks'])
    for k in ('ssh_pod', 'ssh_pod_ns', 'vm_user', 'vm_password', 'concurrency'):
        if kwargs[k] is not None:
            args[k.replace('_', '-')] = kwargs[k]
    if kwargs['dry_run']:
        args['dry-run'] = True
    if kwargs['log_file']:
        args['log-file'] = kwargs['log_file']
    _run_script(ctx, 'run-blkdiscard', args)


@vm_ops.command('power-toggle-vms', context_settings={'help_option_names': ['-h', '--help']})
@click.option('--action', required=True, type=click.Choice(['on', 'off']),
              help='Whether to power VMs on or off')
@click.option('--node', default=None,
              help='Node name. Required for --action off (unless --vm-list-file). '
                   'Optional filter for --action on.')
@click.option('--percentage', type=int, default=None,
              help='Percentage of running VMs to power off (default: 50, --action off only)')
@click.option('--namespace-prefix', default=None,
              help='Namespace prefix for range-based --action on')
@click.option('--start', type=int, default=None, help='Start index for namespace range')
@click.option('--end', type=int, default=None, help='End index for namespace range')
@click.option('--vm-name', default=None, help='VM resource name in each namespace (--action on)')
@click.option('--vm-list-file', type=click.Path(exists=True), default=None,
              help="File of 'namespace/name' lines to act on")
@click.option('--concurrency', type=int, default=None, help='Max concurrent operations (default: 50)')
@click.option('--wait-timeout', type=int, default=None,
              help='Timeout waiting for target phase (default: 300s)')
@click.option('--dry-run', is_flag=True, help='Show what would be done without doing it')
@click.pass_context
def power_toggle_vms(ctx, **kwargs):
    """Power VMs on or off (--action {on,off})."""
    print_banner(f"VM-Ops: Power {kwargs['action'].upper()} VMs")
    args = {'action': kwargs['action'], 'log-level': ctx.obj.log_level.upper()}
    for k in ('node', 'percentage', 'namespace_prefix', 'start', 'end',
             'vm_name', 'vm_list_file', 'concurrency', 'wait_timeout'):
        if kwargs[k] is not None:
            args[k.replace('_', '-')] = kwargs[k]
    if kwargs['dry_run']:
        args['dry-run'] = True
    _run_script(ctx, 'power-toggle-vms', args)
