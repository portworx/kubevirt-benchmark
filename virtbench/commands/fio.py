"""
FIO Benchmark command - Storage I/O performance testing
"""

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from virtbench.common import print_banner


console = Console()


@click.command("fio")
@click.option(
    "--action",
    "-a",
    default="run-all",
    type=click.Choice(["deploy", "status", "gather-results", "cleanup", "run-all"]),
    help="Action to perform",
)
@click.option("--start", "-s", required=True, type=int, help="Start index for test namespaces")
@click.option("--end", "-e", required=True, type=int, help="End index for test namespaces")
@click.option("--storage-class", help="Storage class name (required for deploy/run-all)")
@click.option("--vm-name", "-n", default="fio-vm", help="VM resource name")
@click.option("--vm-template", default="examples/vm-templates/fio-vm-template.yaml", help="Path to VM template YAML")
@click.option("--namespace-prefix", default="fio-benchmark", help="Namespace prefix")
@click.option("--concurrency", "-c", default=20, type=int, help="Max parallel threads")
@click.option("--fio-runtime", default=300, type=int, help="FIO runtime in seconds")
@click.option("--fio-bs", default="4k", help="Block size (e.g., 4k, 8k, 64k)")
@click.option(
    "--fio-rw",
    default="randwrite",
    type=click.Choice(["read", "write", "randread", "randwrite", "randrw", "rw"]),
    help="I/O pattern",
)
@click.option("--fio-iodepth", default=64, type=int, help="I/O depth")
@click.option("--fio-numjobs", default=4, type=int, help="Number of parallel jobs")
@click.option("--fio-size", default="10G", help="Test file size")
@click.option("--results-dir", default="results", help="Base directory for results")
@click.option("--storage-driver", default="Not-Specified", help="Storage driver label for results folder")
@click.option("--disks-per-vm", default="auto", help="Disks per VM for results folder (auto-detect)")
@click.option("--save-results", is_flag=True, help="Save results to JSON/CSV files")
@click.option("--cleanup/--no-cleanup", default=False, help="Delete VMs after test (for run-all)")
@click.option("--collect-retries", default=8, type=int, help="Max retries for collecting results")
@click.option("--collect-retry-delay", default=20, type=int, help="Delay (seconds) between retries")
@click.option("--collect-concurrency", default=5, type=int, help="Max concurrent result collections")
@click.option("--ssh-pod", default="ssh-test-pod", help="SSH helper pod name (must have sshpass)")
@click.option("--ssh-pod-ns", default="default", help="SSH helper pod namespace")
@click.option("--vm-user", default="cloud-user", help="VM SSH user")
@click.option("--vm-password", default="changeme", help="VM SSH password")
@click.option("--log-file", type=click.Path(), help="Log file path")
@click.option("--log-level", default="INFO", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]))
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
    """
    print_banner("FIO Benchmark")

    # Validate storage-class for deploy/run-all
    if kwargs["action"] in ["deploy", "run-all"] and not kwargs["storage_class"]:
        console.print(f"[red]Error:[/red] --storage-class is required for action '{kwargs['action']}'")
        sys.exit(1)

    repo_root = ctx.obj.repo_root
    script_path = repo_root / "io-benchmark" / "fio" / "measure-fio-performance.py"

    if not script_path.exists():
        console.print(f"[red]Error:[/red] Script not found: {script_path}")
        sys.exit(1)

    # Resolve VM template path relative to repo root
    vm_template = kwargs["vm_template"]
    vm_template_path = Path(vm_template)
    if not vm_template_path.is_absolute():
        vm_template_path = repo_root / vm_template

    if kwargs["action"] in ["deploy", "run-all"] and not vm_template_path.exists():
        console.print(f"[red]Error:[/red] VM template not found: {vm_template_path}")
        sys.exit(1)

    # Build command
    cmd = [sys.executable, str(script_path)]

    # Add action
    cmd.extend(["--action", kwargs["action"]])

    # Add arguments
    cmd.extend(["--start", str(kwargs["start"])])
    cmd.extend(["--end", str(kwargs["end"])])
    if kwargs["storage_class"]:
        cmd.extend(["--storage-class", kwargs["storage_class"]])
    cmd.extend(["--vm-name", kwargs["vm_name"]])
    cmd.extend(["--vm-template", str(vm_template_path)])
    cmd.extend(["--namespace-prefix", kwargs["namespace_prefix"]])
    cmd.extend(["--concurrency", str(kwargs["concurrency"])])
    cmd.extend(["--fio-runtime", str(kwargs["fio_runtime"])])
    cmd.extend(["--fio-bs", kwargs["fio_bs"]])
    cmd.extend(["--fio-rw", kwargs["fio_rw"]])
    cmd.extend(["--fio-iodepth", str(kwargs["fio_iodepth"])])
    cmd.extend(["--fio-numjobs", str(kwargs["fio_numjobs"])])
    cmd.extend(["--fio-size", kwargs["fio_size"]])
    cmd.extend(["--results-dir", kwargs["results_dir"]])
    cmd.extend(["--storage-driver", kwargs["storage_driver"]])
    cmd.extend(["--disks-per-vm", kwargs["disks_per_vm"]])
    cmd.extend(["--log-level", kwargs["log_level"]])

    # Collection settings
    cmd.extend(["--collect-retries", str(kwargs["collect_retries"])])
    cmd.extend(["--collect-retry-delay", str(kwargs["collect_retry_delay"])])
    cmd.extend(["--collect-concurrency", str(kwargs["collect_concurrency"])])

    # SSH settings (password-based via existing pod)
    cmd.extend(["--ssh-pod", kwargs["ssh_pod"]])
    cmd.extend(["--ssh-pod-ns", kwargs["ssh_pod_ns"]])
    cmd.extend(["--vm-user", kwargs["vm_user"]])
    cmd.extend(["--vm-password", kwargs["vm_password"]])

    if kwargs["save_results"]:
        cmd.append("--save-results")
    if kwargs["cleanup"]:
        cmd.append("--cleanup")
    if kwargs["log_file"]:
        cmd.extend(["--log-file", kwargs["log_file"]])

    console.print(f"[cyan]Running:[/cyan] {' '.join(cmd[:3])}...")
    console.print(f"[dim]Full command: {' '.join(cmd)}[/dim]\n")

    try:
        result = subprocess.run(cmd, cwd=str(repo_root), check=False)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
