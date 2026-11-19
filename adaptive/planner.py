#!/usr/bin/env python3
"""Adaptive VirtBench round planner.

This module is intentionally small and deterministic. The LLM skill is the
user interface; this file does the repeatable cluster math, command execution,
result parsing, and decision logging.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

try:
    from adaptive.analysis import analysis_to_markdown, build_analysis
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from analysis import analysis_to_markdown, build_analysis
from virtbench.common import find_repo_root


DEFAULT_VM_CPU = 1.0
DEFAULT_VM_MEMORY_GIB = 4.0
DEFAULT_HEADROOM = 0.15


@dataclass
class RoundResult:
    round: int
    scenario: str
    batch_size: int
    reuse_action: str
    command: str
    commands: List[str] = field(default_factory=list)
    result_dirs: List[str] = field(default_factory=list)
    total_vms: Optional[int] = None
    return_code: Optional[int] = None
    result_dir: Optional[str] = None
    success_rate: Optional[float] = None
    key_metric: Optional[str] = None
    workflow_durations_sec: Dict[str, Optional[float]] = field(default_factory=dict)
    decision: str = ""
    next_batch: Optional[int] = None
    next_total_vms: Optional[int] = None
    notes: List[str] = field(default_factory=list)


class PlannerError(RuntimeError):
    """Raised when planning or execution cannot continue."""


def run_json(cmd: List[str], env: Dict[str, str]) -> Optional[dict]:
    proc = subprocess.run(cmd, env=env, text=True, capture_output=True)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def detect_storage_driver(env: Dict[str, str]) -> Optional[str]:
    """Best-effort result label detection. Keeps result folders meaningful."""
    data = run_json(["kubectl", "get", "storagecluster", "-A", "-o", "json"], env)
    for item in (data or {}).get("items", []):
        version = item.get("status", {}).get("version")
        image = item.get("spec", {}).get("image") or ""
        if not version and ":" in image:
            version = image.rsplit(":", 1)[-1]
        if version:
            return f"portworx-{version}"
    return None


def resolve_repo_path(repo_root: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def parse_cpu(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if text.endswith("m"):
        return float(text[:-1]) / 1000.0
    return float(text)


def parse_memory_gib(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0

    match = re.match(r"^([0-9.]+)([A-Za-z]*)$", text)
    if not match:
        return 0.0

    number = float(match.group(1))
    suffix = match.group(2)
    binary = {
        "Ki": 1 / (1024 * 1024),
        "Mi": 1 / 1024,
        "Gi": 1,
        "Ti": 1024,
        "Pi": 1024 * 1024,
    }
    decimal = {
        "K": 1000 / (1024**3),
        "M": 1000**2 / (1024**3),
        "G": 1000**3 / (1024**3),
        "T": 1000**4 / (1024**3),
        "P": 1000**5 / (1024**3),
    }
    if suffix in binary:
        return number * binary[suffix]
    if suffix in decimal:
        return number * decimal[suffix]
    if suffix == "":
        return number / (1024**3)
    return 0.0


def format_float(value: float) -> float:
    return round(value, 3)


def worker_nodes(nodes_json: Optional[dict]) -> List[dict]:
    if not nodes_json:
        return []
    workers = []
    fallback = []
    for node in nodes_json.get("items", []):
        spec = node.get("spec", {})
        if spec.get("unschedulable"):
            continue
        labels = node.get("metadata", {}).get("labels", {})
        is_control = (
            "node-role.kubernetes.io/master" in labels
            or "node-role.kubernetes.io/control-plane" in labels
        )
        if not is_control:
            workers.append(node)
        fallback.append(node)
    return workers or fallback


def pod_requests_by_node(pods_json: Optional[dict]) -> Dict[str, Dict[str, float]]:
    requests: Dict[str, Dict[str, float]] = {}
    if not pods_json:
        return requests

    for pod in pods_json.get("items", []):
        node = pod.get("spec", {}).get("nodeName")
        if not node:
            continue
        phase = pod.get("status", {}).get("phase")
        if phase in {"Succeeded", "Failed"}:
            continue

        cpu = 0.0
        memory = 0.0
        for container in pod.get("spec", {}).get("containers", []):
            req = container.get("resources", {}).get("requests", {})
            cpu += parse_cpu(req.get("cpu"))
            memory += parse_memory_gib(req.get("memory"))

        init_cpu = 0.0
        init_memory = 0.0
        for container in pod.get("spec", {}).get("initContainers", []):
            req = container.get("resources", {}).get("requests", {})
            init_cpu = max(init_cpu, parse_cpu(req.get("cpu")))
            init_memory = max(init_memory, parse_memory_gib(req.get("memory")))

        entry = requests.setdefault(node, {"cpu": 0.0, "memory_gib": 0.0})
        entry["cpu"] += max(cpu, init_cpu)
        entry["memory_gib"] += max(memory, init_memory)

    return requests


def template_vm_cost(template_path: Optional[Path]) -> Tuple[float, float]:
    if not template_path or not template_path.exists():
        return DEFAULT_VM_CPU, DEFAULT_VM_MEMORY_GIB

    try:
        docs = list(yaml.safe_load_all(template_path.read_text()))
    except Exception:
        return DEFAULT_VM_CPU, DEFAULT_VM_MEMORY_GIB

    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") != "VirtualMachine":
            continue
        domain = (
            doc.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("domain", {})
        )
        resources = domain.get("resources", {}).get("requests", {})
        cpu = parse_cpu(resources.get("cpu"))
        if cpu <= 0:
            cpu = parse_cpu(domain.get("cpu", {}).get("cores")) or DEFAULT_VM_CPU

        memory = parse_memory_gib(resources.get("memory"))
        if memory <= 0:
            memory = parse_memory_gib(domain.get("memory", {}).get("guest")) or DEFAULT_VM_MEMORY_GIB
        return cpu, memory

    return DEFAULT_VM_CPU, DEFAULT_VM_MEMORY_GIB


def estimate_capacity(env: Dict[str, str], template_path: Optional[Path]) -> Dict[str, Any]:
    nodes = run_json(["kubectl", "get", "nodes", "-o", "json"], env)
    pods = run_json(["kubectl", "get", "pods", "-A", "-o", "json"], env)
    selected_nodes = worker_nodes(nodes)
    requests = pod_requests_by_node(pods)
    vm_cpu, vm_memory = template_vm_cost(template_path)

    raw_cpu = raw_mem = alloc_cpu = alloc_mem = free_cpu = free_mem = 0.0
    per_node = []
    for node in selected_nodes:
        name = node.get("metadata", {}).get("name", "")
        capacity = node.get("status", {}).get("capacity", {})
        allocatable = node.get("status", {}).get("allocatable", {})
        node_raw_cpu = parse_cpu(capacity.get("cpu"))
        node_raw_mem = parse_memory_gib(capacity.get("memory"))
        node_alloc_cpu = parse_cpu(allocatable.get("cpu"))
        node_alloc_mem = parse_memory_gib(allocatable.get("memory"))
        used = requests.get(name, {"cpu": 0.0, "memory_gib": 0.0})
        node_free_cpu = max(0.0, node_alloc_cpu - used["cpu"])
        node_free_mem = max(0.0, node_alloc_mem - used["memory_gib"])

        raw_cpu += node_raw_cpu
        raw_mem += node_raw_mem
        alloc_cpu += node_alloc_cpu
        alloc_mem += node_alloc_mem
        free_cpu += node_free_cpu
        free_mem += node_free_mem
        per_node.append(
            {
                "name": name,
                "allocatable_cpu_cores": format_float(node_alloc_cpu),
                "allocatable_memory_gib": format_float(node_alloc_mem),
                "requested_cpu_cores": format_float(used["cpu"]),
                "requested_memory_gib": format_float(used["memory_gib"]),
                "free_cpu_cores": format_float(node_free_cpu),
                "free_memory_gib": format_float(node_free_mem),
            }
        )

    effective_cpu = free_cpu * (1 - DEFAULT_HEADROOM)
    effective_mem = free_mem * (1 - DEFAULT_HEADROOM)
    cpu_ceiling = math.floor(effective_cpu / vm_cpu) if vm_cpu > 0 else 0
    mem_ceiling = math.floor(effective_mem / vm_memory) if vm_memory > 0 else 0
    ceiling = max(1, min(cpu_ceiling, mem_ceiling)) if selected_nodes else 1

    return {
        "worker_nodes": len(selected_nodes),
        "raw_cpu_cores": format_float(raw_cpu),
        "raw_memory_gib": format_float(raw_mem),
        "allocatable_cpu_cores": format_float(alloc_cpu),
        "allocatable_memory_gib": format_float(alloc_mem),
        "free_cpu_cores": format_float(free_cpu),
        "free_memory_gib": format_float(free_mem),
        "headroom_fraction": DEFAULT_HEADROOM,
        "vm_cpu_request_cores": format_float(vm_cpu),
        "vm_memory_request_gib": format_float(vm_memory),
        "estimated_vm_ceiling": ceiling,
        "per_node": per_node,
    }


def namespace_index(namespace: str, prefix: str) -> Optional[int]:
    match = re.match(rf"^{re.escape(prefix)}-(\d+)$", namespace)
    if not match:
        return None
    return int(match.group(1))


def inventory_vms(env: Dict[str, str], namespace_prefix: str, vm_name: str) -> Dict[str, Any]:
    vms = run_json(["kubectl", "get", "vm", "-A", "-o", "json"], env) or {"items": []}
    vmis = run_json(["kubectl", "get", "vmi", "-A", "-o", "json"], env) or {"items": []}
    vmi_by_ns_name = {
        (item.get("metadata", {}).get("namespace"), item.get("metadata", {}).get("name")): item
        for item in vmis.get("items", [])
    }

    healthy_indexes = []
    existing_indexes = []
    unhealthy = []
    for vm in vms.get("items", []):
        meta = vm.get("metadata", {})
        ns = meta.get("namespace", "")
        name = meta.get("name", "")
        idx = namespace_index(ns, namespace_prefix)
        if idx is None or name != vm_name:
            continue
        existing_indexes.append(idx)
        vmi = vmi_by_ns_name.get((ns, name))
        phase = (vmi or {}).get("status", {}).get("phase")
        printable = vm.get("status", {}).get("printableStatus")
        if phase == "Running":
            healthy_indexes.append(idx)
        else:
            unhealthy.append({"namespace": ns, "vm": name, "phase": phase, "status": printable})

    return {
        "existing_indexes": sorted(existing_indexes),
        "healthy_indexes": sorted(healthy_indexes),
        "healthy_count": len(healthy_indexes),
        "unhealthy": unhealthy,
    }


def latest_summary_file(results_dir: Path, names: Iterable[str], started_at: float) -> Optional[Path]:
    candidates = []
    wanted = set(names)
    if not results_dir.exists():
        return None
    for path in results_dir.rglob("*.json"):
        if path.name in wanted and path.stat().st_mtime >= started_at - 1:
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_json_file(path: Optional[Path]) -> Optional[dict]:
    if not path:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def metric(summary: dict, name: str, field: str = "max") -> Optional[float]:
    for item in summary.get("metrics", []):
        if item.get("metric") == name:
            value = item.get(field)
            return float(value) if value is not None else None
    return None


def summary_duration(summary_path: Optional[Path], *keys: str) -> Optional[float]:
    summary = load_json_file(summary_path)
    if not summary:
        return None
    for key in keys:
        value = summary.get(key)
        if value is not None:
            return float(value)
    return None


def seconds_metric(name: str, value: Optional[float]) -> str:
    label = {
        "reachability_max": "reachability max",
        "running_max": "running max",
        "vmim_duration_max": "VMIM duration max",
        "observed_duration_max": "observed duration max",
    }.get(name, name.replace("_", " "))
    if value is None:
        return f"{label}: N/A"
    return f"{label}: {round(value, 2)}s"


def parse_bootstorm_result(summary_path: Optional[Path]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    summary = load_json_file(summary_path)
    if not summary:
        return None, None, None
    total = summary.get("total_vms") or 0
    successful = summary.get("successful") or 0
    success_rate = successful / total if total else 0.0
    max_ping = metric(summary, "ping_time_sec", "max")
    max_running = metric(summary, "running_time_sec", "max")
    key = seconds_metric("reachability_max", max_ping) if max_ping is not None else seconds_metric("running_max", max_running)
    return success_rate, key, str(summary_path.parent)


def parse_migration_result(summary_path: Optional[Path]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    summary = load_json_file(summary_path)
    if not summary:
        return None, None, None
    total = summary.get("total_vms") or 0
    successful = summary.get("successful") or 0
    success_rate = successful / total if total else 0.0
    max_vmim = metric(summary, "vmim_time_sec", "max")
    max_observed = metric(summary, "observed_time_sec", "max")
    key = seconds_metric("vmim_duration_max", max_vmim) if max_vmim is not None else seconds_metric("observed_duration_max", max_observed)
    return success_rate, key, str(summary_path.parent)


def parse_creation_result(summary_path: Optional[Path]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    summary = load_json_file(summary_path)
    if not summary:
        return None, None, None
    total = summary.get("total_vms") or 0
    successful = summary.get("successful") or 0
    success_rate = successful / total if total else 0.0
    max_ping = metric(summary, "ping_time_sec", "max")
    max_running = metric(summary, "running_time_sec", "max")
    key = seconds_metric("reachability_max", max_ping) if max_ping is not None else seconds_metric("running_max", max_running)
    return success_rate, key, str(summary_path.parent)


def parse_disk_ops_result(summary_path: Optional[Path]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    data = load_json_file(summary_path)
    if not data:
        return None, None, None
    per_vm = data.get("per_vm_results", []) or []
    total = len(per_vm)
    successes = sum(1 for item in per_vm if item.get("success"))
    success_rate = successes / total if total else None
    hotplug = data.get("hotplug") or {}
    coldplug = data.get("coldplug") or {}
    key = (
        f"hotplug_avg_total_time={hotplug.get('avg_total_time')} "
        f"coldplug_avg_total_time={coldplug.get('avg_total_time')}"
    )
    return success_rate, key.strip(), str(summary_path.parent)


def parse_fio_result(summary_path: Optional[Path]) -> Tuple[Optional[float], Optional[str], Optional[str], float]:
    summary = load_json_file(summary_path)
    if not summary:
        return None, None, None, 0.0
    total = summary.get("total_vms") or 0
    successful = summary.get("successful") or 0
    success_rate = successful / total if total else 0.0
    result_dir = summary_path.parent
    per_vm = load_json_file(result_dir / "fio_benchmark_results.json") or []
    total_iops = 0.0
    total_bw = 0.0
    if isinstance(per_vm, list):
        for row in per_vm:
            if not row.get("success"):
                continue
            total_iops += float(row.get("read_iops") or 0) + float(row.get("write_iops") or 0)
            total_bw += float(row.get("read_bw_mibps") or 0) + float(row.get("write_bw_mibps") or 0)
    key = f"total_iops={round(total_iops, 2)} total_bw_mibps={round(total_bw, 2)}"
    score = total_iops if total_iops > 0 else total_bw
    return success_rate, key, str(result_dir), score


def initial_batch(scenario: str, ceiling: int, max_vms: Optional[int]) -> int:
    if scenario == "migration":
        base = min(10, max(1, math.floor(ceiling * 0.15)))
    elif scenario == "fio":
        base = min(10, max(1, math.floor(ceiling * 0.20)))
    else:
        base = min(20, max(1, math.floor(ceiling * 0.25)))
    if max_vms:
        base = min(base, max_vms)
    return max(1, base)


def cap_total_vms(ceiling: int, max_vms: Optional[int]) -> int:
    cap = min(ceiling, max_vms) if max_vms else ceiling
    return max(1, cap)


def plan_total_vm_counts(ceiling: int, max_vms: Optional[int], rounds: int) -> List[int]:
    """Choose standalone total VM counts spread across the requested rounds."""
    cap = cap_total_vms(ceiling, max_vms)
    rounds = max(1, rounds)
    totals: List[int] = []
    for idx in range(1, rounds + 1):
        total = max(1, math.ceil(cap * idx / rounds))
        if not totals or total > totals[-1]:
            totals.append(total)
    return totals


def capacity_recommendation(best_stable: int, ceiling: int, max_vms: Optional[int]) -> Tuple[str, str, int]:
    """Explain whether the observed stable total hit a user cap or the cluster estimate."""
    effective_ceiling = cap_total_vms(ceiling, max_vms)
    if best_stable <= 0:
        return "No stable VM total was observed.", "no_stable_total", effective_ceiling
    if max_vms and max_vms < ceiling and best_stable >= effective_ceiling:
        return (
            f"{best_stable} VMs was the largest stable total observed, capped by the "
            f"user limit (--max-vms {max_vms}). Cluster estimate was {ceiling} VMs; "
            "increase --max-vms to probe higher.",
            "user_cap_reached",
            effective_ceiling,
        )
    if best_stable >= ceiling:
        return (
            f"{best_stable} VMs was the largest stable total observed at the estimated "
            f"cluster ceiling ({ceiling} VMs).",
            "cluster_ceiling_reached",
            effective_ceiling,
        )
    if best_stable >= effective_ceiling:
        return (
            f"{best_stable} VMs was the largest stable total observed at the effective "
            f"ceiling ({effective_ceiling} VMs).",
            "effective_ceiling_reached",
            effective_ceiling,
        )
    return (
        f"{best_stable} VMs was the largest stable total observed before the effective "
        f"ceiling of {effective_ceiling} VMs.",
        "below_effective_ceiling",
        effective_ceiling,
    )


def choose_next_batch(
    current: int,
    ceiling: int,
    max_vms: Optional[int],
    success_rate: Optional[float],
    last_good: Optional[int],
) -> Tuple[int, str]:
    cap = min(ceiling, max_vms) if max_vms else ceiling
    cap = max(1, cap)
    if success_rate is not None and success_rate >= 0.98:
        next_batch = min(cap, max(current + 1, current * 2))
        if next_batch == current:
            return current, "hold_at_estimated_ceiling"
        return next_batch, "increase"
    if last_good:
        next_batch = max(1, math.floor((last_good + current) / 2))
        return next_batch, "reduce_to_midpoint"
    return max(1, math.floor(current / 2)), "reduce"


def command_to_str(cmd: List[str]) -> str:
    return " ".join(str(part) for part in cmd)


def commands_to_str(commands: List[List[str]]) -> str:
    return "\n".join(command_to_str(cmd) for cmd in commands)


def run_command(cmd: List[str], cwd: Path, env: Dict[str, str], execute: bool) -> Optional[int]:
    print(f"{'Running' if execute else 'Dry-run'}: {command_to_str(cmd)}")
    if not execute:
        return None
    proc = subprocess.run(cmd, cwd=str(cwd), env=env)
    return proc.returncode


def round_args(args: argparse.Namespace, round_no: int) -> argparse.Namespace:
    scoped = copy.copy(args)
    if args.scenario == "vm-workflow-basic" and args.fresh_round_namespaces:
        scoped.namespace_prefix = f"{args.namespace_prefix}-r{round_no}"
    return scoped


def wait_for_vmis_ready(
    env: Dict[str, str],
    namespace_prefix: str,
    vm_name: str,
    end: int,
    timeout: int,
) -> Tuple[bool, List[int]]:
    deadline = time.time() + timeout
    expected = set(range(1, end + 1))
    missing = sorted(expected)
    while time.time() < deadline:
        data = run_json(["kubectl", "get", "vmi", "-A", "-o", "json"], env) or {}
        ready: set[int] = set()
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            namespace = metadata.get("namespace", "")
            name = metadata.get("name")
            match = re.fullmatch(rf"{re.escape(namespace_prefix)}-(\d+)", namespace)
            if not match or name != vm_name:
                continue
            idx = int(match.group(1))
            if idx not in expected:
                continue
            status = item.get("status", {})
            is_ready = any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in status.get("conditions", [])
            )
            if status.get("phase") == "Running" and is_ready:
                ready.add(idx)
        missing = sorted(expected - ready)
        if not missing:
            return True, []
        time.sleep(5)
    return False, missing


def cleanup_namespace_range(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    end: int,
    execute: bool,
) -> Optional[int]:
    namespaces = [f"{args.namespace_prefix}-{idx}" for idx in range(1, end + 1)]
    delete_vm_cmds = [
        [
            "kubectl",
            "delete",
            "vm",
            args.vm_name,
            "-n",
            namespace,
            "--ignore-not-found=true",
            "--wait=false",
        ]
        for namespace in namespaces
    ]
    cmd = [
        "kubectl",
        "delete",
        "namespace",
        *namespaces,
        "--ignore-not-found=true",
        "--wait=false",
    ]
    print(
        f"{'Running' if execute else 'Dry-run'} cleanup: delete VM {args.vm_name} "
        f"in {len(namespaces)} namespaces, then {command_to_str(cmd)}"
    )
    if not execute:
        return None
    return_code = 0
    for delete_vm_cmd in delete_vm_cmds:
        proc = subprocess.run(
            delete_vm_cmd,
            cwd=str(repo_root),
            env=env,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            return_code = proc.returncode

    deadline = time.time() + min(args.ping_timeout, 300)
    namespace_set = set(namespaces)
    remaining = []
    while time.time() < deadline:
        data = run_json(["kubectl", "get", "vmi", "-A", "-o", "json"], env) or {}
        remaining = [
            item
            for item in data.get("items", [])
            if item.get("metadata", {}).get("namespace") in namespace_set
            and item.get("metadata", {}).get("name") == args.vm_name
        ]
        if not remaining:
            break
        time.sleep(5)
    if remaining:
        print(f"Cleanup warning: {len(remaining)} VMIs still present when namespace cleanup was requested.")

    proc = subprocess.run(cmd, cwd=str(repo_root), env=env)
    return return_code or proc.returncode


def virtbench_cmd(repo_root: Path, kubeconfig: Optional[str]) -> List[str]:
    cmd = [sys.executable, "-m", "virtbench.cli"]
    if kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig])
    return cmd


def build_datasource_delta_cmd(
    args: argparse.Namespace,
    repo_root: Path,
    start: int,
    end: int,
) -> List[str]:
    if not args.storage_class:
        raise PlannerError("--storage-class is required to create missing VMs")
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "datasource-clone",
        "--start",
        str(start),
        "--end",
        str(end),
        "--namespace-prefix",
        args.namespace_prefix,
        "--vm-name",
        args.vm_name,
        "--storage-class",
        args.storage_class,
        "--vm-template",
        args.vm_template,
        "--concurrency",
        str(min(50, max(1, end - start + 1))),
        "--namespace-batch-size",
        str(min(50, max(1, end - start + 1))),
        "--ping-timeout",
        str(args.ping_timeout),
        "--save-results",
        "--results-folder",
        args.results_folder,
    ]
    if args.storage_driver:
        cmd.extend(["--storage-driver", args.storage_driver])
    return cmd


def build_rebalance_cmd(args: argparse.Namespace, repo_root: Path, end: int) -> List[str]:
    return virtbench_cmd(repo_root, args.kubeconfig) + [
        "vm-ops",
        "rebalance-vms",
        "--vm-name",
        args.vm_name,
        "--namespace-prefix",
        args.namespace_prefix,
        "--start",
        "1",
        "--end",
        str(end),
    ]


def build_multi_source_migration_cmd(
    args: argparse.Namespace,
    repo_root: Path,
    end: int,
    concurrency: int,
) -> List[str]:
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "migration",
        "--start",
        "1",
        "--end",
        str(end),
        "--namespace-prefix",
        args.namespace_prefix,
        "--vm-name",
        args.vm_name,
        "--source-nodes",
        "all",
        "--source-nodes-use-range",
        "--concurrency",
        str(concurrency),
        "--poll-interval",
        str(args.poll_interval),
        "--migration-timeout",
        str(args.migration_timeout),
        "--ping-timeout",
        str(args.ping_timeout),
        "--save-results",
        "--results-folder",
        args.results_folder,
    ]
    if args.storage_driver:
        cmd.extend(["--storage-driver", args.storage_driver])
    return cmd


def create_datasource_delta(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    start: int,
    end: int,
    execute: bool,
) -> Optional[int]:
    if start > end:
        return None
    cmd = build_datasource_delta_cmd(args, repo_root, start, end)
    return run_command(cmd, repo_root, env, execute)


def plan_reuse(inventory: Dict[str, Any], target: int) -> Tuple[str, int]:
    healthy = set(inventory.get("healthy_indexes", []))
    if len(healthy) >= target and all(i in healthy for i in range(1, target + 1)):
        return "reuse_existing", target + 1
    existing = set(inventory.get("existing_indexes", []))
    contiguous = 0
    for idx in range(1, target + 1):
        if idx in existing:
            contiguous = idx
        else:
            break
    if contiguous > 0:
        return "create_delta", contiguous + 1
    return "none", 1


def plan_running_reuse(inventory: Dict[str, Any], target: int) -> Tuple[str, int, List[int]]:
    """Plan reuse for workflows that require already-running VMIs."""
    healthy = set(inventory.get("healthy_indexes", []))
    existing = set(inventory.get("existing_indexes", []))

    healthy_contiguous = 0
    for idx in range(1, target + 1):
        if idx in healthy:
            healthy_contiguous = idx
        else:
            break

    blockers = sorted(
        idx for idx in existing
        if 1 <= idx <= target and idx not in healthy
    )
    if blockers:
        return "cleanup_required", healthy_contiguous + 1, blockers

    if healthy_contiguous >= target:
        return "reuse_existing", target + 1, []
    if healthy_contiguous > 0:
        return "create_delta", healthy_contiguous + 1, []
    if existing:
        scattered = sorted(idx for idx in existing if 1 <= idx <= target)
        if scattered:
            return "cleanup_required", 1, scattered
    return "none", 1, []


def run_bootstorm_round(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    round_no: int,
    batch: int,
) -> RoundResult:
    inventory = inventory_vms(env, args.namespace_prefix, args.vm_name)
    reuse_action, create_start = plan_reuse(inventory, batch)
    notes = []

    if reuse_action == "create_delta":
        notes.append(f"Reusing existing namespaces 1-{create_start - 1}; creating {create_start}-{batch}.")
        rc = create_datasource_delta(args, repo_root, env, create_start, batch, args.execute)
        if rc not in (None, 0):
            return RoundResult(
                round=round_no,
                scenario="bootstorm",
                batch_size=batch,
                total_vms=batch,
                reuse_action="cleanup_and_retry",
                command="create delta failed",
                return_code=rc,
                decision="stop",
                notes=notes,
            )
    elif reuse_action == "none":
        notes.append(f"No reusable healthy VM range found; creating 1-{batch}.")

    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "datasource-clone",
        "--start",
        "1",
        "--end",
        str(batch),
        "--namespace-prefix",
        args.namespace_prefix,
        "--vm-name",
        args.vm_name,
        "--storage-class",
        args.storage_class,
        "--vm-template",
        args.vm_template,
        "--boot-storm",
        "--save-results",
        "--results-folder",
        args.results_folder,
        "--concurrency",
        str(min(batch, args.max_concurrency)),
        "--ping-timeout",
        str(args.ping_timeout),
    ]
    if args.storage_driver:
        cmd.extend(["--storage-driver", args.storage_driver])
    if reuse_action in {"reuse_existing", "create_delta"}:
        cmd.extend(["--skip-vm-creation", "--skip-namespace-creation"])

    started = time.time()
    rc = run_command(cmd, repo_root, env, args.execute)
    summary = latest_summary_file(Path(args.results_folder), ["summary_boot_storm_results.json"], started)
    success_rate, key_metric, result_dir = parse_bootstorm_result(summary)
    return RoundResult(
        round=round_no,
        scenario="bootstorm",
        batch_size=batch,
        total_vms=batch,
        reuse_action=reuse_action,
        command=command_to_str(cmd),
        return_code=rc,
        result_dir=result_dir,
        success_rate=success_rate,
        key_metric=key_metric,
        notes=notes,
    )


def run_migration_round(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    round_no: int,
    batch: int,
) -> RoundResult:
    inventory = inventory_vms(env, args.namespace_prefix, args.vm_name)
    reuse_action, create_start, blockers = plan_running_reuse(inventory, batch)
    notes = []
    commands: List[List[str]] = []
    result_dirs: List[str] = []
    if reuse_action == "cleanup_required":
        notes.append(
            "Migration requires running VMIs. Found existing non-running or non-contiguous "
            f"VM namespaces in target range: {blockers}."
        )
        return RoundResult(
            round=round_no,
            scenario="migration",
            batch_size=batch,
            total_vms=batch,
            reuse_action="cleanup_required",
            command="migration precheck failed",
            commands=[],
            success_rate=0.0 if args.execute else None,
            decision="stop",
            notes=notes,
        )
    if reuse_action != "reuse_existing":
        notes.append(f"Preparing VM range for migration by creating missing namespaces {create_start}-{batch}.")
        create_started = time.time()
        create_cmd = build_datasource_delta_cmd(args, repo_root, create_start, batch)
        commands.append(create_cmd)
        rc = run_command(create_cmd, repo_root, env, args.execute)
        create_summary = latest_summary_file(Path(args.results_folder), ["summary_vm_creation_results.json"], create_started)
        create_success, create_key, create_dir = parse_creation_result(create_summary)
        if create_summary:
            result_dirs.append(str(create_summary.parent))
        if rc not in (None, 0):
            return RoundResult(
                round=round_no,
                scenario="migration",
                batch_size=batch,
                total_vms=batch,
                reuse_action="cleanup_and_retry",
                command="create delta failed",
                commands=[command_to_str(cmd) for cmd in commands],
                result_dirs=result_dirs,
                return_code=rc,
                result_dir=create_dir,
                success_rate=create_success if create_success is not None else (0.0 if args.execute else None),
                key_metric=f"creation: {create_key}" if create_key else None,
                decision="stop",
                notes=notes,
            )

    concurrency = min(batch, args.max_concurrency, max(1, math.ceil(batch / 2)))
    rebalance_cmd = build_rebalance_cmd(args, repo_root, batch)
    commands.append(rebalance_cmd)
    rebalance_rc = run_command(rebalance_cmd, repo_root, env, args.execute)
    if rebalance_rc not in (None, 0):
        return RoundResult(
            round=round_no,
            scenario="migration",
            batch_size=batch,
            total_vms=batch,
            reuse_action=reuse_action,
            command=commands_to_str(commands),
            commands=[command_to_str(cmd) for cmd in commands],
            result_dirs=result_dirs,
            return_code=rebalance_rc,
            success_rate=0.0 if args.execute else None,
            decision="stop",
            notes=notes + ["Rebalance failed before migration."],
        )

    cmd = build_multi_source_migration_cmd(args, repo_root, batch, concurrency)
    commands.append(cmd)
    notes.append("Default migration workflow: rebalance selected VMs, then run multi-source-node migration.")

    started = time.time()
    rc = run_command(cmd, repo_root, env, args.execute)
    summary = latest_summary_file(Path(args.results_folder), ["summary_migration_results.json"], started)
    success_rate, key_metric, result_dir = parse_migration_result(summary)
    if result_dir:
        result_dirs.append(result_dir)
    return RoundResult(
        round=round_no,
        scenario="migration",
        batch_size=batch,
        total_vms=batch,
        reuse_action=reuse_action,
        command=commands_to_str(commands),
        commands=[command_to_str(c) for c in commands],
        result_dirs=result_dirs,
        return_code=rc,
        result_dir=result_dir,
        success_rate=success_rate,
        key_metric=key_metric,
        notes=notes,
    )


def run_validation_workflow_round(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    round_no: int,
    batch: int,
) -> RoundResult:
    """Creation -> boot storm -> rebalance -> multi-source migration for one AI round."""
    workflow_started = time.time()
    args = round_args(args, round_no)
    inventory = inventory_vms(env, args.namespace_prefix, args.vm_name)
    reuse_action, create_start, blockers = plan_running_reuse(inventory, batch)
    notes = [
        "Full workflow round: create full VM range, boot storm existing VMs, rebalance, then multi-source migration.",
        f"Round namespace prefix: {args.namespace_prefix}.",
    ]
    commands: List[List[str]] = []
    result_dirs: List[str] = []
    success_rates: List[float] = []
    key_parts: List[str] = []
    workflow_durations: Dict[str, Optional[float]] = {
        "total_workflow_duration_sec": None,
        "vm_creation_duration_sec": None,
        "bootstorm_duration_sec": None,
        "migration_duration_sec": None,
    }

    if reuse_action == "cleanup_required":
        notes.append(
            "Found existing non-running or non-contiguous VM namespaces in target range: "
            f"{blockers}."
        )
        return RoundResult(
            round=round_no,
            scenario="vm-workflow-basic",
            batch_size=batch,
            total_vms=batch,
            reuse_action="cleanup_required",
            command="workflow precheck failed",
            success_rate=0.0 if args.execute else None,
            decision="stop",
            notes=notes,
        )

    if reuse_action != "reuse_existing":
        create_started = time.time()
        create_cmd = build_datasource_delta_cmd(args, repo_root, create_start, batch)
        commands.append(create_cmd)
        notes.append(f"Creating missing namespaces {create_start}-{batch}.")
        rc = run_command(create_cmd, repo_root, env, args.execute)
        create_summary = latest_summary_file(Path(args.results_folder), ["summary_vm_creation_results.json"], create_started)
        create_success, create_key, create_dir = parse_creation_result(create_summary)
        workflow_durations["vm_creation_duration_sec"] = summary_duration(create_summary, "total_test_duration_sec")
        if create_dir:
            result_dirs.append(create_dir)
        if create_success is not None:
            success_rates.append(create_success)
        if create_key:
            key_parts.append(f"creation: {create_key}")
        if rc not in (None, 0):
            return RoundResult(
                round=round_no,
                scenario="vm-workflow-basic",
                batch_size=batch,
                reuse_action="cleanup_and_retry",
                command=commands_to_str(commands),
                commands=[command_to_str(cmd) for cmd in commands],
                result_dirs=result_dirs,
                total_vms=batch,
                return_code=rc,
                result_dir=create_dir,
                success_rate=create_success if create_success is not None else (0.0 if args.execute else None),
                decision="stop",
                key_metric="; ".join(key_parts) if key_parts else None,
                workflow_durations_sec=workflow_durations,
                notes=notes,
            )

    boot_cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "datasource-clone",
        "--start",
        "1",
        "--end",
        str(batch),
        "--namespace-prefix",
        args.namespace_prefix,
        "--vm-name",
        args.vm_name,
        "--storage-class",
        args.storage_class,
        "--vm-template",
        args.vm_template,
        "--boot-storm",
        "--skip-vm-creation",
        "--skip-namespace-creation",
        "--save-results",
        "--results-folder",
        args.results_folder,
        "--concurrency",
        str(min(batch, args.max_concurrency)),
        "--ping-timeout",
        str(args.ping_timeout),
    ]
    if args.storage_driver:
        boot_cmd.extend(["--storage-driver", args.storage_driver])
    commands.append(boot_cmd)
    boot_started = time.time()
    boot_rc = run_command(boot_cmd, repo_root, env, args.execute)
    boot_summary = latest_summary_file(Path(args.results_folder), ["summary_boot_storm_results.json"], boot_started)
    boot_success, boot_key, boot_dir = parse_bootstorm_result(boot_summary)
    workflow_durations["bootstorm_duration_sec"] = summary_duration(boot_summary, "total_test_duration_sec")
    if boot_dir:
        result_dirs.append(boot_dir)
    if boot_success is not None:
        success_rates.append(boot_success)
    if boot_key:
        key_parts.append(f"bootstorm: {boot_key}")
    if boot_rc not in (None, 0):
        return RoundResult(
            round=round_no,
            scenario="vm-workflow-basic",
            batch_size=batch,
            reuse_action=reuse_action,
            command=commands_to_str(commands),
            commands=[command_to_str(cmd) for cmd in commands],
            result_dirs=result_dirs,
            total_vms=batch,
            return_code=boot_rc,
            result_dir=boot_dir,
            success_rate=boot_success if boot_success is not None else (0.0 if args.execute else None),
            decision="stop",
            key_metric="; ".join(key_parts) if key_parts else None,
            workflow_durations_sec=workflow_durations,
            notes=notes,
        )

    rebalance_cmd = build_rebalance_cmd(args, repo_root, batch)
    commands.append(rebalance_cmd)
    rebalance_rc = run_command(rebalance_cmd, repo_root, env, args.execute)
    if rebalance_rc not in (None, 0):
        return RoundResult(
            round=round_no,
            scenario="vm-workflow-basic",
            batch_size=batch,
            reuse_action=reuse_action,
            command=commands_to_str(commands),
            commands=[command_to_str(cmd) for cmd in commands],
            result_dirs=result_dirs,
            total_vms=batch,
            return_code=rebalance_rc,
            result_dir=boot_dir,
            success_rate=min(success_rates) if success_rates else (0.0 if args.execute else None),
            decision="stop",
            key_metric="; ".join(key_parts) if key_parts else None,
            workflow_durations_sec=workflow_durations,
            notes=notes + ["Rebalance failed before migration."],
        )

    if args.execute:
        ready, missing = wait_for_vmis_ready(
            env,
            args.namespace_prefix,
            args.vm_name,
            batch,
            min(args.ping_timeout, 1200),
        )
        if not ready:
            return RoundResult(
                round=round_no,
                scenario="vm-workflow-basic",
                batch_size=batch,
                reuse_action=reuse_action,
                command=commands_to_str(commands),
                commands=[command_to_str(cmd) for cmd in commands],
                result_dirs=result_dirs,
                total_vms=batch,
                return_code=1,
                result_dir=boot_dir,
                success_rate=min(success_rates) if success_rates else 0.0,
                decision="stop",
                key_metric="; ".join(key_parts) if key_parts else None,
                workflow_durations_sec=workflow_durations,
                notes=notes + [f"Timed out waiting for VMIs to become ready after rebalance: {missing}."],
            )
        notes.append("All selected VMIs were Running/Ready after rebalance before migration.")

    concurrency = min(batch, args.max_concurrency, max(1, math.ceil(batch / 2)))
    migration_cmd = build_multi_source_migration_cmd(args, repo_root, batch, concurrency)
    commands.append(migration_cmd)
    migration_started = time.time()
    migration_rc = run_command(migration_cmd, repo_root, env, args.execute)
    migration_summary = latest_summary_file(Path(args.results_folder), ["summary_migration_results.json"], migration_started)
    migration_success, migration_key, migration_dir = parse_migration_result(migration_summary)
    workflow_durations["migration_duration_sec"] = summary_duration(migration_summary, "total_migration_duration_sec")
    workflow_durations["total_workflow_duration_sec"] = round(time.time() - workflow_started, 2) if args.execute else None
    if migration_dir:
        result_dirs.append(migration_dir)
    if migration_success is not None:
        success_rates.append(migration_success)
    if migration_key:
        key_parts.append(f"migration: {migration_key}")

    success_rate = min(success_rates) if success_rates else None
    cleanup_rc = None
    if args.cleanup:
        cleanup_rc = cleanup_namespace_range(args, repo_root, env, batch, args.execute)
        if cleanup_rc not in (None, 0):
            notes.append(f"Cleanup failed with return code {cleanup_rc}.")
        else:
            notes.append("Requested VM and namespace cleanup after workflow completion.")

    return RoundResult(
        round=round_no,
        scenario="vm-workflow-basic",
        batch_size=batch,
        reuse_action="fresh_round_cleanup" if args.cleanup else reuse_action,
        command=commands_to_str(commands),
        commands=[command_to_str(cmd) for cmd in commands],
        result_dirs=result_dirs,
        total_vms=batch,
        return_code=cleanup_rc if cleanup_rc not in (None, 0) else migration_rc,
        result_dir=migration_dir or boot_dir,
        success_rate=success_rate,
        key_metric="; ".join(key_parts) if key_parts else None,
        workflow_durations_sec=workflow_durations,
        notes=notes,
    )


def run_vm_creation_round(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    round_no: int,
    batch: int,
) -> RoundResult:
    prefix = f"{args.namespace_prefix}-r{round_no}"
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "datasource-clone",
        "--start",
        "1",
        "--end",
        str(batch),
        "--namespace-prefix",
        prefix,
        "--vm-name",
        args.vm_name,
        "--storage-class",
        args.storage_class,
        "--vm-template",
        args.vm_template,
        "--concurrency",
        str(min(batch, args.max_concurrency)),
        "--namespace-batch-size",
        str(min(50, batch)),
        "--ping-timeout",
        str(args.ping_timeout),
        "--save-results",
        "--results-folder",
        args.results_folder,
    ]
    if args.storage_driver:
        cmd.extend(["--storage-driver", args.storage_driver])
    if args.cleanup:
        cmd.extend(["--cleanup", "--yes"])

    started = time.time()
    rc = run_command(cmd, repo_root, env, args.execute)
    summary = latest_summary_file(Path(args.results_folder), ["summary_vm_creation_results.json"], started)
    success_rate, key_metric, result_dir = parse_creation_result(summary)
    return RoundResult(
        round=round_no,
        scenario="vm-creation",
        batch_size=batch,
        reuse_action="fresh_total_vm_count",
        command=command_to_str(cmd),
        total_vms=batch,
        return_code=rc,
        result_dir=result_dir,
        success_rate=success_rate,
        key_metric=key_metric,
        notes=["VM creation rounds are standalone total VM counts; existing VMs are not reused."],
    )


def run_disk_ops_round(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    round_no: int,
    batch: int,
) -> RoundResult:
    prefix = f"{args.namespace_prefix}-r{round_no}" if args.fresh_round_namespaces else args.namespace_prefix
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "disk-ops",
        "--start",
        "1",
        "--end",
        str(batch),
        "--storage-class",
        args.storage_class,
        "--operation",
        args.disk_operation,
        "--disks",
        str(args.disks),
        "--namespace-prefix",
        prefix,
        "--vm-name",
        args.vm_name,
        "--vm-template",
        args.vm_template,
        "--concurrency",
        str(min(batch, args.max_concurrency)),
        "--results-dir",
        args.results_folder,
        "--px-version",
        args.storage_driver or "ai",
        "--save-results",
        "--create-vms",
    ]
    if args.skip_validation:
        cmd.append("--skip-validation")
    if args.cleanup:
        cmd.append("--cleanup")

    started = time.time()
    rc = run_command(cmd, repo_root, env, args.execute)
    summary = latest_summary_file(Path(args.results_folder), ["disk_ops_results.json"], started)
    success_rate, key_metric, result_dir = parse_disk_ops_result(summary)
    return RoundResult(
        round=round_no,
        scenario="disk-ops",
        batch_size=batch,
        reuse_action="fresh_round",
        command=command_to_str(cmd),
        return_code=rc,
        result_dir=result_dir,
        success_rate=success_rate,
        key_metric=key_metric,
        notes=[f"operation={args.disk_operation}", f"disks={args.disks}"],
    )


def run_assist_command(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    scenario: str,
    cmd: List[str],
    batch_size: int,
) -> RoundResult:
    started = time.time()
    rc = run_command(cmd, repo_root, env, args.execute)
    result_dir = None
    key_metric = None
    success_rate = 1.0 if rc == 0 else 0.0 if rc is not None else None
    if scenario == "chaos":
        summary = latest_summary_file(Path(args.results_folder), ["summary_chaos_benchmark.json"], started)
        data = load_json_file(summary)
        if data:
            result_dir = str(summary.parent)
            key_metric = f"total_vms={data.get('total_vms')} capacity_reached={data.get('capacity_reached')}"
    elif scenario == "failure-recovery":
        summary = latest_summary_file(Path(args.results_folder), ["summary_failure_recovery_results.json"], started)
        data = load_json_file(summary)
        if data:
            result_dir = str(summary.parent)
            total = data.get("total_vms") or 0
            success = data.get("successful") or 0
            success_rate = success / total if total else success_rate
            key_metric = f"successful={success}/{total}"
    elif scenario == "elbencho":
        summary = latest_summary_file(Path(args.results_folder), ["aggregated_results.json"], started)
        data = load_json_file(summary)
        if data:
            result_dir = str(summary.parent)
            agg = data.get("aggregated", {})
            key_metric = f"total_iops={agg.get('total_iops')} throughput_bytes={agg.get('total_throughput_bytes')}"
    return RoundResult(
        round=1,
        scenario=scenario,
        batch_size=batch_size,
        reuse_action="assisted_execution",
        command=command_to_str(cmd),
        return_code=rc,
        result_dir=result_dir,
        success_rate=success_rate,
        key_metric=key_metric,
        decision="assist_execute",
    )


def build_chaos_command(args: argparse.Namespace, repo_root: Path) -> Tuple[List[str], int]:
    if not args.storage_class:
        raise PlannerError("--storage-class is required for chaos")
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "chaos-benchmark",
        "--storage-class",
        args.storage_class,
        "--concurrency",
        str(args.chaos_concurrency),
        "--vms",
        str(args.chaos_vms),
        "--max-iterations",
        str(args.rounds),
        "--results-dir",
        args.results_folder,
        "--save-results",
    ]
    if args.storage_driver:
        cmd.extend(["--storage-driver", args.storage_driver])
    if args.cleanup:
        cmd.append("--cleanup")
    return cmd, args.chaos_vms


def build_failure_recovery_command(args: argparse.Namespace, repo_root: Path) -> Tuple[List[str], int]:
    if not args.node:
        raise PlannerError("--node is required for failure-recovery")
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "failure-recovery",
        "--mode",
        args.mode,
        "--node",
        args.node,
        "--vm-name",
        args.vm_name,
        "--namespace-prefix",
        args.namespace_prefix,
        "--results-folder",
        args.results_folder,
        "--save-results",
    ]
    if args.storage_class:
        cmd.extend(["--storage-class", args.storage_class])
    if args.storage_driver:
        cmd.extend(["--storage-driver", args.storage_driver])
    if args.far_config:
        cmd.extend(["--far-config", args.far_config])
    if args.skip_ping:
        cmd.append("--skip-ping")
    if args.cleanup:
        cmd.extend(["--cleanup", "--yes"])
    return cmd, 0


def build_elbencho_command(args: argparse.Namespace, repo_root: Path, batch: int) -> Tuple[List[str], int]:
    if args.elbencho_action in {"deploy", "run-all"} and not args.elbencho_vm_template:
        raise PlannerError("--elbencho-vm-template is required for elbencho deploy/run-all")
    if args.elbencho_action == "run-all" and args.elbencho_duration <= 0:
        raise PlannerError("--elbencho-duration is required for elbencho run-all")
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "elbencho",
        "--namespace-prefix",
        args.namespace_prefix,
        "--start",
        "1",
        "--end",
        str(batch),
        "--vm-name",
        args.vm_name,
        "--action",
        args.elbencho_action,
        "--duration",
        str(args.elbencho_duration),
        "--block-size",
        args.elbencho_block_size,
        "--iodepth",
        str(args.elbencho_iodepth),
        "--threads",
        str(args.elbencho_threads),
        "--results-dir",
        args.results_folder,
        "--storage-driver",
        args.storage_driver or "ai",
        "--concurrency",
        str(min(batch, args.max_concurrency)),
        "--save-results",
    ]
    if args.elbencho_vm_template:
        cmd.extend(["--vm-template", args.elbencho_vm_template])
    if args.elbencho_iops > 0:
        cmd.extend(["--iops", str(args.elbencho_iops)])
    else:
        rwmixpct = args.elbencho_rwmixpct if args.elbencho_rwmixpct is not None else 100
        if rwmixpct > 0:
            cmd.extend(["--rwmixpct", str(rwmixpct)])
    return cmd, batch


def build_validate_command(args: argparse.Namespace, repo_root: Path) -> Tuple[List[str], int]:
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + ["validate-cluster"]
    if args.storage_class:
        cmd.extend(["--storage-class", args.storage_class])
    if args.datasource:
        cmd.extend(["--datasource", args.datasource])
    if args.datasource_namespace:
        cmd.extend(["--datasource-namespace", args.datasource_namespace])
    if args.quick:
        cmd.append("--quick")
    return cmd, 0


def fio_config(objective: str, round_no: int, batch: int, best_batch: Optional[int]) -> Dict[str, Any]:
    selected_batch = best_batch or batch
    if objective == "max-bandwidth":
        configs = [
            {"batch": batch, "rw": "read", "bs": "1M", "iodepth": 32, "numjobs": 1},
            {"batch": min(batch * 2, selected_batch * 2), "rw": "read", "bs": "1M", "iodepth": 32, "numjobs": 1},
            {"batch": selected_batch, "rw": "read", "bs": "1M", "iodepth": 64, "numjobs": 1},
            {"batch": selected_batch, "rw": "write", "bs": "1M", "iodepth": 32, "numjobs": 1},
            {"batch": selected_batch, "rw": "read", "bs": "256k", "iodepth": 64, "numjobs": 2},
            {"batch": selected_batch, "rw": "read", "bs": "1M", "iodepth": 32, "numjobs": 1},
        ]
    else:
        configs = [
            {"batch": batch, "rw": "randread", "bs": "4k", "iodepth": 64, "numjobs": 4},
            {"batch": min(batch * 2, selected_batch * 2), "rw": "randread", "bs": "4k", "iodepth": 64, "numjobs": 4},
            {"batch": selected_batch, "rw": "randread", "bs": "4k", "iodepth": 128, "numjobs": 4},
            {"batch": selected_batch, "rw": "randread", "bs": "4k", "iodepth": 64, "numjobs": 8},
            {"batch": selected_batch, "rw": "randwrite", "bs": "4k", "iodepth": 64, "numjobs": 4},
            {"batch": selected_batch, "rw": "randread", "bs": "4k", "iodepth": 64, "numjobs": 4},
        ]
    idx = min(round_no - 1, len(configs) - 1)
    return configs[idx]


def run_fio_round(
    args: argparse.Namespace,
    repo_root: Path,
    env: Dict[str, str],
    round_no: int,
    batch: int,
    best_batch: Optional[int],
) -> Tuple[RoundResult, float]:
    cfg = fio_config(args.objective, round_no, batch, best_batch)
    cfg_batch = max(1, min(int(cfg["batch"]), args.max_vms or int(cfg["batch"])))
    prefix = f"{args.namespace_prefix}-r{round_no}"
    cmd = virtbench_cmd(repo_root, args.kubeconfig) + [
        "fio",
        "--action",
        "run-all",
        "--start",
        "1",
        "--end",
        str(cfg_batch),
        "--namespace-prefix",
        prefix,
        "--vm-name",
        args.vm_name,
        "--storage-class",
        args.storage_class,
        "--vm-template",
        args.fio_vm_template,
        "--fio-runtime",
        str(args.fio_runtime),
        "--fio-rw",
        cfg["rw"],
        "--fio-bs",
        cfg["bs"],
        "--fio-iodepth",
        str(cfg["iodepth"]),
        "--fio-numjobs",
        str(cfg["numjobs"]),
        "--fio-size",
        args.fio_size,
        "--concurrency",
        str(min(cfg_batch, args.max_concurrency)),
        "--save-results",
        "--cleanup",
        "--results-dir",
        args.results_folder,
        "--storage-driver",
        args.storage_driver or "ai",
    ]

    started = time.time()
    rc = run_command(cmd, repo_root, env, args.execute)
    summary = latest_summary_file(Path(args.results_folder), ["summary_fio_benchmark.json"], started)
    success_rate, key_metric, result_dir, score = parse_fio_result(summary)
    return (
        RoundResult(
            round=round_no,
            scenario="fio",
            batch_size=cfg_batch,
            reuse_action="fresh_run_cleanup",
            command=command_to_str(cmd),
            return_code=rc,
            result_dir=result_dir,
            success_rate=success_rate,
            key_metric=key_metric,
            notes=[
                f"objective={args.objective}",
                f"rw={cfg['rw']} bs={cfg['bs']} iodepth={cfg['iodepth']} numjobs={cfg['numjobs']}",
            ],
        ),
        score,
    )


def write_ai_rounds(run_dir: Path, payload: Dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "virtbench_ai_rounds.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    analysis = build_analysis(payload)
    (run_dir / "virtbench_ai_analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    (run_dir / "virtbench_ai_analysis.md").write_text(analysis_to_markdown(analysis), encoding="utf-8")
    summary = payload.get("summary", {})
    best_stable_total = summary.get("best_stable_total_vms", summary.get("best_stable_batch", "N/A"))
    lines = [
        f"# VirtBench AI Rounds - {payload.get('scenario')}",
        "",
        f"- Rounds requested: {payload.get('rounds_requested')}",
        f"- Best stable total VMs: {best_stable_total}",
        f"- Recommendation: {summary.get('final_recommendation', 'N/A')}",
        "",
        "| Round | Total VMs | Reuse | Success | Metric Summary | Decision |",
        "| --- | ---: | --- | ---: | --- | --- |",
    ]
    for item in payload.get("rounds", []):
        total_vms = item.get("total_vms", item.get("batch_size"))
        lines.append(
            f"| {item.get('round')} | {total_vms} | {item.get('reuse_action')} | "
            f"{item.get('success_rate', 'N/A')} | {item.get('key_metric', '')} | {item.get('decision', '')} |"
        )
    (run_dir / "virtbench_ai_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def normalize_scenario(value: str) -> str:
    normalized = value.lower().replace("_", "-")
    aliases = {
        "boot-storm": "bootstorm",
        "live-migration": "migration",
        "migrate": "migration",
        "creation-bootstorm-migration": "vm-workflow-basic",
        "creation-bootstorm-live-migration": "vm-workflow-basic",
        "bootstorm-migration": "vm-workflow-basic",
        "full-workflow": "vm-workflow-basic",
        "recommended-workflow": "vm-workflow-basic",
        "validation": "vm-workflow-basic",
        "validation-workflow": "vm-workflow-basic",
        "vm-workflow-basic": "vm-workflow-basic",
        "vmcreation": "vm-creation",
        "vm-creation": "vm-creation",
        "datasource-clone": "vm-creation",
        "creation": "vm-creation",
        "diskops": "disk-ops",
        "disk-operations": "disk-ops",
        "failure": "failure-recovery",
        "recovery": "failure-recovery",
        "failure-recovery": "failure-recovery",
        "chaos-benchmark": "chaos",
        "cluster-validation": "validate-cluster",
        "validate": "validate-cluster",
        "fio-max-iops": "fio",
        "fio-max-bandwidth": "fio",
    }
    return aliases.get(normalized, normalized)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan and run adaptive VirtBench rounds.")
    parser.add_argument(
        "--scenario",
        required=True,
        help="bootstorm, vm-creation, migration, vm-workflow-basic, fio, disk-ops, chaos, failure-recovery, elbencho, or validate-cluster",
    )
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--objective", default="max-iops", choices=["max-iops", "max-bandwidth"])
    parser.add_argument("--execute", action="store_true", help="Run commands. Default is dry-run.")
    parser.add_argument("--kubeconfig", default=os.environ.get("KUBECONFIG"))
    parser.add_argument("--storage-class")
    parser.add_argument("--storage-driver")
    parser.add_argument("--namespace-prefix")
    parser.add_argument("--vm-name", default="rhel-9-vm")
    parser.add_argument("--vm-template", default="examples/vm-templates/rhel9-vm-datasource.yaml")
    parser.add_argument("--fio-vm-template", default="examples/vm-templates/fio-vm-template.yaml")
    parser.add_argument("--results-folder", default="results")
    parser.add_argument("--max-vms", type=int)
    parser.add_argument("--max-concurrency", type=int, default=50)
    parser.add_argument("--ping-timeout", type=int, default=1200)
    parser.add_argument("--poll-interval", type=int, default=1)
    parser.add_argument("--migration-timeout", type=int, default=600)
    parser.add_argument("--fio-runtime", type=int, default=120)
    parser.add_argument("--fio-size", default="10G")
    parser.add_argument("--fresh-round-namespaces", action="store_true", default=None,
                        help="Use a unique namespace prefix per adaptive round where supported.")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--node")
    parser.add_argument("--mode", default="monitor", choices=["monitor", "manual", "far-operator"])
    parser.add_argument("--far-config")
    parser.add_argument("--skip-ping", action="store_true")
    parser.add_argument("--chaos-concurrency", type=int, default=2)
    parser.add_argument("--chaos-vms", type=int, default=5)
    parser.add_argument("--disk-operation", default="all", choices=["hotplug", "coldplug", "all"])
    parser.add_argument("--disks", type=int, default=1)
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--elbencho-action", default="run-all",
                        choices=["run-all", "deploy", "start", "stop", "restart", "status",
                                 "stop-all", "change-workload", "gather-results", "cleanup"])
    parser.add_argument("--elbencho-vm-template")
    parser.add_argument("--elbencho-duration", type=int, default=300)
    parser.add_argument("--elbencho-iops", type=int, default=0)
    parser.add_argument("--elbencho-rwmixpct", type=int)
    parser.add_argument("--elbencho-block-size", default="4K")
    parser.add_argument("--elbencho-iodepth", type=int, default=1)
    parser.add_argument("--elbencho-threads", type=int, default=0)
    parser.add_argument("--datasource", default="rhel9")
    parser.add_argument("--datasource-namespace", default="openshift-virtualization-os-images")
    parser.add_argument("--quick", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    args.scenario = normalize_scenario(args.scenario)
    supported = {
        "bootstorm",
        "vm-creation",
        "migration",
        "vm-workflow-basic",
        "fio",
        "disk-ops",
        "chaos",
        "failure-recovery",
        "elbencho",
        "validate-cluster",
    }
    if args.scenario not in supported:
        raise PlannerError(f"Unsupported scenario: {args.scenario}")
    if args.rounds < 1:
        raise PlannerError("--rounds must be greater than zero")
    if args.scenario in {"bootstorm", "vm-creation", "vm-workflow-basic", "fio", "disk-ops", "chaos"} and not args.storage_class:
        raise PlannerError(f"--storage-class is required for {args.scenario}")

    repo_root = find_repo_root()
    env = os.environ.copy()
    if args.kubeconfig:
        args.kubeconfig = str(Path(args.kubeconfig).expanduser())
        env["KUBECONFIG"] = args.kubeconfig

    if not args.namespace_prefix:
        if args.scenario == "fio":
            args.namespace_prefix = "ai-fio"
        elif args.scenario in {"migration", "failure-recovery", "vm-workflow-basic"}:
            args.namespace_prefix = "ai-bootstorm"
        else:
            args.namespace_prefix = f"ai-{args.scenario}"
    if args.scenario == "fio" and args.vm_name == "rhel-9-vm":
        args.vm_name = "fio-vm"
    if args.scenario == "disk-ops" and args.vm_name == "rhel-9-vm":
        args.vm_name = "disk-ops-vm"
    if args.scenario == "disk-ops" and args.vm_template == "examples/vm-templates/rhel9-vm-datasource.yaml":
        args.vm_template = "disk-ops-benchmark/vm-template.yaml"
    if args.scenario == "elbencho" and args.vm_name == "rhel-9-vm":
        args.vm_name = "rhel-elbencho-1"
    if args.scenario == "vm-creation":
        args.fresh_round_namespaces = True
        args.cleanup = True
    elif args.scenario == "vm-workflow-basic":
        args.fresh_round_namespaces = True
        args.cleanup = True
    elif args.fresh_round_namespaces is None:
        args.fresh_round_namespaces = args.scenario == "disk-ops"

    args.results_folder = str(resolve_repo_path(repo_root, args.results_folder))
    args.vm_template = str(resolve_repo_path(repo_root, args.vm_template))
    args.fio_vm_template = str(resolve_repo_path(repo_root, args.fio_vm_template))
    if args.elbencho_vm_template:
        args.elbencho_vm_template = str(resolve_repo_path(repo_root, args.elbencho_vm_template))
    storage_driver_source = "provided"
    if not args.storage_driver:
        detected_driver = detect_storage_driver(env)
        args.storage_driver = detected_driver or "ai"
        storage_driver_source = "detected" if detected_driver else "fallback"

    template_path = Path(args.fio_vm_template if args.scenario == "fio" else args.vm_template)
    capacity = estimate_capacity(env, template_path)
    ceiling = int(capacity.get("estimated_vm_ceiling") or 1)
    if args.execute and capacity.get("worker_nodes", 0) == 0:
        raise PlannerError("Unable to read schedulable nodes from the cluster; refusing to execute with a guessed capacity.")
    planned_total_vms: List[int] = []
    if args.scenario == "vm-creation":
        planned_total_vms = plan_total_vm_counts(ceiling, args.max_vms, args.rounds)
        batch = planned_total_vms[0]
    elif args.scenario == "vm-workflow-basic":
        planned_total_vms = plan_total_vm_counts(ceiling, args.max_vms, args.rounds)
        batch = planned_total_vms[0]
    else:
        batch = initial_batch(args.scenario, ceiling, args.max_vms)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ai_run_dir = Path(args.results_folder) / "ai-runs" / f"{timestamp}_{args.scenario}"

    print("VirtBench AI planner")
    print(f"Scenario: {args.scenario}")
    print(f"Rounds: {args.rounds}")
    print(f"Execute: {args.execute}")
    print(f"Estimated VM ceiling: {ceiling}")
    if planned_total_vms:
        print(f"Planned total VM counts: {planned_total_vms}")
    else:
        print(f"Initial batch: {batch}")

    rounds: List[RoundResult] = []
    last_good: Optional[int] = None
    best_stable = 0
    best_fio_score = 0.0
    best_fio_batch: Optional[int] = None
    best_fio_metric: Optional[str] = None

    if args.scenario in {"chaos", "failure-recovery", "elbencho", "validate-cluster"}:
        if args.scenario == "chaos":
            cmd, assist_batch = build_chaos_command(args, repo_root)
        elif args.scenario == "failure-recovery":
            cmd, assist_batch = build_failure_recovery_command(args, repo_root)
        elif args.scenario == "elbencho":
            assist_batch = min(args.max_vms or batch, ceiling)
            cmd, assist_batch = build_elbencho_command(args, repo_root, assist_batch)
        else:
            cmd, assist_batch = build_validate_command(args, repo_root)
        result = run_assist_command(args, repo_root, env, args.scenario, cmd, assist_batch)
        rounds.append(result)
        recommendation = f"{args.scenario} assisted execution completed with return code {result.return_code}."
        payload = {
            "schema_version": 1,
            "scenario": args.scenario,
            "objective": None,
            "rounds_requested": args.rounds,
            "created_at": datetime.now().isoformat(),
            "execute": args.execute,
            "mode": "execute" if args.execute else "dry-run",
            "namespace_prefix": args.namespace_prefix,
            "vm_name": args.vm_name,
            "storage_class": args.storage_class,
            "storage_driver": args.storage_driver,
            "storage_driver_source": storage_driver_source,
            "capacity": capacity,
            "rounds": [r.__dict__ for r in rounds],
            "summary": {
                "best_stable_total_vms": assist_batch if args.execute else None,
                "best_stable_batch": assist_batch if args.execute else None,
                "best_fio_metric": None,
                "final_recommendation": recommendation,
            },
        }
        write_ai_rounds(ai_run_dir, payload)
        print(f"AI rounds written to: {ai_run_dir / 'virtbench_ai_rounds.json'}")
        if args.execute and any(r.return_code not in (0, None) for r in rounds):
            return 1
        return 0

    adaptive_round_count = len(planned_total_vms) if planned_total_vms else args.rounds
    for round_no in range(1, adaptive_round_count + 1):
        if args.scenario in {"vm-creation", "vm-workflow-basic"} and planned_total_vms:
            batch = planned_total_vms[min(round_no - 1, len(planned_total_vms) - 1)]

        if args.scenario == "bootstorm":
            result = run_bootstorm_round(args, repo_root, env, round_no, batch)
        elif args.scenario == "vm-creation":
            result = run_vm_creation_round(args, repo_root, env, round_no, batch)
        elif args.scenario == "migration":
            result = run_migration_round(args, repo_root, env, round_no, batch)
        elif args.scenario == "vm-workflow-basic":
            result = run_validation_workflow_round(args, repo_root, env, round_no, batch)
        elif args.scenario == "disk-ops":
            result = run_disk_ops_round(args, repo_root, env, round_no, batch)
        else:
            result, score = run_fio_round(args, repo_root, env, round_no, batch, best_fio_batch)
            if result.success_rate is not None and result.success_rate >= 0.98 and score >= best_fio_score:
                best_fio_score = score
                best_fio_batch = result.batch_size
                best_fio_metric = result.key_metric

        if result.decision == "stop":
            result.next_batch = None
            rounds.append(result)
            break

        if args.execute and result.return_code not in (0, None):
            effective_success_rate = 0.0
        else:
            effective_success_rate = result.success_rate

        dry_run_planning = False
        if not args.execute and effective_success_rate is None:
            effective_success_rate = 1.0
            dry_run_planning = True

        if args.execute and effective_success_rate is not None and effective_success_rate >= 0.98:
            last_good = result.batch_size
            best_stable = max(best_stable, result.batch_size)

        if args.scenario in {"vm-creation", "vm-workflow-basic"} and planned_total_vms:
            if args.execute and effective_success_rate is not None and effective_success_rate < 0.98:
                result.decision = "stop_after_failed_total"
                result.next_batch = None
                result.next_total_vms = None
                rounds.append(result)
                break
            next_total = planned_total_vms[round_no] if round_no < len(planned_total_vms) else None
            if args.scenario == "vm-workflow-basic":
                if next_total and next_total > result.batch_size:
                    result.decision = "increase_total_vms"
                elif next_total:
                    result.decision = "repeat_full_workflow_total"
                else:
                    result.decision = "finish"
            else:
                result.decision = "increase_total_vms" if next_total else "finish"
            result.next_batch = next_total
            result.next_total_vms = next_total
            rounds.append(result)
            batch = next_total or result.batch_size
            continue

        next_batch, decision = choose_next_batch(
            result.batch_size,
            ceiling,
            args.max_vms,
            effective_success_rate,
            last_good,
        )
        if args.scenario == "fio":
            decision = "continue_fio_search" if round_no < args.rounds else "finish"
        elif dry_run_planning:
            decision = f"dry_run_plan_{decision}"
        result.decision = decision
        result.next_batch = next_batch if round_no < args.rounds else None
        result.next_total_vms = result.next_batch
        rounds.append(result)
        batch = next_batch

    effective_vm_ceiling = cap_total_vms(ceiling, args.max_vms)
    recommendation_reason = "not_executed"
    if not args.execute:
        recommendation = "Dry-run only. Commands and planned total VM counts were generated, but no stable total was observed."
        best_stable_batch = None
    elif args.scenario == "fio":
        recommendation = f"Best {args.objective} run: {best_fio_metric or 'not available'}."
        best_stable_batch = best_fio_batch or best_stable
        recommendation_reason = "fio_objective_best"
    else:
        recommendation, recommendation_reason, effective_vm_ceiling = capacity_recommendation(
            best_stable,
            ceiling,
            args.max_vms,
        )
        best_stable_batch = best_stable

    payload = {
        "schema_version": 1,
        "scenario": args.scenario,
        "objective": args.objective if args.scenario == "fio" else None,
        "rounds_requested": args.rounds,
        "created_at": datetime.now().isoformat(),
        "execute": args.execute,
        "mode": "execute" if args.execute else "dry-run",
        "namespace_prefix": args.namespace_prefix,
        "vm_name": args.vm_name,
        "storage_class": args.storage_class,
        "storage_driver": args.storage_driver,
        "storage_driver_source": storage_driver_source,
        "capacity": capacity,
        "user_max_vms": args.max_vms,
        "effective_vm_ceiling": effective_vm_ceiling,
        "planned_total_vm_counts": planned_total_vms,
        "rounds": [r.__dict__ for r in rounds],
        "summary": {
            "best_stable_total_vms": best_stable_batch,
            "best_stable_batch": best_stable_batch,
            "best_fio_metric": best_fio_metric,
            "user_max_vms": args.max_vms,
            "effective_vm_ceiling": effective_vm_ceiling,
            "recommendation_reason": recommendation_reason,
            "final_recommendation": recommendation,
        },
    }
    write_ai_rounds(ai_run_dir, payload)
    print(f"AI rounds written to: {ai_run_dir / 'virtbench_ai_rounds.json'}")
    if args.execute and any(r.return_code not in (0, None) for r in rounds):
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PlannerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
