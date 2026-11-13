#!/usr/bin/env python3
"""Interpret VirtBench AI round artifacts.

The planner records what it did. This module explains what the run means in a
small, deterministic structure that the dashboard and CLI can render.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple


SUCCESS_THRESHOLD = 0.98

PHASE_LABELS = {
    "vm_creation_duration_sec": "VM creation",
    "bootstorm_duration_sec": "Bootstorm",
    "migration_duration_sec": "Migration",
}


def _num(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    if number is None:
        return None
    return int(number)


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def _format_seconds(value: Optional[float]) -> str:
    rounded = _round(value)
    if rounded is None:
        return ""
    return str(int(rounded)) if float(rounded).is_integer() else str(rounded)


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _round_total_vms(item: dict) -> Optional[int]:
    return _int(_first_value(item.get("total_vms"), item.get("batch_size")))


def _load_json(path: Path) -> Optional[Any]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _metric(summary: dict, metric_name: str, stat_name: str) -> Optional[float]:
    for item in summary.get("metrics") or []:
        if item.get("metric") == metric_name:
            return _num(item.get(stat_name))
    return None


def _failed_resources(result_dir: Path, detail_filename: str, limit: int = 8) -> List[str]:
    data = _load_json(result_dir / detail_filename)
    if not isinstance(data, list):
        return []
    failed = []
    for item in data:
        if not isinstance(item, dict) or item.get("success", True):
            continue
        name = item.get("namespace") or item.get("vm") or item.get("name")
        if name:
            failed.append(str(name))
        if len(failed) >= limit:
            break
    return failed


def _failed_records(result_dir: Path, detail_filename: str) -> List[dict]:
    data = _load_json(result_dir / detail_filename)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and not item.get("success", True)]


def _result_dirs(item: dict) -> List[Path]:
    paths: List[Path] = []
    for value in (item.get("result_dirs") or []) + ([item.get("result_dir")] if item.get("result_dir") else []):
        path = Path(str(value)).expanduser()
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


SUMMARY_FILES = [
    ("VM creation", "summary_vm_creation_results.json", "vm_creation_results.json", "total_test_duration_sec"),
    ("Bootstorm", "summary_boot_storm_results.json", "boot_storm_results.json", "total_test_duration_sec"),
    ("Migration", "summary_migration_results.json", "migration_results.json", "total_migration_duration_sec"),
]


def _vm_creation_cause(result_dir: Path, summary: dict, failed_records: List[dict]) -> Dict[str, Any]:
    if not failed_records:
        return {}

    log_text = ""
    try:
        log_text = (result_dir / "datasource-clone.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    ping_timeout = None
    timeout_match = re.search(r"Ping timeout:\s*([0-9.]+)s", log_text)
    if timeout_match:
        ping_timeout = _num(timeout_match.group(1))

    running_values = [_num(item.get("running_time_sec")) for item in failed_records]
    running_values = [value for value in running_values if value is not None]
    clone_values = [_num(item.get("clone_duration_sec")) for item in failed_records]
    clone_values = [value for value in clone_values if value is not None]
    ping_missing = all(item.get("ping_time_sec") in (None, "") for item in failed_records)
    failed_names = [str(item.get("namespace")) for item in failed_records if item.get("namespace")]
    ip_count = 0
    ping_timeout_count = 0
    for name in failed_names:
        if re.search(rf"\[{re.escape(name)}\]\s+VMI IP:", log_text):
            ip_count += 1
        if re.search(rf"\[{re.escape(name)}\]\s+Ping timeout after", log_text):
            ping_timeout_count += 1

    max_successful_ping = _metric(summary, "ping_time_sec", "max")
    evidence = []
    if running_values:
        evidence.append(
            f"Failed VMs reached Running in {_format_seconds(min(running_values))}-{_format_seconds(max(running_values))}s."
        )
    if ip_count:
        evidence.append(f"{ip_count}/{len(failed_records)} failed VMs had VMI IPs recorded.")
    if ping_timeout_count:
        timeout_text = f" after {_format_seconds(ping_timeout)}s" if ping_timeout is not None else ""
        evidence.append(f"{ping_timeout_count}/{len(failed_records)} failed VMs hit ping timeout{timeout_text}.")
    if clone_values:
        evidence.append(f"Clone completed for failed VMs; max clone duration was {_format_seconds(max(clone_values))}s.")
    if max_successful_ping is not None:
        evidence.append(f"Slowest successful VM reachability was {_format_seconds(max_successful_ping)}s.")

    if ping_missing and running_values and ip_count:
        timeout_text = _format_seconds(ping_timeout) if ping_timeout is not None else "the configured"
        failure_reason = "VM reachability timeout after Running/IP assignment."
        analysis = (
            f"{len(failed_records)} VMs reached Running in "
            f"{_format_seconds(min(running_values))}-{_format_seconds(max(running_values))}s "
            f"and had IPs, but did not respond to ping from ssh-test-pod within {timeout_text}s. "
            "PVC clone completed for those VMs, so this was not a VM API create or PVC clone failure."
        )
        if max_successful_ping is not None:
            analysis += (
                " The rest of the batch showed severe reachability delay, with the slowest successful "
                f"VM reachable after {_format_seconds(max_successful_ping)}s."
            )
        return {
            "failure_category": "guest_reachability_timeout",
            "failure_reason": failure_reason,
            "failure_analysis": analysis,
            "root_cause_status": "not_proven_from_saved_artifacts",
            "cause_evidence": evidence,
            "recommended_checks": [
                "Inspect failed VM console or guest logs for network initialization issues.",
                "Compare node placement for failed and successful VMIs from the same round.",
                "Verify ssh-test-pod to VMI network path and CNI/OVN events during the timeout window.",
                "Check node pressure and pod scheduling events around the failed VMIs.",
            ],
        }

    return {"cause_evidence": evidence} if evidence else {}


def _summary_failure_detail(item: dict) -> Optional[Dict[str, Any]]:
    summaries = []
    for result_dir in _result_dirs(item):
        for phase, summary_name, detail_name, duration_key in SUMMARY_FILES:
            summary = _load_json(result_dir / summary_name)
            if not isinstance(summary, dict):
                continue

            total = _int(_first_value(summary.get("total_vms"), item.get("total_vms"), item.get("batch_size")))
            successful = _int(summary.get("successful"))
            failed = _int(summary.get("failed"))
            if failed is None and total is not None and successful is not None:
                failed = max(0, total - successful)
            success_rate = (successful / total) if total and successful is not None else _num(item.get("success_rate"))

            metrics = []
            metric_map = [
                ("ping_time_sec", "max", "Max reachability"),
                ("running_time_sec", "max", "Max running time"),
                ("clone_duration_sec", "max", "Max clone duration"),
                ("vmim_time_sec", "max", "Max VMIM duration"),
                ("observed_time_sec", "max", "Max observed duration"),
            ]
            for metric_name, stat_name, label in metric_map:
                value = _metric(summary, metric_name, stat_name)
                if value is not None:
                    metrics.append(f"{label}: {_round(value)}s")

            failed_records = _failed_records(result_dir, detail_name)
            detail = {
                "round": item.get("round"),
                "phase": phase,
                "total_vms": total,
                "successful": successful,
                "failed": failed,
                "success_percent": _round(success_rate * 100.0 if success_rate is not None else None),
                "duration_sec": _round(_num(summary.get(duration_key))),
                "result_dir": str(result_dir),
                "failed_resources": _failed_resources(result_dir, detail_name),
                "metrics": metrics,
            }
            if phase == "VM creation":
                detail.update(_vm_creation_cause(result_dir, summary, failed_records))
            summaries.append(detail)

    if not summaries:
        return None

    for summary in summaries:
        if summary.get("failed"):
            return summary
    if item.get("return_code") not in (0, None):
        return summaries[-1]
    return None


def _failure_analysis(payload: dict) -> List[Dict[str, Any]]:
    failures = []
    for item in payload.get("rounds") or []:
        if _round_status(item, bool(payload.get("execute"))) != "failed":
            continue

        detail = _summary_failure_detail(item) or {
            "round": item.get("round"),
            "phase": "Execution",
            "total_vms": _round_total_vms(item),
            "successful": None,
            "failed": None,
            "success_percent": _round((_num(item.get("success_rate")) or 0.0) * 100.0),
            "duration_sec": None,
            "result_dir": item.get("result_dir"),
            "failed_resources": [],
            "metrics": [],
        }

        total = detail.get("total_vms")
        successful = detail.get("successful")
        failed = detail.get("failed")
        phase = detail.get("phase")
        if successful is not None and failed is not None and total is not None:
            reason = f"Round {detail.get('round')} failed during {phase} at {total} VMs: {successful}/{total} passed, {failed} failed."
        elif total is not None:
            reason = f"Round {detail.get('round')} failed during {phase} at {total} VMs."
        else:
            reason = f"Round {detail.get('round')} failed during {phase}."

        detail["reason"] = reason
        detail["return_code"] = item.get("return_code")
        failures.append(detail)
    return failures


def _duration_values(rounds: Iterable[dict], key: str) -> List[Tuple[int, float]]:
    values = []
    for item in rounds:
        durations = item.get("workflow_durations_sec") or {}
        value = _num(durations.get(key))
        if value is not None:
            values.append((int(item.get("round") or len(values) + 1), value))
    return values


def _trend(values: List[Tuple[int, float]]) -> Dict[str, Any]:
    if len(values) < 2:
        return {
            "direction": "insufficient_data",
            "message": "Not enough duration data to determine a trend.",
        }

    first_round, first = values[0]
    last_round, last = values[-1]
    delta = last - first
    pct = (delta / first * 100.0) if first else None
    if abs(delta) < max(5.0, first * 0.05):
        direction = "flat"
        message = f"Duration stayed roughly flat from round {first_round} to round {last_round}."
    elif delta > 0:
        direction = "increased"
        message = f"Duration increased from round {first_round} to round {last_round}."
    else:
        direction = "decreased"
        message = f"Duration decreased from round {first_round} to round {last_round}."

    return {
        "direction": direction,
        "first_round": first_round,
        "last_round": last_round,
        "first_value_sec": _round(first),
        "last_value_sec": _round(last),
        "delta_sec": _round(delta),
        "delta_percent": _round(pct),
        "message": message,
    }


def _round_status(item: dict, executed: bool) -> str:
    rc = item.get("return_code")
    success = _num(item.get("success_rate"))
    if not executed:
        return "planned"
    if rc not in (0, None):
        return "failed"
    if success is None:
        return "unknown"
    return "passed" if success >= SUCCESS_THRESHOLD else "failed"


def _analysis_status(payload: dict, round_rows: List[dict]) -> str:
    if not payload.get("execute"):
        return "dry-run"
    statuses = {row.get("status") for row in round_rows}
    if "failed" in statuses:
        return "failed"
    if statuses and statuses <= {"passed"}:
        return "passed"
    if "passed" in statuses:
        return "partial"
    return "unknown"


def _limit_context(payload: dict) -> Dict[str, Any]:
    summary = payload.get("summary") or {}
    capacity = payload.get("capacity") or {}
    reason = summary.get("recommendation_reason")
    user_max = payload.get("user_max_vms", summary.get("user_max_vms"))
    estimated = capacity.get("estimated_vm_ceiling")
    effective = payload.get("effective_vm_ceiling", summary.get("effective_vm_ceiling"))
    best = summary.get("best_stable_total_vms", summary.get("best_stable_batch"))

    limited_by = "unknown"
    message = "Limit context was not available in the AI rounds artifact."
    if not payload.get("execute"):
        limited_by = "dry_run"
        message = "This was a dry-run plan; no runtime ceiling was measured."
    elif reason == "user_cap_reached":
        limited_by = "user_cap"
        message = (
            f"{best} VMs passed, but the run stopped at the user cap of {user_max}. "
            f"This is not a discovered cluster ceiling."
        )
    elif reason == "cluster_ceiling_reached":
        limited_by = "cluster_estimate"
        message = f"{best} VMs reached the estimated cluster ceiling of {estimated}."
    elif reason == "below_effective_ceiling":
        limited_by = "observed_below_cap"
        message = f"{best} VMs was the largest stable total below the effective cap of {effective}."
    elif reason == "no_stable_total":
        limited_by = "no_stable_total"
        message = "No stable VM total was observed."
    elif best not in (None, ""):
        limited_by = "observed"
        message = f"{best} VMs was the largest stable total observed."

    return {
        "limited_by": limited_by,
        "best_stable_total_vms": best,
        "user_max_vms": user_max,
        "estimated_vm_ceiling": estimated,
        "effective_vm_ceiling": effective,
        "message": message,
    }


def _workflow_duration_analysis(rounds: List[dict]) -> Dict[str, Any]:
    phase_stats = {}
    for key, label in PHASE_LABELS.items():
        values = [value for _, value in _duration_values(rounds, key)]
        if values:
            phase_stats[key] = {
                "label": label,
                "average_sec": _round(mean(values)),
                "max_sec": _round(max(values)),
                "rounds_with_data": len(values),
            }

    slowest_key = None
    if phase_stats:
        slowest_key = max(phase_stats, key=lambda key: phase_stats[key]["average_sec"] or 0)

    return {
        "total_workflow_trend": _trend(_duration_values(rounds, "total_workflow_duration_sec")),
        "phase_stats": phase_stats,
        "slowest_phase": phase_stats.get(slowest_key) if slowest_key else None,
    }


def _round_analysis(payload: dict) -> List[Dict[str, Any]]:
    executed = bool(payload.get("execute"))
    rows = []
    for item in payload.get("rounds") or []:
        durations = item.get("workflow_durations_sec") or {}
        total = _round_total_vms(item)
        success = _num(item.get("success_rate"))
        phase_values = {
            key: _num(durations.get(key))
            for key in PHASE_LABELS
            if _num(durations.get(key)) is not None
        }
        slowest_phase = None
        if phase_values:
            slowest_key = max(phase_values, key=phase_values.get)
            slowest_phase = {
                "name": PHASE_LABELS[slowest_key],
                "duration_sec": _round(phase_values[slowest_key]),
            }

        rows.append(
            {
                "round": item.get("round"),
                "total_vms": total,
                "status": _round_status(item, executed),
                "success_percent": _round(success * 100.0 if success is not None else None),
                "ai_decision": item.get("decision"),
                "next_total_vms": _first_value(item.get("next_total_vms"), item.get("next_batch")),
                "total_workflow_duration_sec": _round(_num(durations.get("total_workflow_duration_sec"))),
                "vm_creation_duration_sec": _round(_num(durations.get("vm_creation_duration_sec"))),
                "bootstorm_duration_sec": _round(_num(durations.get("bootstorm_duration_sec"))),
                "migration_duration_sec": _round(_num(durations.get("migration_duration_sec"))),
                "slowest_phase": slowest_phase,
            }
        )
    return rows


def _bottleneck_hints(
    payload: dict,
    round_rows: List[dict],
    duration_analysis: dict,
    limit_context: dict,
    failures: List[dict],
) -> List[dict]:
    hints: List[dict] = []
    scenario = payload.get("scenario")

    failure_by_round = {item.get("round"): item for item in failures}
    for item in payload.get("rounds") or []:
        rc = item.get("return_code")
        success = _num(item.get("success_rate"))
        total = _round_total_vms(item)
        failure = failure_by_round.get(item.get("round"))
        if rc not in (0, None):
            evidence_parts = []
            if failure:
                if failure.get("failure_analysis"):
                    evidence_parts.append(failure.get("failure_analysis"))
                evidence_parts.extend(failure.get("cause_evidence") or [])
                evidence_parts.extend(failure.get("metrics") or [])
            recommended_checks = failure.get("recommended_checks") if failure else None
            hints.append(
                {
                    "severity": "high",
                    "category": "execution",
                    "message": failure.get("reason") if failure else f"Round {item.get('round')} returned {rc} at {total} VMs.",
                    "evidence": (
                        "; ".join(evidence_parts)
                        if evidence_parts
                        else "Command return code was non-zero."
                    ),
                    "next_check": (
                        "; ".join(recommended_checks)
                        if recommended_checks
                        else "Inspect the failed namespace logs and placement for this round before increasing VM count."
                    )
                    if failure
                    else None,
                }
            )
        elif success is not None and success < SUCCESS_THRESHOLD:
            hints.append(
                {
                    "severity": "high",
                    "category": "stability",
                    "message": f"Round {item.get('round')} reached only {round(success * 100, 2)}% success at {total} VMs.",
                    "evidence": "Success rate was below 98%.",
                }
            )

    if limit_context.get("limited_by") == "user_cap":
        hints.append(
            {
                "severity": "info",
                "category": "capacity",
                "message": "The largest stable total is user-capped, not a discovered platform limit.",
                "evidence": limit_context.get("message"),
            }
        )

    slowest = duration_analysis.get("slowest_phase") or {}
    slowest_label = slowest.get("label")
    if scenario == "vm-workflow-basic" and slowest_label:
        guidance = {
            "VM creation": "Check PVC clone time, scheduler placement, API throttling, and storage class behavior.",
            "Bootstorm": "Check VMI readiness latency, node pressure, CPU saturation, and image pull or guest boot delays.",
            "Migration": "Check VMIM duration, source/target balance after rebalance, storage latency, and network throughput.",
        }.get(slowest_label)
        hints.append(
            {
                "severity": "medium",
                "category": "duration",
                "message": f"{slowest_label} was the slowest workflow phase by average duration.",
                "evidence": f"Average duration was {slowest.get('average_sec')} seconds.",
                "next_check": guidance,
            }
        )

    total_trend = duration_analysis.get("total_workflow_trend") or {}
    if total_trend.get("direction") == "increased" and _num(total_trend.get("delta_percent")) and total_trend["delta_percent"] > 50:
        hints.append(
            {
                "severity": "medium",
                "category": "scaling",
                "message": "Total workflow duration increased sharply across rounds.",
                "evidence": (
                    f"Duration changed by {total_trend.get('delta_sec')} seconds "
                    f"({total_trend.get('delta_percent')}%)."
                ),
            }
        )

    if not hints:
        hints.append(
            {
                "severity": "info",
                "category": "result",
                "message": "No obvious bottleneck was detected from the AI rounds artifact.",
                "evidence": "All recorded rounds were stable and no phase-level outlier was identified.",
            }
        )
    return hints


def _action_items(payload: dict, status: str, limit_context: dict, hints: List[dict], failures: List[dict]) -> List[str]:
    actions = []
    scenario = payload.get("scenario")
    if limit_context.get("limited_by") == "user_cap":
        actions.append("Increase max VMs in the next run to discover the real cluster ceiling.")
    if status == "failed":
        first_failure = failures[0] if failures else {}
        if first_failure.get("failed_resources"):
            resources = ", ".join(first_failure["failed_resources"][:5])
            actions.append(f"Inspect failed namespaces from round {first_failure.get('round')}: {resources}.")
        else:
            actions.append("Inspect the first failed round before increasing the VM total.")
    if scenario == "vm-workflow-basic":
        categories = {hint.get("category") for hint in hints}
        if "duration" in categories:
            actions.append("Compare phase durations with raw creation, bootstorm, and migration result tabs.")
    if not actions:
        actions.append("Repeat with a higher VM cap or more rounds if the goal is to find the ceiling.")
    return actions


def build_analysis(payload: dict) -> Dict[str, Any]:
    round_rows = _round_analysis(payload)
    failures = _failure_analysis(payload)
    failure_by_round = {item.get("round"): item for item in failures}
    for row in round_rows:
        failure = failure_by_round.get(row.get("round"))
        if not failure:
            continue
        if row.get("success_percent") in (None, 0, 0.0) and failure.get("success_percent") is not None:
            row["success_percent"] = failure.get("success_percent")
        if row.get("total_vms") in (None, "") and failure.get("total_vms") is not None:
            row["total_vms"] = failure.get("total_vms")

    status = _analysis_status(payload, round_rows)
    limit_context = _limit_context(payload)
    duration_analysis = _workflow_duration_analysis(payload.get("rounds") or [])
    hints = _bottleneck_hints(payload, round_rows, duration_analysis, limit_context, failures)
    summary = payload.get("summary") or {}

    return {
        "schema_version": 1,
        "analysis_type": "virtbench-ai-validation",
        "created_at": datetime.now().isoformat(),
        "source_created_at": payload.get("created_at"),
        "scenario": payload.get("scenario"),
        "objective": payload.get("objective"),
        "status": status,
        "headline": limit_context.get("message"),
        "largest_stable_total_vms": limit_context.get("best_stable_total_vms"),
        "limited_by": limit_context.get("limited_by"),
        "limit_context": limit_context,
        "round_analysis": round_rows,
        "failure_analysis": failures,
        "duration_analysis": duration_analysis,
        "bottleneck_hints": hints,
        "action_items": _action_items(payload, status, limit_context, hints, failures),
        "recommendation": summary.get("final_recommendation"),
    }


def analysis_to_markdown(analysis: dict) -> str:
    lines = [
        f"# VirtBench AI Validation Analysis - {analysis.get('scenario')}",
        "",
        f"- Status: {analysis.get('status')}",
        f"- Largest stable total VMs: {analysis.get('largest_stable_total_vms', 'N/A')}",
        f"- Limit context: {analysis.get('headline', 'N/A')}",
        f"- Recommendation: {analysis.get('recommendation', 'N/A')}",
        "",
        "## Bottleneck Hints",
    ]
    for hint in analysis.get("bottleneck_hints") or []:
        lines.append(f"- [{hint.get('severity')}] {hint.get('message')}")
        if hint.get("next_check"):
            lines.append(f"  Next check: {hint.get('next_check')}")

    failures = analysis.get("failure_analysis") or []
    if failures:
        lines.extend(["", "## Failure Analysis"])
        for failure in failures:
            lines.append(f"- {failure.get('reason')}")
            if failure.get("failure_reason"):
                lines.append(f"  Failure reason: {failure.get('failure_reason')}")
            if failure.get("failure_analysis"):
                lines.append(f"  Analysis: {failure.get('failure_analysis')}")
            if failure.get("cause_evidence"):
                lines.append(f"  Cause evidence: {'; '.join(failure.get('cause_evidence'))}")
            if failure.get("recommended_checks"):
                lines.append(f"  Recommended checks: {'; '.join(failure.get('recommended_checks'))}")
            if failure.get("metrics"):
                lines.append(f"  Evidence: {'; '.join(failure.get('metrics'))}")
            if failure.get("failed_resources"):
                lines.append(f"  Failed namespaces: {', '.join(failure.get('failed_resources'))}")

    lines.extend(["", "## Action Items"])
    for item in analysis.get("action_items") or []:
        lines.append(f"- {item}")

    return "\n".join(lines) + "\n"


def write_analysis(rounds_path: Path, output_dir: Optional[Path] = None) -> Dict[str, Any]:
    payload = json.loads(rounds_path.read_text(encoding="utf-8"))
    analysis = build_analysis(payload)
    target_dir = output_dir or rounds_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "virtbench_ai_analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    (target_dir / "virtbench_ai_analysis.md").write_text(analysis_to_markdown(analysis), encoding="utf-8")
    return analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a VirtBench AI rounds artifact.")
    parser.add_argument("rounds_json", help="Path to virtbench_ai_rounds.json")
    parser.add_argument("--output-dir", help="Directory for virtbench_ai_analysis.json/md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    analysis = write_analysis(Path(args.rounds_json).expanduser(), output_dir)
    print(json.dumps(analysis, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
