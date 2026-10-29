"""CDI root-disk provisioning benchmark command."""

from pathlib import Path
from typing import Any, Dict, List
import time

import click
from rich.console import Console
from rich.table import Table

from virtbench.common import print_banner
from virtbench.provisioning import (
    BenchmarkError,
    CLONE_STRATEGIES,
    Kubectl,
    aggregate_results,
    build_clone_vm,
    cleanup_run,
    ensure_namespace,
    find_root_volume,
    golden_reference,
    load_virtual_machine,
    monitor_single_vm,
    mounted_by_pods,
    new_run_id,
    parse_duration,
    resolve_golden_image,
    resolve_input_path,
    safe_name,
    save_results,
    strategy_preflight,
    utc_now,
)


console = Console()


def _strategies(value: str) -> List[str]:
    return list(CLONE_STRATEGIES) if value == "all" else [value]


def _format_seconds(value: Any) -> str:
    return "-" if value is None else f"{float(value):.2f}s"


def _print_results(rows: List[Dict[str, Any]]) -> None:
    table = Table(title="CDI root-disk results")
    table.add_column("Strategy")
    table.add_column("Run", justify="right")
    table.add_column("Status")
    table.add_column("Observed")
    table.add_column("Root disk", justify="right")
    table.add_column("VMI running", justify="right")
    table.add_column("Ready", justify="right")
    for row in rows:
        table.add_row(
            str(row.get("requested_strategy", "-")),
            str(row.get("iteration", "-")),
            str(row.get("status", "-")),
            str(row.get("observed_strategy") or "-"),
            _format_seconds(row.get("root_disk_seconds")),
            _format_seconds(row.get("vmi_running_seconds")),
            _format_seconds(row.get("total_seconds")),
        )
    console.print(table)


@click.command("cdi-root-disk")
@click.option("--datasource-name", default="rhel9", show_default=True, help="DataSource name")
@click.option(
    "--datasource-namespace",
    default="openshift-virtualization-os-images",
    show_default=True,
    help="DataSource namespace",
)
@click.option(
    "--golden-image",
    help="Use a golden PVC (or DataSource) as namespace/name instead of --datasource-name",
)
@click.option(
    "--golden-image-kind",
    type=click.Choice(["pvc", "datasource"]),
    default="pvc",
    show_default=True,
    help="Kind of --golden-image",
)
@click.option(
    "--vm-spec",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="VirtualMachine YAML used for every strategy",
)
@click.option("--root-volume", help="Root volume name; auto-detected from bootOrder when omitted")
@click.option(
    "--strategy",
    "--clone-strategy",
    type=click.Choice(["all", *CLONE_STRATEGIES]),
    default="all",
    show_default=True,
)
@click.option("--namespace", default="virtbench-root-disk", show_default=True)
@click.option("--storage-class", help="Target StorageClass; defaults to the golden PVC class")
@click.option("--iterations", type=click.IntRange(min=1), default=3, show_default=True)
@click.option(
    "--ready-when",
    type=click.Choice(["vmi-running", "vmi-ready", "guest-agent"]),
    default="vmi-ready",
    show_default=True,
)
@click.option("--poll-interval", type=click.FloatRange(min=0.1), default=1.0, show_default=True)
@click.option(
    "--allow-mounted-source",
    is_flag=True,
    help="Attempt cloning even when a running pod mounts the golden PVC",
)
@click.option(
    "--cleanup/--keep-resources",
    default=True,
    show_default=True,
    help="Remove each generated VM and root disk after it is measured",
)
@click.option("--save-results/--no-save-results", default=True, show_default=True)
@click.option("--results-folder", type=click.Path(path_type=Path), default=Path("results"))
@click.pass_context
def cdi_root_disk(ctx: click.Context, **kwargs: Any) -> None:
    """Compare CDI strategies while provisioning a VM root disk.

    The command clones one golden PVC with csi-clone, snapshot, and copy,
    injects each result as the root disk of the same VM specification, and
    measures root-disk, VMI-running, and readiness time independently.
    """
    print_banner("CDI Root Disk Provisioning Benchmark")
    client = Kubectl()
    run_id = safe_name(ctx.obj.uuid) if ctx.obj.uuid else new_run_id("cdi-root")
    strategies = _strategies(kwargs["strategy"])
    timeout = parse_duration(ctx.obj.timeout)
    repo_root = ctx.obj.repo_root
    vm_path = resolve_input_path(kwargs["vm_spec"], repo_root)

    rows: List[Dict[str, Any]] = []
    created_namespace = False
    resource_run_ids: List[str] = []
    payload: Dict[str, Any] = {
        "benchmark": "cdi-root-disk",
        "run_id": run_id,
        "started_at": utc_now(),
        "configuration": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in kwargs.items()
        },
    }
    payload["configuration"].update(
        {"timeout": ctx.obj.timeout, "kubeconfig": ctx.obj.kubeconfig}
    )

    try:
        vm_source = load_virtual_machine(vm_path)
        root_volume = find_root_volume(vm_source, kwargs["root_volume"])
        reference, reference_kind = golden_reference(
            kwargs["golden_image"],
            kwargs["golden_image_kind"],
            kwargs["datasource_name"],
            kwargs["datasource_namespace"],
        )
        source_ns, source_name, source_pvc = resolve_golden_image(
            client, reference, reference_kind, kwargs["namespace"]
        )
        mounted = mounted_by_pods(client, source_ns, source_name)
        if mounted and not kwargs["allow_mounted_source"]:
            raise BenchmarkError(
                f"Golden PVC {source_ns}/{source_name} is mounted by: {', '.join(mounted)}. "
                "Stop those pods or pass --allow-mounted-source."
            )
        if mounted:
            console.print(
                f"[yellow]Warning: golden PVC is mounted by {', '.join(mounted)}; "
                "optimized clones may wait or fail.[/yellow]"
            )
        created_namespace = ensure_namespace(client, kwargs["namespace"], run_id)
        preflight = strategy_preflight(
            client,
            strategies,
            source_ns,
            kwargs["namespace"],
            source_pvc,
            kwargs["storage_class"],
        )
        payload["source"] = {"namespace": source_ns, "pvc": source_name}
        payload["root_volume"] = root_volume
        payload["preflight"] = preflight

        for requested in strategies:
            check = preflight[requested]
            if not check["supported"]:
                console.print(f"[yellow]{requested}: unsupported — {check['reason']}[/yellow]")
                rows.append(
                    {
                        "iteration": 0,
                        "requested_strategy": requested,
                        "status": "unsupported",
                        "error": check["reason"],
                    }
                )
                continue
            console.print(f"[cyan]Benchmarking {requested}[/cyan]")
            for iteration in range(1, kwargs["iterations"] + 1):
                resource_run_id = new_run_id(f"root-{requested}-{iteration}")
                resource_run_ids.append(resource_run_id)
                vm_name = safe_name(f"vb-root-{requested}-{iteration}-{run_id[-8:]}")
                dv_name = safe_name(f"{vm_name}-root")
                vm = build_clone_vm(
                    vm_source,
                    name=vm_name,
                    namespace=kwargs["namespace"],
                    root_volume=root_volume,
                    source_namespace=source_ns,
                    source_name=source_name,
                    source_pvc=source_pvc,
                    strategy=requested,
                    run_id=resource_run_id,
                    benchmark="cdi-root-disk",
                    storage_class=kwargs["storage_class"],
                    data_volume_name=dv_name,
                )
                started = time.monotonic()
                try:
                    client.create(vm)
                    result = monitor_single_vm(
                        client,
                        namespace=kwargs["namespace"],
                        vm_name=vm_name,
                        dv_name=dv_name,
                        requested_strategy=requested,
                        ready_when=kwargs["ready_when"],
                        timeout=timeout,
                        poll_interval=kwargs["poll_interval"],
                        started=started,
                    )
                except BenchmarkError as exc:
                    result = {
                        "requested_strategy": requested,
                        "status": "failed",
                        "error": str(exc),
                    }
                result.update(
                    {
                        "iteration": iteration,
                        "vm": vm_name,
                        "data_volume": dv_name,
                        "resource_run_id": resource_run_id,
                    }
                )
                rows.append(result)
                color = "green" if result["status"] == "succeeded" else "red"
                console.print(
                    f"  [{color}]run {iteration}: {result['status']}"
                    f" ({_format_seconds(result.get('total_seconds'))})[/{color}]"
                )
                if kwargs["cleanup"]:
                    cleanup_run(client, kwargs["namespace"], resource_run_id)

        payload["results"] = rows
        payload["summary"] = aggregate_results(rows, "total_seconds")
        payload["completed_at"] = utc_now()
        _print_results(rows)
        if kwargs["save_results"]:
            base = kwargs["results_folder"]
            if not base.is_absolute():
                base = repo_root / base
            directory = save_results(base, "cdi-root-disk", run_id, payload, rows)
            console.print(f"Results saved to [cyan]{directory}[/cyan]")
    except BenchmarkError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if kwargs["cleanup"]:
            for resource_run_id in resource_run_ids:
                cleanup_run(client, kwargs["namespace"], resource_run_id)
            if created_namespace:
                client.delete("namespace", [kwargs["namespace"]], wait=False)

    if not any(row.get("status") == "succeeded" for row in rows) or any(
        row.get("status") in {"failed", "timeout", "fallback"} for row in rows
    ):
        raise click.exceptions.Exit(1)
