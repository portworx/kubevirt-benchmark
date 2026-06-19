"""
VM Migration benchmark command
"""

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from virtbench.common import build_python_command, generate_log_filename, print_banner
from virtbench.utils.yaml_modifier import modify_storage_class


console = Console()


def _normalize_source_nodes(values):
    """Accept repeated flags and comma-separated node lists."""
    nodes = []
    for value in values or ():
        for raw in str(value).split(","):
            node = raw.strip()
            if node:
                nodes.append(node)
    return nodes


@click.command("migration")
@click.option("--start", "-s", default=1, type=int, help="Start index for test namespaces")
@click.option("--end", "-e", default=10, type=int, help="End index for test namespaces")
@click.option("--vm-name", "-n", default="rhel-9-vm", help="VM resource name")
@click.option(
    "--vm-template", default="examples/vm-templates/rhel9-vm-datasource.yaml", help="Path to VM template YAML"
)
@click.option("--storage-class", help="Storage class name (required with --create-vms)")
@click.option("--namespace-prefix", default="migration", help="Namespace prefix")
@click.option("--source-node", help="Source node for VM creation and migration")
@click.option(
    "--source-nodes",
    multiple=True,
    help="Multi-node evacuation: list of source nodes whose VMs will all be migrated "
    "in parallel. Pass a comma-separated list, repeat the flag, or use "
    "--source-nodes all to target every worker. VMs are discovered directly "
    "from each node (no --start/--end range required) and submitted in an "
    "interleaved order so load is spread across source nodes from the start.",
)
@click.option("--target-node", help="Target node name to migrate VMs to")
@click.option(
    "--create-vms", is_flag=True, help="Create VMs on source node before migration (requires --storage-class)"
)
@click.option("--parallel", is_flag=True, help="Migrate all VMs in parallel")
@click.option("--evacuate", is_flag=True, help="Evacuate all VMs from source node")
@click.option(
    "--auto-select-busiest", is_flag=True, help="Auto-select the node with the most matching VMs for evacuation"
)
@click.option("--round-robin", is_flag=True, help="Migrate VMs to randomly selected different worker nodes")
@click.option(
    "--interleaved-scheduling", is_flag=True, help="Interleave parallel migration scheduling across detected nodes"
)
@click.option("--concurrency", "-c", default=50, type=int, help="Max parallel threads")
@click.option("--poll-interval", default=1, type=int, help="Seconds between status checks")
@click.option("--migration-timeout", default=600, type=int, help="Timeout for migration in seconds")
@click.option("--max-migration-retries", default=3, type=int, help="Maximum retries for failed migrations (default: 3)")
@click.option(
    "--vm-startup-timeout",
    default=3600,
    type=int,
    help="Timeout waiting for VMs to reach Running state (default: 3600s = 1 hour)",
)
@click.option(
    "--ping-timeout", default=3600, type=int, help="Timeout for ping validation in seconds (default: 3600s = 1 hour)"
)
@click.option("--skip-ping", is_flag=True, help="Skip ping validation after migration")
@click.option("--ssh-pod", default="ssh-test-pod", help="SSH pod name for ping tests (default: ssh-test-pod)")
@click.option("--ssh-pod-ns", default="default", help="SSH pod namespace (default: default)")
@click.option("--cleanup/--no-cleanup", default=False, help="Delete test resources after completion")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option("--save-results", is_flag=True, help="Save detailed results to results folder")
@click.option("--results-folder", default="../results", help="Base directory to store test results")
@click.option("--storage-driver", help="Storage driver label for results path (for example: portworx-3.6, ceph)")
@click.option("--log-file", type=click.Path(), help="Log file path (auto-generated if not specified)")
@click.pass_context
def migration(ctx, **kwargs):
    """
    Run VM migration benchmark

    This workload tests the performance of live migrating VMs between nodes.
    VMs must exist before migration. Use --create-vms with --storage-class
    to create VMs as part of the test.

    \b
    Examples:
      # Create VMs on source node and migrate to target
      virtbench migration --start 1 --end 10 --source-node worker-1 --target-node worker-2 \\
        --create-vms --storage-class YOUR-STORAGE-CLASS --save-results

      # Create VMs, migrate, and cleanup after test
      virtbench migration --start 1 --end 10 --source-node worker-1 \\
        --create-vms --storage-class YOUR-STORAGE-CLASS --cleanup --save-results

      # Parallel migration with VM creation
      virtbench migration --start 1 --end 50 --source-node worker-1 \\
        --create-vms --storage-class YOUR-STORAGE-CLASS --parallel --concurrency 10

      # Migrate existing VMs (no --create-vms)
      virtbench migration --start 1 --end 10 --source-node worker-1 --save-results

      # Evacuate all VMs from a node
      virtbench migration --start 1 --end 100 --source-node worker-1 --evacuate

      # Multi-node evacuation: discover VMs on multiple nodes and migrate
      # them in parallel, interleaved across source nodes
      virtbench migration --source-nodes worker-1,worker-2,worker-3 \\
        --concurrency 20 --save-results

      # Evacuate every worker node in the cluster
      virtbench migration --source-nodes all --concurrency 20 --save-results
    """
    print_banner("VM Migration Benchmark")

    # Get repo root from context
    repo_root = ctx.obj.repo_root

    # Validate --create-vms requires --storage-class
    if kwargs["create_vms"] and not kwargs["storage_class"]:
        console.print("[red]Error: --storage-class is required when using --create-vms[/red]")
        console.print("[yellow]Hint: Specify the storage class to use for VM creation:[/yellow]")
        console.print("  virtbench migration --create-vms --storage-class YOUR-STORAGE-CLASS ...")
        sys.exit(1)

    # Resolve template path
    template_path = Path(kwargs["vm_template"])
    if not template_path.is_absolute():
        template_path = repo_root / template_path

    if kwargs["create_vms"] and not template_path.exists():
        console.print(f"[red]Error: Template file not found: {template_path}[/red]")
        sys.exit(1)

    # Handle storage class modification
    if kwargs["storage_class"] and kwargs["create_vms"]:
        console.print(f"[cyan]Using storage class: {kwargs['storage_class']}[/cyan]")
        console.print(f"[cyan]VMs will be created on source node: {kwargs.get('source_node', 'auto-selected')}[/cyan]")
        try:
            modify_storage_class(template_path, kwargs["storage_class"])
        except Exception as e:
            console.print(f"[red]Error modifying storage class: {e}[/red]")
            sys.exit(1)

    # Build Python script command
    script_path = repo_root / "migration" / "measure-vm-migration-time.py"

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
        "migration-timeout": kwargs["migration_timeout"],
        "max-migration-retries": kwargs["max_migration_retries"],
        "vm-startup-timeout": kwargs["vm_startup_timeout"],
        "ping-timeout": kwargs["ping_timeout"],
        "ssh-pod": kwargs["ssh_pod"],
        "ssh-pod-ns": kwargs["ssh_pod_ns"],
        "results-folder": kwargs["results_folder"],
        "log-level": ctx.obj.log_level.upper(),
    }

    # Add boolean flags
    if kwargs["create_vms"]:
        python_args["create-vms"] = True
    if kwargs["parallel"]:
        python_args["parallel"] = True
    if kwargs["evacuate"]:
        python_args["evacuate"] = True
    if kwargs["auto_select_busiest"]:
        python_args["auto-select-busiest"] = True
    if kwargs["round_robin"]:
        python_args["round-robin"] = True
    if kwargs["interleaved_scheduling"]:
        python_args["interleaved-scheduling"] = True
    if kwargs["cleanup"]:
        python_args["cleanup"] = True
    if kwargs["yes"]:
        python_args["yes"] = True
    if kwargs["save_results"]:
        python_args["save-results"] = True
    if kwargs["skip_ping"]:
        python_args["skip-ping"] = True

    # Add optional args
    if kwargs.get("source_node"):
        python_args["source-node"] = kwargs["source_node"]
    source_nodes = _normalize_source_nodes(kwargs.get("source_nodes"))
    if source_nodes:
        # build_python_command emits this as "--source-nodes n1 n2 n3",
        # matching the script's argparse nargs='+'.
        python_args["source-nodes"] = source_nodes
    if kwargs.get("target_node"):
        python_args["target-node"] = kwargs["target_node"]
    if kwargs.get("storage_driver"):
        python_args["storage-driver"] = kwargs["storage_driver"]

    # Add log-file only when explicitly requested. With --save-results, the
    # script creates the run directory and writes migration.log next to JSON/CSV.
    if kwargs.get("log_file"):
        python_args["log-file"] = kwargs["log_file"]
    elif ctx.obj.log_file:
        python_args["log-file"] = ctx.obj.log_file
    elif not kwargs["save_results"]:
        python_args["log-file"] = generate_log_filename("migration")

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
