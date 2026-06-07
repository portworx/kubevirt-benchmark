#!/usr/bin/env python3
"""
Chaos benchmark command
"""
import click
import subprocess
import sys
from pathlib import Path
from rich.console import Console

from virtbench.common import print_banner, build_python_command, generate_log_filename

console = Console()


@click.command('chaos-benchmark')
@click.option('--storage-class', required=False, help='Storage class name (required unless --cleanup-only)')
@click.option('--concurrency', '-c', required=True, type=int, help='Number of concurrent operations (REQUIRED)')
@click.option('--namespace', '-n', default='virt-chaos-benchmark', help='Namespace for test resources')
@click.option('--vms', default=5, type=int, help='Number of VMs to create per iteration')
@click.option('--max-iterations', default=0, type=int, help='Maximum number of iterations (0 for unlimited)')
@click.option('--data-volume-count', default=1, type=int, help='Number of data volumes per VM (default: 1)')
@click.option('--min-vol-size', default='30Gi', help='Minimum volume size (e.g., 30Gi, 100Mi)')
@click.option('--min-vol-inc-size', default='10Gi', help='Volume size increment for resize (e.g., 10Gi, 50Mi)')
@click.option('--vm-yaml', default='examples/vm-templates/vm-template.yaml', help='Path to VM YAML template')
@click.option('--vm-name', default='rhel-9-vm', help='Base VM name')
@click.option('--datasource-name', default='rhel9', help='DataSource name')
@click.option('--datasource-namespace', default='openshift-virtualization-os-images', help='DataSource namespace')
@click.option('--vm-memory', default='2048M', help='VM memory')
@click.option('--vm-cpu-cores', default=1, type=int, help='VM CPU cores')
@click.option('--skip-resize', is_flag=True, help='Skip volume resize phase')
@click.option('--skip-clone', is_flag=True, help='Skip volume clone phase')
@click.option('--skip-snapshot', is_flag=True, help='Skip VM snapshot phase')
@click.option('--skip-restart', is_flag=True, help='Skip VM restart phase')
@click.option('--scheduling-timeout', default=120, type=int,
              help='Seconds to wait in Scheduling/Provisioning state before failing (default: 120)')
@click.option('--vm-timeout', default=1800, type=int, help='Total timeout for VM to reach Running state (default: 1800)')
@click.option('--max-create-retries', default=5, type=int, help='Maximum retries for VM creation (default: 5)')
@click.option('--cleanup/--no-cleanup', default=False, help='Delete test resources after completion')
@click.option('--cleanup-only', is_flag=True, help='Only cleanup resources from previous runs')
@click.option('--save-results', is_flag=True, help='Save results to JSON/CSV files in results directory')
@click.option('--results-dir', default='results', help='Directory to save results (default: results)')
@click.option('--storage-driver', default=None,
              help='Storage driver label for results path (for example: portworx-3.6, ceph)')
@click.option('--log-file', type=click.Path(), help='Log file path (auto-generated if not specified)')
@click.option('--log-level', default='INFO', type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']),
              help='Logging level')
@click.pass_context
def chaos_benchmark(ctx, **kwargs):
    """
    Run chaos benchmark

    This workload tests cluster resilience by running concurrent chaos operations
    including VM creation, volume resize, volume clone, VM restart, and snapshots.

    \b
    Examples:
      # Run chaos test with 5 VMs per iteration
      virtbench chaos-benchmark --storage-class YOUR-STORAGE-CLASS --concurrency 2 --vms 5

      # Run with custom max iterations
      virtbench chaos-benchmark --storage-class YOUR-STORAGE-CLASS --concurrency 5 --max-iterations 20

      # Skip specific phases
      virtbench chaos-benchmark --storage-class YOUR-STORAGE-CLASS --concurrency 2 --skip-clone

      # Cleanup only mode
      virtbench chaos-benchmark --cleanup-only --concurrency 1
    """
    print_banner("Chaos Benchmark")

    repo_root = ctx.obj.repo_root

    # Validate: storage-class is required unless cleanup-only
    if not kwargs['cleanup_only'] and not kwargs.get('storage_class'):
        console.print("[red]Error: --storage-class is required unless using --cleanup-only[/red]")
        sys.exit(1)

    script_path = repo_root / 'chaos-benchmark' / 'measure-chaos.py'

    if not script_path.exists():
        console.print(f"[red]Error: Script not found: {script_path}[/red]")
        sys.exit(1)

    # Handle cleanup-only mode
    if kwargs['cleanup_only']:
        python_args = {
            'namespace': kwargs['namespace'],
            'concurrency': kwargs['concurrency'],
            'log-level': kwargs['log_level'],
            'cleanup-only': True,
        }
        if kwargs.get('log_file'):
            python_args['log-file'] = kwargs['log_file']
        elif ctx.obj.log_file:
            python_args['log-file'] = ctx.obj.log_file
        else:
            python_args['log-file'] = generate_log_filename('chaos-benchmark')

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

    # Resolve template path
    template_path = Path(kwargs['vm_yaml'])
    if not template_path.is_absolute():
        template_path = repo_root / template_path

    if not template_path.exists():
        console.print(f"[red]Error: Template file not found: {template_path}[/red]")
        sys.exit(1)

    console.print(f"[cyan]Using storage class: {kwargs['storage_class']}[/cyan]")
    console.print(f"[cyan]Concurrency: {kwargs['concurrency']}[/cyan]")

    # Map CLI args to Python script args
    python_args = {
        'storage-class': kwargs['storage_class'],
        'concurrency': kwargs['concurrency'],
        'namespace': kwargs['namespace'],
        'vms': kwargs['vms'],
        'max-iterations': kwargs['max_iterations'],
        'data-volume-count': kwargs['data_volume_count'],
        'min-vol-size': kwargs['min_vol_size'],
        'min-vol-inc-size': kwargs['min_vol_inc_size'],
        'vm-yaml': str(template_path),
        'vm-name': kwargs['vm_name'],
        'datasource-name': kwargs['datasource_name'],
        'datasource-namespace': kwargs['datasource_namespace'],
        'vm-memory': kwargs['vm_memory'],
        'vm-cpu-cores': kwargs['vm_cpu_cores'],
        'scheduling-timeout': kwargs['scheduling_timeout'],
        'vm-timeout': kwargs['vm_timeout'],
        'max-create-retries': kwargs['max_create_retries'],
        'log-level': kwargs['log_level'],
    }

    # Add skip flags
    if kwargs['skip_resize']:
        python_args['skip-resize'] = True
    if kwargs['skip_clone']:
        python_args['skip-clone'] = True
    if kwargs['skip_snapshot']:
        python_args['skip-snapshot'] = True
    if kwargs['skip_restart']:
        python_args['skip-restart'] = True

    # Add cleanup flag
    if kwargs['cleanup']:
        python_args['cleanup'] = True

    # Add save-results flags
    if kwargs['save_results']:
        python_args['save-results'] = True
        python_args['results-dir'] = kwargs['results_dir']
        if kwargs.get('storage_driver'):
            python_args['storage-driver'] = kwargs['storage_driver']

    # Add log-file
    if kwargs.get('log_file'):
        python_args['log-file'] = kwargs['log_file']
    elif ctx.obj.log_file:
        python_args['log-file'] = ctx.obj.log_file
    else:
        python_args['log-file'] = generate_log_filename('chaos-benchmark')

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
