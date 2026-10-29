"""KubeVirt storage tuning profiles and FIO comparison helpers."""

from statistics import mean, median, pstdev
from typing import Dict, Iterable, List, Optional
from xml.etree import ElementTree

import yaml


PROFILE_ORDER = [
    "baseline",
    "iothreads-shared",
    "iothreads-auto",
    "iothreads-supplemental",
    "dedicated-iothread",
    "block-multiqueue",
    "combined",
]

# Wording follows the KubeVirt user guide, "High-performance features":
# https://kubevirt.io/user-guide/storage/disks_and_volumes/#high-performance-features
PROFILE_DESCRIPTIONS = {
    "baseline": "KubeVirt defaults (no ioThreadsPolicy, dedicatedIOThread, or blockMultiQueue)",
    "iothreads-shared": "ioThreadsPolicy: shared - one IOThread shared by all disks",
    "iothreads-auto": "ioThreadsPolicy: auto - disks round-robin over a pool of up to 2x vCPUs",
    "iothreads-supplemental": "ioThreadsPolicy: supplementalPool - a user-sized IOThread pool",
    "dedicated-iothread": "dedicatedIOThread: true on the benchmark disk (shared policy for the rest)",
    "block-multiqueue": "blockMultiQueue: true - one virtio-blk queue per vCPU",
    "combined": "Shared IOThreads, a dedicated benchmark-disk IOThread, and block multiqueue",
}

ALL_PROFILES = set(PROFILE_DESCRIPTIONS)
# dedicatedIOThread and blockMultiQueue are virtio-blk features (KubeVirt rejects
# dedicated IOThreads on a SCSI bus; multiqueue has no effect there).
NEEDS_VIRTIO_DISK = {"dedicated-iothread", "block-multiqueue", "combined"}
BENCHMARK_DISK_SERIAL = "virtbenchfio"

# Named I/O patterns for ``--workloads``. ``primary`` is the metric a pattern
# is ranked on: IOPS for small random I/O, bandwidth for large sequential I/O.
WORKLOADS = {
    "randread-4k": {"rw": "randread", "bs": "4k", "primary": "iops", "label": "4K randread"},
    "randwrite-4k": {"rw": "randwrite", "bs": "4k", "primary": "iops", "label": "4K randwrite"},
    "randrw-4k": {"rw": "randrw", "bs": "4k", "rwmixread": 70, "primary": "iops",
                  "label": "4K randrw 70/30"},
    "seqread-128k": {"rw": "read", "bs": "128k", "primary": "bw", "label": "128K seqread"},
    "seqwrite-128k": {"rw": "write", "bs": "128k", "primary": "bw", "label": "128K seqwrite"},
}
DEFAULT_WORKLOAD = "default"


def parse_workloads(value: str) -> List[str]:
    """Parse and validate a comma-separated workload-pattern list."""
    names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(names) - set(WORKLOADS))
    if unknown:
        raise ValueError(
            f"unknown workload(s): {', '.join(unknown)}; "
            f"valid workloads: {', '.join(WORKLOADS)}"
        )
    if not names:
        raise ValueError("at least one workload is required")
    if len(names) != len(set(names)):
        raise ValueError("workloads must not be repeated")
    return names


def custom_workload(rw: str, bs: str, rwmixread: int) -> Dict:
    """Describe the single ad-hoc workload given by --fio-rw/--fio-bs."""
    block_bytes = _block_size_bytes(bs)
    spec = {
        "name": f"{rw}-{bs}",
        "rw": rw,
        "bs": bs,
        "primary": "bw" if block_bytes >= 64 * 1024 else "iops",
        "label": f"{bs} {rw}",
    }
    if rw in ("randrw", "rw"):
        spec["rwmixread"] = rwmixread
    return spec


def resolve_workload(name: str, rwmixread: int = 70) -> Dict:
    """Return a copy of a named workload including its own name."""
    spec = {"name": name, **WORKLOADS[name]}
    spec.setdefault("rwmixread", rwmixread)
    return spec


def _block_size_bytes(bs: str) -> int:
    text = bs.strip().lower()
    scale = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
    if text and text[-1] in scale:
        return int(float(text[:-1]) * scale[text[-1]])
    return int(float(text))


def parse_profiles(value: str) -> List[str]:
    """Parse and validate a comma-separated profile list."""
    profiles = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(profiles) - ALL_PROFILES)
    if unknown:
        raise ValueError(
            f"unknown profile(s): {', '.join(unknown)}; "
            f"valid profiles: {', '.join(PROFILE_ORDER)}"
        )
    if not profiles:
        raise ValueError("at least one profile is required")
    if len(profiles) != len(set(profiles)):
        raise ValueError("profiles must not be repeated")
    return profiles


def configure_manifest(
    template_text: str,
    profile: str,
    vm_cpus: int,
    node_name: Optional[str] = None,
    supplemental_pool_threads: int = 4,
    benchmark_disk: str = "scratchdisk",
) -> str:
    """Apply a KubeVirt storage profile to an already-substituted VM manifest.

    CPU is always set explicitly: KubeVirt only honours ``blockMultiQueue`` when
    a specific CPU allocation is requested, and an equal request and limit keeps
    profiles comparable.
    """
    if profile not in ALL_PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    manifest = yaml.safe_load(template_text)
    vm_spec = manifest["spec"]["template"]["spec"]
    domain = vm_spec["domain"]
    devices = domain["devices"]

    domain["cpu"] = {"cores": vm_cpus}
    resources = domain.setdefault("resources", {})
    resources["requests"] = {**resources.get("requests", {}), "cpu": str(vm_cpus)}
    resources["limits"] = {**resources.get("limits", {}), "cpu": str(vm_cpus)}

    if node_name:
        vm_spec.setdefault("nodeSelector", {})["kubernetes.io/hostname"] = node_name

    disk = next((item for item in devices.get("disks", []) if item.get("name") == benchmark_disk), None)
    if disk is None:
        raise ValueError(f"benchmark disk '{benchmark_disk}' was not found in VM template")
    disk["serial"] = BENCHMARK_DISK_SERIAL
    if profile in NEEDS_VIRTIO_DISK:
        bus = disk.get("disk", {}).get("bus", "virtio") if "disk" in disk else None
        if bus != "virtio":
            raise ValueError(
                f"profile {profile!r} needs benchmark disk {benchmark_disk!r} to be a virtio "
                f"disk, found bus {bus!r}: KubeVirt does not support dedicated IOThreads or "
                "block multiqueue on SCSI disks"
            )

    if profile in {"iothreads-shared", "dedicated-iothread", "combined"}:
        domain["ioThreadsPolicy"] = "shared"
    elif profile == "iothreads-auto":
        domain["ioThreadsPolicy"] = "auto"
    elif profile == "iothreads-supplemental":
        domain["ioThreadsPolicy"] = "supplementalPool"
        domain["ioThreads"] = {"supplementalPoolThreadCount": supplemental_pool_threads}

    if profile in {"dedicated-iothread", "combined"}:
        disk["dedicatedIOThread"] = True

    if profile in {"block-multiqueue", "combined"}:
        devices["blockMultiQueue"] = True

    labels = manifest.setdefault("metadata", {}).setdefault("labels", {})
    labels["virtbench.io/benchmark"] = "fio-profile-comparison"
    labels["virtbench.io/profile"] = profile
    pod_labels = manifest["spec"]["template"].setdefault("metadata", {}).setdefault("labels", {})
    pod_labels.update(labels)
    return yaml.safe_dump(manifest, sort_keys=False)


def parse_domain_xml(xml_text: str, serial: str = BENCHMARK_DISK_SERIAL) -> Dict:
    """Read the effective storage tuning from a libvirt domain XML.

    This is what QEMU is actually running, so it shows whether KubeVirt honoured
    a requested profile rather than silently ignoring it.
    """
    root = ElementTree.fromstring(xml_text)
    vcpus = int((root.findtext("vcpu") or "0").strip() or 0)
    iothreads = int((root.findtext("iothreads") or "0").strip() or 0)
    disks = []
    for node in root.findall("./devices/disk"):
        driver = node.find("driver")
        attrs = driver.attrib if driver is not None else {}
        disks.append(
            {
                "device": (node.find("target").attrib.get("dev") if node.find("target") is not None else None),
                "serial": node.findtext("serial"),
                "iothread": int(attrs["iothread"]) if "iothread" in attrs else None,
                "queues": int(attrs["queues"]) if "queues" in attrs else None,
                "cache": attrs.get("cache"),
                "io": attrs.get("io"),
            }
        )
    benchmark = next((disk for disk in disks if disk["serial"] == serial), None)
    return {"vcpus": vcpus, "iothreads": iothreads, "disks": disks, "benchmark_disk": benchmark}


def verify_profile(
    profile: str, observed: Dict, vm_cpus: int, supplemental_pool_threads: int
) -> List[str]:
    """Return reasons the running domain does not match ``profile`` (empty if it does)."""
    problems: List[str] = []
    disk = observed.get("benchmark_disk")
    if disk is None:
        return [f"benchmark disk (serial {BENCHMARK_DISK_SERIAL}) is not in the running domain"]
    if observed["vcpus"] != vm_cpus:
        problems.append(f"domain has {observed['vcpus']} vCPUs, expected {vm_cpus}")
    others = [item for item in observed["disks"] if item is not disk and item["iothread"] is not None]

    if profile in {"iothreads-shared", "iothreads-auto", "iothreads-supplemental",
                   "dedicated-iothread", "combined"}:
        if observed["iothreads"] < 1:
            problems.append("no IOThreads were created")
        if profile == "iothreads-auto" and observed["iothreads"] > 2 * vm_cpus:
            problems.append(
                f"{observed['iothreads']} IOThreads exceeds the auto pool limit of {2 * vm_cpus}"
            )
        if profile == "iothreads-supplemental" and observed["iothreads"] < supplemental_pool_threads:
            problems.append(
                f"{observed['iothreads']} IOThreads, expected at least "
                f"{supplemental_pool_threads} from the supplemental pool"
            )
    if profile in {"dedicated-iothread", "combined"}:
        if disk["iothread"] is None:
            problems.append("benchmark disk has no IOThread")
        elif any(item["iothread"] == disk["iothread"] for item in others):
            problems.append(
                f"benchmark disk shares IOThread {disk['iothread']} with another disk"
            )
    if profile in {"block-multiqueue", "combined"} and disk["queues"] != vm_cpus:
        problems.append(
            f"benchmark disk has queues={disk['queues']}, expected {vm_cpus} "
            "(KubeVirt ignores blockMultiQueue without an explicit CPU allocation)"
        )
    if profile == "baseline":
        if disk["queues"] is not None:
            problems.append(f"baseline disk unexpectedly has queues={disk['queues']}")
    return problems


def _percentile(io_stats: Dict, percentile: str) -> float:
    latency = io_stats.get("clat_ns") or io_stats.get("lat_ns") or {}
    values = latency.get("percentile", {})
    target = float(percentile)
    for key, value in values.items():
        try:
            if abs(float(key) - target) < 0.0001:
                return float(value) / 1_000_000
        except (TypeError, ValueError):
            continue
    return 0.0


def parse_iteration(
    raw_data: Dict, profile: str, iteration: int, workload: str = DEFAULT_WORKLOAD
) -> Dict:
    """Extract throughput and tail latency from one FIO JSON result."""
    result = {
        "workload": workload,
        "profile": profile,
        "iteration": iteration,
        "read_iops": 0.0,
        "write_iops": 0.0,
        "read_bw_mibps": 0.0,
        "write_bw_mibps": 0.0,
        "read_p95_ms": 0.0,
        "read_p99_ms": 0.0,
        "read_p999_ms": 0.0,
        "write_p95_ms": 0.0,
        "write_p99_ms": 0.0,
        "write_p999_ms": 0.0,
    }
    for job in raw_data.get("jobs", []):
        for direction in ("read", "write"):
            stats = job.get(direction, {})
            result[f"{direction}_iops"] += float(stats.get("iops", 0))
            result[f"{direction}_bw_mibps"] += float(stats.get("bw_bytes", 0)) / 1024 / 1024
            for label, value in (("p95", "95"), ("p99", "99"), ("p999", "99.9")):
                result[f"{direction}_{label}_ms"] = max(
                    result[f"{direction}_{label}_ms"], _percentile(stats, value)
                )
    result["total_iops"] = result["read_iops"] + result["write_iops"]
    result["total_bw_mibps"] = result["read_bw_mibps"] + result["write_bw_mibps"]
    return {key: round(value, 3) if isinstance(value, float) else value for key, value in result.items()}


def summarize_profiles(
    iterations: Iterable[Dict],
    vm_cpus: int,
    supplemental_pool_threads: int,
    observed_iothreads: Optional[Dict[str, int]] = None,
) -> List[Dict]:
    """Calculate per-workload, per-profile medians and baseline changes.

    Baseline deltas compare a profile only with the baseline run of the *same*
    workload; a 4K randread result is never compared with a 128K sequential one.
    """
    rows = list(iterations)
    workloads: List[str] = []
    for row in rows:
        name = row.get("workload", DEFAULT_WORKLOAD)
        if name not in workloads:
            workloads.append(name)
    summaries: List[Dict] = []
    for workload in workloads:
        subset = [row for row in rows if row.get("workload", DEFAULT_WORKLOAD) == workload]
        summaries.extend(
            _summarize_workload(
                subset, workload, vm_cpus, supplemental_pool_threads, observed_iothreads or {}
            )
        )
    return summaries


_METRIC_NAMES = [
    "total_iops", "total_bw_mibps", "read_iops", "write_iops",
    "read_p95_ms", "read_p99_ms", "read_p999_ms",
    "write_p95_ms", "write_p99_ms", "write_p999_ms",
]


def _summarize_workload(
    rows: List[Dict],
    workload: str,
    vm_cpus: int,
    supplemental_pool_threads: int,
    observed_iothreads: Dict[str, int],
) -> List[Dict]:
    profiles: List[str] = []
    for row in rows:
        if row["profile"] not in profiles:
            profiles.append(row["profile"])

    summaries = []
    for profile in profiles:
        values = [row for row in rows if row["profile"] == profile]
        summary = {
            "workload": workload,
            "profile": profile,
            "description": PROFILE_DESCRIPTIONS[profile],
            "iterations": len(values),
        }
        for metric in _METRIC_NAMES:
            summary[f"median_{metric}"] = round(median(row[metric] for row in values), 3)
        total_iops_values = [row["total_iops"] for row in values]
        average_iops = mean(total_iops_values)
        summary["min_total_iops"] = round(min(total_iops_values), 3)
        summary["max_total_iops"] = round(max(total_iops_values), 3)
        summary["total_iops_cv_pct"] = round(
            (pstdev(total_iops_values) / average_iops * 100) if average_iops else 0, 2
        )
        # vCPU threads plus the IOThreads QEMU actually runs. Fall back to the
        # requested supplemental pool when the domain could not be inspected.
        if profile in observed_iothreads:
            extra = observed_iothreads[profile]
        else:
            extra = supplemental_pool_threads if profile == "iothreads-supplemental" else 0
        thread_budget = vm_cpus + extra
        summary["thread_budget"] = thread_budget
        summary["median_iops_per_thread"] = round(summary["median_total_iops"] / thread_budget, 3)
        summaries.append(summary)

    baseline = next((row for row in summaries if row["profile"] == "baseline"), None)
    for summary in summaries:
        if baseline:
            for metric in ("total_iops", "total_bw_mibps"):
                base = baseline[f"median_{metric}"]
                summary[f"{metric}_change_pct"] = round(
                    ((summary[f"median_{metric}"] - base) / base * 100) if base else 0, 2
                )
            for metric in ("read_p99_ms", "write_p99_ms"):
                base = baseline[f"median_{metric}"]
                summary[f"{metric}_improvement_pct"] = round(
                    ((base - summary[f"median_{metric}"]) / base * 100) if base else 0, 2
                )
        else:
            summary["total_iops_change_pct"] = None
            summary["total_bw_mibps_change_pct"] = None
            summary["read_p99_ms_improvement_pct"] = None
            summary["write_p99_ms_improvement_pct"] = None
    return summaries


# --- ranking and recommendation ---------------------------------------------

LABEL_LATENCY = "Database / latency-sensitive"
LABEL_THROUGHPUT = "Throughput-heavy / multi-vCPU"
LABEL_EFFICIENCY = "Cost-efficient / few threads"
LABEL_BASELINE = "Dev / light workloads (defaults)"
LABEL_GENERAL = "General production"
# Scores this close are treated as equal, then ordered by tail latency.
TIE_TOLERANCE = 0.01


def primary_metric(workload: Dict) -> str:
    """Summary key that ranks profiles for a workload."""
    return "median_total_bw_mibps" if workload.get("primary") == "bw" else "median_total_iops"


def worst_p99_ms(summary: Dict) -> float:
    """Worst median p99 across read and write for one summarized profile."""
    return max(summary["median_read_p99_ms"], summary["median_write_p99_ms"])


def rank_profiles(summaries: List[Dict], workload: Dict) -> List[Dict]:
    """Rank one workload's profile summaries, best first.

    Profiles within ``TIE_TOLERANCE`` of the leader on the primary metric are
    ordered by lower tail latency so a noise-level IOPS gap cannot hide a
    clearly better latency profile.
    """
    key = primary_metric(workload)
    rows = [dict(row) for row in summaries]
    if not rows:
        return rows
    leader = max(row[key] for row in rows)
    floor = leader * (1 - TIE_TOLERANCE)

    def order(row: Dict):
        contender = row[key] >= floor
        return (0 if contender else 1, worst_p99_ms(row) if contender else 0, -row[key])

    rows.sort(key=order)
    for position, row in enumerate(rows, 1):
        row["rank"] = position
        row["primary_metric"] = key
        row["primary_value"] = row[key]
    return rows


def _geomean(values: List[float]) -> float:
    positive = [value for value in values if value > 0]
    if not positive or len(positive) != len(values):
        return 0.0
    product = 1.0
    for value in positive:
        product *= value
    return product ** (1 / len(positive))


def recommend(summaries: List[Dict], workloads: Dict[str, Dict]) -> Dict:
    """Build per-workload rankings and one overall recommendation.

    The overall score is the geometric mean, across workloads, of each
    profile's primary metric relative to the best profile for that workload.
    ``Best for`` labels come from fixed rules (lowest tail latency, highest
    throughput score, highest IOPS per thread, baseline) rather than opinion.
    """
    per_workload: Dict[str, List[Dict]] = {}
    for name, spec in workloads.items():
        subset = [row for row in summaries if row.get("workload") == name]
        if subset:
            per_workload[name] = rank_profiles(subset, spec)

    profiles: List[str] = []
    for ranked in per_workload.values():
        for row in ranked:
            if row["profile"] not in profiles:
                profiles.append(row["profile"])

    overall = {
        profile: {
            "profile": profile,
            "description": PROFILE_DESCRIPTIONS[profile],
            "throughput": [],
            "latency": [],
            "efficiency": [],
            "values": {},
            "workloads_measured": 0,
        }
        for profile in profiles
    }
    for name, ranked in per_workload.items():
        best_primary = max(row["primary_value"] for row in ranked)
        best_p99 = min((worst_p99_ms(row) for row in ranked if worst_p99_ms(row) > 0), default=0)
        best_eff = max(row["median_iops_per_thread"] for row in ranked)
        for row in ranked:
            entry = overall[row["profile"]]
            entry["workloads_measured"] += 1
            entry["values"][name] = {
                "primary_metric": row["primary_metric"],
                "primary_value": row["primary_value"],
                "total_iops": row["median_total_iops"],
                "total_bw_mibps": row["median_total_bw_mibps"],
                "worst_p99_ms": worst_p99_ms(row),
                "rank": row["rank"],
            }
            entry["throughput"].append(row["primary_value"] / best_primary if best_primary else 0)
            p99 = worst_p99_ms(row)
            entry["latency"].append(best_p99 / p99 if p99 and best_p99 else 0)
            entry["efficiency"].append(
                row["median_iops_per_thread"] / best_eff if best_eff else 0
            )

    complete = [
        entry for entry in overall.values() if entry["workloads_measured"] == len(per_workload)
    ]
    for entry in overall.values():
        entry["throughput_score"] = round(_geomean(entry.pop("throughput")), 4)
        entry["latency_score"] = round(_geomean(entry.pop("latency")), 4)
        entry["efficiency_score"] = round(_geomean(entry.pop("efficiency")), 4)
        entry["complete"] = entry in complete

    candidates = [entry for entry in overall.values() if entry["complete"]]
    ordered: List[Dict] = []
    if candidates:
        leader = max(entry["throughput_score"] for entry in candidates)
        floor = leader * (1 - TIE_TOLERANCE)
        ordered = sorted(
            candidates,
            key=lambda entry: (
                0 if entry["throughput_score"] >= floor else 1,
                -entry["latency_score"] if entry["throughput_score"] >= floor else 0,
                -entry["throughput_score"],
            ),
        )
        best_latency = max(candidates, key=lambda entry: entry["latency_score"])
        best_throughput = ordered[0]
        best_efficiency = max(candidates, key=lambda entry: entry["efficiency_score"])
        for position, entry in enumerate(ordered, 1):
            entry["rank"] = position
            labels = []
            if entry is best_latency and entry["latency_score"] > 0:
                labels.append(LABEL_LATENCY)
            if entry is best_throughput:
                labels.append(LABEL_THROUGHPUT)
            if entry is best_efficiency and entry is not best_throughput:
                labels.append(LABEL_EFFICIENCY)
            if entry["profile"] == "baseline" and not labels:
                labels.append(LABEL_BASELINE)
            entry["best_for"] = " / ".join(labels) if labels else LABEL_GENERAL
    incomplete = [entry for entry in overall.values() if not entry["complete"]]
    for entry in incomplete:
        entry["rank"] = None
        entry["best_for"] = "Not ranked (missing workloads)"
    return {
        "workloads": {name: spec for name, spec in workloads.items() if name in per_workload},
        "per_workload": per_workload,
        "overall": ordered + incomplete,
    }
