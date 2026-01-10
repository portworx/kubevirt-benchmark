#!/usr/bin/env python3
"""
FIO Benchmark command - Storage I/O performance testing
"""
import click
import subprocess
import sys
from pathlib import Path
from rich.console import Console

from virtbench.common import print_banner, generate_log_filename

console = Console()


@click.command('fio')
@click.option('--start', '-s', required=True, type=int, help='Start index for test namespaces')
@click.option('--end', '-e', required=True, type=int, help='End index for test namespaces')
@click.option('--storage-class', required=True, help='Storage class name')
@click.option('--vm-name', '-n', default='fio-vm', help='VM resource name')
@click.option('--vm-template', default='examples/vm-templates/fio-vm-template.yaml',
              help='Path to VM template YAML')
@click.option('--namespace-prefix', default='fio-benchmark', help='Namespace prefix')
@click.option('--concurrency', '-c', default=20, type=int, help='Max parallel threads')
@click.option('--fio-runtime', default=300, type=int, help='FIO runtime in seconds')
@click.option('--fio-bs', default='4k', help='Block size (e.g., 4k, 8k, 64k)')
@click.option('--fio-rw', default='randwrite', 
              type=click.Choice(['read', 'write', 'randread', 'randwrite', 'randrw', 'rw']),
              help='I/O pattern')
@click.option('--fio-iodepth', default=64, type=int, help='I/O depth')
@click.option('--fio-numjobs', default=4, type=int, help='Number of parallel jobs')
@click.option('--fio-size', default='10G', help='Test file size')
@click.option('--results-dir', default='results', help='Base directory for results')
@click.option('--save-results', is_flag=True, help='Save results to JSON/CSV')
@click.option('--cleanup/--no-cleanup', default=False, help='Delete VMs after test')
@click.option('--log-file', type=click.Path(), help='Log file path')
@click.option('--log-level', default='INFO',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']))
@click.pass_context
def fio(ctx, **kwargs):
    """
    Run FIO benchmark across multiple VMs

    This workload tests storage I/O performance by running FIO benchmarks
    on multiple VMs in parallel. Results include IOPS, bandwidth, and latency.

    \b
    Examples:
      # Run FIO on 10 VMs
      virtbench fio --start 1 --end 10 --storage-class px-csi

      # Custom FIO parameters
      virtbench fio --start 1 --end 50 --storage-class px-csi \\
          --fio-runtime 600 --fio-rw randrw --fio-bs 8k

      # Save results and cleanup
      virtbench fio --start 1 --end 20 --storage-class px-csi \\
          --save-results --cleanup
    """
    print_banner("FIO Benchmark")

    repo_root = ctx.obj.repo_root
    script_path = repo_root / 'fio-benchmark' / 'measure-fio-performance.py'

    if not script_path.exists():
        console.print(f"[red]Error:[/red] Script not found: {script_path}")
        sys.exit(1)

    # Resolve VM template path relative to repo root
    vm_template = kwargs['vm_template']
    vm_template_path = Path(vm_template)
    if not vm_template_path.is_absolute():
        vm_template_path = repo_root / vm_template

    if not vm_template_path.exists():
        console.print(f"[red]Error:[/red] VM template not found: {vm_template_path}")
        sys.exit(1)

    # Build command
    cmd = [sys.executable, str(script_path)]

    # Add arguments
    cmd.extend(['--start', str(kwargs['start'])])
    cmd.extend(['--end', str(kwargs['end'])])
    cmd.extend(['--storage-class', kwargs['storage_class']])
    cmd.extend(['--vm-name', kwargs['vm_name']])
    cmd.extend(['--vm-template', str(vm_template_path)])
    cmd.extend(['--namespace-prefix', kwargs['namespace_prefix']])
    cmd.extend(['--concurrency', str(kwargs['concurrency'])])
    cmd.extend(['--fio-runtime', str(kwargs['fio_runtime'])])
    cmd.extend(['--fio-bs', kwargs['fio_bs']])
    cmd.extend(['--fio-rw', kwargs['fio_rw']])
    cmd.extend(['--fio-iodepth', str(kwargs['fio_iodepth'])])
    cmd.extend(['--fio-numjobs', str(kwargs['fio_numjobs'])])
    cmd.extend(['--fio-size', kwargs['fio_size']])
    cmd.extend(['--results-dir', kwargs['results_dir']])
    cmd.extend(['--log-level', kwargs['log_level']])
    
    if kwargs['save_results']:
        cmd.append('--save-results')
    if kwargs['cleanup']:
        cmd.append('--cleanup')
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

