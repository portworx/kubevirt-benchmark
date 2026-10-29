"""VirtualMachinePool scale and recovery benchmark command."""

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
    RUN_LABEL,
    Kubectl,
    aggregate_results,
    build_clone_vm,
    build_vm_pool,
    cleanup_run,
    ensure_namespace,
    find_root_volume,
    golden_reference,
    load_virtual_machine,
    monitor_pool,
    monitor_pool_recovery,
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


def _seconds(value: Any) -> str:
    return "-" if value is None else f"{float(value):.2f}s"


def _print_results(rows: List[Dict[str, Any]]) -> None:
    table = Table(title="VirtualMachinePool results")
    table.add_column("Strategy")
    table.add_column("Run", justify="right")
    table.add_column("Status")
    table.add_column("VM objects", justify="right")
    table.add_column("Root disks", justify="right")
    table.add_column("50% ready", justify="right")
    table.add_column("All ready", justify="right")
    table.add_column("Recovery", justify="right")
    for row in rows:
        recovery = row.get("recovery") or {}
        table.add_row(
            str(row.get("requested_strategy", "-")),
            str(row.get("iteration", "-")),
            str(row.get("status", "-")),
            _seconds(row.get("all_vm_objects_seconds")),
            _seconds(row.get("all_root_disks_seconds")),
            _seconds(row.get("ready_50_seconds")),
            _seconds(row.get("all_ready_seconds")),
            _seconds(recovery.get("seconds")),
        )
    console.print(table)


@click.command("vm-pool")
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
    help="VirtualMachine YAML used as the pool template",
)
@click.option("--root-volume", help="Root volume name; auto-detected from bootOrder when omitted")
@click.option(
    "--strategy",
    "--clone-strategy",
    type=click.Choice(["all", *CLONE_STRATEGIES]),
    default="all",
    show_default=True,
)
@click.option("--replicas", type=click.IntRange(min=1), default=10, show_default=True)
@click.option(
    "--recovery-delete",
    type=click.IntRange(min=0),
    default=0,
    show_default=True,
    help="After scale-out, delete this many managed VMs and measure recovery",
)
@click.option("--namespace", default="virtbench-vm-pool", show_default=True)
@click.option("--storage-class", help="Target StorageClass; defaults to the golden PVC class")
@click.option("--iterations", type=click.IntRange(min=1), default=1, show_default=True)
@click.option(
    "--ready-when",
    type=click.Choice(["vmi-running", "vmi-ready", "guest-agent"]),
    default="vmi-ready",
    show_default=True,
)
@click.option("--poll-interval", type=click.FloatRange(min=0.1), default=1.0, show_default=True)
@click.option("--allow-mounted-source", is_flag=True)
@click.option(
    "--cleanup/--keep-resources",
    default=True,
    show_default=True,
    help="Remove each pool and its generated disks after measurement",
)
@click.option("--save-results/--no-save-results", default=True, show_default=True)
@click.option("--results-folder", type=click.Path(path_type=Path), default=Path("results"))
@click.pass_context
def vm_pool(ctx: click.Context, **kwargs: Any) -> None:
    """Benchmark VirtualMachinePool scale-out and optional self-healing.

    Each pool replica receives a unique CDI-cloned root disk from the supplied
    golden image. Use --strategy all to compare storage strategies under the
    same replica count and VM specification.
    """
    print_banner("VirtualMachinePool Scale and Recovery Benchmark")
    if kwargs["recovery_delete"] > kwargs["replicas"]:
        raise click.UsageError("--recovery-delete cannot exceed --replicas")

    client = Kubectl()
    run_id = safe_name(ctx.obj.uuid) if ctx.obj.uuid else new_run_id("vm-pool")
    strategies = _strategies(kwargs["strategy"])
    timeout = parse_duration(ctx.obj.timeout)
    repo_root = ctx.obj.repo_root
    vm_path = resolve_input_path(kwargs["vm_spec"], repo_root)
    rows: List[Dict[str, Any]] = []
    resource_run_ids: List[str] = []
    created_namespace = False
    payload: Dict[str, Any] = {
        "benchmark": "vm-pool",
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
        if client.get("crd", "virtualmachinepools.pool.kubevirt.io") is None:
            raise BenchmarkError("VirtualMachinePool CRD is not installed on this cluster")
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
            console.print(
                f"[cyan]Scaling {requested} pool to {kwargs['replicas']} replicas[/cyan]"
            )
            for iteration in range(1, kwargs["iterations"] + 1):
                resource_run_id = new_run_id(f"pool-{requested}-{iteration}")
                resource_run_ids.append(resource_run_id)
                pool_name = safe_name(f"vb-pool-{requested}-{iteration}-{run_id[-8:]}")
                root_dv_base = safe_name(f"{pool_name}-root")
                transformed = build_clone_vm(
                    vm_source,
                    name=pool_name,
                    namespace=kwargs["namespace"],
                    root_volume=root_volume,
                    source_namespace=source_ns,
                    source_name=source_name,
                    source_pvc=source_pvc,
                    strategy=requested,
                    run_id=resource_run_id,
                    benchmark="vm-pool",
                    storage_class=kwargs["storage_class"],
                    data_volume_name=root_dv_base,
                )
                pool = build_vm_pool(
                    transformed,
                    name=pool_name,
                    namespace=kwargs["namespace"],
                    replicas=0,
                    run_id=resource_run_id,
                )
                try:
                    client.create(pool)
                    started = time.monotonic()
                    client.patch(
                        "virtualmachinepool",
                        pool_name,
                        kwargs["namespace"],
                        {"spec": {"replicas": kwargs["replicas"]}},
                    )
                    result = monitor_pool(
                        client,
                        namespace=kwargs["namespace"],
                        run_id=resource_run_id,
                        replicas=kwargs["replicas"],
                        requested_strategy=requested,
                        ready_when=kwargs["ready_when"],
                        timeout=timeout,
                        poll_interval=kwargs["poll_interval"],
                        started=started,
                    )
                    if result["status"] == "succeeded" and kwargs["recovery_delete"]:
                        selector = f"{RUN_LABEL}={resource_run_id}"
                        vms = client.list("vm", kwargs["namespace"], selector)
                        victims = sorted(vms, key=lambda item: item["metadata"]["name"])[
                            : kwargs["recovery_delete"]
                        ]
                        baseline_uids = [item["metadata"].get("uid") for item in vms]
                        recovery_started = time.monotonic()
                        client.delete(
                            "vm",
                            [item["metadata"]["name"] for item in victims],
                            kwargs["namespace"],
                            wait=False,
                        )
                        result["recovery"] = monitor_pool_recovery(
                            client,
                            namespace=kwargs["namespace"],
                            run_id=resource_run_id,
                            replicas=kwargs["replicas"],
                            ready_when=kwargs["ready_when"],
                            baseline_uids=baseline_uids,
                            replacements=kwargs["recovery_delete"],
                            timeout=timeout,
                            poll_interval=kwargs["poll_interval"],
                            started=recovery_started,
                        )
                        if result["recovery"]["status"] != "succeeded":
                            result["status"] = "failed"
                            result["error"] = result["recovery"]["error"]
                except BenchmarkError as exc:
                    result = {
                        "requested_strategy": requested,
                        "status": "failed",
                        "error": str(exc),
                    }
                result.update(
                    {
                        "iteration": iteration,
                        "pool": pool_name,
                        "replicas": kwargs["replicas"],
                        "resource_run_id": resource_run_id,
                    }
                )
                rows.append(result)
                color = "green" if result["status"] == "succeeded" else "red"
                console.print(
                    f"  [{color}]run {iteration}: {result['status']}"
                    f" ({_seconds(result.get('all_ready_seconds'))})[/{color}]"
                )
                if kwargs["cleanup"]:
                    cleanup_run(client, kwargs["namespace"], resource_run_id)

        payload["results"] = rows
        payload["summary"] = aggregate_results(rows, "all_ready_seconds")
        payload["completed_at"] = utc_now()
        _print_results(rows)
        if kwargs["save_results"]:
            base = kwargs["results_folder"]
            if not base.is_absolute():
                base = repo_root / base
            directory = save_results(base, "vm-pool", run_id, payload, rows)
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
