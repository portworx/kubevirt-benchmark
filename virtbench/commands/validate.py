"""
Cluster validation command
"""

import subprocess
import sys

import click
from rich.console import Console

from virtbench.common import build_python_command, print_banner


console = Console()


@click.command("validate-cluster")
@click.option("--storage-class", help="Storage class name to validate")
@click.option("--datasource", default="rhel9", help="DataSource name to validate")
@click.option("--datasource-namespace", default="openshift-virtualization-os-images", help="DataSource namespace")
@click.option("--ssh-pod", default="ssh-test-pod", help="SSH test pod name")
@click.option("--ssh-pod-namespace", default="default", help="SSH test pod namespace")
@click.option("--min-worker-nodes", default=1, type=int, help="Minimum required worker nodes")
@click.option("--all", "run_all", is_flag=True, help="Run all validation checks")
@click.option("--quick", is_flag=True, help="Run quick validation (skip some checks)")
@click.pass_context
def validate_cluster(ctx, **kwargs):
    """
    Validate cluster prerequisites

    Checks that the cluster has all required components for running benchmarks:
    - KubeVirt installation
    - Storage class availability
    - Worker nodes
    - Required permissions

    \b
    Examples:
      # Validate cluster with storage class
      virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS

      # Quick validation
      virtbench validate-cluster --quick

      # Validate a custom DataSource
      virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS \\
        --datasource fedora --datasource-namespace openshift-virtualization-os-images
    """
    print_banner("Cluster Validation")

    # Get repo root from context
    repo_root = ctx.obj.repo_root

    # Build Python script command
    script_path = repo_root / "utils" / "validate_cluster.py"

    if not script_path.exists():
        console.print(f"[red]Error: Script not found: {script_path}[/red]")
        sys.exit(1)

    # Map CLI args to Python script args
    python_args = {
        "log-level": ctx.obj.log_level.upper(),
        "datasource": kwargs["datasource"],
        "datasource-namespace": kwargs["datasource_namespace"],
        "ssh-pod": kwargs["ssh_pod"],
        "ssh-pod-namespace": kwargs["ssh_pod_namespace"],
        "min-worker-nodes": kwargs["min_worker_nodes"],
    }

    # Add optional args
    if kwargs.get("storage_class"):
        python_args["storage-class"] = kwargs["storage_class"]

    # Add boolean flags
    if kwargs["quick"]:
        python_args["quick"] = True
    if kwargs["run_all"]:
        python_args["all"] = True

    # Add global flags from context
    if ctx.obj.kubeconfig:
        python_args["kubeconfig"] = ctx.obj.kubeconfig

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
