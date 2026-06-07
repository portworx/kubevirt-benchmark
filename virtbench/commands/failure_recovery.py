#!/usr/bin/env python3
"""
Failure Recovery benchmark command
"""
import click
import subprocess
import sys
from pathlib import Path
from rich.console import Console

from virtbench.utils.yaml_modifier import modify_storage_class
from virtbench.common import print_banner, build_python_command, generate_log_filename

console = Console()


@click.command('failure-recovery')
@click.option('--node', required=True, help='Node name to auto-detect VMs from')
@click.option('--vm-name', '-n', default='rhel-9-vm', help='VM resource name')
@click.option('--vm-template',
              default='examples/vm-templates/rhel9-vm-datasource.yaml',
              help='Path to VM template YAML')
@click.option('--storage-class', help='Storage class name (overrides template value)')
@click.option('--namespace-prefix', default='failure-recovery', help='Namespace prefix')
@click.option('--concurrency', '-c', default=10, type=int, help='Max parallel threads')
@click.option('--poll-interval', default=5, type=int, help='Seconds between status checks')
@click.option('--recovery-timeout', default=600, type=int, help='Timeout for recovery in seconds')
@click.option('--cleanup/--no-cleanup', default=False, help='Delete test resources after completion')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation prompts')
@click.option('--save-results', is_flag=True, help='Save detailed results to results folder')
@click.option('--results-folder', default='../results', help='Base directory to store test results')
@click.option('--storage-driver', help='Storage driver label for results path (for example: portworx-3.6, ceph)')
@click.option('--log-file', type=click.Path(), help='Log file path (auto-generated if not specified)')
@click.pass_context
def failure_recovery(ctx, **kwargs):
    """
    Run failure recovery benchmark

    This workload tests VM recovery time after simulated node failures.
    VMs are auto-detected on the specified node.

    \b
    Examples:
      # Auto-detect VMs on a node and monitor recovery
      virtbench failure-recovery --node worker-1 --vm-name rhel-9-vm

      # Run with cleanup
      virtbench failure-recovery --node worker-1 --cleanup
    """
    print_banner("Failure Recovery Benchmark")
    
    # Get repo root from context
    repo_root = ctx.obj.repo_root
    
    # Resolve template path
    template_path = Path(kwargs['vm_template'])
    if not template_path.is_absolute():
        template_path = repo_root / template_path
    
    if not template_path.exists():
        console.print(f"[red]Error: Template file not found: {template_path}[/red]")
        sys.exit(1)
    
    # Handle storage class modification
    if kwargs['storage_class']:
        console.print(f"[cyan]Using storage class: {kwargs['storage_class']}[/cyan]")
        try:
            modify_storage_class(template_path, kwargs['storage_class'])
        except Exception as e:
            console.print(f"[red]Error modifying storage class: {e}[/red]")
            sys.exit(1)
    
    # Build Python script command
    script_path = repo_root / 'failure-recovery' / 'recovery-test.py'

    if not script_path.exists():
        console.print(f"[red]Error: Script not found: {script_path}[/red]")
        sys.exit(1)

    # Map CLI args to Python script args
    python_args = {
        'mode': 'monitor',
        'node': kwargs['node'],
        'vm-name': kwargs['vm_name'],
        'vm-template': str(template_path),
        'namespace-prefix': kwargs['namespace_prefix'],
        'concurrency': kwargs['concurrency'],
        'poll-interval': kwargs['poll_interval'],
        'recovery-timeout': kwargs['recovery_timeout'],
        'results-folder': kwargs['results_folder'],
        'log-level': ctx.obj.log_level.upper(),
    }

    # Add boolean flags
    if kwargs['cleanup']:
        python_args['cleanup'] = True
    if kwargs['yes']:
        python_args['yes'] = True
    if kwargs['save_results']:
        python_args['save-results'] = True

    if kwargs.get('storage_driver'):
        python_args['storage-driver'] = kwargs['storage_driver']
    
    # Add log-file (prefer subcommand option, then global context, then auto-generate)
    if kwargs.get('log_file'):
        python_args['log-file'] = kwargs['log_file']
    elif ctx.obj.log_file:
        python_args['log-file'] = ctx.obj.log_file
    else:
        python_args['log-file'] = generate_log_filename('failure-recovery')
    
    # Build and run command
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
