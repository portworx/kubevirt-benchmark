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

import json
import argparse
import pandas as pd
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict


LABEL_MAP = {
    "running_time_sec": "Running Time",
    "ping_time_sec": "Ping Time",
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

    return df.to_html(
        classes="display compact nowrap",
        table_id="table_manual_results",
        index=False,
        border=0
    )

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

    return df.to_html(
        classes="display compact nowrap",
        table_id=table_id,
        index=False,
        border=0
    )

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

    df = pd.DataFrame(records).sort_values("VMs")
    x_vals = df["VMs"].tolist()
    y_secs = df["Seconds"].tolist()
    folder_labels = df["Folder"].tolist()  # used in hover only

    max_sec = max(y_secs) if y_secs else 0
    step = 10 if max_sec <= 60 else 30 if max_sec <= 180 else 60 if max_sec <= 600 else 120
    ticks = list(range(0, int(max_sec + step), step))
    ticktext = [format_mmss(t) for t in ticks]

    return f"""
    <div class="col-12 col-md-4">
      <div id="{chart_id}" class="plotly-chart" style="height:360px; width:100%;"></div>
      <script>
        Plotly.newPlot('{chart_id}', [{{
          x: {x_vals},
          y: {y_secs},
          type: 'bar',
          marker: {{
            color: '{color}',
            line: {{color: 'rgba(0,0,0,0.35)', width: 1}}
          }},
          hovertemplate: 'Folder: %{{text}}<br>VMs: %{{x}}<br>Duration: %{{customdata}}<extra></extra>',
          text: {folder_labels},
          customdata: { [format_mmss(v) for v in y_secs] },
          textposition: 'none'
        }}], {{
          title: '{title}',
          xaxis: {{
            title: 'Total VMs',
            type: 'category',
            categoryorder: 'array',
            categoryarray: {x_vals}
          }},
          yaxis: {{
            title: 'Duration (mm:ss)',
            tickmode: 'array',
            tickvals: {ticks},
            ticktext: {ticktext}
          }},
          margin: {{t: 50, l: 50, r: 20, b: 50}},
          bargap: 0.3
        }}, {{responsive: true,  displayModeBar: false}});
      </script>
    </div>
    """


# ---------------- Folder Builders ----------------
def build_creation_boot_content(folder: Path, uid: str) -> str:
    """Creation + Boot Storm section (original style)."""
    creation_summary = load_json(folder / "summary_vm_creation_results.json")
    boot_summary = load_json(folder / "summary_boot_storm_results.json")
    creation_results = load_json(folder / "vm_creation_results.json")
    boot_results = load_json(folder / "boot_storm_results.json")

    creation_df = pd.DataFrame(creation_results or [])
    boot_df = pd.DataFrame(boot_results or [])
    if not creation_df.empty and "success" in creation_df.columns:
        cols = [c for c in creation_df.columns if c != "success"] + ["success"]
        creation_df = creation_df[cols]

    creation_summary_df, _, creation_total_info = summary_to_df(creation_summary)
    boot_summary_df, _, boot_total_info = summary_to_df(boot_summary)
    creation_total_info = creation_total_info or {}
    boot_total_info = boot_total_info or {}

    creation_total_time = creation_summary.get("total_test_duration_sec") if creation_summary else None
    boot_total_time = boot_summary.get("total_test_duration_sec") if boot_summary else None

    header_html = (
        f'<h6><strong>Results Directory:</strong> {folder.name}</h6>'
        f'<h6><strong>Total VMs:</strong> {creation_total_info.get("total_vms", "?")}</h6>'
        f'{f"<h6><strong>Total Creation Duration:</strong> {creation_total_time:.2f} s</h6>" if creation_total_time else ""}'
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
              <td>{status_badge} <a href="#" onclick="event.preventDefault(); showVmChart_{uid}({idx});">{ns}</a></td>
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


# ---------------- Disk and PX Builders ----------------
def build_disk_tab(px_version: str, disk_name: str, folders: list) -> str:
    """Disk-level tab with charts and nested VM-size tabs."""
    creation_rec, boot_rec, mig_rec, cap_rec = [], [], [], []
    by_vms = defaultdict(list)

    for folder in sorted(folders, key=lambda p: p.name):
        vms = get_vm_count_from_folder(folder.name)
        by_vms[vms].append(folder)
        csum = load_json(folder / "summary_vm_creation_results.json")
        bsum = load_json(folder / "summary_boot_storm_results.json")
        msum = load_json(folder / "summary_migration_results.json")
        capsum = load_json(folder / "summary_chaos_benchmark.json")
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
        build_bar_chart_mmss(creation_rec, "Creation Duration", "rgb(26,118,255)", f"chart_{px_version}_{disk_name}_creation"),
        build_bar_chart_mmss(boot_rec, "Boot Storm Duration", "rgb(0,204,150)", f"chart_{px_version}_{disk_name}_boot"),
        build_bar_chart_mmss(mig_rec, "Live Migration Duration", "rgb(255,99,71)", f"chart_{px_version}_{disk_name}_mig"),
    ]
    if cap_rec:
        chart_items.append(build_bar_chart_mmss(cap_rec, "Chaos Benchmark Duration", "rgb(153,102,255)", f"chart_{px_version}_{disk_name}_cap"))

    charts_html = f"""
    <div class="container-fluid mt-3">
      <div class="row g-3">
        {''.join(chart_items)}
      </div>
      <hr>
    </div>
    """

    # VM-size nested tabs
    vm_sizes_sorted = sorted([v for v in by_vms.keys() if v > 0])
    if not vm_sizes_sorted:
        return charts_html + "<p>No result folders found under this disk.</p>"

    vm_tabs_nav, vm_tabs_body = [], []
    for idx, vm_count in enumerate(vm_sizes_sorted):
        vm_id = f"{px_version}_{disk_name}_{vm_count}".replace(".", "_").replace("-", "_")
        active_cls = "active" if idx == 0 else ""
        show_cls = "show" if idx == 0 else ""
        vm_tabs_nav.append(
            f'<li class="nav-item"><button class="nav-link {active_cls}" id="tab-{vm_id}-tab" data-bs-toggle="tab" '
            f'data-bs-target="#tab_{vm_id}" type="button" role="tab">{vm_count} VMs</button></li>'
        )

        cb_sections = "".join(
            build_creation_boot_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in by_vms[vm_count] if (f / "summary_vm_creation_results.json").exists()
        ) or "<p>No Creation/Boot Storm data for this VM size.</p>"

        mig_sections = "".join(
            build_migration_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in by_vms[vm_count] if (f / "summary_migration_results.json").exists()
        ) or "<p>No Live Migration data for this VM size.</p>"

        cap_sections = "".join(
            build_capacity_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in by_vms[vm_count] if (f / "summary_chaos_benchmark.json").exists()
        ) or "<p>No Chaos Benchmark data for this VM size.</p>"

        fio_sections = "".join(
            build_fio_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in by_vms[vm_count] if (f / "summary_fio_benchmark.json").exists() or (f / "fio_benchmark_results.json").exists()
        ) or "<p>No FIO Benchmark data for this VM size.</p>"

        elbencho_sections = "".join(
            build_elbencho_content(f, uid=f"{px_version}_{disk_name}_{vm_count}_{f.name}".replace(".", "_").replace("-", "_"))
            for f in by_vms[vm_count] if (f / "aggregated_results.json").exists()
        ) or "<p>No Elbencho Benchmark data for this VM size.</p>"

        # Check if we have capacity/fio/elbencho data to show the tabs
        has_capacity_data = any((f / "summary_capacity_benchmark.json").exists() for f in by_vms[vm_count])
        has_fio_data = any((f / "summary_fio_benchmark.json").exists() or (f / "fio_benchmark_results.json").exists() for f in by_vms[vm_count])
        has_elbencho_data = any((f / "aggregated_results.json").exists() for f in by_vms[vm_count])
        # Check if we have chaos data to show the tab
        has_capacity_data = any((f / "summary_chaos_benchmark.json").exists() for f in by_vms[vm_count])

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
    <ul class="nav nav-tabs" role="tablist">{''.join(vm_tabs_nav)}</ul>
    <div class="tab-content mt-3">{''.join(vm_tabs_body)}</div>
    """


def build_px_tab(px_version: str, disk_map: dict) -> str:
    """Top-level PX version tab with disk subtabs."""
    disk_names = sorted(disk_map.keys(), key=lambda d: (int(d.split('-')[0]) if d.split('-')[0].isdigit() else 0, d))
    if not disk_names:
        return "<p>No disk groups found for this PX version.</p>"

    nav, body = [], []
    for idx, dname in enumerate(disk_names):
        did = f"{px_version}_{dname}".replace(".", "_").replace("-", "_")
        active_cls = "active" if idx == 0 else ""
        show_cls = "show" if idx == 0 else ""
        nav.append(f'<li class="nav-item"><button class="nav-link {active_cls}" id="tab-{did}-tab" data-bs-toggle="tab" data-bs-target="#tab_{did}" type="button" role="tab">{dname}</button></li>')
        body.append(f'<div class="tab-pane fade {show_cls} {active_cls}" id="tab_{did}" role="tabpanel">{build_disk_tab(px_version, dname, disk_map[dname])}</div>')

    return f"<ul class='nav nav-tabs mt-2' role='tablist'>{''.join(nav)}</ul><div class='tab-content mt-3'>{''.join(body)}</div>"


# ---------------- Main HTML ----------------
def build_html_page(px_nav, px_body):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
body {{ margin: 20px; }}
h3 {{ margin-top: 30px; }}
.dataTables_wrapper {{ width: 100%; margin: 0 auto; }}
table.dataTable {{ width: 100% !important; }}
</style>
</head>
<body>
<h1 class="mb-2">KubeVirt Performance Dashboard</h1>
<p><strong>Generated:</strong> {now}</p>

<ul class="nav nav-tabs" id="pxTab" role="tablist">{''.join(px_nav)}</ul>
<div class="tab-content" id="pxTabContent" style="margin-top: 1rem;">{''.join(px_body)}</div>

<script>
$(document).ready(function() {{
  $('table.display').DataTable({{ scrollX: true, autoWidth: false, pageLength: 25 }});

  $('a[data-bs-toggle="tab"], button[data-bs-toggle="tab"]').on('shown.bs.tab', function () {{
    $.fn.dataTable.tables({{visible: true, api: true}}).columns.adjust();
    setTimeout(function () {{
      document.querySelectorAll('.tab-pane.active .plotly-chart').forEach(function (el) {{
        try {{ Plotly.Plots.resize(el); }} catch(e) {{}}
      }});
    }}, 100);
  }});

  const topTabs = document.querySelectorAll('#pxTab .nav-link');
  if (topTabs.length > 0) {{
    const first = new bootstrap.Tab(topTabs[0]);
    first.show();
  }}

  setTimeout(function () {{
    document.querySelectorAll('.plotly-chart').forEach(function (el) {{
      try {{ Plotly.Plots.resize(el); }} catch(e) {{}}
    }});
  }}, 400);
}});
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

    for px in sorted([d for d in base_dir.iterdir() if d.is_dir()]):
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
        px_body.append(f'<div class="tab-pane fade {show_cls} {active_cls}" id="tab_{px_id}" role="tabpanel">{build_px_tab(px_version, disk_map)}</div>')

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
