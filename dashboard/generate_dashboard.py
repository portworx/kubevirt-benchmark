#!/usr/bin/env python3
"""
Generate an interactive HTML dashboard for KubeVirt performance test results.

Structure:
  PX Version (top-level tab)
    └── Disk Count tab (e.g., 1-disk, 2-disk, 4-disk)
          ├── 3 compact charts (Creation / Boot Storm / Migration) with times in mm:ss
          └── VM Size tabs (e.g., 50 VMs, 100 VMs, 200 VMs, 400 VMs)
                ├── Creation + Boot Storm subtab (original tables intact)
                └── Live Migration subtab (original tables intact)

Usage:
  python3 generate_dashboard.py [--days N] [--base-dir PATH] [--output-html FILE]
"""

import html
import json
import argparse
import base64
import html
import numbers
import re
import pandas as pd
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict


LABEL_MAP = {
    "running_time_sec": "Running Time",
    "ping_time_sec": "Ping Time",
    "reachability_lag_sec": "Ping Lag",
    "clone_duration_sec": "Clone Duration",
    "observed_time_sec": "Observed Time",
    "vmim_time_sec": "VMIM Time",
}

# FIO metric labels for display
FIO_LABEL_MAP = {
    "read_iops": "Read IOPS",
    "write_iops": "Write IOPS",
    "read_bw_mibps": "Read Bandwidth (MiB/s)",
    "write_bw_mibps": "Write Bandwidth (MiB/s)",
    "read_lat_mean_ms": "Read Latency Mean (ms)",
    "write_lat_mean_ms": "Write Latency Mean (ms)",
    "read_lat_p99_ms": "Read Latency P99 (ms)",
    "write_lat_p99_ms": "Write Latency P99 (ms)",
}

SCENARIO_LABEL_MAP = {
    "vm-creation": "VM Creation",
    "vm-bootstorm": "VM Boot Storm",
    "boot-storm": "Boot Storm",
    "live-migration": "Live Migration",
    "migration": "Live Migration",
    "vm-workflow-basic": "VM Workflow Basic",
    "failure-recovery": "Failure Recovery",
    "fio": "FIO",
    "elbencho": "Elbencho",
    "chaos": "Chaos",
    "validate-cluster": "Validate Cluster",
}


def format_scenario_label(value: str) -> str:
    """Convert scenario slugs into readable dashboard labels."""
    if not value:
        return ""

    text = str(value)
    if " / " in text:
        return " / ".join(format_scenario_label(part) for part in text.split(" / "))

    mapped = SCENARIO_LABEL_MAP.get(text)
    if mapped:
        return mapped

    return text.replace("_", " ").replace("-", " ").title()


def format_best_stable_total(summary: dict, capacity: dict, data: dict) -> str:
    """Show stable VM count with the cap context that shaped the run."""
    value = summary.get("best_stable_total_vms", summary.get("best_stable_batch"))
    if value in (None, ""):
        return "N/A"

    reason = summary.get("recommendation_reason")
    user_max = summary.get("user_max_vms", data.get("user_max_vms"))
    effective_ceiling = summary.get("effective_vm_ceiling", data.get("effective_vm_ceiling"))
    estimated_ceiling = capacity.get("estimated_vm_ceiling")
    try:
        value_num = int(value)
        user_max_num = int(user_max) if user_max not in (None, "") else None
        estimated_ceiling_num = int(estimated_ceiling) if estimated_ceiling not in (None, "") else None
    except (TypeError, ValueError):
        value_num = None
        user_max_num = None
        estimated_ceiling_num = None

    if reason == "user_cap_reached":
        return f"{value} (user cap {user_max})"
    if reason == "cluster_ceiling_reached":
        return f"{value} (cluster estimate)"
    if reason == "effective_ceiling_reached" and effective_ceiling:
        return f"{value} (effective cap {effective_ceiling})"
    if reason == "below_effective_ceiling" and effective_ceiling:
        return f"{value} (below cap {effective_ceiling})"

    # Backward compatibility for older AI artifacts that do not include a reason.
    if (
        user_max_num is not None
        and estimated_ceiling_num is not None
        and value_num is not None
        and user_max_num < estimated_ceiling_num
        and value_num >= user_max_num
    ):
        return f"{value} (user cap {user_max})"
    return str(value)


def ai_metric_column_label(scenario: str, objective: str = "") -> str:
    if scenario == "vm-workflow-basic":
        return "Step Timing Summary"
    if scenario in {"vm-creation", "bootstorm", "vm-bootstorm", "boot-storm", "migration", "live-migration"}:
        return "Timing Summary"
    if scenario == "fio" and objective:
        return f"{objective.replace('-', ' ').title()} Summary"
    return "Metric Summary"


def format_ai_metric_summary(value: str) -> str:
    """Render older internal metric names as readable dashboard text."""
    if not value:
        return ""
    text = str(value)
    regex_replacements = [
        (r"creation:max_ping_time_sec=([0-9.]+)", r"creation: reachability max: \1s"),
        (r"bootstorm:max_ping_time_sec=([0-9.]+)", r"bootstorm: reachability max: \1s"),
        (r"migration:max_vmim_time_sec=([0-9.]+)", r"migration: VMIM duration max: \1s"),
        (r"creation:reachability_max=([0-9.]+)s?", r"creation: reachability max: \1s"),
        (r"bootstorm:reachability_max=([0-9.]+)s?", r"bootstorm: reachability max: \1s"),
        (r"migration:vmim_duration_max=([0-9.]+)s?", r"migration: VMIM duration max: \1s"),
        (r"max_ping_time_sec=([0-9.]+)", r"reachability max: \1s"),
        (r"max_running_time_sec=([0-9.]+)", r"running max: \1s"),
        (r"max_vmim_time_sec=([0-9.]+)", r"VMIM duration max: \1s"),
        (r"max_observed_time_sec=([0-9.]+)", r"observed duration max: \1s"),
        (r"reachability_max=([0-9.]+)s?", r"reachability max: \1s"),
        (r"running_max=([0-9.]+)s?", r"running max: \1s"),
        (r"vmim_duration_max=([0-9.]+)s?", r"VMIM duration max: \1s"),
        (r"observed_duration_max=([0-9.]+)s?", r"observed duration max: \1s"),
    ]
    for pattern, replacement in regex_replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(?<!;)\s+bootstorm:", "; bootstorm:", text)
    text = re.sub(r"(?<!;)\s+migration:", "; migration:", text)
    return text


def parse_result_dir_timestamp(path_value: str):
    if not path_value:
        return None
    try:
        timestamp = Path(path_value).name.split("_")[0]
        return datetime.strptime(timestamp, "%Y%m%d-%H%M%S")
    except Exception:
        return None


def summary_duration_from_dir(result_dir: str, filename: str, *keys: str):
    if not result_dir:
        return None
    data = load_json(Path(result_dir) / filename)
    if not data:
        return None
    for key in keys:
        value = data.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def workflow_durations_for_round(item: dict) -> dict:
    durations = dict(item.get("workflow_durations_sec") or {})
    result_dirs = item.get("result_dirs") or []

    for result_dir in result_dirs:
        path_name = Path(result_dir).name
        if "live_migration" in path_name:
            if durations.get("migration_duration_sec") is None:
                durations["migration_duration_sec"] = summary_duration_from_dir(
                    result_dir,
                    "summary_migration_results.json",
                    "total_migration_duration_sec",
                )
        elif durations.get("vm_creation_duration_sec") is None:
            durations["vm_creation_duration_sec"] = summary_duration_from_dir(
                result_dir,
                "summary_vm_creation_results.json",
                "total_test_duration_sec",
            )
        elif durations.get("bootstorm_duration_sec") is None:
            durations["bootstorm_duration_sec"] = summary_duration_from_dir(
                result_dir,
                "summary_boot_storm_results.json",
                "total_test_duration_sec",
            )

    if durations.get("total_workflow_duration_sec") is None:
        first_start = parse_result_dir_timestamp(result_dirs[0]) if result_dirs else None
        last_start = parse_result_dir_timestamp(result_dirs[-1]) if result_dirs else None
        migration_duration = durations.get("migration_duration_sec")
        if first_start and last_start and migration_duration is not None:
            durations["total_workflow_duration_sec"] = (
                last_start - first_start
            ).total_seconds() + float(migration_duration)
        else:
            step_values = [
                durations.get("vm_creation_duration_sec"),
                durations.get("bootstorm_duration_sec"),
                durations.get("migration_duration_sec"),
            ]
            if all(value is not None for value in step_values):
                durations["total_workflow_duration_sec"] = sum(float(value) for value in step_values)

    return durations


def format_duration(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return format_mmss(float(value))
    except (TypeError, ValueError):
        return str(value)


def get_logo_data_uri() -> str:
    """Embed the VirtBench logo so generated dashboards are portable."""
    logo_path = Path(__file__).resolve().parent.parent / "docs" / "virtbench-transparent.png"
    try:
        encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return ""

# ---------------- Utility Functions ----------------
def load_json(path: Path):
    """Safely load JSON file; return None if not found or invalid."""
    try:
        if not path.exists():
            return None
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None

def load_cluster_info(yaml_path: str):
    """Load cluster info YAML for a single cluster."""
    path = Path(yaml_path)
    if not path.exists():
        print(f"Cluster info file not found: {yaml_path}")
        return None

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return data.get("cluster", {})
    except Exception as e:
        print(f"⚠️  Failed to parse {yaml_path}: {e}")
        return None

def build_cluster_info_tab(cluster_info: dict) -> str:
    """Render the cluster info as an HTML table with auto-generated labels from keys."""
    if not cluster_info:
        return "<p>No cluster information provided.</p>"

    rows = ""
    for key, value in cluster_info.items():
        label = key.replace("_", " ").upper()

        if isinstance(value, str) and "\n" in value:
            rows += f"<tr><th>{label}</th><td><pre style='margin:0;white-space:pre-wrap'>{value.strip()}</pre></td></tr>"
        else:
            rows += f"<tr><th>{label}</th><td>{value}</td></tr>"

    return f"<table class='table table-bordered w-auto'><tbody>{rows}</tbody></table>"

def load_manual_results(yaml_path: str):
    """Load manual results YAML."""
    path = Path(yaml_path)
    if not path.exists():
        print(f"Manual results file not found: {yaml_path}")
        return None
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return data.get("results", [])
    except Exception as e:
        print(f"Failed to parse {yaml_path}: {e}")
        return None


def build_manual_results_tab(manual_results: list) -> str:
    """Render manual results as an interactive table."""
    if not manual_results:
        return "<p>No manual results provided.</p>"

    df = pd.DataFrame(manual_results)
    if df.empty:
        return "<p>Manual results file is empty.</p>"

    # Round any numeric columns and apply friendly names
    numeric_cols = df.select_dtypes(include="number").columns
    df[numeric_cols] = df[numeric_cols].round(2)

    return df_to_html_table(df, "table_manual_results")


def discover_ai_round_runs(base_dir: Path, cutoff: datetime) -> list:
    """Find AI round artifacts under results/ai-runs."""
    ai_root = base_dir / "ai-runs"
    if not ai_root.exists():
        return []

    runs = []
    for path in sorted(ai_root.glob("*/virtbench_ai_rounds.json")):
        data = load_json(path)
        if not data:
            continue
        try:
            created_at = datetime.fromisoformat(data.get("created_at"))
        except Exception:
            try:
                ts = path.parent.name.split("_")[0]
                created_at = datetime.strptime(ts, "%Y%m%d-%H%M%S")
            except Exception:
                created_at = datetime.fromtimestamp(path.stat().st_mtime)
        if created_at >= cutoff:
            runs.append((path, data, created_at))
    return runs


def group_ai_runs_by_result_location(base_dir: Path, ai_runs: list) -> dict:
    """Map AI runs to the PX/disk result tab that contains their raw result folders."""
    grouped = defaultdict(list)
    base_dir = base_dir.expanduser()

    disk_roots = []
    if base_dir.exists():
        for px_dir in sorted([d for d in base_dir.iterdir() if d.is_dir() and d.name != "ai-runs"]):
            for disk_dir in sorted([d for d in px_dir.iterdir() if d.is_dir()]):
                try:
                    disk_roots.append((px_dir.name, disk_dir.name, disk_dir.resolve()))
                except Exception:
                    continue

    for run in ai_runs:
        _, data, _ = run
        locations = set()
        for item in data.get("rounds", []):
            raw_dirs = []
            if item.get("result_dir"):
                raw_dirs.append(item.get("result_dir"))
            raw_dirs.extend(item.get("result_dirs") or [])
            for result_dir in raw_dirs:
                if not result_dir:
                    continue
                path = Path(result_dir).expanduser()
                relative = None
                try:
                    relative = path.absolute().relative_to(base_dir.absolute())
                except Exception:
                    try:
                        relative = path.resolve().relative_to(base_dir.resolve())
                    except Exception:
                        relative = None

                if relative:
                    parts = relative.parts
                    if len(parts) >= 2:
                        locations.add((parts[0], parts[1]))
                    continue

                # If a combined dashboard uses symlinked disk folders, resolving
                # the result path may place it outside base_dir. Match against
                # the resolved disk roots so AI panels still land on the right tab.
                try:
                    resolved_path = path.resolve()
                except Exception:
                    continue
                for px_name, disk_name, disk_root in disk_roots:
                    try:
                        resolved_path.relative_to(disk_root)
                    except Exception:
                        continue
                    locations.add((px_name, disk_name))
                    break

        for location in locations:
            grouped[location].append(run)

    return grouped


def format_ai_decision(decision: str) -> str:
    """Convert planner decision codes into dashboard-friendly labels."""
    if not decision:
        return ""

    if decision.startswith("dry_run_plan_"):
        base = format_ai_decision(decision.removeprefix("dry_run_plan_"))
        return f"Dry-run: {base}" if base else "Dry-run plan"

    labels = {
        "increase_total_vms": "Increase total VMs",
        "finish": "Complete",
        "stop_after_failed_total": "Stop: capacity limit reached",
        "increase": "Increase batch",
        "hold_at_estimated_ceiling": "Hold at estimated ceiling",
        "reduce_to_midpoint": "Reduce and retry midpoint",
        "reduce": "Reduce batch",
        "continue_fio_search": "Continue FIO search",
        "stop": "Stop: setup issue",
        "assist_execute": "Assisted run",
    }
    return labels.get(decision, decision.replace("_", " ").title())


def format_ai_limited_by(value: str) -> str:
    labels = {
        "user_cap": "User cap",
        "cluster_estimate": "Cluster estimate",
        "observed_below_cap": "Observed below cap",
        "no_stable_total": "No stable total",
        "dry_run": "Dry run",
        "observed": "Observed",
    }
    return labels.get(value, str(value or "").replace("_", " ").title())


def first_display_value(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def build_ai_analysis_panel(analysis: dict) -> str:
    """Render the interpreted AI analysis artifact."""
    if not analysis:
        return ""

    hints = analysis.get("bottleneck_hints") or []
    actions = analysis.get("action_items") or []
    failures = analysis.get("failure_analysis") or []
    duration = analysis.get("duration_analysis") or {}
    slowest = duration.get("slowest_phase") or {}
    trend = duration.get("total_workflow_trend") or {}

    hint_items = []
    for hint in hints:
        parts = [
            f"<strong>{html.escape(str(hint.get('severity', '')).title())}</strong>",
            html.escape(str(hint.get("message", ""))),
        ]
        if hint.get("evidence"):
            parts.append(f"<span class=\"text-muted\">{html.escape(str(hint.get('evidence')))}</span>")
        if hint.get("next_check"):
            parts.append(f"<span>{html.escape(str(hint.get('next_check')))}</span>")
        hint_items.append(f"<li>{'<br>'.join(parts)}</li>")

    action_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in actions)
    failure_items = []

    def stacked_detail(label, values):
        if not values:
            return ""
        lines = "".join(f"<span>{html.escape(str(value))}</span>" for value in values)
        return (
            f"<div class=\"failure-detail-stack\"><strong>{html.escape(label)}:</strong>"
            f"<span class=\"stack-cell\">{lines}</span></div>"
        )

    for failure in failures:
        details = [html.escape(str(failure.get("reason", "")))]
        if failure.get("failure_reason"):
            details.append(f"<strong>Failure reason:</strong> {html.escape(str(failure.get('failure_reason')))}")
        if failure.get("failure_analysis"):
            details.append(f"<strong>Analysis:</strong> {html.escape(str(failure.get('failure_analysis')))}")
        if failure.get("cause_evidence"):
            details.append(stacked_detail("Cause evidence", failure.get("cause_evidence")))
        if failure.get("recommended_checks"):
            details.append(stacked_detail("Recommended checks", failure.get("recommended_checks")))
        if failure.get("duration_sec") not in (None, ""):
            details.append(f"Duration: {html.escape(format_duration(failure.get('duration_sec')))}")
        if failure.get("success_percent") not in (None, ""):
            details.append(f"Success: {html.escape(str(failure.get('success_percent')))}%")
        if failure.get("metrics"):
            details.append(html.escape("; ".join(str(item) for item in failure.get("metrics"))))
        if failure.get("failed_resources"):
            resources = ", ".join(str(item) for item in failure.get("failed_resources")[:8])
            details.append(f"Failed namespaces: {html.escape(resources)}")
        if failure.get("result_dir"):
            details.append(f"<code class=\"wrap-code\">{html.escape(str(failure.get('result_dir')))}</code>")
        failure_items.append(f"<li>{'<br>'.join(details)}</li>")

    failure_html = ""
    if failure_items:
        failure_html = f"""
        <div class="col-12">
          <h5>Failure Analysis</h5>
          <ul>{''.join(failure_items)}</ul>
        </div>
        """

    trend_text = trend.get("message") or "No workflow trend was available."
    slowest_text = "N/A"
    if slowest:
        slowest_text = f"{slowest.get('label')} ({format_duration(slowest.get('average_sec'))} avg)"

    return f"""
    <div class="ai-analysis-panel mb-4">
      <h4>AI Validation Analysis</h4>
      <div class="row g-3">
        <div class="col-12 col-md-3">
          <div class="card border-secondary h-100">
            <div class="card-body">
              <div class="text-muted small">Status</div>
              <div class="fw-bold">{html.escape(str(analysis.get('status', 'N/A')).title())}</div>
            </div>
          </div>
        </div>
        <div class="col-12 col-md-3">
          <div class="card border-secondary h-100">
            <div class="card-body">
              <div class="text-muted small">Limited By</div>
              <div class="fw-bold">{html.escape(format_ai_limited_by(analysis.get('limited_by')))}</div>
            </div>
          </div>
        </div>
        <div class="col-12 col-md-3">
          <div class="card border-secondary h-100">
            <div class="card-body">
              <div class="text-muted small">Largest Stable Total VMs</div>
              <div class="fw-bold">{html.escape(str(analysis.get('largest_stable_total_vms', 'N/A')))}</div>
            </div>
          </div>
        </div>
        <div class="col-12 col-md-3">
          <div class="card border-secondary h-100">
            <div class="card-body">
              <div class="text-muted small">Slowest Phase</div>
              <div class="fw-bold">{html.escape(str(slowest_text))}</div>
            </div>
          </div>
        </div>
      </div>
      <p class="mt-3 mb-2"><strong>Analysis:</strong> {html.escape(str(analysis.get('headline', 'N/A')))}</p>
      <p class="mb-2"><strong>Trend:</strong> {html.escape(str(trend_text))}</p>
      <div class="row g-3">
        <div class="col-12 col-lg-7">
          <h5>Bottleneck Hints</h5>
          <ul>{''.join(hint_items)}</ul>
        </div>
        <div class="col-12 col-lg-5">
          <h5>Next Actions</h5>
          <ul>{action_items}</ul>
        </div>
        {failure_html}
      </div>
    </div>
    """


def build_ai_rounds_tab(ai_runs: list) -> str:
    """Render adaptive AI round decisions grouped by scenario."""
    if not ai_runs:
        return "<p>No AI round runs found.</p>"

    assisted_scenarios = {"chaos", "failure-recovery", "elbencho", "validate-cluster"}
    by_scenario = defaultdict(list)
    for run in ai_runs:
        _, data, _ = run
        scenario = data.get("scenario", "unknown")
        objective = data.get("objective")
        group = f"{scenario} / {objective}" if scenario == "fio" and objective else scenario
        by_scenario[group].append(run)

    scenario_nav = []
    scenario_body = []
    for idx, scenario in enumerate(sorted(by_scenario.keys())):
        scenario_label = format_scenario_label(scenario)
        runs = sorted(by_scenario[scenario], key=lambda item: item[2])
        latest_path = runs[-1][0]
        latest = runs[-1][1]
        latest_analysis = load_json(latest_path.parent / "virtbench_ai_analysis.json") or {}
        latest_summary = latest.get("summary", {})
        latest_capacity = latest.get("capacity", {})
        latest_scenario = latest.get("scenario", scenario)
        summary_rows = []
        round_rows = []

        for path, data, created_at in runs:
            run_name = path.parent.name
            objective = data.get("objective")
            summary = data.get("summary", {})
            capacity = data.get("capacity", {})
            summary_row = {
                "Run": run_name,
                "Created": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "Mode": data.get("mode") or ("execute" if data.get("execute") else "dry-run"),
                "Rounds": data.get("rounds_requested"),
                "Best Stable Total VMs": format_best_stable_total(summary, capacity, data),
                "Recommendation": summary.get("final_recommendation"),
                "Estimated Ceiling": capacity.get("estimated_vm_ceiling"),
                "Free CPU": capacity.get("free_cpu_cores"),
                "Free Memory GiB": capacity.get("free_memory_gib"),
                "Storage Driver": data.get("storage_driver"),
            }
            analysis = load_json(path.parent / "virtbench_ai_analysis.json") or {}
            if analysis:
                summary_row["Analysis Status"] = str(analysis.get("status", "")).title()
                summary_row["Limited By"] = format_ai_limited_by(analysis.get("limited_by"))
            if objective:
                summary_row["Objective"] = objective
            summary_rows.append(summary_row)

            data_scenario = data.get("scenario", latest_scenario)
            failure_by_round = {
                item.get("round"): item
                for item in analysis.get("failure_analysis", [])
                if isinstance(item, dict)
            }
            for item in data.get("rounds", []):
                success = item.get("success_rate")
                if isinstance(success, (int, float)):
                    success = round(success * 100, 2)
                failure = failure_by_round.get(item.get("round"))
                if failure and success in (None, 0, 0.0) and failure.get("success_percent") not in (None, ""):
                    success = failure.get("success_percent")
                row = {
                    "Run": run_name,
                    "Round": item.get("round"),
                    "Total VMs": first_display_value(
                        item.get("total_vms"),
                        failure.get("total_vms") if failure else None,
                        item.get("batch_size"),
                    ),
                    "AI Decision": format_ai_decision(item.get("decision")),
                    "Success %": success,
                }
                if data_scenario == "vm-workflow-basic":
                    durations = workflow_durations_for_round(item)
                    row.update({
                        "Total Workflow Duration": format_duration(durations.get("total_workflow_duration_sec")),
                        "VM Creation Duration": format_duration(durations.get("vm_creation_duration_sec")),
                        "Bootstorm Duration": format_duration(durations.get("bootstorm_duration_sec")),
                        "Migration Duration": format_duration(durations.get("migration_duration_sec")),
                    })
                else:
                    metric_column = ai_metric_column_label(data_scenario, data.get("objective"))
                    row[metric_column] = format_ai_metric_summary(item.get("key_metric"))
                row.update({
                    "Next Total VMs": item.get("next_total_vms", item.get("next_batch")),
                    "Result Dir": item.get("result_dir"),
                    "Command": item.get("command"),
                })
                round_rows.append(row)

        cards = [
            ("Scenario", scenario_label),
            ("Runs", len(runs)),
            ("Estimated Ceiling", latest_capacity.get("estimated_vm_ceiling", "N/A")),
        ]
        if latest_scenario not in assisted_scenarios:
            cards.insert(
                2,
                (
                    "Best Stable Total VMs",
                    format_best_stable_total(latest_summary, latest_capacity, latest),
                ),
            )
        else:
            cards.append(("Mode", latest.get("mode") or ("execute" if latest.get("execute") else "dry-run")))
        card_html = "".join(
            f"""
            <div class="col-12 col-md-3">
              <div class="card border-secondary mb-3">
                <div class="card-body">
                  <div class="text-muted small">{html.escape(str(label))}</div>
                  <div class="fw-bold">{html.escape(str(value))}</div>
                </div>
              </div>
            </div>
            """
            for label, value in cards
        )

        scenario_id = f"ai_{scenario}".replace(".", "_").replace("-", "_").replace("/", "_").replace(" ", "_")
        active_cls = "active" if idx == 0 else ""
        show_cls = "show" if idx == 0 else ""
        scenario_nav.append(
            f'<li class="nav-item"><button class="nav-link {active_cls}" '
            f'id="tab-{scenario_id}-tab" data-bs-toggle="tab" '
            f'data-bs-target="#tab_{scenario_id}" type="button" role="tab">'
            f'{html.escape(scenario_label)}</button></li>'
        )
        summary_table = df_to_html_table(
            pd.DataFrame(summary_rows),
            f"table_ai_{scenario_id}_summary"
        )
        rounds_table = df_to_html_table(
            pd.DataFrame(round_rows),
            f"table_ai_{scenario_id}_rounds"
        )
        recommendation = latest_summary.get("final_recommendation", "N/A")
        scenario_body.append(f"""
        <div class="tab-pane fade {show_cls} {active_cls}" id="tab_{scenario_id}" role="tabpanel">
          <div class="row">{card_html}</div>
          <p><strong>Latest recommendation:</strong> {html.escape(str(recommendation))}</p>
          {build_ai_analysis_panel(latest_analysis)}
          <h4>Runs</h4>
          {summary_table}
          <h4 class="mt-4">Round Decisions</h4>
          {rounds_table}
        </div>
        """)

    return f"""
    <h3>AI Validation Rounds</h3>
    <ul class="nav nav-pills mt-2" role="tablist">{''.join(scenario_nav)}</ul>
    <div class="tab-content mt-3">{''.join(scenario_body)}</div>
    """

def format_folder_name(folder_name: str) -> str:
    """Convert '20251014-165952_kubevirt-perf-test_1-50' → '2025-10-14 16:59:52 — 50 VMs'"""
    try:
        parts = folder_name.split("_")
        timestamp = parts[0]
        vm_range = parts[-1]
        dt = datetime.strptime(timestamp, "%Y%m%d-%H%M%S")

        if "-" in vm_range:
            start_str, end_str = vm_range.split("-")
            start = int(start_str)
            end = int(end_str)
            num_vms = (end - start) + 1
        else:
            num_vms = int(vm_range)

        suffix = "VM" if num_vms == 1 else "VMs"
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} — {num_vms} {suffix}"
    except Exception:
        return folder_name


def summary_to_df(summary_json):
    """
    Convert a summary JSON into a DataFrame for display.
    Ensures friendly metric names and rounds all numeric columns to 2 decimals.
    """
    if not summary_json or "metrics" not in summary_json:
        return pd.DataFrame(), None, None

    rows = []
    difference_row = None

    for m in summary_json["metrics"]:
        metric = m.get("metric")
        if metric == "difference_observed_vmim_sec":
            difference_row = m
            continue

        # Apply friendly names and rounding
        rows.append({
            "Metric": LABEL_MAP.get(metric, metric),
            "Average (s)": round(m.get("avg", 0), 2) if m.get("avg") is not None else None,
            "Max (s)": round(m.get("max", 0), 2) if m.get("max") is not None else None,
            "Min (s)": round(m.get("min", 0), 2) if m.get("min") is not None else None,
            "Count": m.get("count", ""),
        })

    df = pd.DataFrame(rows)
    total_info = {
        "total_vms": summary_json.get("total_vms"),
        "successful": summary_json.get("successful"),
        "failed": summary_json.get("failed")
    }
    return df, difference_row, total_info


def df_to_html_table(df: pd.DataFrame, table_id: str) -> str:
    """
    Convert DataFrame into an interactive HTML table (DataTables style).
    Applies consistent label mapping and rounds numeric values to 2 decimals
    for both summary and detailed result tables.
    """
    if df is None or df.empty:
        return f"<p>No data found for {table_id}</p>"

    column_rename_map = {
        "namespace": "Namespace",
        "running_time_sec": "Running Time (s)",
        "ping_time_sec": "Ping Time (s)",
        "reachability_lag_sec": "Ping Lag (s)",
        "clone_duration_sec": "Clone Duration (s)",
        "observed_time_sec": "Observed Time (s)",
        "vmim_time_sec": "VMIM Time (s)",
        "success": "Success",
        "status": "Status",
        "source_node": "Source Node",
        "target_node": "Target Node",
    }

    # Rename columns if present
    df = df.rename(columns={k: v for k, v in column_rename_map.items() if k in df.columns})

    numeric_cols = df.select_dtypes(include="number").columns
    df[numeric_cols] = df[numeric_cols].round(2)

    if "Success" in df.columns:
        df["Success"] = df["Success"].map({True: "True", False: "False"})

    def is_scalar_missing(value):
        if value is None:
            return True
        if not isinstance(value, (list, dict)):
            try:
                return bool(pd.isna(value))
            except (TypeError, ValueError):
                return False
        return False

    df = df.apply(lambda series: series.map(lambda value: "" if is_scalar_missing(value) else value))

    def format_cell(value, column):
        if value is None:
            return ""

        if isinstance(value, numbers.Real) and not isinstance(value, bool):
            text = str(int(value)) if float(value).is_integer() else f"{float(value):.2f}"
        elif isinstance(value, (list, dict)):
            text = json.dumps(value, sort_keys=True)
        else:
            text = str(value)
            if text in {"<NA>", "NaT", "nan"}:
                return ""
            try:
                numeric_text = float(text)
                if numeric_text.is_integer() and text.endswith(".0"):
                    text = str(int(numeric_text))
            except ValueError:
                pass

        escaped = html.escape(text)
        title = html.escape(text, quote=True)

        if column == "Command" and text:
            compact = " ".join(text.split())
            for token in (
                " datasource-clone ",
                " migration ",
                " validate-cluster ",
                " fio ",
                " disk-ops ",
                " chaos-benchmark ",
                " failure-recovery ",
                " elbencho ",
            ):
                idx = compact.find(token)
                if idx >= 0:
                    compact = compact[idx + 1:]
                    break
            preview = compact[:96] + ("..." if len(compact) > 96 else "")
            return (
                '<details class="cmd-cell">'
                f"<summary>{html.escape(preview)}</summary>"
                f"<pre>{escaped}</pre>"
                "</details>"
            )

        if column in {"Result Dir", "Run", "Namespace", "Source Node", "Target Node"}:
            return f'<code class="wrap-code" title="{title}">{escaped}</code>'

        if column.endswith("Summary") and (";" in text or "\n" in text):
            parts = [part.strip() for part in re.split(r";|\n", text) if part.strip()]
            if len(parts) > 1:
                lines = "".join(f"<span>{html.escape(part)}</span>" for part in parts)
                return f'<span class="stack-cell" title="{title}">{lines}</span>'

        if len(text) > 80:
            return f'<span class="wrap-cell" title="{title}">{escaped}</span>'

        return escaped

    df = df.rename(columns={column: html.escape(str(column)) for column in df.columns})
    rendered_df = df.copy()
    for column in rendered_df.columns:
        rendered_df[column] = rendered_df[column].map(lambda value, column=column: format_cell(value, column))

    table_html = rendered_df.to_html(
        classes="display compact virtbench-table",
        table_id=table_id,
        index=False,
        border=0,
        escape=False,
        na_rep="",
    )
    return f'<div class="table-frame">{table_html}</div>'

def get_vm_count_from_folder(folder_name: str) -> int:
    """Extract VM count from folder suffix.

    Handles formats:
    - 20251014-165952_kubevirt-perf-test_1-50 → 50 VMs
    - 20251207-123456_chaos_benchmark_15vms → 15 VMs
    """
    try:
        vm_range = folder_name.split("_")[-1]

        # Handle chaos benchmark format: "15vms"
        if vm_range.endswith("vms"):
            return int(vm_range[:-3])

        # Handle range format: "1-50"
        if "-" in vm_range:
            start_str, end_str = vm_range.split("-")
            start = int(start_str)
            end = int(end_str)
            return (end - start) + 1

        return int(vm_range)
    except Exception:
        return 0


def get_workflow_round_from_folder(folder_name: str):
    """Return the workflow round number from names like ai-workflow-r1_1-80."""
    match = re.search(r"(?:^|[_-])r(\d+)(?=[_-])", folder_name)
    if not match:
        return None
    return int(match.group(1))


def get_run_label_from_folder(folder_name: str):
    """Return a readable run label when the folder name carries a workflow round."""
    round_number = get_workflow_round_from_folder(folder_name)
    if round_number is None:
        return None
    return f"Run {round_number}"


def vm_count_label(vm_count) -> str:
    """Format a VM count for chart labels."""
    suffix = "VM" if vm_count == 1 else "VMs"
    return f"{vm_count} {suffix}"


def html_id(value) -> str:
    """Build a stable HTML id fragment from display values."""
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")


def format_mmss(seconds: float) -> str:
    """Format seconds → mm:ss"""
    if seconds is None:
        return "0:00"
    total = int(round(seconds))
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


# ---------------- Chart Builder ----------------
def build_bar_chart_mmss(records, title, color, chart_id):
    """Builds a compact 30%-width bar chart with mm:ss formatting and folder name in hover."""
    if not records:
        return f"<div class='col-12 col-md-4'><p>No data for {title}</p></div>"

    df = pd.DataFrame(records).sort_values(["VMs", "Folder"])
    vm_values = df["VMs"].tolist()
    y_secs = df["Seconds"].tolist()
    folder_labels = df["Folder"].tolist()  # used in hover only
    duplicate_vm_counts = df["VMs"].duplicated(keep=False).any()

    if duplicate_vm_counts:
        seen_by_vm = defaultdict(int)
        x_vals = []
        for _, row in df.iterrows():
            vm_count = row["VMs"]
            seen_by_vm[vm_count] += 1
            run_label = row.get("Run") or get_run_label_from_folder(row["Folder"]) or f"Run {seen_by_vm[vm_count]}"
            x_vals.append(f"{vm_count_label(vm_count)} ({run_label})")
    else:
        x_vals = [vm_count_label(v) for v in vm_values]

    max_sec = max(y_secs) if y_secs else 0
    step = 10 if max_sec <= 60 else 30 if max_sec <= 180 else 60 if max_sec <= 600 else 120
    ticks = list(range(0, int(max_sec + step), step))
    ticktext = [format_mmss(t) for t in ticks]
    duration_labels = [format_mmss(v) for v in y_secs]

    return f"""
    <div class="col-12 col-md-4">
      <div id="{chart_id}" class="plotly-chart" style="height:360px; width:100%;"></div>
      <script>
        Plotly.newPlot('{chart_id}', [{{
          x: {json.dumps(x_vals)},
          y: {json.dumps(y_secs)},
          type: 'bar',
          marker: {{
            color: '{color}',
            line: {{color: 'rgba(0,0,0,0.35)', width: 1}}
          }},
          hovertemplate: 'Folder: %{{text}}<br>Total VMs: %{{x}}<br>Duration: %{{customdata}}<extra></extra>',
          text: {json.dumps(folder_labels)},
          customdata: {json.dumps(duration_labels)},
          textposition: 'none'
        }}], {{
          title: '{title}',
          xaxis: {{
            title: 'Total VMs',
            type: 'category',
            categoryorder: 'array',
            categoryarray: {json.dumps(x_vals)}
          }},
          yaxis: {{
            title: 'Duration (mm:ss)',
            tickmode: 'array',
            tickvals: {json.dumps(ticks)},
            ticktext: {json.dumps(ticktext)}
          }},
          margin: {{t: 50, l: 50, r: 20, b: 50}},
          bargap: 0.3
        }}, {{responsive: true,  displayModeBar: false}});
      </script>
    </div>
    """


# ---------------- Folder Builders ----------------
def build_creation_boot_content(folder: Path = None, uid: str = "", boot_folder: Path = None) -> str:
    """Creation + Boot Storm section (original style)."""
    boot_source = boot_folder or folder
    creation_summary = load_json(folder / "summary_vm_creation_results.json") if folder else None
    boot_summary = load_json(boot_source / "summary_boot_storm_results.json") if boot_source else None
    creation_results = load_json(folder / "vm_creation_results.json") if folder else None
    boot_results = load_json(boot_source / "boot_storm_results.json") if boot_source else None

    creation_df = pd.DataFrame(creation_results or [])
    boot_df = pd.DataFrame(boot_results or [])
    if not creation_df.empty and {"running_time_sec", "ping_time_sec"}.issubset(creation_df.columns):
        creation_df["reachability_lag_sec"] = creation_df["ping_time_sec"] - creation_df["running_time_sec"]
    if not creation_df.empty and "success" in creation_df.columns:
        cols = [c for c in creation_df.columns if c != "success"] + ["success"]
        creation_df = creation_df[cols]

    creation_summary_df, _, creation_total_info = summary_to_df(creation_summary)
    boot_summary_df, _, boot_total_info = summary_to_df(boot_summary)
    creation_total_info = creation_total_info or {}
    boot_total_info = boot_total_info or {}

    creation_total_time = creation_summary.get("total_test_duration_sec") if creation_summary else None
    boot_total_time = boot_summary.get("total_test_duration_sec") if boot_summary else None
    creation_running_total_time = None
    if creation_summary:
        for metric in creation_summary.get("metrics", []):
            if metric.get("metric") == "running_time_sec":
                creation_running_total_time = metric.get("max")
                break

    total_vms = creation_total_info.get("total_vms") or boot_total_info.get("total_vms") or "?"
    if folder and boot_source and folder != boot_source:
        directory_html = (
            f'<h6><strong>Creation Directory:</strong> {folder.name}</h6>'
            f'<h6><strong>Boot Storm Directory:</strong> {boot_source.name}</h6>'
        )
    elif folder:
        directory_html = f'<h6><strong>Results Directory:</strong> {folder.name}</h6>'
    elif boot_source:
        directory_html = f'<h6><strong>Boot Storm Directory:</strong> {boot_source.name}</h6>'
    else:
        directory_html = ""

    header_html = (
        f'{directory_html}'
        f'<h6><strong>Total VMs:</strong> {total_vms}</h6>'
        f'{f"<h6><strong>Total VM Creation Duration:</strong> {creation_total_time:.2f} s</h6>" if creation_total_time else ""}'
        f'{f"<h6><strong>Total Running Duration:</strong> {creation_running_total_time:.2f} s</h6>" if creation_running_total_time is not None else ""}'
        f'{f"<h6><strong>Total Boot Storm Duration:</strong> {boot_total_time:.2f} s</h6>" if boot_total_time else ""}'
    )

    creation_summary_html = df_to_html_table(creation_summary_df, f"table_summary_creation_{uid}")
    boot_summary_html = df_to_html_table(boot_summary_df, f"table_summary_boot_{uid}")
    creation_html = df_to_html_table(creation_df, f"table_creation_{uid}")
    boot_html = df_to_html_table(boot_df, f"table_boot_{uid}")

    return f"""
    <div class="mb-4">
      {header_html}

      <h4 class="mt-4">Creation Summary</h4>
      {creation_summary_html}
      <p><strong>Total VMs:</strong> {creation_total_info.get("total_vms", "N/A")} |
         <strong>Successful:</strong> {creation_total_info.get("successful", "N/A")} |
         <strong>Failed:</strong> {creation_total_info.get("failed", "N/A")}</p>

      <h4 class="mt-5">Boot Storm Summary</h4>
      {boot_summary_html}
      <p><strong>Total VMs:</strong> {boot_total_info.get("total_vms", "N/A")} |
         <strong>Successful:</strong> {boot_total_info.get("successful", "N/A")} |
         <strong>Failed:</strong> {boot_total_info.get("failed", "N/A")}</p>

      <h4 class="mt-5">Creation Results</h4>
      {creation_html}

      <h4 class="mt-5">Boot Storm Results</h4>
      {boot_html}
    </div>
    """


def build_migration_content(folder: Path, uid: str) -> str:
    """Live Migration section (original style)."""
    migration_summary = load_json(folder / "summary_migration_results.json")
    migration_results = load_json(folder / "migration_results.json")

    migration_df = pd.DataFrame(migration_results or [])
    if not migration_df.empty and "status" in migration_df.columns:
        cols = [c for c in migration_df.columns if c != "status"] + ["status"]
        migration_df = migration_df[cols]

    migration_summary_df, migration_diff_row, migration_total_info = summary_to_df(migration_summary)
    migration_total_info = migration_total_info or {}
    total_time = migration_summary.get("total_migration_duration_sec") or migration_summary.get("total_test_duration_sec") if migration_summary else None

    header_html = (
        f'<h6><strong>Results Directory:</strong> {folder.name}</h6>'
        f'<h6><strong>Total VMs:</strong> {migration_total_info.get("total_vms", "?")}</h6>'
        f'{f"<h6><strong>Total Migration Duration:</strong> {total_time:.2f} s</h6>" if total_time else ""}'
    )

    migration_summary_html = df_to_html_table(migration_summary_df, f"table_summary_migration_{uid}")
    migration_html = df_to_html_table(migration_df, f"table_migration_{uid}")

    diff_line = (
        f"<p><strong>Average Difference (Observed - VMIM):</strong> "
        f"{migration_diff_row.get('avg', 'N/A')} s</p>" if migration_diff_row else ""
    )

    return f"""
    <div class="mb-4">
      {header_html}

      <h4 class="mt-4">Migration Summary</h4>
      {migration_summary_html}
      <p><strong>Total VMs:</strong> {migration_total_info.get("total_vms", "N/A")} |
         <strong>Successful:</strong> {migration_total_info.get("successful", "N/A")} |
         <strong>Failed:</strong> {migration_total_info.get("failed", "N/A")}</p>
      {diff_line}

      <h4 class="mt-5">Migration Results (Per VM)</h4>
      {migration_html}
    </div>
    """


def build_capacity_content(folder: Path, uid: str) -> str:
    """Chaos Benchmark section."""
    capacity_summary = load_json(folder / "summary_chaos_benchmark.json")
    capacity_results = load_json(folder / "chaos_benchmark_results.json")

    if not capacity_summary and not capacity_results:
        return "<p>No chaos benchmark data found.</p>"

    # Build header info
    total_vms = capacity_summary.get("total_vms", 0) if capacity_summary else 0
    total_pvcs = capacity_summary.get("total_pvcs", 0) if capacity_summary else 0
    iterations = capacity_summary.get("iterations_completed", 0) if capacity_summary else 0
    capacity_reached = capacity_summary.get("capacity_reached", False) if capacity_summary else False
    total_time = capacity_summary.get("total_test_duration_sec") if capacity_summary else None

    # Get config from detailed results
    config = capacity_results.get("config", {}) if capacity_results else {}
    results_data = capacity_results.get("results", {}) if capacity_results else {}

    header_html = f"""
    <h6><strong>Results Directory:</strong> {folder.name}</h6>
    <h6><strong>Storage Class(es):</strong> {config.get('storage_classes', 'N/A')}</h6>
    <h6><strong>Total VMs Created:</strong> {total_vms}</h6>
    <h6><strong>Total PVCs Created:</strong> {total_pvcs}</h6>
    <h6><strong>Iterations Completed:</strong> {iterations}</h6>
    {f"<h6><strong>Total Test Duration:</strong> {total_time:.2f} s</h6>" if total_time else ""}
    """

    # Capacity status
    if capacity_reached:
        status_html = '<span class="badge bg-success">Capacity Reached</span>'
    else:
        end_reason = results_data.get("end_reason", "unknown")
        if end_reason == "max_iterations":
            status_html = '<span class="badge bg-warning text-dark">Max Iterations Reached</span>'
        elif end_reason == "interrupted":
            status_html = '<span class="badge bg-secondary">Interrupted</span>'
        elif end_reason == "error":
            status_html = '<span class="badge bg-danger">Error</span>'
        else:
            status_html = f'<span class="badge bg-info">{end_reason}</span>'

    # Build config table
    config_rows = ""
    config_items = [
        ("VMs per Iteration", config.get("vms_per_iteration", "N/A")),
        ("Data Volumes per VM", config.get("data_volumes_per_vm", "N/A")),
        ("Volume Size", config.get("volume_size", "N/A")),
        ("VM Memory", config.get("vm_memory", "N/A")),
        ("VM CPU Cores", config.get("vm_cpu_cores", "N/A")),
    ]
    for label, value in config_items:
        config_rows += f"<tr><th>{label}</th><td>{value}</td></tr>"

    config_table = f"""
    <table class="table table-bordered w-auto">
      <tbody>{config_rows}</tbody>
    </table>
    """

    # Build results table
    results_rows = ""
    results_items = [
        ("Iterations Completed", iterations),
        ("Total VMs Created", total_vms),
        ("Total PVCs Created", total_pvcs),
        ("Capacity Reached", "Yes" if capacity_reached else "No"),
        ("End Reason", results_data.get("end_reason", "N/A")),
    ]
    for label, value in results_items:
        results_rows += f"<tr><th>{label}</th><td>{value}</td></tr>"

    results_table = f"""
    <table class="table table-bordered w-auto">
      <tbody>{results_rows}</tbody>
    </table>
    """

    # Phases skipped
    phases_skipped = capacity_results.get("phases_skipped", []) if capacity_results else []
    phases_html = ", ".join(phases_skipped) if phases_skipped else "None"

    return f"""
    <div class="mb-4">
      {header_html}
      <p><strong>Status:</strong> {status_html}</p>

      <h4 class="mt-4">Test Configuration</h4>
      {config_table}

      <h4 class="mt-4">Test Results</h4>
      {results_table}

      <p><strong>Phases Skipped:</strong> {phases_html}</p>
    </div>
    """


def build_fio_content(folder: Path, uid: str) -> str:
    """FIO Benchmark section - displays storage I/O performance metrics."""
    fio_summary = load_json(folder / "summary_fio_benchmark.json")
    fio_results = load_json(folder / "fio_benchmark_results.json")

    if not fio_summary and not fio_results:
        return "<p>No FIO benchmark data found.</p>"

    data = fio_summary or {}

    # Check if this is multi-VM format (has test_type: fio_benchmark)
    is_multi_vm = data.get("test_type") == "fio_benchmark"

    if is_multi_vm:
        return build_fio_multi_vm_content(folder, uid, data, fio_results)
    else:
        return build_fio_single_vm_content(folder, uid, data)


def _fmt_vm_cpus(summary: dict) -> str:
    """vCPUs (and CPU request) per FIO VM; older runs did not record it."""
    cpus = summary.get("vm_cpus")
    if cpus is None:
        return "not recorded (older runs used 2)"
    text = ", ".join(map(str, cpus)) if isinstance(cpus, list) else str(cpus)
    req = summary.get("vm_cpu_request")
    if req is not None:
        text += f" (CPU request {', '.join(map(str, req)) if isinstance(req, list) else req})"
    return text


def _fmt_placement(summary: dict) -> str:
    """Placement policy, whether it was verified, and VMs per node."""
    p = summary.get("placement")
    if not p:
        return "not recorded"
    status = {True: "verified even", False: "NOT even", None: "not enforced"}.get(p.get("balanced"), "unknown")
    counts = ", ".join(f"{n}: {c}" for n, c in (p.get("per_node_counts") or {}).items())
    return f"{p.get('policy', 'unknown')} ({status}){' &mdash; ' + counts if counts else ''}"


def build_fio_multi_vm_content(folder: Path, uid: str, summary: dict, per_vm_results: list) -> str:
    """Build FIO content for multi-VM benchmark results."""
    config = summary.get("config", {})
    metrics = summary.get("metrics", [])

    # Header
    total_vms = summary.get("total_vms", 0)
    successful = summary.get("successful", 0)
    failed = summary.get("failed", 0)
    duration = summary.get("total_test_duration_sec", 0)
    timestamp = summary.get("timestamp", "N/A")

    header_html = f"""
    <h6><strong>Results Directory:</strong> {folder.name}</h6>
    <h6><strong>Timestamp:</strong> {timestamp}</h6>
    <h6><strong>Total VMs:</strong> {total_vms} (Success: {successful}, Failed: {failed})</h6>
    <h6><strong>Test Duration:</strong> {duration:.1f} s</h6>
    """

    # Config table
    config_rows = "".join([
        f"<tr><th>Block Size</th><td>{config.get('bs', 'N/A')}</td></tr>",
        f"<tr><th>I/O Pattern</th><td>{config.get('rw', 'N/A')}</td></tr>",
        f"<tr><th>I/O Depth</th><td>{config.get('iodepth', 'N/A')}</td></tr>",
        f"<tr><th>Num Jobs</th><td>{config.get('numjobs', 'N/A')}</td></tr>",
        f"<tr><th>Runtime</th><td>{config.get('runtime', 'N/A')} s</td></tr>",
        f"<tr><th>File Size</th><td>{config.get('size', 'N/A')}</td></tr>",
        f"<tr><th>vCPUs per VM</th><td>{_fmt_vm_cpus(summary)}</td></tr>",
        f"<tr><th>VM Placement</th><td>{_fmt_placement(summary)}</td></tr>",
    ])
    config_table = f'<table class="table table-bordered w-auto"><tbody>{config_rows}</tbody></table>'

    # Format metric names properly
    def format_metric_name(metric: str) -> str:
        name_map = {
            "read_iops": "Read IOPS",
            "write_iops": "Write IOPS",
            "read_bw_mibps": "Read BW (MiB/s)",
            "write_bw_mibps": "Write BW (MiB/s)",
            "read_lat_ms": "Read Latency (ms)",
            "write_lat_ms": "Write Latency (ms)",
        }
        return name_map.get(metric, metric.replace("_", " ").title())

    # Summary metrics table (Avg/Max/Min)
    metrics_rows = ""
    for m in metrics:
        metric_key = m.get("metric", "")
        metric_name = format_metric_name(metric_key)
        avg_val = m.get("avg", 0)
        max_val = m.get("max", 0)
        min_val = m.get("min", 0)

        # Format based on metric type
        if "iops" in metric_key:
            metrics_rows += f"<tr><th>{metric_name}</th><td>{avg_val:,.0f}</td><td>{max_val:,.0f}</td><td>{min_val:,.0f}</td></tr>"
        elif "bw" in metric_key:
            metrics_rows += f"<tr><th>{metric_name}</th><td>{avg_val:,.2f}</td><td>{max_val:,.2f}</td><td>{min_val:,.2f}</td></tr>"
        else:
            metrics_rows += f"<tr><th>{metric_name}</th><td>{avg_val:,.3f}</td><td>{max_val:,.3f}</td><td>{min_val:,.3f}</td></tr>"

    metrics_table = f"""
    <table class="table table-bordered table-striped w-auto">
      <thead class="table-dark"><tr><th>Metric</th><th>Avg</th><th>Max</th><th>Min</th></tr></thead>
      <tbody>{metrics_rows}</tbody>
    </table>
    """

    # Per-VM results table (collapsible) with clickable namespaces
    per_vm_rows = ""
    per_vm_data_js = []
    if isinstance(per_vm_results, list):
        for idx, r in enumerate(per_vm_results):
            ns = r.get("namespace", "N/A")
            success = r.get("success", False)
            status_badge = '<span class="badge bg-success">✓</span>' if success else '<span class="badge bg-danger">✗</span>'
            read_iops = r.get('read_iops', 0)
            write_iops = r.get('write_iops', 0)
            read_bw = r.get('read_bw_mibps', 0)
            write_bw = r.get('write_bw_mibps', 0)
            read_lat = r.get('read_lat_ms', 0)
            write_lat = r.get('write_lat_ms', 0)

            per_vm_data_js.append({
                "ns": ns, "read_iops": read_iops, "write_iops": write_iops,
                "read_bw": read_bw, "write_bw": write_bw, "read_lat": read_lat, "write_lat": write_lat
            })

            per_vm_rows += f"""
            <tr class="vm-row-clickable" style="cursor: pointer;" onclick="showVmChart_{uid}({idx})">
              <td>{status_badge} <a href="#" onclick="event.preventDefault(); showVmChart_{uid}({idx});">{ns}</a>{f' <small class="text-muted">({html.escape(str(r["node"]))})</small>' if r.get("node") else ""}</td>
              <td>{read_iops:,.0f}</td>
              <td>{write_iops:,.0f}</td>
              <td>{read_bw:,.2f}</td>
              <td>{write_bw:,.2f}</td>
              <td>{read_lat:,.3f}</td>
              <td>{write_lat:,.3f}</td>
            </tr>
            """

    import json
    per_vm_data_json = json.dumps(per_vm_data_js)

    per_vm_table = f"""
    <div class="accordion" id="perVmAccordion_{uid}">
      <div class="accordion-item">
        <h2 class="accordion-header">
          <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse"
                  data-bs-target="#perVmCollapse_{uid}">
            Per-VM Results ({total_vms} VMs) - Click namespace to view chart
          </button>
        </h2>
        <div id="perVmCollapse_{uid}" class="accordion-collapse collapse">
          <div class="accordion-body">
            <table class="table table-sm table-hover table-striped">
              <thead class="table-light">
                <tr>
                  <th>Namespace</th>
                  <th>Read IOPS</th>
                  <th>Write IOPS</th>
                  <th>Read BW (MiB/s)</th>
                  <th>Write BW (MiB/s)</th>
                  <th>Read Latency (ms)</th>
                  <th>Write Latency (ms)</th>
                </tr>
              </thead>
              <tbody>{per_vm_rows}</tbody>
            </table>
            <!-- Per-VM Chart Modal -->
            <div id="vmChartModal_{uid}" class="modal fade" tabindex="-1">
              <div class="modal-dialog modal-lg">
                <div class="modal-content">
                  <div class="modal-header">
                    <h5 class="modal-title" id="vmChartTitle_{uid}">VM Performance</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                  </div>
                  <div class="modal-body">
                    <div class="row">
                      <div class="col-md-6"><canvas id="vmIopsChart_{uid}" height="200"></canvas></div>
                      <div class="col-md-6"><canvas id="vmBwChart_{uid}" height="200"></canvas></div>
                    </div>
                    <div class="row mt-3">
                      <div class="col-12"><canvas id="vmLatChart_{uid}" height="150"></canvas></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <script>
              var perVmData_{uid} = {per_vm_data_json};
              var vmIopsChartInstance_{uid} = null;
              var vmBwChartInstance_{uid} = null;
              var vmLatChartInstance_{uid} = null;

              function showVmChart_{uid}(idx) {{
                var vm = perVmData_{uid}[idx];
                document.getElementById('vmChartTitle_{uid}').innerText = vm.ns + ' Performance';

                // Destroy existing charts
                if (vmIopsChartInstance_{uid}) vmIopsChartInstance_{uid}.destroy();
                if (vmBwChartInstance_{uid}) vmBwChartInstance_{uid}.destroy();
                if (vmLatChartInstance_{uid}) vmLatChartInstance_{uid}.destroy();

                // IOPS Chart
                vmIopsChartInstance_{uid} = new Chart(document.getElementById('vmIopsChart_{uid}'), {{
                  type: 'bar',
                  data: {{
                    labels: ['Read', 'Write'],
                    datasets: [{{ data: [vm.read_iops, vm.write_iops], backgroundColor: ['#2196F3', '#f44336'] }}]
                  }},
                  options: {{ responsive: true, plugins: {{ legend: {{ display: false }}, title: {{ display: true, text: 'IOPS' }} }} }}
                }});

                // Bandwidth Chart
                vmBwChartInstance_{uid} = new Chart(document.getElementById('vmBwChart_{uid}'), {{
                  type: 'bar',
                  data: {{
                    labels: ['Read', 'Write'],
                    datasets: [{{ data: [vm.read_bw, vm.write_bw], backgroundColor: ['#2196F3', '#f44336'] }}]
                  }},
                  options: {{ responsive: true, plugins: {{ legend: {{ display: false }}, title: {{ display: true, text: 'Bandwidth (MiB/s)' }} }} }}
                }});

                // Latency Chart
                vmLatChartInstance_{uid} = new Chart(document.getElementById('vmLatChart_{uid}'), {{
                  type: 'bar',
                  data: {{
                    labels: ['Read Latency', 'Write Latency'],
                    datasets: [{{ data: [vm.read_lat, vm.write_lat], backgroundColor: ['#4CAF50', '#FF9800'] }}]
                  }},
                  options: {{ responsive: true, plugins: {{ legend: {{ display: false }}, title: {{ display: true, text: 'Latency (ms)' }} }} }}
                }});

                var modal = new bootstrap.Modal(document.getElementById('vmChartModal_{uid}'));
                modal.show();
              }}
            </script>
          </div>
        </div>
      </div>
    </div>
    """

    # Extract metrics for charts
    read_iops = write_iops = read_bw = write_bw = read_lat = write_lat = 0
    for m in metrics:
        metric = m.get("metric", "")
        avg = m.get("avg", 0)
        if metric == "read_iops": read_iops = avg
        elif metric == "write_iops": write_iops = avg
        elif metric == "read_bw_mibps": read_bw = avg
        elif metric == "write_bw_mibps": write_bw = avg
        elif metric == "read_lat_ms": read_lat = avg
        elif metric == "write_lat_ms": write_lat = avg

    total_iops = read_iops + write_iops
    total_bw = read_bw + write_bw

    # Config subtitle (fio-plot style)
    io_pattern = config.get('rw', 'N/A')
    block_size = config.get('bs', 'N/A')
    io_depth = config.get('iodepth', 'N/A')
    num_jobs = config.get('numjobs', 'N/A')
    runtime = config.get('runtime', 'N/A')

    config_subtitle = f"| rw {io_pattern} | bs {block_size} | iodepth {io_depth} | numjobs {num_jobs} | runtime {runtime}s |"

    chart_html = f"""
    <div class="text-center mb-4" style="font-family: monospace; font-size: 0.95rem; background: #f8f9fa; padding: 10px; border-radius: 6px;">
      <strong>FIO Config:</strong> {config_subtitle}
    </div>

    <!-- KPI Summary Cards -->
    <div class="row mb-4">
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">Total IOPS</div>
            <div class="text-white fw-bold fs-4">{total_iops:,.0f}</div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">Total BW</div>
            <div class="text-white fw-bold fs-4">{total_bw:,.1f} <small>MiB/s</small></div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">Read Latency</div>
            <div class="text-white fw-bold fs-4">{read_lat:,.2f} <small>ms</small></div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">Write Latency</div>
            <div class="text-white fw-bold fs-4">{write_lat:,.2f} <small>ms</small></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Main Performance Chart - IOPS with Latency overlay -->
    <div class="card shadow-sm mb-4">
      <div class="card-header bg-dark text-white">
        <strong>Performance Overview</strong> <span class="text-muted small">— IOPS (bars) &amp; Latency (line)</span>
      </div>
      <div class="card-body">
        <canvas id="fioMainChart_{uid}" height="120"></canvas>
      </div>
    </div>

    <!-- Bandwidth Chart -->
    <div class="row mb-4">
      <div class="col-md-6">
        <div class="card shadow-sm h-100">
          <div class="card-header bg-light"><strong>IOPS Breakdown</strong></div>
          <div class="card-body">
            <canvas id="fioIopsChart_{uid}" height="160"></canvas>
          </div>
        </div>
      </div>
      <div class="col-md-6">
        <div class="card shadow-sm h-100">
          <div class="card-header bg-light"><strong>Bandwidth Breakdown</strong></div>
          <div class="card-body">
            <canvas id="fioBwChart_{uid}" height="160"></canvas>
          </div>
        </div>
      </div>
    </div>

    <script>
      // Main dual-axis chart (IOPS bars + Latency line)
      new Chart(document.getElementById('fioMainChart_{uid}'), {{
        type: 'bar',
        data: {{
          labels: ['Read', 'Write'],
          datasets: [
            {{
              label: 'IOPS',
              data: [{read_iops}, {write_iops}],
              backgroundColor: ['rgba(52, 152, 219, 0.85)', 'rgba(231, 76, 60, 0.85)'],
              borderColor: ['#2980b9', '#c0392b'],
              borderWidth: 2,
              borderRadius: 4,
              yAxisID: 'y'
            }},
            {{
              label: 'Latency (ms)',
              data: [{read_lat}, {write_lat}],
              type: 'line',
              borderColor: '#f39c12',
              backgroundColor: 'rgba(243, 156, 18, 0.2)',
              borderWidth: 3,
              pointRadius: 6,
              pointBackgroundColor: '#f39c12',
              fill: false,
              yAxisID: 'y1'
            }}
          ]
        }},
        options: {{
          responsive: true,
          interaction: {{ mode: 'index', intersect: false }},
          plugins: {{
            legend: {{ position: 'top' }},
            tooltip: {{ callbacks: {{ label: function(ctx) {{ return ctx.dataset.label + ': ' + ctx.parsed.y.toLocaleString(); }} }} }}
          }},
          scales: {{
            y: {{ type: 'linear', position: 'left', beginAtZero: true, title: {{ display: true, text: 'IOPS', font: {{ weight: 'bold' }} }}, grid: {{ color: 'rgba(0,0,0,0.05)' }} }},
            y1: {{ type: 'linear', position: 'right', beginAtZero: true, title: {{ display: true, text: 'Latency (ms)', font: {{ weight: 'bold' }} }}, grid: {{ drawOnChartArea: false }} }}
          }}
        }}
      }});

      // IOPS horizontal bar
      new Chart(document.getElementById('fioIopsChart_{uid}'), {{
        type: 'bar',
        data: {{
          labels: ['Read', 'Write'],
          datasets: [{{ data: [{read_iops}, {write_iops}], backgroundColor: ['#3498db', '#e74c3c'], borderRadius: 4 }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{ x: {{ beginAtZero: true, title: {{ display: true, text: 'IOPS' }} }} }}
        }},
        plugins: [{{
          afterDatasetsDraw: function(chart) {{
            var ctx = chart.ctx;
            chart.data.datasets.forEach(function(dataset, i) {{
              var meta = chart.getDatasetMeta(i);
              meta.data.forEach(function(bar, index) {{
                var data = dataset.data[index].toLocaleString();
                ctx.fillStyle = '#fff';
                ctx.font = 'bold 13px sans-serif';
                ctx.textAlign = 'right';
                ctx.fillText(data, bar.x - 10, bar.y + 5);
              }});
            }});
          }}
        }}]
      }});

      // Bandwidth horizontal bar
      new Chart(document.getElementById('fioBwChart_{uid}'), {{
        type: 'bar',
        data: {{
          labels: ['Read', 'Write'],
          datasets: [{{ data: [{read_bw}, {write_bw}], backgroundColor: ['#3498db', '#e74c3c'], borderRadius: 4 }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{ x: {{ beginAtZero: true, title: {{ display: true, text: 'MiB/s' }} }} }}
        }},
        plugins: [{{
          afterDatasetsDraw: function(chart) {{
            var ctx = chart.ctx;
            chart.data.datasets.forEach(function(dataset, i) {{
              var meta = chart.getDatasetMeta(i);
              meta.data.forEach(function(bar, index) {{
                var data = dataset.data[index].toFixed(2);
                ctx.fillStyle = '#fff';
                ctx.font = 'bold 13px sans-serif';
                ctx.textAlign = 'right';
                ctx.fillText(data, bar.x - 10, bar.y + 5);
              }});
            }});
          }}
        }}]
      }});
    </script>
    """

    return f"""
    <div class="mb-4">
      {header_html}
      <h4 class="mt-4">FIO Configuration</h4>
      {config_table}
      <h4 class="mt-4">Aggregated Results (Across All VMs)</h4>
      {metrics_table}
      {chart_html}
      <h4 class="mt-4">Per-VM Details</h4>
      {per_vm_table}
    </div>
    """


def build_fio_single_vm_content(folder: Path, uid: str, data: dict) -> str:
    """Build FIO content for single-VM benchmark results."""
    config = data.get("config", {})
    job_config = data.get("job_config", config)
    metrics = data.get("metrics", {})
    read_metrics = metrics.get("read", {})
    write_metrics = metrics.get("write", {})

    test_name = data.get("test_name", "FIO Benchmark")
    timestamp = data.get("timestamp", "N/A")
    runtime = data.get("runtime_sec", config.get("runtime", "N/A"))

    header_html = f"""
    <h6><strong>Results Directory:</strong> {folder.name}</h6>
    <h6><strong>Test Name:</strong> {test_name}</h6>
    <h6><strong>Timestamp:</strong> {timestamp}</h6>
    <h6><strong>Runtime:</strong> {runtime} s</h6>
    """

    config_rows = ""
    config_items = [
        ("Block Size", job_config.get("bs", config.get("bs", "N/A"))),
        ("I/O Pattern", job_config.get("rw", config.get("rw", "N/A"))),
        ("I/O Engine", job_config.get("ioengine", config.get("ioengine", "libaio"))),
        ("I/O Depth", job_config.get("iodepth", config.get("iodepth", "N/A"))),
        ("Num Jobs", job_config.get("numjobs", config.get("numjobs", "N/A"))),
        ("Direct I/O", "Yes" if job_config.get("direct", config.get("direct", 1)) else "No"),
        ("File Size", job_config.get("size", config.get("size", "N/A"))),
    ]
    for label, value in config_items:
        config_rows += f"<tr><th>{label}</th><td>{value}</td></tr>"

    config_table = f'<table class="table table-bordered w-auto"><tbody>{config_rows}</tbody></table>'

    perf_rows = ""
    if read_metrics:
        read_iops = read_metrics.get("iops", 0)
        read_bw = read_metrics.get("bw_mibps", 0)
        read_lat = read_metrics.get("lat_mean_ms", 0)
        if read_iops or read_bw:
            perf_rows += f"<tr><th>Read IOPS</th><td>{read_iops:,.2f}</td></tr>"
            perf_rows += f"<tr><th>Read Bandwidth</th><td>{read_bw:,.2f} MiB/s</td></tr>"
            perf_rows += f"<tr><th>Read Latency</th><td>{read_lat:,.3f} ms</td></tr>"

    if write_metrics:
        write_iops = write_metrics.get("iops", 0)
        write_bw = write_metrics.get("bw_mibps", 0)
        write_lat = write_metrics.get("lat_mean_ms", 0)
        if write_iops or write_bw:
            perf_rows += f"<tr><th>Write IOPS</th><td>{write_iops:,.2f}</td></tr>"
            perf_rows += f"<tr><th>Write Bandwidth</th><td>{write_bw:,.2f} MiB/s</td></tr>"
            perf_rows += f"<tr><th>Write Latency</th><td>{write_lat:,.3f} ms</td></tr>"

    perf_table = f'<table class="table table-bordered w-auto"><tbody>{perf_rows}</tbody></table>' if perf_rows else "<p>No metrics.</p>"

    status = data.get("status", "completed")
    status_html = '<span class="badge bg-success">Completed</span>' if status == "completed" else f'<span class="badge bg-info">{status}</span>'

    return f"""
    <div class="mb-4">
      {header_html}
      <p><strong>Status:</strong> {status_html}</p>
      <h4 class="mt-4">Job Configuration</h4>
      {config_table}
      <h4 class="mt-4">Performance Results</h4>
      {perf_table}
    </div>
    """


# ---------------- Elbencho Benchmark Builder ----------------
def build_elbencho_content(folder: Path, uid: str) -> str:
    """Elbencho Benchmark section - displays storage I/O performance metrics from elbencho."""
    elbencho_results = load_json(folder / "aggregated_results.json")

    if not elbencho_results:
        return "<p>No elbencho benchmark data found.</p>"

    # Extract data from aggregated_results.json
    timestamp = elbencho_results.get("timestamp", "N/A")
    vms_with_results = elbencho_results.get("vms_with_results", 0)
    vms_skipped = elbencho_results.get("vms_skipped", 0)
    elapsed_seconds = elbencho_results.get("elapsed_seconds", 0)
    aggregated = elbencho_results.get("aggregated", {})
    per_vm_results = elbencho_results.get("per_vm_results", [])

    # Aggregated metrics
    total_iops = aggregated.get("total_iops", 0)
    read_iops = aggregated.get("read_iops", 0)
    write_iops = aggregated.get("write_iops", 0)
    total_throughput_bytes = aggregated.get("total_throughput_bytes", 0)
    read_throughput_bytes = aggregated.get("read_throughput_bytes", 0)
    write_throughput_bytes = aggregated.get("write_throughput_bytes", 0)
    avg_latency_us = aggregated.get("avg_latency_us", 0)
    min_latency_us = aggregated.get("min_latency_us", 0)
    max_latency_us = aggregated.get("max_latency_us", 0)

    # Convert throughput to MB/s
    total_throughput_mbs = total_throughput_bytes / 1024 / 1024
    read_throughput_mbs = read_throughput_bytes / 1024 / 1024
    write_throughput_mbs = write_throughput_bytes / 1024 / 1024

    # Convert latency to ms
    avg_latency_ms = avg_latency_us / 1000
    min_latency_ms = min_latency_us / 1000
    max_latency_ms = max_latency_us / 1000

    # Header
    header_html = f"""
    <h6><strong>Results Directory:</strong> {folder.name}</h6>
    <h6><strong>Timestamp:</strong> {timestamp}</h6>
    <h6><strong>VMs with Results:</strong> {vms_with_results} (Skipped: {vms_skipped})</h6>
    <h6><strong>Collection Duration:</strong> {elapsed_seconds:.1f} s</h6>
    """

    # KPI Summary Cards (similar to FIO)
    kpi_html = f"""
    <div class="row mb-4">
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">Total IOPS</div>
            <div class="text-white fw-bold fs-4">{total_iops:,.0f}</div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">Total Throughput</div>
            <div class="text-white fw-bold fs-4">{total_throughput_mbs:,.1f} <small>MB/s</small></div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">Avg Latency</div>
            <div class="text-white fw-bold fs-4">{avg_latency_ms:,.2f} <small>ms</small></div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color: rgba(255,255,255,0.7); font-size: 0.8rem;">VMs Tested</div>
            <div class="text-white fw-bold fs-4">{vms_with_results}</div>
          </div>
        </div>
      </div>
    </div>
    """

    # Aggregated metrics table
    metrics_rows = f"""
    <tr><th>Read IOPS</th><td>{read_iops:,.0f}</td></tr>
    <tr><th>Write IOPS</th><td>{write_iops:,.0f}</td></tr>
    <tr><th>Total IOPS</th><td>{total_iops:,.0f}</td></tr>
    <tr><th>Read Throughput</th><td>{read_throughput_mbs:,.2f} MB/s</td></tr>
    <tr><th>Write Throughput</th><td>{write_throughput_mbs:,.2f} MB/s</td></tr>
    <tr><th>Total Throughput</th><td>{total_throughput_mbs:,.2f} MB/s</td></tr>
    <tr><th>Avg Latency</th><td>{avg_latency_ms:,.3f} ms ({avg_latency_us:,.0f} µs)</td></tr>
    <tr><th>Min Latency</th><td>{min_latency_ms:,.3f} ms ({min_latency_us:,.0f} µs)</td></tr>
    <tr><th>Max Latency</th><td>{max_latency_ms:,.3f} ms ({max_latency_us:,.0f} µs)</td></tr>
    """
    metrics_table = f"""
    <table class="table table-bordered table-striped w-auto">
      <thead class="table-dark"><tr><th>Metric</th><th>Value</th></tr></thead>
      <tbody>{metrics_rows}</tbody>
    </table>
    """

    # Charts
    chart_html = f"""
    <!-- Main Performance Chart - IOPS with Latency overlay -->
    <div class="card shadow-sm mb-4">
      <div class="card-header bg-dark text-white">
        <strong>Performance Overview</strong> <span class="text-muted small">— IOPS (bars) &amp; Latency (line)</span>
      </div>
      <div class="card-body">
        <canvas id="elbenchoMainChart_{uid}" height="120"></canvas>
      </div>
    </div>

    <!-- IOPS and Throughput Charts -->
    <div class="row mb-4">
      <div class="col-md-6">
        <div class="card shadow-sm h-100">
          <div class="card-header bg-light"><strong>IOPS Breakdown</strong></div>
          <div class="card-body">
            <canvas id="elbenchoIopsChart_{uid}" height="160"></canvas>
          </div>
        </div>
      </div>
      <div class="col-md-6">
        <div class="card shadow-sm h-100">
          <div class="card-header bg-light"><strong>Throughput Breakdown</strong></div>
          <div class="card-body">
            <canvas id="elbenchoThroughputChart_{uid}" height="160"></canvas>
          </div>
        </div>
      </div>
    </div>

    <script>
      // Main dual-axis chart (IOPS bars + Latency line)
      new Chart(document.getElementById('elbenchoMainChart_{uid}'), {{
        type: 'bar',
        data: {{
          labels: ['Read', 'Write'],
          datasets: [
            {{
              label: 'IOPS',
              data: [{read_iops}, {write_iops}],
              backgroundColor: ['rgba(52, 152, 219, 0.85)', 'rgba(231, 76, 60, 0.85)'],
              borderColor: ['#2980b9', '#c0392b'],
              borderWidth: 2,
              borderRadius: 4,
              yAxisID: 'y'
            }},
            {{
              label: 'Latency (ms)',
              data: [{avg_latency_ms}, {avg_latency_ms}],
              type: 'line',
              borderColor: '#f39c12',
              backgroundColor: 'rgba(243, 156, 18, 0.2)',
              borderWidth: 3,
              pointRadius: 6,
              pointBackgroundColor: '#f39c12',
              fill: false,
              yAxisID: 'y1'
            }}
          ]
        }},
        options: {{
          responsive: true,
          interaction: {{ mode: 'index', intersect: false }},
          plugins: {{
            legend: {{ position: 'top' }},
            tooltip: {{ callbacks: {{ label: function(ctx) {{ return ctx.dataset.label + ': ' + ctx.parsed.y.toLocaleString(); }} }} }}
          }},
          scales: {{
            y: {{ type: 'linear', position: 'left', beginAtZero: true, title: {{ display: true, text: 'IOPS', font: {{ weight: 'bold' }} }}, grid: {{ color: 'rgba(0,0,0,0.05)' }} }},
            y1: {{ type: 'linear', position: 'right', beginAtZero: true, title: {{ display: true, text: 'Latency (ms)', font: {{ weight: 'bold' }} }}, grid: {{ drawOnChartArea: false }} }}
          }}
        }}
      }});

      // IOPS horizontal bar
      new Chart(document.getElementById('elbenchoIopsChart_{uid}'), {{
        type: 'bar',
        data: {{
          labels: ['Read', 'Write'],
          datasets: [{{ data: [{read_iops}, {write_iops}], backgroundColor: ['#3498db', '#e74c3c'], borderRadius: 4 }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{ x: {{ beginAtZero: true, title: {{ display: true, text: 'IOPS' }} }} }}
        }}
      }});

      // Throughput horizontal bar
      new Chart(document.getElementById('elbenchoThroughputChart_{uid}'), {{
        type: 'bar',
        data: {{
          labels: ['Read', 'Write'],
          datasets: [{{ data: [{read_throughput_mbs}, {write_throughput_mbs}], backgroundColor: ['#3498db', '#e74c3c'], borderRadius: 4 }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{ x: {{ beginAtZero: true, title: {{ display: true, text: 'MB/s' }} }} }}
        }}
      }});
    </script>
    """

    # Per-VM results table (collapsible)
    per_vm_rows = ""
    if isinstance(per_vm_results, list):
        for r in per_vm_results:
            ns = r.get("namespace", "N/A")
            vm_name = r.get("vm_name", "N/A")
            vm_iops = r.get("total_iops", 0)
            vm_read_iops = r.get("total_read_iops", 0)
            vm_write_iops = r.get("total_write_iops", 0)
            vm_throughput = r.get("total_throughput_bytes", 0) / 1024 / 1024
            vm_latency = r.get("avg_latency_us", 0)
            skipped = r.get("skipped", False)
            error = r.get("error", "")

            if skipped:
                status_badge = f'<span class="badge bg-warning">Skipped</span>'
            elif error:
                status_badge = f'<span class="badge bg-danger" title="{error}">Error</span>'
            else:
                status_badge = '<span class="badge bg-success">✓</span>'

            per_vm_rows += f"""
            <tr>
              <td>{status_badge} {ns}/{vm_name}</td>
              <td>{vm_read_iops:,.0f}</td>
              <td>{vm_write_iops:,.0f}</td>
              <td>{vm_iops:,.0f}</td>
              <td>{vm_throughput:,.2f}</td>
              <td>{vm_latency:,.0f}</td>
            </tr>
            """

    per_vm_table = f"""
    <div class="accordion" id="perVmAccordionElbencho_{uid}">
      <div class="accordion-item">
        <h2 class="accordion-header">
          <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse"
                  data-bs-target="#perVmCollapseElbencho_{uid}">
            Per-VM Results ({vms_with_results} VMs)
          </button>
        </h2>
        <div id="perVmCollapseElbencho_{uid}" class="accordion-collapse collapse">
          <div class="accordion-body">
            <table class="table table-sm table-hover table-striped">
              <thead class="table-light">
                <tr>
                  <th>VM</th>
                  <th>Read IOPS</th>
                  <th>Write IOPS</th>
                  <th>Total IOPS</th>
                  <th>Throughput (MB/s)</th>
                  <th>Avg Latency (µs)</th>
                </tr>
              </thead>
              <tbody>{per_vm_rows}</tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    """

    return f"""
    <div class="mb-4">
      {header_html}
      {kpi_html}
      <h4 class="mt-4">Aggregated Results (Across All VMs)</h4>
      {metrics_table}
      {chart_html}
      <h4 class="mt-4">Per-VM Details</h4>
      {per_vm_table}
    </div>
    """


# ---------------- Disk Ops Benchmark Builder ----------------
def build_disk_ops_content(folder: Path, uid: str) -> str:
    """Disk Operations Benchmark section - hotplug, coldplug, and unplug timing metrics."""
    import json as _json

    data = load_json(folder / "disk_ops_results.json")

    if not data:
        return "<p>No disk operations benchmark data found.</p>"

    config = data.get("config", {})
    summary = data.get("summary", {})
    hotplug = data.get("hotplug") or {}
    coldplug = data.get("coldplug") or {}
    unplug = data.get("unplug") or {}
    per_vm = data.get("per_vm_results", []) or []

    timestamp = data.get("timestamp", "N/A")
    total_vms = summary.get("total_vms", 0)
    total_ops = summary.get("total_operations", 0)

    # Header
    header_html = f"""
    <h6><strong>Timestamp:</strong> {timestamp}</h6>
    <h6><strong>Total VMs:</strong> {total_vms} &nbsp;|&nbsp; <strong>Total Operations:</strong> {total_ops}</h6>
    """

    # Config table
    op_label = config.get("operation", "N/A").upper()
    config_rows = "".join([
        f"<tr><th>Operation</th><td>{op_label}</td></tr>",
        f"<tr><th>Disks per VM</th><td>{config.get('disks_per_vm', 'N/A')}</td></tr>",
        f"<tr><th>Disk Size</th><td>{config.get('disk_size', 'N/A')}</td></tr>",
        f"<tr><th>Storage Class</th><td>{config.get('storage_class', 'N/A')}</td></tr>",
        f"<tr><th>Test Unplug</th><td>{'Yes' if config.get('test_unplug') else 'No'}</td></tr>",
        f"<tr><th>Skip Validation</th><td>{'Yes' if config.get('skip_validation') else 'No'}</td></tr>",
    ])
    config_table = f'<table class="table table-bordered w-auto"><tbody>{config_rows}</tbody></table>'

    # KPI cards — success rates for all three operations
    def success_pct(d):
        c = d.get("count", 0)
        s = d.get("success_count", 0)
        return f"{s}/{c} ({100*s/c:.0f}%)" if c else "N/A"

    hp_rate = success_pct(hotplug) if hotplug else "N/A"
    cp_rate = success_pct(coldplug) if coldplug else "N/A"
    up_rate = success_pct(unplug) if unplug else "N/A"

    kpi_html = f"""
    <div class="row mb-4">
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color:rgba(255,255,255,0.7);font-size:0.8rem;">Total VMs</div>
            <div class="text-white fw-bold fs-4">{total_vms}</div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color:rgba(255,255,255,0.7);font-size:0.8rem;">Hotplug Success</div>
            <div class="text-white fw-bold fs-4">{hp_rate}</div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color:rgba(255,255,255,0.7);font-size:0.8rem;">Coldplug Success</div>
            <div class="text-white fw-bold fs-4">{cp_rate}</div>
          </div>
        </div>
      </div>
      <div class="col-md-3">
        <div class="card text-center border-0 shadow-sm" style="background: #1e3a5f;">
          <div class="card-body py-3">
            <div style="color:rgba(255,255,255,0.7);font-size:0.8rem;">Unplug Success</div>
            <div class="text-white fw-bold fs-4">{up_rate}</div>
          </div>
        </div>
      </div>
    </div>
    """

    # Operation summary tables
    def op_table(title, d, extra_rows=""):
        if not d:
            return ""
        count = d.get("count", 0)
        success = d.get("success_count", 0)
        badge = "bg-success" if success == count else ("bg-warning text-dark" if success > 0 else "bg-danger")
        rows = f"""
        <tr><th>VMs</th><td>{count}</td></tr>
        <tr><th>Successful</th><td><span class="badge {badge}">{success}/{count}</span></td></tr>
        {extra_rows}
        <tr><th>Avg Total Time</th><td>{d.get('avg_total_time', 0):.3f} s</td></tr>
        """
        return f"""
        <div class="col-md-4">
          <div class="card shadow-sm h-100">
            <div class="card-header bg-dark text-white"><strong>{title}</strong></div>
            <div class="card-body p-2">
              <table class="table table-sm table-bordered mb-0"><tbody>{rows}</tbody></table>
            </div>
          </div>
        </div>
        """

    hp_extra = f"""
    <tr><th>Disks Attached</th><td>{hotplug.get('total_disks_attached', 0)}</td></tr>
    <tr><th>Disks Validated</th><td>{hotplug.get('total_disks_validated', 0)}</td></tr>
    <tr><th>Avg API Attach</th><td>{hotplug.get('avg_api_attach_time', 0):.3f} s</td></tr>
    <tr><th>Avg Volume Ready</th><td>{hotplug.get('avg_volume_ready_time', 0):.3f} s</td></tr>
    <tr><th>Avg Validation</th><td>{hotplug.get('avg_validation_time', 0):.3f} s</td></tr>
    """ if hotplug else ""

    cp_extra = f"""
    <tr><th>Disks Attached</th><td>{coldplug.get('total_disks_attached', 0)}</td></tr>
    <tr><th>Disks Validated</th><td>{coldplug.get('total_disks_validated', 0)}</td></tr>
    <tr><th>Avg API Attach</th><td>{coldplug.get('avg_api_attach_time', 0):.3f} s</td></tr>
    <tr><th>Avg VM Boot</th><td>{coldplug.get('avg_vm_boot_time', 0):.3f} s</td></tr>
    <tr><th>Avg Validation</th><td>{coldplug.get('avg_validation_time', 0):.3f} s</td></tr>
    """ if coldplug else ""

    up_extra = f"""
    <tr><th>Disks Removed</th><td>{unplug.get('total_disks_removed', 0)}</td></tr>
    """ if unplug else ""

    ops_html = f"""
    <div class="row mb-4">
      {op_table("Hotplug", hotplug, hp_extra)}
      {op_table("Coldplug", coldplug, cp_extra)}
      {op_table("Unplug", unplug, up_extra)}
    </div>
    """

    # Chart — only total time per operation (no validation/phase breakdown)
    chart_labels, chart_data, chart_colors = [], [], []
    if hotplug:
        chart_labels.append("Hotplug Total")
        chart_data.append(hotplug.get("avg_total_time", 0))
        chart_colors.append("rgba(52,152,219,0.85)")
    if coldplug:
        chart_labels.append("Coldplug Total")
        chart_data.append(coldplug.get("avg_total_time", 0))
        chart_colors.append("rgba(46,204,113,0.85)")
    if unplug:
        chart_labels.append("Unplug Total")
        chart_data.append(unplug.get("avg_total_time", 0))
        chart_colors.append("rgba(231,76,60,0.85)")

    chart_html = ""
    if chart_data:
        chart_html = f"""
        <div class="card shadow-sm mb-4">
          <div class="card-header bg-dark text-white">
            <strong>Average Total Time per Operation (seconds)</strong>
          </div>
          <div class="card-body">
            <canvas id="diskOpsTimingChart_{uid}" height="80"></canvas>
          </div>
        </div>
        <script>
          new Chart(document.getElementById('diskOpsTimingChart_{uid}'), {{
            type: 'bar',
            data: {{
              labels: {_json.dumps(chart_labels)},
              datasets: [{{
                data: {_json.dumps(chart_data)},
                backgroundColor: {_json.dumps(chart_colors)},
                borderRadius: 6,
                borderSkipped: false
              }}]
            }},
            options: {{
              responsive: true,
              plugins: {{
                legend: {{ display: false }},
                tooltip: {{ callbacks: {{ label: function(ctx) {{ return ctx.parsed.y.toFixed(3) + ' s'; }} }} }}
              }},
              scales: {{
                y: {{ beginAtZero: true, title: {{ display: true, text: 'Seconds' }} }}
              }}
            }}
          }});
        </script>
        """

    # Per-VM table with click-to-chart popup
    per_vm_js = []
    per_vm_rows = ""
    for idx, r in enumerate(per_vm):
        ns = r.get("namespace", "N/A")
        op = r.get("operation", "N/A")
        d_req = r.get("disks_requested", r.get("disks_removed", 0))
        d_att = r.get("disks_attached", r.get("disks_removed", "-"))
        d_val = r.get("disks_validated", "-")
        t_sec = r.get("total_time", 0)
        ok = r.get("success", False)
        errors = "; ".join(r.get("errors", [])) if r.get("errors") else ""
        badge = '<span class="badge bg-success">✓</span>' if ok else f'<span class="badge bg-danger" title="{errors}">✗</span>'

        # Build per-operation chart data depending on type
        if op == "hotplug":
            chart_labels_vm = ["API Attach", "Vol Ready", "Total"]
            chart_vals_vm   = [r.get("api_attach_time", 0), r.get("volume_ready_time", 0), r.get("total_time", 0)]
            bar_colors_vm   = ["#3498db", "#9b59b6", "#2980b9"]
        elif op == "coldplug":
            chart_labels_vm = ["API Attach", "VM Boot", "Total"]
            chart_vals_vm   = [r.get("api_attach_time", 0), r.get("vm_boot_time", 0), r.get("total_time", 0)]
            bar_colors_vm   = ["#2ecc71", "#f39c12", "#27ae60"]
        else:  # unplug
            chart_labels_vm = ["Total"]
            chart_vals_vm   = [r.get("total_time", 0)]
            bar_colors_vm   = ["#e74c3c"]

        per_vm_js.append({
            "ns": ns, "op": op,
            "labels": chart_labels_vm,
            "vals": chart_vals_vm,
            "colors": bar_colors_vm
        })

        per_vm_rows += f"""
        <tr style="cursor:pointer;" onclick="showDiskOpsVmChart_{uid}({idx})">
          <td>{badge} <a href="#" onclick="event.preventDefault();showDiskOpsVmChart_{uid}({idx});">{ns}</a></td>
          <td>{op}</td>
          <td>{d_req}</td>
          <td>{d_att}</td>
          <td>{d_val}</td>
          <td>{t_sec:.3f}</td>
        </tr>
        """

    per_vm_js_json = _json.dumps(per_vm_js)
    no_data_row = "<tr><td colspan='6' class='text-center text-muted'>No per-VM results recorded.</td></tr>"

    per_vm_table = f"""
    <table class="table table-sm table-hover table-striped">
      <thead class="table-light">
        <tr>
          <th>Namespace</th><th>Operation</th><th>Disks Requested</th>
          <th>Disks Attached</th><th>Disks Validated</th><th>Total Time (s)</th>
        </tr>
      </thead>
      <tbody>{per_vm_rows if per_vm_rows else no_data_row}</tbody>
    </table>

    <!-- Per-VM chart modal -->
    <div id="diskOpsVmModal_{uid}" class="modal fade" tabindex="-1">
      <div class="modal-dialog modal-lg">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title" id="diskOpsVmModalTitle_{uid}">Operation Timing</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body">
            <canvas id="diskOpsVmChart_{uid}" height="180"></canvas>
          </div>
        </div>
      </div>
    </div>

    <script>
      var diskOpsVmData_{uid} = {per_vm_js_json};
      var diskOpsVmChartInst_{uid} = null;
      var diskOpsModalInst_{uid} = null;

      (function() {{
        var modalEl = document.getElementById('diskOpsVmModal_{uid}');
        diskOpsModalInst_{uid} = new bootstrap.Modal(modalEl, {{ backdrop: true }});
        modalEl.addEventListener('hidden.bs.modal', function() {{
          document.body.classList.remove('modal-open');
          document.body.style.removeProperty('overflow');
          document.body.style.removeProperty('padding-right');
          document.querySelectorAll('.modal-backdrop').forEach(function(el) {{ el.remove(); }});
        }});
      }})();

      function showDiskOpsVmChart_{uid}(idx) {{
        var d = diskOpsVmData_{uid}[idx];
        document.getElementById('diskOpsVmModalTitle_{uid}').innerText =
          d.ns + ' — ' + d.op.charAt(0).toUpperCase() + d.op.slice(1) + ' Timing';

        if (diskOpsVmChartInst_{uid}) {{ diskOpsVmChartInst_{uid}.destroy(); diskOpsVmChartInst_{uid} = null; }}

        diskOpsVmChartInst_{uid} = new Chart(document.getElementById('diskOpsVmChart_{uid}'), {{
          type: 'bar',
          data: {{
            labels: d.labels,
            datasets: [{{
              data: d.vals,
              backgroundColor: d.colors,
              borderRadius: 6,
              borderSkipped: false
            }}]
          }},
          options: {{
            responsive: true,
            plugins: {{
              legend: {{ display: false }},
              tooltip: {{ callbacks: {{ label: function(ctx) {{ return ctx.parsed.y.toFixed(3) + ' s'; }} }} }}
            }},
            scales: {{
              y: {{ beginAtZero: true, title: {{ display: true, text: 'Seconds' }} }}
            }}
          }}
        }});

        diskOpsModalInst_{uid}.show();
      }}
    </script>
    """

    return f"""
    <div class="mb-4">
      {header_html}
      {kpi_html}
      <h4 class="mt-4">Test Configuration</h4>
      {config_table}
      <h4 class="mt-4">Operation Results</h4>
      {ops_html}
      {chart_html}
      <h4 class="mt-4">Per-VM Details <small class="text-muted fs-6">(click a row to view timing chart)</small></h4>
      {per_vm_table}
    </div>
    """


# ---------------- Disk and PX Builders ----------------
def build_creation_boot_sections(px_version: str, disk_name: str, vm_count: int, folders: list) -> str:
    """Build Creation + Boot Storm sections, pairing split workflow folders by round."""
    creation_folders = sorted(
        [f for f in folders if (f / "summary_vm_creation_results.json").exists()],
        key=lambda p: p.name,
    )
    boot_folders = sorted(
        [f for f in folders if (f / "summary_boot_storm_results.json").exists()],
        key=lambda p: p.name,
    )
    boot_by_round = defaultdict(list)
    for boot_folder in boot_folders:
        round_number = get_workflow_round_from_folder(boot_folder.name)
        if round_number is not None:
            boot_by_round[round_number].append(boot_folder)

    used_boot_folders = set()
    sections = []

    for creation_folder in creation_folders:
        boot_folder = None
        if (creation_folder / "summary_boot_storm_results.json").exists():
            boot_folder = creation_folder
        else:
            round_number = get_workflow_round_from_folder(creation_folder.name)
            if round_number is not None and boot_by_round.get(round_number):
                for candidate in boot_by_round[round_number]:
                    if candidate not in used_boot_folders:
                        boot_folder = candidate
                        break
            elif len(creation_folders) == 1 and len(boot_folders) == 1:
                boot_folder = boot_folders[0]

        if boot_folder:
            used_boot_folders.add(boot_folder)

        uid = f"{px_version}_{disk_name}_{vm_count}_{creation_folder.name}".replace(".", "_").replace("-", "_")
        sections.append(build_creation_boot_content(creation_folder, uid=uid, boot_folder=boot_folder))

    for boot_folder in boot_folders:
        if boot_folder in used_boot_folders or (boot_folder / "summary_vm_creation_results.json").exists():
            continue
        uid = f"{px_version}_{disk_name}_{vm_count}_{boot_folder.name}".replace(".", "_").replace("-", "_")
        sections.append(build_creation_boot_content(None, uid=uid, boot_folder=boot_folder))

    return "".join(sections) or "<p>No Creation/Boot Storm data for this VM size.</p>"


def build_detail_groups(by_vms: dict) -> list:
    """Return detailed-result groups, splitting duplicate workflow VM counts by run."""
    groups = []
    for vm_count in sorted([v for v in by_vms.keys() if v > 0]):
        folders = sorted(by_vms[vm_count], key=lambda p: p.name)
        by_round = defaultdict(list)
        without_round = []

        for folder in folders:
            round_number = get_workflow_round_from_folder(folder.name)
            if round_number is None:
                without_round.append(folder)
            else:
                by_round[round_number].append(folder)

        if len(by_round) > 1:
            for round_number in sorted(by_round):
                groups.append({
                    "vm_count": vm_count,
                    "label": f"{vm_count_label(vm_count)} (Run {round_number})",
                    "id": f"{vm_count}_run_{round_number}",
                    "folders": by_round[round_number],
                })
            if without_round:
                groups.append({
                    "vm_count": vm_count,
                    "label": vm_count_label(vm_count),
                    "id": str(vm_count),
                    "folders": without_round,
                })
        else:
            groups.append({
                "vm_count": vm_count,
                "label": vm_count_label(vm_count),
                "id": str(vm_count),
                "folders": folders,
            })

    return groups


def build_disk_tab(px_version: str, disk_name: str, folders: list, ai_runs: list = None) -> str:
    """Disk-level tab with charts and nested VM-size tabs."""
    creation_rec, boot_rec, mig_rec, cap_rec = [], [], [], []
    by_vms = defaultdict(list)
    ai_runs = ai_runs or []

    for folder in sorted(folders, key=lambda p: p.name):
        csum = load_json(folder / "summary_vm_creation_results.json")
        bsum = load_json(folder / "summary_boot_storm_results.json")
        msum = load_json(folder / "summary_migration_results.json")
        capsum = load_json(folder / "summary_chaos_benchmark.json")
        vms = (
            (csum or {}).get("total_vms")
            or (bsum or {}).get("total_vms")
            or (msum or {}).get("total_vms")
            or (capsum or {}).get("total_vms")
            or get_vm_count_from_folder(folder.name)
        )
        by_vms[vms].append(folder)
        if csum and csum.get("total_test_duration_sec"):
            creation_rec.append({"VMs": csum.get("total_vms", vms), "Seconds": csum["total_test_duration_sec"], "Folder": folder.name})
        if bsum and bsum.get("total_test_duration_sec"):
            boot_rec.append({"VMs": bsum.get("total_vms", vms), "Seconds": bsum["total_test_duration_sec"], "Folder": folder.name})
        if msum and (msum.get("total_migration_duration_sec") or msum.get("total_test_duration_sec")):
            total = msum.get("total_migration_duration_sec") or msum.get("total_test_duration_sec")
            mig_rec.append({"VMs": msum.get("total_vms", vms), "Seconds": total, "Folder": folder.name})
        if capsum and capsum.get("total_test_duration_sec"):
            cap_rec.append({"VMs": capsum.get("total_vms", vms), "Seconds": capsum["total_test_duration_sec"], "Folder": folder.name})

    # Build charts - include capacity chart only if we have data
    chart_items = [
        build_bar_chart_mmss(creation_rec, "VM Creation Duration", "rgb(26,118,255)", f"chart_{px_version}_{disk_name}_creation"),
        build_bar_chart_mmss(boot_rec, "Boot Storm Duration", "rgb(0,204,150)", f"chart_{px_version}_{disk_name}_boot"),
        build_bar_chart_mmss(mig_rec, "Live Migration Duration", "rgb(255,99,71)", f"chart_{px_version}_{disk_name}_mig"),
    ]
    if cap_rec:
        chart_items.append(build_bar_chart_mmss(cap_rec, "Chaos Benchmark Duration", "rgb(153,102,255)", f"chart_{px_version}_{disk_name}_cap"))

    charts_html = f"""
    <h3 class="section-heading">Summary Graphs</h3>
    <div class="container-fluid mt-3">
      <div class="row g-3">
        {''.join(chart_items)}
      </div>
      <hr>
    </div>
    """
    ai_rounds_html = ""
    if ai_runs:
        ai_rounds_html = f"""
        <section class="ai-rounds-section mb-4">
          {build_ai_rounds_tab(ai_runs)}
        </section>
        """

    # Detailed-result tabs. If the same VM count appears in multiple workflow
    # rounds, split it into per-run tabs instead of collapsing everything.
    detail_groups = build_detail_groups(by_vms)
    if not detail_groups:
        return charts_html + "<p>No result folders found under this disk.</p>"

    vm_tabs_nav, vm_tabs_body = [], []
    for idx, group in enumerate(detail_groups):
        vm_count = group["vm_count"]
        group_folders = group["folders"]
        vm_id = html_id(f"{px_version}_{disk_name}_{group['id']}")
        active_cls = "active" if idx == 0 else ""
        show_cls = "show" if idx == 0 else ""
        vm_tabs_nav.append(
            f'<li class="nav-item"><button class="nav-link {active_cls}" id="tab-{vm_id}-tab" data-bs-toggle="tab" '
            f'data-bs-target="#tab_{vm_id}" type="button" role="tab">{group["label"]}</button></li>'
        )

        cb_sections = build_creation_boot_sections(px_version, disk_name, vm_count, group_folders)

        mig_sections = "".join(
            build_migration_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in group_folders if (f / "summary_migration_results.json").exists()
        ) or "<p>No Live Migration data for this VM size.</p>"

        cap_sections = "".join(
            build_capacity_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in group_folders if (f / "summary_chaos_benchmark.json").exists()
        ) or "<p>No Chaos Benchmark data for this VM size.</p>"

        fio_sections = "".join(
            build_fio_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in group_folders if (f / "summary_fio_benchmark.json").exists() or (f / "fio_benchmark_results.json").exists()
        ) or "<p>No FIO Benchmark data for this VM size.</p>"

        elbencho_sections = "".join(
            build_elbencho_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in group_folders if (f / "aggregated_results.json").exists()
        ) or "<p>No Elbencho Benchmark data for this VM size.</p>"

        disk_ops_folders = [f for f in group_folders if (f / "disk_ops_results.json").exists()]
        if disk_ops_folders:
            accordion_items = []
            for aidx, f in enumerate(disk_ops_folders):
                fold_uid = f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_")
                content = build_disk_ops_content(f, uid=fold_uid)
                expanded = "show" if aidx == 0 else ""
                collapsed = "" if aidx == 0 else "collapsed"
                accordion_items.append(f"""
                <div class="accordion-item">
                  <h2 class="accordion-header">
                    <button class="accordion-button {collapsed}" type="button"
                            data-bs-toggle="collapse" data-bs-target="#diskops_acc_{fold_uid}">
                      {f.name}
                    </button>
                  </h2>
                  <div id="diskops_acc_{fold_uid}" class="accordion-collapse collapse {expanded}">
                    <div class="accordion-body">{content}</div>
                  </div>
                </div>
                """)
            disk_ops_sections = f'<div class="accordion" id="diskOpsAccordion_{vm_id}">{"".join(accordion_items)}</div>'
        else:
            disk_ops_sections = "<p>No Disk Operations benchmark data for this VM size.</p>"

        # Check if we have capacity/fio/elbencho data to show the tabs
        has_capacity_data = any((f / "summary_capacity_benchmark.json").exists() for f in group_folders)
        has_fio_data = any((f / "summary_fio_benchmark.json").exists() or (f / "fio_benchmark_results.json").exists() for f in group_folders)
        has_elbencho_data = any((f / "aggregated_results.json").exists() for f in group_folders)
        has_disk_ops_data = any((f / "disk_ops_results.json").exists() for f in group_folders)
        # Check if we have chaos data to show the tab
        has_capacity_data = any((f / "summary_chaos_benchmark.json").exists() for f in group_folders)

        # Build tab navigation - include capacity/fio tabs only if data exists
        tab_nav_items = [
            f'<li class="nav-item"><button class="nav-link active" id="tab-{vm_id}_cb-tab" data-bs-toggle="tab" data-bs-target="#tab_{vm_id}_cb" type="button" role="tab">Creation + Boot Storm</button></li>',
            f'<li class="nav-item"><button class="nav-link" id="tab-{vm_id}_mig-tab" data-bs-toggle="tab" data-bs-target="#tab_{vm_id}_mig" type="button" role="tab">Live Migration</button></li>',
        ]
        tab_content_items = [
            f'<div class="tab-pane fade show active" id="tab_{vm_id}_cb" role="tabpanel">{cb_sections}</div>',
            f'<div class="tab-pane fade" id="tab_{vm_id}_mig" role="tabpanel">{mig_sections}</div>',
        ]

        if has_capacity_data:
            tab_nav_items.append(
                f'<li class="nav-item"><button class="nav-link" id="tab-{vm_id}_cap-tab" data-bs-toggle="tab" data-bs-target="#tab_{vm_id}_cap" type="button" role="tab">Chaos Benchmark</button></li>'
            )
            tab_content_items.append(
                f'<div class="tab-pane fade" id="tab_{vm_id}_cap" role="tabpanel">{cap_sections}</div>'
            )

        if has_fio_data:
            tab_nav_items.append(
                f'<li class="nav-item"><button class="nav-link" id="tab-{vm_id}_fio-tab" data-bs-toggle="tab" data-bs-target="#tab_{vm_id}_fio" type="button" role="tab">FIO Benchmark</button></li>'
            )
            tab_content_items.append(
                f'<div class="tab-pane fade" id="tab_{vm_id}_fio" role="tabpanel">{fio_sections}</div>'
            )

        if has_elbencho_data:
            tab_nav_items.append(
                f'<li class="nav-item"><button class="nav-link" id="tab-{vm_id}_elbencho-tab" data-bs-toggle="tab" data-bs-target="#tab_{vm_id}_elbencho" type="button" role="tab">Elbencho Benchmark</button></li>'
            )
            tab_content_items.append(
                f'<div class="tab-pane fade" id="tab_{vm_id}_elbencho" role="tabpanel">{elbencho_sections}</div>'
            )

        if has_disk_ops_data:
            tab_nav_items.append(
                f'<li class="nav-item"><button class="nav-link" id="tab-{vm_id}_diskops-tab" data-bs-toggle="tab" data-bs-target="#tab_{vm_id}_diskops" type="button" role="tab">Disk Ops</button></li>'
            )
            tab_content_items.append(
                f'<div class="tab-pane fade" id="tab_{vm_id}_diskops" role="tabpanel">{disk_ops_sections}</div>'
            )

        vm_tabs_body.append(
            f"""
            <div class="tab-pane fade {show_cls} {active_cls}" id="tab_{vm_id}" role="tabpanel">
              <ul class="nav nav-pills mt-2" role="tablist">
                {''.join(tab_nav_items)}
              </ul>
              <div class="tab-content mt-3">
                {''.join(tab_content_items)}
              </div>
            </div>
            """
        )

    return f"""
    {charts_html}
    {ai_rounds_html}
    <section class="detailed-results-section">
      <h3 class="section-heading">Detailed Results</h3>
      <ul class="nav nav-tabs" role="tablist">{''.join(vm_tabs_nav)}</ul>
      <div class="tab-content mt-3">{''.join(vm_tabs_body)}</div>
    </section>
    """


def build_px_tab(px_version: str, disk_map: dict, ai_runs_by_location: dict = None) -> str:
    """Top-level PX version tab with disk subtabs."""
    ai_runs_by_location = ai_runs_by_location or {}
    disk_names = sorted(disk_map.keys(), key=lambda d: (int(d.split('-')[0]) if d.split('-')[0].isdigit() else 0, d))
    if not disk_names:
        return "<p>No disk groups found for this PX version.</p>"

    nav, body = [], []
    for idx, dname in enumerate(disk_names):
        did = f"{px_version}_{dname}".replace(".", "_").replace("-", "_")
        active_cls = "active" if idx == 0 else ""
        show_cls = "show" if idx == 0 else ""
        nav.append(f'<li class="nav-item"><button class="nav-link {active_cls}" id="tab-{did}-tab" data-bs-toggle="tab" data-bs-target="#tab_{did}" type="button" role="tab">{dname}</button></li>')
        disk_ai_runs = ai_runs_by_location.get((px_version, dname), [])
        body.append(f'<div class="tab-pane fade {show_cls} {active_cls}" id="tab_{did}" role="tabpanel">{build_disk_tab(px_version, dname, disk_map[dname], disk_ai_runs)}</div>')

    return f"<ul class='nav nav-tabs mt-2' role='tablist'>{''.join(nav)}</ul><div class='tab-content mt-3'>{''.join(body)}</div>"


DASHBOARD_CSS = r"""
:root {
  --page-bg: #fff8f1;
  --panel: rgba(255, 255, 255, 0.52);
  --panel-solid: #ffffff;
  --panel-soft: rgba(255, 250, 244, 0.38);
  --ink: #2b1f16;
  --muted: #826d5b;
  --line: rgba(219, 151, 88, 0.24);
  --line-strong: rgba(197, 104, 24, 0.34);
  --orange: #ff7900;
  --orange-dark: #b84d00;
  --shadow: 0 24px 70px rgba(139, 68, 0, 0.11);
  --glass-shadow: 0 18px 50px rgba(139, 68, 0, 0.10), inset 0 1px 0 rgba(255,255,255,0.74), inset 0 -1px 0 rgba(255,255,255,0.24);
  --radius: 8px;
}

body.theme-dark {
  --page-bg: #180f09;
  --panel: rgba(43, 31, 22, 0.42);
  --panel-solid: #241810;
  --panel-soft: rgba(55, 36, 22, 0.36);
  --ink: #fff7ed;
  --muted: #d5b99f;
  --line: rgba(255, 174, 50, 0.20);
  --line-strong: rgba(255, 174, 50, 0.36);
  --orange: #ff8a1f;
  --orange-dark: #ffb15e;
  --shadow: 0 26px 78px rgba(0, 0, 0, 0.32);
  --glass-shadow: 0 18px 50px rgba(0, 0, 0, 0.24), inset 0 1px 0 rgba(255,255,255,0.16), inset 0 -1px 0 rgba(255,255,255,0.06);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  color: var(--ink);
  background:
    radial-gradient(circle at 14% 10%, rgba(255,255,255,0.98) 0 13%, rgba(255,255,255,0) 31%),
    radial-gradient(circle at 90% 8%, rgba(255, 121, 0, 0.07) 0 13%, rgba(255,255,255,0) 38%),
    radial-gradient(circle at 68% 52%, rgba(255, 255, 255, 0.82) 0 12%, rgba(255,255,255,0) 34%),
    radial-gradient(circle at 16% 76%, rgba(255, 174, 50, 0.07) 0 12%, rgba(255,255,255,0) 36%),
    linear-gradient(135deg, #ffffff 0%, #fffdfb 34%, #fff7ee 62%, #ffffff 100%);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 15px;
}

body.theme-dark { background: #000; }

.dashboard-shell {
  width: min(100%, 1600px);
  margin: 0 auto;
  padding: 24px;
}

.dashboard-hero,
.nav-tabs,
.plotly-chart,
.col-12.col-md-4 > p,
.table-frame,
.card,
.ai-rounds-section,
.detailed-results-section,
.recommendation-callout,
.theme-toggle,
.brand-mark,
h6 {
  background:
    linear-gradient(135deg, rgba(255,255,255,0.64), rgba(255,255,255,0.28)),
    linear-gradient(135deg, rgba(255, 121, 0, 0.10), rgba(255,255,255,0.04));
  border: 1px solid rgba(255,255,255,0.66);
  border-radius: var(--radius);
  box-shadow: var(--glass-shadow);
  backdrop-filter: blur(26px) saturate(1.34);
  -webkit-backdrop-filter: blur(26px) saturate(1.34);
}

body.theme-dark .dashboard-hero,
body.theme-dark .nav-tabs,
body.theme-dark .plotly-chart,
body.theme-dark .col-12.col-md-4 > p,
body.theme-dark .table-frame,
body.theme-dark .card,
body.theme-dark .ai-rounds-section,
body.theme-dark .detailed-results-section,
body.theme-dark .recommendation-callout,
body.theme-dark .theme-toggle,
body.theme-dark .brand-mark,
body.theme-dark h6 {
  background:
    linear-gradient(135deg, rgba(44, 28, 17, 0.56), rgba(18, 12, 8, 0.30)),
    linear-gradient(135deg, rgba(255, 121, 0, 0.12), rgba(255,255,255,0.025));
  border-color: rgba(255,255,255,0.12);
}

.dashboard-hero {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 11px;
  padding: 9px 13px;
  color: var(--ink);
}

.dashboard-hero::after {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: linear-gradient(90deg, #ff7900, #ffae32, rgba(255,255,255,0));
}

.brand-mark {
  position: relative;
  flex: 0 0 auto;
  width: 76px;
  height: 76px;
  display: grid;
  place-items: center;
}

.brand-mark img {
  width: 66px;
  height: 66px;
  object-fit: contain;
  display: block;
}

.brand-logo-fallback {
  color: var(--orange-dark);
  font-weight: 820;
  font-size: 1rem;
}

.brand-copy { min-width: 0; }

.brand-label {
  margin-bottom: 2px;
  color: var(--orange-dark);
  font-size: 0.58rem;
  font-weight: 820;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

h1 {
  position: relative;
  margin: 0;
  font-size: clamp(1.04rem, 1.65vw, 1.55rem);
  line-height: 1;
  font-weight: 760;
  letter-spacing: 0;
  max-width: 920px;
}

.dashboard-hero p {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 0.68rem;
}

.dashboard-hero p strong {
  color: var(--ink);
  font-weight: 700;
}

.dashboard-actions {
  margin-left: auto;
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 8px;
}

.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  padding: 3px 7px 3px 4px;
  border-radius: 999px;
  color: var(--orange-dark);
  font-weight: 780;
  font-size: 0.68rem;
  cursor: pointer;
}

.theme-toggle:focus-visible {
  outline: 3px solid rgba(255, 121, 0, .25);
  outline-offset: 2px;
}

.theme-toggle-knob {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: linear-gradient(135deg, #ff7900, #ffbd57);
  box-shadow: 0 5px 12px rgba(197, 87, 0, .24);
}

h3, h4 {
  color: var(--ink);
  font-weight: 760;
  letter-spacing: 0;
}

h3 {
  margin: 30px 0 14px;
  font-size: 1.28rem;
}

.section-heading {
  margin: 20px 0 12px;
  color: var(--ink);
  font-size: 1.12rem;
  font-weight: 760;
}

h4 {
  margin-bottom: 12px;
  font-size: 1.05rem;
}

h6 {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  margin: 0 8px 8px 0;
  padding: 7px 10px;
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.2;
}

h6 strong {
  color: var(--ink);
  margin-right: 4px;
}

hr {
  margin: 14px 0;
  border-color: var(--line);
  opacity: 1;
}

.nav {
  flex-wrap: wrap;
  gap: 0.4rem;
}

#pxTab::before,
#pxTabContent > .tab-pane > .nav-tabs::before,
.detailed-results-section > .nav-tabs::before,
.detailed-results-section .tab-pane > .nav-pills::before {
  display: inline-flex;
  align-items: center;
  margin: 0 10px 0 2px;
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 820;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

#pxTab::before { content: "Storage driver"; }
#pxTabContent > .tab-pane > .nav-tabs::before { content: "Disk profile"; }
.detailed-results-section > .nav-tabs::before { content: "Total VMs"; }
.detailed-results-section .tab-pane > .nav-pills::before { content: "Scenario"; }

.nav-tabs,
.detailed-results-section .tab-pane > .nav-pills {
  border: 0;
  padding: 7px;
  border-radius: var(--radius);
}

#pxTab { margin-top: 14px; }

#pxTabContent > .tab-pane > .nav-tabs {
  margin-top: 10px !important;
  margin-left: 0;
  max-width: 100%;
}

.nav-pills { gap: 0.45rem; }

.nav-tabs .nav-link,
.nav-pills .nav-link {
  border: 1px solid transparent;
  border-radius: 7px;
  color: #44524d;
  font-size: 0.9rem;
  font-weight: 680;
  padding: 0.48rem 0.72rem;
  transition: background-color .16s ease, border-color .16s ease, color .16s ease, box-shadow .16s ease;
}

body.theme-dark .nav-tabs .nav-link,
body.theme-dark .nav-pills .nav-link {
  color: var(--muted);
}

.nav-tabs .nav-link:hover,
.nav-pills .nav-link:hover {
  border-color: var(--line-strong);
  color: var(--ink);
  background: rgba(255,255,255,.74);
}

body.theme-dark .nav-tabs .nav-link:hover,
body.theme-dark .nav-pills .nav-link:hover {
  background: rgba(255,255,255,.08);
}

.nav-tabs .nav-link.active,
.nav-pills .nav-link.active {
  border-color: rgba(255, 121, 0, .25);
  background: rgba(255,255,255,0.48) !important;
  color: var(--orange-dark);
  box-shadow: 0 10px 24px rgba(168, 86, 17, 0.12);
  backdrop-filter: blur(18px) saturate(1.24);
  -webkit-backdrop-filter: blur(18px) saturate(1.24);
}

body.theme-dark .nav-tabs .nav-link.active,
body.theme-dark .nav-pills .nav-link.active {
  background: rgba(255,255,255,0.08) !important;
  border-color: rgba(255, 174, 50, .30);
}

.tab-content { max-width: 100%; }

#pxTabContent > .tab-pane,
.tab-content > .tab-pane > .tab-content,
.ai-rounds-section,
.detailed-results-section,
.mb-4 {
  border-radius: var(--radius);
}

.container-fluid { padding: 0; }
.row.g-3 { align-items: stretch; }

.plotly-chart { overflow: hidden; }

.col-12.col-md-4 > p,
p.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 24px;
  color: var(--muted);
  background:
    repeating-linear-gradient(
      -45deg,
      rgba(255,255,255,.20),
      rgba(255,255,255,.20) 10px,
      rgba(255, 121, 0, .075) 10px,
      rgba(255, 121, 0, .075) 20px
    ),
    linear-gradient(135deg, rgba(255,255,255,0.46), rgba(255,255,255,0.16));
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius);
  text-align: center;
  font-weight: 650;
}

body.theme-dark .col-12.col-md-4 > p,
body.theme-dark p.empty-state {
  background:
    repeating-linear-gradient(
      -45deg,
      rgba(255,255,255,.045),
      rgba(255,255,255,.045) 10px,
      rgba(255, 121, 0, .075) 10px,
      rgba(255, 121, 0, .075) 20px
    ),
    linear-gradient(135deg, rgba(44, 28, 17, 0.54), rgba(18, 12, 8, 0.28));
}

.col-12.col-md-4 > p { min-height: 360px; }
p.empty-state { min-height: 150px; }

.card { border-color: var(--line) !important; }
.card-body { padding: 14px 16px; }

.card .small {
  color: var(--muted) !important;
  font-weight: 720;
  text-transform: uppercase;
  font-size: 0.7rem;
  letter-spacing: 0.04em;
}

.card .fw-bold {
  margin-top: 6px;
  color: var(--ink);
  font-size: clamp(1.05rem, 1.65vw, 1.45rem);
  line-height: 1.05;
}

.ai-rounds-section,
.detailed-results-section {
  margin: 6px 0 24px;
  padding: 14px 22px 22px;
}

.ai-rounds-section h3,
.detailed-results-section .section-heading {
  margin-top: 0;
  margin-bottom: 14px;
  font-size: 1.28rem;
}

.recommendation-callout {
  margin: 18px 0 22px;
  padding: 14px 16px;
  border-left: 4px solid var(--orange);
  color: #7a3600;
  font-weight: 620;
}

body.theme-dark .recommendation-callout { color: #ffd3aa; }

.table-frame {
  max-width: 100%;
  overflow-x: auto;
  margin-bottom: 20px;
}

.table-frame .dataTables_wrapper {
  width: 100%;
  margin: 0 auto;
  overflow-x: visible;
  padding: 12px;
}

.dataTables_length,
.dataTables_filter,
.dataTables_info,
.dataTables_paginate {
  margin: 0.35rem 0;
  color: var(--muted) !important;
  font-size: 0.82rem;
}

[id^="table_ai_"][id$="_summary_wrapper"] .dataTables_length,
[id^="table_ai_"][id$="_summary_wrapper"] .dataTables_filter {
  display: none;
}

.dataTables_filter input,
.dataTables_length select {
  min-height: 34px;
  border: 1px solid var(--line-strong) !important;
  border-radius: 7px !important;
  background: rgba(255,255,255,0.48) !important;
  color: var(--ink) !important;
  outline: none;
  backdrop-filter: blur(18px) saturate(1.24);
  -webkit-backdrop-filter: blur(18px) saturate(1.24);
}

.dataTables_filter input:focus,
.dataTables_length select:focus {
  border-color: var(--orange) !important;
  box-shadow: 0 0 0 3px rgba(255, 121, 0, .14);
}

.dataTables_wrapper .dataTables_paginate .paginate_button {
  border: 1px solid transparent !important;
  border-radius: 7px !important;
  color: #40504a !important;
}

.dataTables_wrapper .dataTables_paginate .paginate_button.current,
.dataTables_wrapper .dataTables_paginate .paginate_button.current:hover {
  border-color: rgba(255, 121, 0, .25) !important;
  background: rgba(255,255,255,0.48) !important;
  color: var(--orange-dark) !important;
}

.dataTables_wrapper .dataTables_paginate .paginate_button:hover {
  border-color: var(--line-strong) !important;
  background: rgba(255,255,255,.60) !important;
  color: var(--ink) !important;
}

.dataTables_wrapper,
table.virtbench-table,
table.virtbench-table tbody,
table.virtbench-table tbody tr,
table.virtbench-table tbody td {
  background: transparent !important;
}

table.virtbench-table {
  width: 100% !important;
  max-width: 100%;
  table-layout: auto;
  border-collapse: separate !important;
  border-spacing: 0;
}

table.virtbench-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  border: 0 !important;
  border-bottom: 1px solid var(--line-strong) !important;
  background: rgba(255, 238, 217, .54) !important;
  color: #5c3720;
  font-size: 0.76rem;
  font-weight: 780;
  text-transform: uppercase;
  letter-spacing: 0.035em;
  overflow-wrap: break-word;
  word-break: normal;
  backdrop-filter: blur(18px) saturate(1.18);
  -webkit-backdrop-filter: blur(18px) saturate(1.18);
}

body.theme-dark table.virtbench-table thead th {
  background: rgba(64, 39, 20, .54) !important;
  color: #ffd8b0;
}

table.virtbench-table th,
table.virtbench-table td {
  white-space: normal !important;
  vertical-align: top;
}

table.virtbench-table td {
  border-top: 1px solid rgba(219, 151, 88, 0.18) !important;
  color: var(--ink);
  font-size: 0.84rem;
  line-height: 1.35;
  overflow-wrap: break-word;
  word-break: normal;
}

table.virtbench-table tbody tr:hover td {
  background: rgba(255,255,255,0.26) !important;
}

body.theme-dark table.virtbench-table tbody tr:hover td {
  background: rgba(255,255,255,0.06) !important;
}

.wrap-code {
  display: inline-block;
  max-width: 100%;
  color: var(--ink);
  background: rgba(255,255,255,0.48) !important;
  border: 1px solid rgba(130, 109, 91, .22);
  border-radius: 6px;
  padding: 2px 6px;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  backdrop-filter: blur(18px) saturate(1.24);
  -webkit-backdrop-filter: blur(18px) saturate(1.24);
}

body.theme-dark .wrap-code {
  background: rgba(255,255,255,0.08) !important;
  border-color: rgba(255,255,255,.12);
}

.wrap-cell {
  display: inline-block;
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.stack-cell {
  display: grid;
  gap: 3px;
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.stack-cell span {
  display: block;
}

.failure-detail-stack {
  display: grid;
  gap: 3px;
}

.cmd-cell { max-width: 38ch; }

.cmd-cell summary {
  cursor: pointer;
  color: var(--ink);
  list-style-position: outside;
  overflow-wrap: anywhere;
  font-weight: 680;
}

.cmd-cell pre {
  margin: 0.6rem 0 0;
  max-width: 100%;
  max-height: 12rem;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  border: 1px solid #cfdad5;
  border-radius: var(--radius);
  background: #2b1f16;
  color: #fff7ed;
  font-size: 0.78rem;
  padding: 12px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  min-width: 54px;
  justify-content: center;
  padding: 3px 8px;
  border-radius: 999px;
  font-weight: 760;
  font-size: 0.74rem;
  background: rgba(255,255,255,0.48) !important;
  backdrop-filter: blur(18px) saturate(1.24);
  -webkit-backdrop-filter: blur(18px) saturate(1.24);
}

.status-pill.ok {
  color: #15803d;
  border: 1px solid rgba(21, 128, 61, .24);
}

body.theme-dark .status-pill.ok {
  border-color: rgba(74, 222, 128, .28);
  color: #4ade80;
}

.status-pill.fail {
  color: #dc2626;
  border: 1px solid rgba(220, 38, 38, .24);
}

body.theme-dark .status-pill.fail {
  border-color: rgba(248, 113, 113, .30);
  color: #f87171;
}

@media (max-width: 768px) {
  .dashboard-shell { padding: 12px; }
  .dashboard-hero {
    align-items: flex-start;
    padding: 10px;
    gap: 8px;
  }
  .dashboard-actions {
    width: 100%;
    margin-left: 68px;
    margin-top: -4px;
  }
  .brand-mark {
    width: 62px;
    height: 62px;
  }
  .brand-mark img {
    width: 54px;
    height: 54px;
  }
  h1 {
    font-size: 0.95rem;
    line-height: 1.05;
  }
  table.virtbench-table th,
  table.virtbench-table td {
    font-size: 0.76rem;
    padding: 0.42rem;
  }
  .card .fw-bold { font-size: 1.35rem; }
}
"""


DASHBOARD_JS = r"""
$(document).ready(function() {
  const themeToggle = document.getElementById('themeToggle');

  function applyPlotlyTheme() {
    const isDark = document.body.classList.contains('theme-dark');
    document.querySelectorAll('.plotly-chart').forEach(function (el) {
      try {
        Plotly.relayout(el, {
          paper_bgcolor: 'rgba(0,0,0,0)',
          plot_bgcolor: 'rgba(0,0,0,0)',
          font: {family: 'Inter, Segoe UI, sans-serif', color: isDark ? '#fff7ed' : '#25302d'},
          'title.font.size': 18,
          'title.font.color': isDark ? '#fff7ed' : '#25302d',
          'xaxis.gridcolor': isDark ? 'rgba(255,174,50,.14)' : '#eef2f0',
          'xaxis.zerolinecolor': isDark ? 'rgba(255,174,50,.22)' : '#d9e0dd',
          'xaxis.title.font.color': isDark ? '#d5b99f' : '#44524d',
          'yaxis.gridcolor': isDark ? 'rgba(255,174,50,.14)' : '#eef2f0',
          'yaxis.zerolinecolor': isDark ? 'rgba(255,174,50,.22)' : '#d9e0dd',
          'yaxis.title.font.color': isDark ? '#d5b99f' : '#44524d'
        });
      } catch(e) {}
    });
  }

  function syncThemeToggle() {
    const isDark = document.body.classList.contains('theme-dark');
    if (themeToggle) {
      themeToggle.setAttribute('aria-pressed', isDark ? 'true' : 'false');
      const label = themeToggle.querySelector('.theme-toggle-text');
      if (label) label.textContent = isDark ? 'Light' : 'Dark';
    }
  }

  try {
    if (localStorage.getItem('virtbench-theme') === 'dark') {
      document.body.classList.add('theme-dark');
    }
  } catch(e) {}

  syncThemeToggle();

  if (themeToggle) {
    themeToggle.addEventListener('click', function() {
      document.body.classList.toggle('theme-dark');
      const isDark = document.body.classList.contains('theme-dark');
      try { localStorage.setItem('virtbench-theme', isDark ? 'dark' : 'light'); } catch(e) {}
      syncThemeToggle();
      applyPlotlyTheme();
    });
  }

  $('p').each(function() {
    const text = $(this).text().trim();
    if (text.startsWith('No data') || text.startsWith('No Live Migration data')) {
      $(this).addClass('empty-state');
    }
    if (text.startsWith('Latest recommendation:')) {
      $(this).addClass('recommendation-callout');
    }
  });

  $('table.virtbench-table td').each(function() {
    const text = $(this).text().trim();
    if (text === 'True' || text === 'False') {
      $(this).html('<span class="status-pill ' + (text === 'True' ? 'ok' : 'fail') + '">' + text + '</span>');
    }
  });

  $('table.display').DataTable({
    scrollX: false,
    autoWidth: false,
    pageLength: 25
  });

  $('a[data-bs-toggle="tab"], button[data-bs-toggle="tab"]').on('shown.bs.tab', function () {
    $.fn.dataTable.tables({visible: true, api: true}).columns.adjust();
    setTimeout(function () {
      applyPlotlyTheme();
      document.querySelectorAll('.tab-pane.active .plotly-chart').forEach(function (el) {
        try { Plotly.Plots.resize(el); } catch(e) {}
      });
    }, 100);
  });

  const topTabs = document.querySelectorAll('#pxTab .nav-link');
  if (topTabs.length > 0) {
    const first = new bootstrap.Tab(topTabs[0]);
    first.show();
  }

  setTimeout(function () {
    applyPlotlyTheme();
    document.querySelectorAll('.plotly-chart').forEach(function (el) {
      try { Plotly.Plots.resize(el); } catch(e) {}
    });
  }, 400);
});
"""


# ---------------- Main HTML ----------------
def build_html_page(px_nav, px_body):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logo_uri = get_logo_data_uri()
    logo_markup = (
        f'<img src="{logo_uri}" alt="VirtBench logo">'
        if logo_uri
        else '<div class="brand-logo-fallback">VB</div>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>KubeVirt Performance Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
{DASHBOARD_CSS}
</style>
</head>
<body>
<main class="dashboard-shell">
<header class="dashboard-hero">
<div class="brand-mark">{logo_markup}</div>
<div class="brand-copy">
<div class="brand-label">VirtBench</div>
<h1 class="mb-2">KubeVirt Performance Dashboard</h1>
<p><strong>Generated:</strong> {now}</p>
</div>
<div class="dashboard-actions">
<button class="theme-toggle" id="themeToggle" type="button" aria-pressed="false" title="Toggle dark mode">
<span class="theme-toggle-knob" aria-hidden="true"></span>
<span class="theme-toggle-text">Dark</span>
</button>
</div>
</header>

<ul class="nav nav-tabs" id="pxTab" role="tablist">{''.join(px_nav)}</ul>
<div class="tab-content" id="pxTabContent" style="margin-top: 1rem;">{''.join(px_body)}</div>
</main>

<script>
{DASHBOARD_JS}
</script>
</body>
</html>
"""


# ---------------- Discover + Build ----------------
def main():
    parser = argparse.ArgumentParser(description="Generate KubeVirt Performance Dashboard (PX version → disk → Total VMs).")
    parser.add_argument("--days", type=int, default=15)
    parser.add_argument("--base-dir", type=str, default="results")
    parser.add_argument("--output-html", type=str, default="results_dashboard.html")
    parser.add_argument("--cluster-info", type=str, help="Path to cluster_info.yaml file", default=None)
    parser.add_argument("--manual-results", type=str, help="Path to manual_results.yaml file", default=None)

    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    cutoff = datetime.now() - timedelta(days=args.days)
    px_map = {}
    ai_runs = discover_ai_round_runs(base_dir, cutoff)
    ai_runs_by_location = group_ai_runs_by_result_location(base_dir, ai_runs)

    if base_dir.exists():
        for px in sorted([d for d in base_dir.iterdir() if d.is_dir() and d.name != "ai-runs"]):
            disk_map = defaultdict(list)
            for disk_dir in sorted([d for d in px.iterdir() if d.is_dir()]):
                for run in sorted([r for r in disk_dir.iterdir() if r.is_dir()], key=lambda p: p.name):
                    try:
                        ts = run.name.split("_")[0]
                        dt = datetime.strptime(ts, "%Y%m%d-%H%M%S")
                        if dt >= cutoff:
                            disk_map[disk_dir.name].append(run)
                    except Exception:
                        continue
            if disk_map:
                px_map[px.name] = disk_map

    if not px_map:
        print("No results found within the timeframe.")
        return

    px_nav, px_body = [], []
    for idx, (px_version, disk_map) in enumerate(sorted(px_map.items())):
        px_id = f"px_{px_version}".replace(".", "_").replace("-", "_")
        active_cls = "active" if idx == 0 else ""
        show_cls = "show" if idx == 0 else ""
        px_nav.append(f'<li class="nav-item"><button class="nav-link {active_cls}" id="tab-{px_id}-tab" data-bs-toggle="tab" data-bs-target="#tab_{px_id}" type="button" role="tab">{px_version}</button></li>')
        px_body.append(f'<div class="tab-pane fade {show_cls} {active_cls}" id="tab_{px_id}" role="tabpanel">{build_px_tab(px_version, disk_map, ai_runs_by_location)}</div>')

    cluster_info = load_cluster_info(args.cluster_info) if args.cluster_info else None

    if cluster_info:
        cluster_html = build_cluster_info_tab(cluster_info)
        px_nav.append(
            '<li class="nav-item"><button class="nav-link" id="tab-clusterinfo-tab" data-bs-toggle="tab" data-bs-target="#tab_clusterinfo" type="button" role="tab">Cluster Info</button></li>')
        px_body.append(f'<div class="tab-pane fade" id="tab_clusterinfo" role="tabpanel">{cluster_html}</div>')

    manual_results = load_manual_results(args.manual_results) if args.manual_results else None
    if manual_results:
        manual_html = build_manual_results_tab(manual_results)
        px_nav.append(
            '<li class="nav-item"><button class="nav-link" id="tab-manualresults-tab" '
            'data-bs-toggle="tab" data-bs-target="#tab_manualresults" type="button" role="tab">Manual Results</button></li>'
        )
        px_body.append(f'<div class="tab-pane fade" id="tab_manualresults" role="tabpanel">{manual_html}</div>')

    out = Path(args.output_html)
    out.write_text(build_html_page(px_nav, px_body), encoding="utf-8")
    print(f"Dashboard generated: {out.absolute()}")


if __name__ == "__main__":
    main()
