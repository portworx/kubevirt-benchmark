"""Shared CDI root-disk and VirtualMachinePool benchmark support.

The module intentionally uses ``kubectl`` instead of a Python Kubernetes client so
the new workloads follow VirtBench's existing authentication and kubeconfig model.
All resources created by these benchmarks carry a unique run label, which makes
monitoring and cleanup deterministic.
"""

from __future__ import annotations

import copy
import csv
import json
import math
import re
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml


CLONE_STRATEGIES = ("csi-clone", "snapshot", "copy")
RUN_LABEL = "virtbench.io/run"
BENCHMARK_LABEL = "virtbench.io/benchmark"
STRATEGY_LABEL = "virtbench.io/clone-strategy"
ROOT_DISK_LABEL = "virtbench.io/root-disk"
CLONE_TYPE_ANNOTATION = "cdi.kubevirt.io/cloneType"
CLONE_FALLBACK_ANNOTATION = "cdi.kubevirt.io/cloneFallbackReason"


class BenchmarkError(RuntimeError):
    """Raised when a benchmark cannot safely continue."""


class Kubectl:
    """Small, injectable kubectl JSON client."""

    def __init__(self, binary: str = "kubectl") -> None:
        self.binary = binary

    def run(
        self,
        args: Sequence[str],
        *,
        obj: Optional[Dict[str, Any]] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        command = [self.binary, *args]
        payload = json.dumps(obj) if obj is not None else None
        try:
            result = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BenchmarkError(
                f"{self.binary} was not found; install kubectl or make it available in PATH"
            ) from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BenchmarkError(f"{' '.join(command)} failed: {detail}")
        return result

    def get(
        self, resource: str, name: str, namespace: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        args = ["get", resource, name]
        if namespace:
            args.extend(["-n", namespace])
        args.extend(["-o", "json"])
        result = self.run(args, check=False)
        if result.returncode != 0:
            if "not found" in (result.stderr or "").lower():
                return None
            detail = (result.stderr or result.stdout).strip()
            raise BenchmarkError(f"{' '.join([self.binary, *args])} failed: {detail}")
        return json.loads(result.stdout)

    def list(
        self,
        resource: str,
        namespace: Optional[str] = None,
        labels: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        args = ["get", resource]
        if namespace:
            args.extend(["-n", namespace])
        if labels:
            args.extend(["-l", labels])
        args.extend(["-o", "json"])
        result = self.run(args)
        return json.loads(result.stdout).get("items", [])

    def create(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        result = self.run(["create", "-f", "-", "-o", "json"], obj=obj)
        return json.loads(result.stdout)

    def patch(
        self,
        resource: str,
        name: str,
        namespace: str,
        patch: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = self.run(
            [
                "patch",
                resource,
                name,
                "-n",
                namespace,
                "--type",
                "merge",
                "-p",
                json.dumps(patch),
                "-o",
                "json",
            ]
        )
        return json.loads(result.stdout)

    def delete(
        self,
        resource: str,
        names: Sequence[str],
        namespace: Optional[str] = None,
        wait: bool = False,
    ) -> None:
        if not names:
            return
        args = ["delete", resource, *names]
        if namespace:
            args.extend(["-n", namespace])
        args.append(f"--wait={'true' if wait else 'false'}")
        args.append("--ignore-not-found=true")
        self.run(args)

    def can_i(self, verb: str, resource: str, namespace: str) -> bool:
        result = self.run(
            ["auth", "can-i", verb, resource, "-n", namespace], check=False
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "yes"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id(prefix: str) -> str:
    return safe_name(f"{prefix}-{uuid.uuid4().hex[:8]}")


def safe_name(value: str, limit: int = 63) -> str:
    """Return a DNS-label-safe Kubernetes name."""
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    value = re.sub(r"-+", "-", value)
    if not value:
        value = "virtbench"
    return value[:limit].rstrip("-")


def parse_duration(value: str) -> float:
    """Parse a compact duration such as 30s, 15m, 4h, or a seconds value."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(value))
    if not match:
        raise BenchmarkError(f"Invalid timeout {value!r}; use seconds, m, or h")
    amount = float(match.group(1))
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    return amount * multiplier


def split_reference(value: str, default_namespace: str) -> Tuple[str, str]:
    parts = value.split("/", 1)
    if len(parts) == 1:
        return default_namespace, parts[0]
    if not parts[0] or not parts[1]:
        raise BenchmarkError(f"Invalid resource reference {value!r}; use namespace/name")
    return parts[0], parts[1]


def load_virtual_machine(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            documents = [doc for doc in yaml.safe_load_all(stream) if doc]
    except (OSError, yaml.YAMLError) as exc:
        raise BenchmarkError(f"Unable to read VM specification {path}: {exc}") from exc
    vms = [doc for doc in documents if doc.get("kind") == "VirtualMachine"]
    if len(vms) != 1:
        raise BenchmarkError(
            f"{path} must contain exactly one VirtualMachine document; found {len(vms)}"
        )
    return vms[0]


def find_root_volume(vm: Dict[str, Any], requested: Optional[str]) -> str:
    template_spec = vm.get("spec", {}).get("template", {}).get("spec", {})
    disks = template_spec.get("domain", {}).get("devices", {}).get("disks", [])
    volumes = template_spec.get("volumes", [])
    volume_names = {item.get("name") for item in volumes}
    if requested:
        if requested not in volume_names:
            raise BenchmarkError(
                f"Root volume {requested!r} does not exist in the VM specification"
            )
        return requested
    boot_disks = [item.get("name") for item in disks if item.get("bootOrder") == 1]
    if len(boot_disks) == 1 and boot_disks[0] in volume_names:
        return boot_disks[0]
    if disks and disks[0].get("name") in volume_names:
        return disks[0]["name"]
    raise BenchmarkError("Unable to identify the root volume; provide --root-volume")


def resolve_input_path(path: Path, repo_root: Path) -> Path:
    """Resolve a user file path against the cwd first, then the repository root."""
    if path.is_absolute() or path.exists():
        return path.resolve()
    candidate = repo_root / path
    if candidate.exists():
        return candidate
    raise BenchmarkError(
        f"File {path} was not found in the current directory or in {repo_root}"
    )


def golden_reference(
    golden_image: Optional[str],
    golden_image_kind: str,
    datasource_name: str,
    datasource_namespace: str,
) -> Tuple[str, str]:
    """Return (reference, kind): --golden-image when given, else the DataSource."""
    if golden_image:
        return golden_image, golden_image_kind
    return f"{datasource_namespace}/{datasource_name}", "datasource"


def resolve_golden_image(
    client: Kubectl,
    reference: str,
    kind: str,
    default_namespace: str,
) -> Tuple[str, str, Dict[str, Any]]:
    namespace, name = split_reference(reference, default_namespace)
    if kind == "datasource":
        source = client.get("datasource", name, namespace)
        if source is None:
            raise BenchmarkError(f"DataSource {namespace}/{name} was not found")
        pvc_ref = source.get("spec", {}).get("source", {}).get("pvc")
        if not pvc_ref or not pvc_ref.get("name"):
            raise BenchmarkError(
                f"DataSource {namespace}/{name} must currently point to a PVC; "
                "snapshot-backed DataSources cannot compare all three clone strategies"
            )
        namespace = pvc_ref.get("namespace") or namespace
        name = pvc_ref["name"]
    pvc = client.get("pvc", name, namespace)
    if pvc is None:
        raise BenchmarkError(f"Golden image PVC {namespace}/{name} was not found")
    if pvc.get("status", {}).get("phase") != "Bound":
        raise BenchmarkError(f"Golden image PVC {namespace}/{name} is not Bound")
    return namespace, name, pvc


def mounted_by_pods(client: Kubectl, namespace: str, pvc_name: str) -> List[str]:
    mounted = []
    for pod in client.list("pods", namespace):
        if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
            continue
        for volume in pod.get("spec", {}).get("volumes", []):
            claim = volume.get("persistentVolumeClaim", {}).get("claimName")
            if claim == pvc_name:
                mounted.append(pod.get("metadata", {}).get("name", "unknown"))
                break
    return mounted


def ensure_namespace(client: Kubectl, namespace: str, run_id: str) -> bool:
    """Ensure a namespace exists and return True only when this run created it."""
    if client.get("namespace", namespace):
        return False
    client.create(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace, "labels": {RUN_LABEL: run_id}},
        }
    )
    return True


def snapshot_capability(client: Kubectl, storage_class: str) -> Tuple[bool, str]:
    sc = client.get("storageclass", storage_class)
    if sc is None:
        return False, f"StorageClass {storage_class} was not found"
    driver = sc.get("provisioner")
    try:
        classes = client.list("volumesnapshotclass")
    except BenchmarkError as exc:
        return False, f"VolumeSnapshot API unavailable: {exc}"
    matches = [item for item in classes if item.get("driver") == driver]
    if not matches:
        return False, f"no VolumeSnapshotClass uses CSI driver {driver}"
    return True, matches[0].get("metadata", {}).get("name", "available")


def strategy_preflight(
    client: Kubectl,
    strategies: Sequence[str],
    source_namespace: str,
    target_namespace: str,
    source_pvc: Dict[str, Any],
    target_storage_class: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    source_storage_class = source_pvc.get("spec", {}).get("storageClassName")
    if not source_storage_class:
        raise BenchmarkError("Golden image PVC does not specify a StorageClass")
    storage_class = target_storage_class or source_storage_class
    result: Dict[str, Dict[str, Any]] = {}
    for strategy in strategies:
        result[strategy] = {"supported": True, "reason": "preflight passed"}
    if "snapshot" in result:
        supported, reason = snapshot_capability(client, storage_class)
        result["snapshot"] = {"supported": supported, "reason": reason}
    if "csi-clone" in result:
        result["csi-clone"]["reason"] = (
            "CSI clone capability is verified by the attempted clone and observed cloneType"
        )
    if storage_class != source_storage_class:
        reason = (
            "optimized CDI clones require source and target to use the same StorageClass "
            f"({source_storage_class} != {storage_class})"
        )
        for strategy in ("snapshot", "csi-clone"):
            if strategy in result:
                result[strategy] = {"supported": False, "reason": reason}
    if source_namespace != target_namespace and not client.can_i(
        "create", "datavolumes/source", source_namespace
    ):
        reason = (
            f"missing create permission for datavolumes/source in {source_namespace}"
        )
        for strategy in result:
            result[strategy] = {"supported": False, "reason": reason}
    return result


def _source_storage(source_pvc: Dict[str, Any], override: Optional[str]) -> Dict[str, Any]:
    spec = source_pvc.get("spec", {})
    storage: Dict[str, Any] = {
        "resources": {
            "requests": {
                "storage": spec.get("resources", {})
                .get("requests", {})
                .get("storage")
            }
        }
    }
    if not storage["resources"]["requests"]["storage"]:
        raise BenchmarkError("Golden image PVC has no requested storage size")
    storage_class = override or spec.get("storageClassName")
    if storage_class:
        storage["storageClassName"] = storage_class
    if spec.get("accessModes"):
        storage["accessModes"] = copy.deepcopy(spec["accessModes"])
    if spec.get("volumeMode"):
        storage["volumeMode"] = spec["volumeMode"]
    return storage


def build_clone_vm(
    vm_source: Dict[str, Any],
    *,
    name: str,
    namespace: str,
    root_volume: str,
    source_namespace: str,
    source_name: str,
    source_pvc: Dict[str, Any],
    strategy: str,
    run_id: str,
    benchmark: str,
    storage_class: Optional[str] = None,
    data_volume_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a VM whose root DataVolume clones the supplied golden PVC."""
    if strategy not in CLONE_STRATEGIES:
        raise BenchmarkError(f"Unknown CDI clone strategy: {strategy}")
    vm = copy.deepcopy(vm_source)
    vm["apiVersion"] = "kubevirt.io/v1"
    vm["kind"] = "VirtualMachine"
    vm.pop("status", None)
    metadata = vm.setdefault("metadata", {})
    for server_field in (
        "uid",
        "resourceVersion",
        "creationTimestamp",
        "managedFields",
        "generation",
        "selfLink",
        "ownerReferences",
        "finalizers",
    ):
        metadata.pop(server_field, None)
    metadata["name"] = safe_name(name)
    metadata["namespace"] = namespace
    labels = metadata.setdefault("labels", {})
    labels.update(
        {RUN_LABEL: run_id, BENCHMARK_LABEL: benchmark, STRATEGY_LABEL: strategy}
    )

    spec = vm.setdefault("spec", {})
    spec.pop("running", None)
    spec["runStrategy"] = "Always"
    vmi_meta = spec.setdefault("template", {}).setdefault("metadata", {})
    vmi_meta.setdefault("labels", {}).update(labels)
    vmi_spec = spec["template"].setdefault("spec", {})
    volumes = vmi_spec.setdefault("volumes", [])
    root = next((item for item in volumes if item.get("name") == root_volume), None)
    if root is None:
        raise BenchmarkError(f"Root volume {root_volume!r} disappeared from VM template")

    old_dv_name = root.get("dataVolume", {}).get("name")
    dv_name = safe_name(data_volume_name or f"{name}-root")
    root.clear()
    root.update({"name": root_volume, "dataVolume": {"name": dv_name}})

    data_volume_templates = spec.setdefault("dataVolumeTemplates", [])
    retained = []
    for item in data_volume_templates:
        if old_dv_name and item.get("metadata", {}).get("name") == old_dv_name:
            continue
        else:
            retained.append(item)

    storage = _source_storage(source_pvc, storage_class)
    content_type = source_pvc.get("metadata", {}).get("annotations", {}).get(
        "cdi.kubevirt.io/storage.contentType", "kubevirt"
    )
    retained.append(
        {
            "metadata": {
                "name": dv_name,
                "labels": {**copy.deepcopy(labels), ROOT_DISK_LABEL: "true"},
                "annotations": {CLONE_TYPE_ANNOTATION: strategy},
            },
            "spec": {
                "source": {
                    "pvc": {"namespace": source_namespace, "name": source_name}
                },
                "storage": storage,
                "contentType": content_type,
            },
        }
    )
    spec["dataVolumeTemplates"] = retained
    return vm


def build_vm_pool(
    vm: Dict[str, Any],
    *,
    name: str,
    namespace: str,
    replicas: int,
    run_id: str,
) -> Dict[str, Any]:
    """Wrap a transformed VirtualMachine in a VirtualMachinePool."""
    if replicas < 0:
        raise BenchmarkError("Pool replica count cannot be negative")
    pool_name = safe_name(name)
    vm_spec = copy.deepcopy(vm.get("spec", {}))
    vm_spec.pop("running", None)
    vm_spec["runStrategy"] = "Always"
    vm_labels = copy.deepcopy(vm.get("metadata", {}).get("labels", {}))
    vm_labels["kubevirt.io/vmpool"] = pool_name
    vm_labels[RUN_LABEL] = run_id
    vm_spec.setdefault("template", {}).setdefault("metadata", {}).setdefault(
        "labels", {}
    ).update(vm_labels)
    return {
        "apiVersion": "pool.kubevirt.io/v1alpha1",
        "kind": "VirtualMachinePool",
        "metadata": {
            "name": pool_name,
            "namespace": namespace,
            "labels": copy.deepcopy(vm.get("metadata", {}).get("labels", {})),
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"kubevirt.io/vmpool": pool_name}},
            "virtualMachineTemplate": {
                "metadata": {"labels": vm_labels},
                "spec": vm_spec,
            },
        },
    }


def _condition_true(obj: Dict[str, Any], condition_type: str) -> bool:
    return any(
        item.get("type") == condition_type and item.get("status") == "True"
        for item in obj.get("status", {}).get("conditions", [])
    )


def is_vmi_ready(vmi: Dict[str, Any], ready_when: str) -> bool:
    if ready_when == "vmi-running":
        return vmi.get("status", {}).get("phase") == "Running"
    if ready_when == "vmi-ready":
        return _condition_true(vmi, "Ready")
    if ready_when == "guest-agent":
        return _condition_true(vmi, "AgentConnected")
    raise BenchmarkError(f"Unknown readiness condition: {ready_when}")


def observed_strategy(obj: Optional[Dict[str, Any]]) -> Optional[str]:
    if not obj:
        return None
    return obj.get("metadata", {}).get("annotations", {}).get(CLONE_TYPE_ANNOTATION)


def fallback_reason(obj: Optional[Dict[str, Any]]) -> Optional[str]:
    if not obj:
        return None
    return obj.get("metadata", {}).get("annotations", {}).get(
        CLONE_FALLBACK_ANNOTATION
    )


def condition_details(obj: Optional[Dict[str, Any]]) -> str:
    if not obj:
        return ""
    details = []
    for condition in obj.get("status", {}).get("conditions", []):
        reason = condition.get("reason")
        message = condition.get("message")
        if reason or message:
            text = ": ".join(part for part in (reason, message) if part)
            if text not in details:
                details.append(text)
    return "; ".join(details)


def monitor_single_vm(
    client: Kubectl,
    *,
    namespace: str,
    vm_name: str,
    dv_name: str,
    requested_strategy: str,
    ready_when: str,
    timeout: float,
    poll_interval: float,
    started: float,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "requested_strategy": requested_strategy,
        "observed_strategy": None,
        "status": "timeout",
        "error": None,
        "root_disk_seconds": None,
        "vmi_running_seconds": None,
        "guest_ready_seconds": None,
        "total_seconds": None,
        "fallback_reason": None,
    }
    deadline = started + timeout
    while time.monotonic() < deadline:
        now = time.monotonic()
        dv = client.get("datavolume", dv_name, namespace)
        pvc = client.get("pvc", dv_name, namespace)
        vmi = client.get("vmi", vm_name, namespace)
        if dv:
            phase = dv.get("status", {}).get("phase")
            if phase == "Failed":
                result["status"] = "failed"
                detail = condition_details(dv)
                result["error"] = "DataVolume entered Failed phase"
                if detail:
                    result["error"] += f": {detail}"
                break
            if phase == "Succeeded" and result["root_disk_seconds"] is None:
                result["root_disk_seconds"] = round(now - started, 3)
        if vmi and vmi.get("status", {}).get("phase") == "Running":
            if result["vmi_running_seconds"] is None:
                result["vmi_running_seconds"] = round(now - started, 3)
        if vmi and is_vmi_ready(vmi, ready_when):
            result["guest_ready_seconds"] = round(now - started, 3)
        if result["root_disk_seconds"] is not None and result["guest_ready_seconds"] is not None:
            result["total_seconds"] = round(now - started, 3)
            result["status"] = "succeeded"
            result["observed_strategy"] = observed_strategy(pvc) or observed_strategy(dv)
            result["fallback_reason"] = fallback_reason(pvc) or fallback_reason(dv)
            if result["observed_strategy"] != requested_strategy:
                result["status"] = "fallback"
                result["error"] = (
                    f"requested {requested_strategy}, observed "
                    f"{result['observed_strategy'] or 'unknown'}"
                )
            break
        time.sleep(poll_interval)
    if result["status"] == "timeout":
        result["error"] = f"VM did not reach {ready_when} within {timeout:.0f}s"
        dv = client.get("datavolume", dv_name, namespace)
        pvc = client.get("pvc", dv_name, namespace)
        vmi = client.get("vmi", vm_name, namespace)
        result["error"] += (
            f" (DataVolume={dv.get('status', {}).get('phase') if dv else 'missing'}, "
            f"VMI={vmi.get('status', {}).get('phase') if vmi else 'missing'})"
        )
        result["observed_strategy"] = observed_strategy(pvc) or observed_strategy(dv)
        result["fallback_reason"] = fallback_reason(pvc) or fallback_reason(dv)
    elif result["observed_strategy"] is None:
        dv = client.get("datavolume", dv_name, namespace)
        pvc = client.get("pvc", dv_name, namespace)
        result["observed_strategy"] = observed_strategy(pvc) or observed_strategy(dv)
        result["fallback_reason"] = fallback_reason(pvc) or fallback_reason(dv)
    return result


def _threshold(replica_count: int, fraction: float) -> int:
    return max(1, math.ceil(replica_count * fraction))


def monitor_pool(
    client: Kubectl,
    *,
    namespace: str,
    run_id: str,
    replicas: int,
    requested_strategy: str,
    ready_when: str,
    timeout: float,
    poll_interval: float,
    started: float,
) -> Dict[str, Any]:
    selector = f"{RUN_LABEL}={run_id}"
    root_selector = f"{selector},{ROOT_DISK_LABEL}=true"
    metrics: Dict[str, Any] = {
        "requested_strategy": requested_strategy,
        "observed_strategies": {},
        "status": "timeout",
        "error": None,
        "first_vm_object_seconds": None,
        "all_vm_objects_seconds": None,
        "first_root_disk_seconds": None,
        "all_root_disks_seconds": None,
        "first_ready_seconds": None,
        "ready_50_seconds": None,
        "ready_90_seconds": None,
        "all_ready_seconds": None,
        "peak_vm_count": 0,
        "peak_vmi_count": 0,
        "ready_replicas": 0,
        "failed_datavolumes": 0,
        "failed_datavolume_details": [],
    }
    deadline = started + timeout
    while time.monotonic() < deadline:
        now = time.monotonic()
        elapsed = round(now - started, 3)
        vms = client.list("vm", namespace, selector)
        vmis = client.list("vmi", namespace, selector)
        dvs = client.list("datavolume", namespace, root_selector)
        metrics["peak_vm_count"] = max(metrics["peak_vm_count"], len(vms))
        metrics["peak_vmi_count"] = max(metrics["peak_vmi_count"], len(vmis))
        if vms and metrics["first_vm_object_seconds"] is None:
            metrics["first_vm_object_seconds"] = elapsed
        if len(vms) >= replicas and metrics["all_vm_objects_seconds"] is None:
            metrics["all_vm_objects_seconds"] = elapsed

        succeeded_dvs = [item for item in dvs if item.get("status", {}).get("phase") == "Succeeded"]
        failed_dvs = [item for item in dvs if item.get("status", {}).get("phase") == "Failed"]
        metrics["failed_datavolumes"] = len(failed_dvs)
        metrics["failed_datavolume_details"] = [
            {
                "name": item.get("metadata", {}).get("name"),
                "conditions": condition_details(item),
            }
            for item in failed_dvs
        ]
        if succeeded_dvs and metrics["first_root_disk_seconds"] is None:
            metrics["first_root_disk_seconds"] = elapsed
        if len(succeeded_dvs) >= replicas and metrics["all_root_disks_seconds"] is None:
            metrics["all_root_disks_seconds"] = elapsed

        ready = sum(1 for item in vmis if is_vmi_ready(item, ready_when))
        metrics["ready_replicas"] = ready
        if ready >= 1 and metrics["first_ready_seconds"] is None:
            metrics["first_ready_seconds"] = elapsed
        if ready >= _threshold(replicas, 0.5) and metrics["ready_50_seconds"] is None:
            metrics["ready_50_seconds"] = elapsed
        if ready >= _threshold(replicas, 0.9) and metrics["ready_90_seconds"] is None:
            metrics["ready_90_seconds"] = elapsed
        if ready >= replicas and metrics["all_ready_seconds"] is None:
            metrics["all_ready_seconds"] = elapsed

        if failed_dvs:
            metrics["status"] = "failed"
            metrics["error"] = f"{len(failed_dvs)} DataVolume(s) failed"
            break
        if metrics["all_root_disks_seconds"] is not None and metrics["all_ready_seconds"] is not None:
            metrics["status"] = "succeeded"
            break
        time.sleep(poll_interval)

    pvcs = client.list("pvc", namespace, root_selector)
    observed = Counter(filter(None, (observed_strategy(item) for item in pvcs)))
    if not observed:
        dvs = client.list("datavolume", namespace, root_selector)
        observed.update(filter(None, (observed_strategy(item) for item in dvs)))
    metrics["observed_strategies"] = dict(observed)
    observed_total = sum(observed.values())
    if metrics["status"] == "succeeded" and (
        observed_total < replicas or observed.get(requested_strategy, 0) != replicas
    ):
        metrics["status"] = "fallback"
        metrics["error"] = (
            f"requested {requested_strategy}; observed clone types: {dict(observed)}"
        )
    if metrics["status"] == "timeout":
        metrics["error"] = f"Pool did not reach {replicas} ready replicas within {timeout:.0f}s"
    return metrics


def monitor_pool_recovery(
    client: Kubectl,
    *,
    namespace: str,
    run_id: str,
    replicas: int,
    ready_when: str,
    baseline_uids: Iterable[str],
    replacements: int,
    timeout: float,
    poll_interval: float,
    started: float,
) -> Dict[str, Any]:
    selector = f"{RUN_LABEL}={run_id}"
    old_uids = set(baseline_uids)
    deadline = started + timeout
    while time.monotonic() < deadline:
        vms = client.list("vm", namespace, selector)
        vmis = client.list("vmi", namespace, selector)
        current_uids = {
            item.get("metadata", {}).get("uid") for item in vms if item.get("metadata", {}).get("uid")
        }
        new_uids = len(current_uids - old_uids)
        ready = sum(1 for item in vmis if is_vmi_ready(item, ready_when))
        if len(vms) >= replicas and ready >= replicas and new_uids >= replacements:
            return {
                "status": "succeeded",
                "seconds": round(time.monotonic() - started, 3),
                "replacement_vms": new_uids,
                "error": None,
            }
        time.sleep(poll_interval)
    return {
        "status": "timeout",
        "seconds": None,
        "replacement_vms": 0,
        "error": f"Pool did not recover within {timeout:.0f}s",
    }


def percentile(values: Sequence[float], percentage: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 3)
    rank = (len(ordered) - 1) * percentage
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(float(ordered[low]), 3)
    interpolated = ordered[low] + (ordered[high] - ordered[low]) * (rank - low)
    return round(float(interpolated), 3)


def aggregate_results(
    results: Sequence[Dict[str, Any]], metric: str
) -> Dict[str, Dict[str, Any]]:
    aggregate: Dict[str, Dict[str, Any]] = {}
    for strategy in CLONE_STRATEGIES:
        rows = [row for row in results if row.get("requested_strategy") == strategy]
        if not rows:
            continue
        values = [
            float(row[metric])
            for row in rows
            if row.get("status") == "succeeded" and row.get(metric) is not None
        ]
        aggregate[strategy] = {
            "runs": len(rows),
            "succeeded": sum(row.get("status") == "succeeded" for row in rows),
            "fallback": sum(row.get("status") == "fallback" for row in rows),
            "failed": sum(row.get("status") in {"failed", "timeout"} for row in rows),
            "unsupported": sum(row.get("status") == "unsupported" for row in rows),
            "p50_seconds": percentile(values, 0.50),
            "p95_seconds": percentile(values, 0.95),
            "p99_seconds": percentile(values, 0.99),
        }
    return aggregate


def save_results(
    base: Path,
    benchmark: str,
    run_id: str,
    payload: Dict[str, Any],
    rows: Sequence[Dict[str, Any]],
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = base / benchmark / f"{stamp}-{run_id}"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "results.json").open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    if rows:
        keys: List[str] = []
        for row in rows:
            for key in row:
                if key not in keys:
                    keys.append(key)
        with (directory / "results.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        key: json.dumps(value, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                        for key, value in row.items()
                    }
                )
    return directory


def cleanup_run(
    client: Kubectl,
    namespace: str,
    run_id: str,
    *,
    delete_namespace: bool = False,
) -> None:
    selector = f"{RUN_LABEL}={run_id}"
    # Delete controllers first; their owner references remove generated resources.
    for resource in ("virtualmachinepool", "virtualmachine", "datavolume", "pvc"):
        try:
            names = [
                item.get("metadata", {}).get("name")
                for item in client.list(resource, namespace, selector)
            ]
            client.delete(
                resource, [name for name in names if name], namespace, wait=True
            )
        except BenchmarkError:
            # A cluster may not have the pool CRD; other cleanup failures are material.
            if resource == "virtualmachinepool":
                continue
            raise
    if delete_namespace:
        client.delete("namespace", [namespace], wait=False)
