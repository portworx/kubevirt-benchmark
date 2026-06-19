"""
DataSource Clone benchmark command
"""

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from virtbench.common import build_python_command, generate_log_filename, print_banner
from virtbench.utils.yaml_modifier import modify_storage_class


console = Console()


@click.command("datasource-clone")
@click.option("--start", "-s", default=1, type=int, help="Start index for test namespaces")
@click.option("--end", "-e", default=10, type=int, help="End index for test namespaces")
@click.option("--vm-name", "-n", default="rhel-9-vm", help="VM resource name")
@click.option(
    "--vm-template", default="examples/vm-templates/rhel9-vm-datasource.yaml", help="Path to VM template YAML"
)
@click.option("--secret-yaml", type=click.Path(exists=True), help="Path to cloudinit secret YAML file (optional)")
@click.option("--storage-class", help="Storage class name (overrides template value)")
@click.option("--namespace-prefix", default="datasource-clone", help="Namespace prefix")
@click.option("--concurrency", "-c", default=50, type=int, help="Max parallel threads for monitoring")
@click.option("--poll-interval", default=1, type=int, help="Seconds between status checks")
@click.option("--ping-timeout", default=300, type=int, help="Timeout for ping tests in seconds")
@click.option("--ssh-pod", default="ssh-test-pod", help="Pod name for ping tests")
@click.option("--ssh-pod-ns", default="default", help="Namespace for SSH test pod")
@click.option("--cleanup/--no-cleanup", default=False, help="Delete test resources after completion")
@click.option(
    "--cleanup-on-failure/--no-cleanup-on-failure", default=False, help="Clean up resources even if tests fail"
)
@click.option(
    "--dry-run-cleanup/--no-dry-run-cleanup", default=False, help="Show what would be deleted without actually deleting"
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt for cleanup")
@click.option("--skip-namespace-creation", is_flag=True, help="Skip namespace creation (use existing namespaces)")
@click.option("--boot-storm", is_flag=True, help="After initial test, shutdown all VMs and test boot storm")
@click.option(
    "--skip-vm-creation", is_flag=True, help="Skip VM creation phase (use with --boot-storm to test existing VMs)"
)
@click.option(
    "--num-disks",
    type=int,
    default=None,
    help="Number of disks per VM (auto-detected from template or existing VM if not specified)",
)
@click.option("--namespace-batch-size", default=20, type=int, help="Number of namespaces to create in parallel")
@click.option("--single-node", is_flag=True, help="Run all VMs on a single node")
@click.option("--node-name", help="Specific node name for single-node testing")
@click.option("--save-results", is_flag=True, help="Save detailed results (JSON and CSV) to results folder")
@click.option("--results-folder", default="results", help="Base directory to store test results")
@click.option("--storage-driver", help="Storage driver label for results path (for example: portworx-3.6, ceph)")
@click.option("--log-file", type=click.Path(), help="Log file path (auto-generated if not specified)")
@click.pass_context
def datasource_clone(ctx, **kwargs):
    """
    Run DataSource clone benchmark

    This workload tests the performance of creating VMs by cloning from a DataSource,
    which is the recommended approach for VM provisioning in KubeVirt.

    \b
    Examples:
      # Run with 10 VMs (namespaces 1-10)
      virtbench datasource-clone --start 1 --end 10

      # Run with custom storage class
      virtbench datasource-clone --start 1 --end 50 --storage-class YOUR-STORAGE-CLASS

      # Run with cleanup after test
      virtbench datasource-clone --start 1 --end 20 --cleanup

      # Boot storm test
      virtbench datasource-clone --start 1 --end 10 --boot-storm

      # Single node test
      virtbench datasource-clone --start 1 --end 10 --single-node --node-name worker-1
    """
    print_banner("DataSource Clone Benchmark")

    # Get repo root from context
    repo_root = ctx.obj.repo_root

    # Resolve template path
    template_path = Path(kwargs["vm_template"])
    if not template_path.is_absolute():
        template_path = repo_root / template_path

    if not template_path.exists():
        console.print(f"[red]Error: Template file not found: {template_path}[/red]")
        sys.exit(1)

    # Resolve secret YAML path if provided
    secret_yaml_path = None
    if kwargs.get("secret_yaml"):
        secret_yaml_path = Path(kwargs["secret_yaml"])
        if not secret_yaml_path.is_absolute():
            secret_yaml_path = repo_root / secret_yaml_path

        if not secret_yaml_path.exists():
            console.print(f"[red]Error: Secret YAML file not found: {secret_yaml_path}[/red]")
            sys.exit(1)

    # Handle storage class modification
    if kwargs["storage_class"]:
        console.print(f"[cyan]Using storage class: {kwargs['storage_class']}[/cyan]")
        try:
            modify_storage_class(template_path, kwargs["storage_class"])
        except Exception as e:
            console.print(f"[red]Error modifying storage class: {e}[/red]")
            sys.exit(1)

    # Build Python script command
    script_path = repo_root / "datasource-clone" / "measure-vm-creation-time.py"

    if not script_path.exists():
        console.print(f"[red]Error: Script not found: {script_path}[/red]")
        sys.exit(1)

    # Map CLI args to Python script args
    python_args = {
        "start": kwargs["start"],
        "end": kwargs["end"],
        "vm-name": kwargs["vm_name"],
        "vm-template": str(template_path),
        "namespace-prefix": kwargs["namespace_prefix"],
        "concurrency": kwargs["concurrency"],
        "poll-interval": kwargs["poll_interval"],
        "ping-timeout": kwargs["ping_timeout"],
        "ssh-pod": kwargs["ssh_pod"],
        "ssh-pod-ns": kwargs["ssh_pod_ns"],
        "namespace-batch-size": kwargs["namespace_batch_size"],
        "results-folder": kwargs["results_folder"],
        "log-level": ctx.obj.log_level.upper(),
    }

    # Add boolean flags
    if kwargs["cleanup"]:
        python_args["cleanup"] = True
    if kwargs["cleanup_on_failure"]:
        python_args["cleanup-on-failure"] = True
    if kwargs["dry_run_cleanup"]:
        python_args["dry-run-cleanup"] = True
    if kwargs["yes"]:
        python_args["yes"] = True
    if kwargs["skip_namespace_creation"]:
        python_args["skip-namespace-creation"] = True
    if kwargs["boot_storm"]:
        python_args["boot-storm"] = True
    if kwargs["skip_vm_creation"]:
        python_args["skip-vm-creation"] = True
    if kwargs["single_node"]:
        python_args["single-node"] = True
    if kwargs["save_results"]:
        python_args["save-results"] = True

    # Add optional args
    if kwargs.get("node_name"):
        python_args["node-name"] = kwargs["node_name"]
    if kwargs.get("storage_driver"):
        python_args["storage-driver"] = kwargs["storage_driver"]
    if kwargs.get("num_disks"):
        python_args["num-disks"] = kwargs["num_disks"]
    if secret_yaml_path:
        python_args["secret-yaml"] = str(secret_yaml_path)

    # Add log-file only when explicitly requested. With --save-results, the
    # script creates the run directory first and writes the log next to JSON/CSV.
    if kwargs.get("log_file"):
        python_args["log-file"] = kwargs["log_file"]
    elif ctx.obj.log_file:
        python_args["log-file"] = ctx.obj.log_file
    elif not kwargs["save_results"]:
        python_args["log-file"] = generate_log_filename("datasource-clone")

    # Build and run command
    cmd = build_python_command(script_path, python_args)

    console.print(f"[dim]Running: {' '.join(cmd[:2])} ...[/dim]")
    console.print()

    try:
        result = subprocess.run(cmd, cwd=repo_root, check=False)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
