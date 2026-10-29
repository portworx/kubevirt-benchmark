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
@click.option('--action', '-a', default='run-all',
              type=click.Choice(['deploy', 'status', 'gather-results', 'cleanup', 'run-all',
                                 'compare-profiles']),
              help='Action to perform')
@click.option('--start', '-s', type=int, help='Start index for test namespaces')
@click.option('--end', '-e', type=int, help='End index for test namespaces')
@click.option('--storage-class', help='Storage class name (required for deploy/run-all)')
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
@click.option('--fio-ramp-time', default=15, type=click.IntRange(min=0),
              help='Warm-up time excluded from each measured iteration')
@click.option('--fio-rwmixread', default=70, type=click.IntRange(0, 100),
              help='Read percentage for mixed workloads')
@click.option('--profiles',
              default='baseline,iothreads-shared,iothreads-auto,iothreads-supplemental,'
                      'dedicated-iothread,block-multiqueue,combined',
              help='Comma-separated storage tuning profiles')
@click.option('--profile-iterations', default=3, type=click.IntRange(min=1),
              help='Measured repetitions per profile')
@click.option('--vm-cpus', default=4, type=click.IntRange(min=1),
              help='Equal vCPU request and limit for all profiles')
@click.option('--node-name', help='Pin every profile to one Kubernetes node')
@click.option('--supplemental-pool-threads', default=4, type=click.IntRange(min=1),
              help='Threads in the supplemental IOThread pool')
@click.option('--benchmark-disk', default='scratchdisk', help='Disk to tune and benchmark')
@click.option('--workloads',
              help='Comma-separated I/O patterns to run against every profile '
                   '(randread-4k, randwrite-4k, randrw-4k, seqread-128k, seqwrite-128k)')
@click.option('--recommend', is_flag=True,
              help='Emit ranked per-workload tables and an overall recommendation')
@click.option('--keep-profile-resources', is_flag=True,
              help='Keep comparison namespace and VMs after the run')
@click.option('--results-dir', default='results', help='Base directory for results')
@click.option('--storage-driver', default='Not-Specified', help='Storage driver label for results folder')
@click.option('--disks-per-vm', default='auto', help='Disks per VM for results folder (auto-detect)')
@click.option('--save-results', is_flag=True, help='Save results to JSON/CSV files')
@click.option('--cleanup/--no-cleanup', default=False, help='Delete VMs after test (for run-all)')
@click.option('--collect-retries', default=8, type=int, help='Max retries for collecting results')
@click.option('--collect-retry-delay', default=20, type=int, help='Delay (seconds) between retries')
@click.option('--collect-concurrency', default=5, type=int, help='Max concurrent result collections')
@click.option('--ssh-pod', default='ssh-test-pod', help='SSH helper pod name (must have sshpass)')
@click.option('--ssh-pod-ns', default='default', help='SSH helper pod namespace')
@click.option('--vm-user', default='cloud-user', help='VM SSH user')
@click.option('--vm-password', default='changeme', help='VM SSH password')
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
    Actions:
      deploy         Deploy VMs with FIO pre-configured (FIO runs on boot)
      status         Check VM and FIO status
      gather-results Collect FIO results from VMs (requires SSH pod)
      cleanup        Delete VMs and namespaces
      run-all        Full workflow: deploy, wait, gather (default)
      compare-profiles Compare KubeVirt IOThreads and block multiqueue settings

    \b
    Notes:
      --save-results is a FLAG that saves results to JSON/CSV files
      --action gather-results is an ACTION that collects results from VMs
      Use both together: --action gather-results --save-results

    \b
    Examples:
      # Full workflow (deploy + wait + gather + save)
      virtbench fio --action run-all -s 1 -e 10 --storage-class px-csi --save-results

      # Step-by-step workflow
      virtbench fio --action deploy -s 1 -e 10 --storage-class px-csi
      virtbench fio --action status -s 1 -e 10
      virtbench fio --action gather-results -s 1 -e 10 --save-results
      virtbench fio --action cleanup -s 1 -e 10

      # Custom FIO parameters
      virtbench fio -a run-all -s 1 -e 50 --storage-class px-csi \\
          --fio-runtime 600 --fio-rw randrw --fio-bs 8k --save-results

      # Controlled KubeVirt storage tuning comparison
      virtbench fio -a compare-profiles --storage-class px-csi \\
          --node-name worker-0 --fio-rw randrw --profile-iterations 3

      # Ranked decision table across workload patterns
      virtbench fio -a compare-profiles --storage-class px-csi \\
          --profiles baseline,iothreads-auto,block-multiqueue,combined \\
          --workloads randread-4k,randwrite-4k,seqread-128k --vm-cpus 8 --recommend
    """
    print_banner("FIO Benchmark")

    # Validate storage-class for deploy/run-all
    if kwargs['action'] in ['deploy', 'run-all', 'compare-profiles'] and not kwargs['storage_class']:
        console.print(f"[red]Error:[/red] --storage-class is required for action '{kwargs['action']}'")
        sys.exit(1)
    if kwargs['action'] != 'compare-profiles' and (
        kwargs['start'] is None or kwargs['end'] is None
    ):
        console.print("[red]Error:[/red] --start and --end are required for this action")
        sys.exit(1)

    repo_root = ctx.obj.repo_root
    script_path = repo_root / 'io-benchmark' / 'fio' / 'measure-fio-performance.py'

    if not script_path.exists():
        console.print(f"[red]Error:[/red] Script not found: {script_path}")
        sys.exit(1)

    # Resolve VM template path relative to repo root
    vm_template = kwargs['vm_template']
    vm_template_path = Path(vm_template)
    if not vm_template_path.is_absolute():
        vm_template_path = repo_root / vm_template

    if kwargs['action'] in ['deploy', 'run-all', 'compare-profiles'] and not vm_template_path.exists():
        console.print(f"[red]Error:[/red] VM template not found: {vm_template_path}")
        sys.exit(1)

    # Build command
    cmd = [sys.executable, str(script_path)]

    # Add action
    cmd.extend(['--action', kwargs['action']])

    # Add arguments
    if kwargs['start'] is not None:
        cmd.extend(['--start', str(kwargs['start'])])
    if kwargs['end'] is not None:
        cmd.extend(['--end', str(kwargs['end'])])
    if kwargs['storage_class']:
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
    cmd.extend(['--fio-ramp-time', str(kwargs['fio_ramp_time'])])
    cmd.extend(['--fio-rwmixread', str(kwargs['fio_rwmixread'])])
    cmd.extend(['--profiles', kwargs['profiles']])
    cmd.extend(['--profile-iterations', str(kwargs['profile_iterations'])])
    cmd.extend(['--vm-cpus', str(kwargs['vm_cpus'])])
    cmd.extend(['--supplemental-pool-threads', str(kwargs['supplemental_pool_threads'])])
    cmd.extend(['--benchmark-disk', kwargs['benchmark_disk']])
    if kwargs['node_name']:
        cmd.extend(['--node-name', kwargs['node_name']])
    if kwargs['workloads']:
        cmd.extend(['--workloads', kwargs['workloads']])
    if kwargs['recommend']:
        cmd.append('--recommend')
    if kwargs['keep_profile_resources']:
        cmd.append('--keep-profile-resources')
    cmd.extend(['--results-dir', kwargs['results_dir']])
    cmd.extend(['--storage-driver', kwargs['storage_driver']])
    cmd.extend(['--disks-per-vm', kwargs['disks_per_vm']])
    cmd.extend(['--log-level', kwargs['log_level']])

    # Collection settings
    cmd.extend(['--collect-retries', str(kwargs['collect_retries'])])
    cmd.extend(['--collect-retry-delay', str(kwargs['collect_retry_delay'])])
    cmd.extend(['--collect-concurrency', str(kwargs['collect_concurrency'])])

    # SSH settings (password-based via existing pod)
    cmd.extend(['--ssh-pod', kwargs['ssh_pod']])
    cmd.extend(['--ssh-pod-ns', kwargs['ssh_pod_ns']])
    cmd.extend(['--vm-user', kwargs['vm_user']])
    cmd.extend(['--vm-password', kwargs['vm_password']])

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
