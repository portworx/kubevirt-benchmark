"""
Failure Recovery benchmark command
"""

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from virtbench.common import build_python_command, generate_log_filename, print_banner
from virtbench.utils.yaml_modifier import modify_storage_class


console = Console()


@click.command("failure-recovery")
@click.option(
    "--mode",
    type=click.Choice(["monitor", "manual", "far-operator"]),
    default="monitor",
    show_default=True,
    help="Failure workflow: monitor external failure, wait for manual failure, or trigger FAR",
)
@click.option("--node", required=True, help="Node name to auto-detect VMs from")
@click.option("--vm-name", "-n", default="rhel-9-vm", help="VM resource name")
@click.option(
    "--vm-template", default="examples/vm-templates/rhel9-vm-datasource.yaml", help="Path to VM template YAML"
)
@click.option("--storage-class", help="Storage class name (overrides template value)")
@click.option("--namespace-prefix", default="failure-recovery", help="Namespace prefix")
@click.option(
    "--far-config", default="failure-recovery/far-template.yaml", help="FAR YAML manifest for --mode far-operator"
)
@click.option("--remove-node-selector", is_flag=True, help="Remove nodeSelector from VMs before recovery monitoring")
@click.option("--concurrency", "-c", default=10, type=int, help="Max parallel threads")
@click.option("--poll-interval", default=5, type=int, help="Seconds between status checks")
@click.option("--node-timeout", default=600, type=int, help="Timeout for node to become NotReady")
@click.option("--recovery-timeout", default=600, type=int, help="Timeout for recovery in seconds")
@click.option("--skip-ping", is_flag=True, help="Skip ping recovery checks")
@click.option("--ssh-pod", default="ssh-test-pod", help="SSH pod name for ping checks")
@click.option("--ssh-pod-namespace", default="default", help="SSH pod namespace for ping checks")
@click.option("--cleanup/--no-cleanup", default=False, help="Delete test resources after completion")
@click.option("--cleanup-vms", is_flag=True, help="Also delete VMs and namespaces during cleanup")
@click.option("--dry-run-cleanup", is_flag=True, help="Show cleanup actions without applying them")
@click.option("--far-name", help="FAR resource name to delete during cleanup")
@click.option("--far-namespace", default="default", help="FAR resource namespace")
@click.option("--failed-node", help="Node to uncordon during cleanup (defaults to --node)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option("--save-results", is_flag=True, help="Save detailed results to results folder")
@click.option("--results-folder", default="../results", help="Base directory to store test results")
@click.option("--storage-driver", help="Storage driver label for results path (for example: portworx-3.6, ceph)")
@click.option("--log-file", type=click.Path(), help="Log file path (auto-generated if not specified)")
@click.pass_context
def failure_recovery(ctx, **kwargs):
    """
    Run failure recovery benchmark

    This workload tests VM recovery time after simulated node failures.
    VMs are auto-detected on the specified node.

    \b
    Examples:
      virtbench failure-recovery --node worker-1 --vm-name rhel-9-vm

      virtbench failure-recovery --mode manual --node worker-1

      virtbench failure-recovery --mode far-operator --node worker-1
    """
    print_banner("Failure Recovery Benchmark")

    # Get repo root from context
    repo_root = ctx.obj.repo_root

    # Resolve template path
    template_path = Path(kwargs["vm_template"])
    if not template_path.is_absolute():
        template_path = repo_root / template_path

    if not template_path.exists():
        console.print(f"[red]Error: Template file not found: {template_path}[/red]")
        sys.exit(1)

    # Handle storage class modification
    if kwargs["storage_class"]:
        console.print(f"[cyan]Using storage class: {kwargs['storage_class']}[/cyan]")
        try:
            modify_storage_class(template_path, kwargs["storage_class"])
        except Exception as e:
            console.print(f"[red]Error modifying storage class: {e}[/red]")
            sys.exit(1)

    far_config_path = None
    if kwargs["far_config"]:
        far_config_path = Path(kwargs["far_config"])
        if not far_config_path.is_absolute():
            far_config_path = repo_root / far_config_path

    if kwargs["mode"] == "far-operator":
        if not far_config_path or not far_config_path.exists():
            console.print(f"[red]Error: FAR config file not found: {far_config_path}[/red]")
            sys.exit(1)

    # Build Python script command
    script_path = repo_root / "failure-recovery" / "recovery-test.py"

    if not script_path.exists():
        console.print(f"[red]Error: Script not found: {script_path}[/red]")
        sys.exit(1)

    # Map CLI args to Python script args
    python_args = {
        "mode": kwargs["mode"],
        "node": kwargs["node"],
        "vm-name": kwargs["vm_name"],
        "vm-template": str(template_path),
        "namespace-prefix": kwargs["namespace_prefix"],
        "far-config": str(far_config_path) if far_config_path else None,
        "concurrency": kwargs["concurrency"],
        "poll-interval": kwargs["poll_interval"],
        "node-timeout": kwargs["node_timeout"],
        "recovery-timeout": kwargs["recovery_timeout"],
        "ssh-pod": kwargs["ssh_pod"],
        "ssh-pod-namespace": kwargs["ssh_pod_namespace"],
        "results-folder": kwargs["results_folder"],
        "log-level": ctx.obj.log_level.upper(),
    }

    # Add boolean flags
    if kwargs["cleanup"]:
        python_args["cleanup"] = True
    if kwargs["cleanup_vms"]:
        python_args["cleanup-vms"] = True
    if kwargs["dry_run_cleanup"]:
        python_args["dry-run-cleanup"] = True
    if kwargs["remove_node_selector"]:
        python_args["remove-node-selector"] = True
    if kwargs["skip_ping"]:
        python_args["skip-ping"] = True
    if kwargs["yes"]:
        python_args["yes"] = True
    if kwargs["save_results"]:
        python_args["save-results"] = True

    if kwargs.get("storage_driver"):
        python_args["storage-driver"] = kwargs["storage_driver"]
    if kwargs.get("far_name"):
        python_args["far-name"] = kwargs["far_name"]
    if kwargs.get("far_namespace"):
        python_args["far-namespace"] = kwargs["far_namespace"]
    if kwargs.get("failed_node"):
        python_args["failed-node"] = kwargs["failed_node"]

    # Add log-file only when explicitly requested. With --save-results, the
    # script creates the run directory first and writes the log next to JSON/CSV.
    if kwargs.get("log_file"):
        python_args["log-file"] = kwargs["log_file"]
    elif ctx.obj.log_file:
        python_args["log-file"] = ctx.obj.log_file
    elif not kwargs["save_results"]:
        python_args["log-file"] = generate_log_filename("failure-recovery")

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
