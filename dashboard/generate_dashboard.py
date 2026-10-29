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
import sys
import pandas as pd
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

try:  # the logo lives in the virtbench package; the dashboard still works without it
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from virtbench.dashboard_logo import LOGO_PNG_BASE64
except ImportError:
    LOGO_PNG_BASE64 = ""


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

# A run folder is only listed when it holds at least one of these (incomplete runs leave an empty folder behind).
RESULT_FILES = (
    "summary_vm_creation_results.json",
    "summary_boot_storm_results.json",
    "summary_migration_results.json",
    "summary_chaos_benchmark.json",
    "summary_fio_benchmark.json",
    "fio_benchmark_results.json",
    "aggregated_results.json",
    "disk_ops_results.json",
)


def has_results(folder: Path) -> bool:
    return any((folder / name).exists() for name in RESULT_FILES)


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

    return f"<table class='table w-100'><tbody>{rows}</tbody></table>"

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


def fmt_secs(seconds) -> str:
    """45.2 s, or 9m 24s once it passes a minute."""
    if seconds is None:
        return "–"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.1f} s"
    m, sec = divmod(int(round(seconds)), 60)
    return f"{m}m {sec:02d}s"


def friendly_date(folder_name: str) -> str:
    """'20260428-151839_disk_ops_benchmark_1-10' -> '28 Apr 2026, 15:18' (the name itself when it has no date)."""
    try:
        when = datetime.strptime(folder_name.split("_")[0], "%Y%m%d-%H%M%S")
        return when.strftime("%d %b %Y, %H:%M").lstrip("0")
    except ValueError:
        return folder_name


def run_header(folder_name: str, facts=None, tags=None, status_html: str = "", show_date: bool = True) -> str:
    """The card that opens every run: its date, the folder it came from, the headline facts and the settings used.

    facts: (label, value) or (label, value, sub_html) tuples; None entries are skipped.
    tags: (label, value) tuples shown as small pills.
    """
    try:
        when = datetime.strptime(folder_name.split("_")[0], "%Y%m%d-%H%M%S")
        title = when.strftime("%d %b %Y, %H:%M").lstrip("0")
    except ValueError:
        title = folder_name

    fact_html = ""
    for fact in facts or []:
        if not fact:
            continue
        label, value = fact[0], fact[1]
        sub = f'<div class="s">{fact[2]}</div>' if len(fact) > 2 and fact[2] else ""
        fact_html += f'<div class="fact"><div class="k">{label}</div><div class="v">{value}</div>{sub}</div>'

    tag_html = "".join(
        f'<span class="pill"><span class="k">{label}</span> {value}</span>'
        for label, value in (tags or [])
        if value not in (None, "", "N/A")
    )
    folder_html = ""
    return f"""
    <div class="run-head">
      {f'<div class="run-top"><span class="run-date" title="{folder_name}">{title}</span>{folder_html}{status_html}</div>' if show_date else ""}
      {f'<div class="run-facts">{fact_html}</div>' if fact_html else ""}
      {f'<div class="run-tags">{tag_html}</div>' if tag_html else ""}
    </div>
    """


READ_COLOR, WRITE_COLOR = "#5598e7", "#f08a5d"


def split_card(title: str, unit: str, read: float, write: float, digits: int = 0) -> str:
    """A card with the combined number up top and one bar showing how it divides between read and write."""
    total = read + write
    read_pct = 100.0 * read / total if total else 0.0
    write_pct = 100.0 - read_pct if total else 0.0
    return f"""
    <div class="col-md-4">
      <div class="card h-100 op-card">
        <div class="op-top"><span class="op-name">{title}</span></div>
        <div class="op-big">{total:,.{digits}f} <small>{unit} total</small></div>
        <div class="split">
          <span style="width:{read_pct:.1f}%;background:{READ_COLOR}"></span><span style="width:{write_pct:.1f}%;background:{WRITE_COLOR}"></span>
        </div>
        <div class="split-leg">
          <span><i class="dot" style="background:{READ_COLOR}"></i>Read <b>{read:,.{digits}f}</b> <em>{read_pct:.0f}%</em></span>
          <span><i class="dot" style="background:{WRITE_COLOR}"></i>Write <b>{write:,.{digits}f}</b> <em>{write_pct:.0f}%</em></span>
        </div>
      </div>
    </div>
    """


def latency_card(title: str, read_ms: float, write_ms: float) -> str:
    """Read and write latency are not additive, so they get one bar each, drawn on the same scale."""
    top = max(read_ms, write_ms) or 1
    rows = ""
    for label, value, color in (("Read", read_ms, READ_COLOR), ("Write", write_ms, WRITE_COLOR)):
        width = max(2.0, 100.0 * value / top)
        rows += (
            f'<div class="ph"><span>{label}</span>'
            f'<span class="track"><span class="fill" style="width:{width:.1f}%;background:{color}"></span></span>'
            f'<span class="val">{value:,.2f} ms</span></div>'
        )
    return f"""
    <div class="col-md-4">
      <div class="card h-100 op-card">
        <div class="op-top"><span class="op-name">{title}</span><span class="text-muted small">lower is better</span></div>
        <div class="phases" style="margin-top:20px">{rows}</div>
      </div>
    </div>
    """


def single_latency_card(title: str, avg_ms: float, min_ms: float, max_ms: float) -> str:
    return f"""
    <div class="col-md-4">
      <div class="card h-100 op-card">
        <div class="op-top"><span class="op-name">{title}</span><span class="text-muted small">lower is better</span></div>
        <div class="op-big">{avg_ms:,.2f} <small>ms average</small></div>
        <div class="op-sub">min {min_ms:,.2f} ms · max {max_ms:,.2f} ms</div>
      </div>
    </div>
    """


# ---------------- Chart Builder ----------------
def build_bar_chart_mmss(records, title, color, chart_id):
    """A duration-vs-VM-count bar chart in a card, with mm:ss labels and the folder name on hover."""
    if not records:
        return ""

    df = pd.DataFrame(records).sort_values("VMs")
    x_vals = df["VMs"].tolist()
    y_secs = df["Seconds"].tolist()
    mmss = [format_mmss(v) for v in y_secs]
    customdata = [[m, f] for m, f in zip(mmss, df["Folder"].tolist())]

    max_sec = max(y_secs) if y_secs else 0
    step = 10 if max_sec <= 60 else 30 if max_sec <= 180 else 60 if max_sec <= 600 else 120
    ticks = list(range(0, int(max_sec + step), step))
    ticktext = [format_mmss(t) for t in ticks]

    return f"""
    <div class="col-12 col-md-4">
      <div class="card h-100">
        <div class="card-header"><strong>{title}</strong> <span class="hint">mm:ss by total VMs</span></div>
        <div class="card-body p-2">
          <div id="{chart_id}" class="plotly-chart" style="height:300px; width:100%;"></div>
        </div>
      </div>
      <script>
        (function () {{
          var ink = VB.css('--ink'), ink2 = VB.css('--ink-2'), ink3 = VB.css('--ink-3');
          Plotly.newPlot('{chart_id}', [{{
            x: {x_vals},
            y: {y_secs},
            type: 'bar',
            marker: {{ color: '{color}', cornerradius: 4 }},
            text: {mmss},
            textposition: 'outside',
            cliponaxis: false,
            textfont: {{ color: ink2, size: 12 }},
            customdata: {customdata},
            hovertemplate: 'Folder: %{{customdata[1]}}<br>VMs: %{{x}}<br>Duration: %{{customdata[0]}}<extra></extra>'
          }}], {{
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: {{ family: VB.font, color: ink2, size: 12 }},
            xaxis: {{
              title: {{ text: 'Total VMs', font: {{ color: ink3, size: 12 }} }},
              type: 'category',
              categoryorder: 'array',
              categoryarray: {x_vals},
              showgrid: false,
              linecolor: VB.css('--axis'),
              tickfont: {{ color: ink2 }}
            }},
            yaxis: {{
              tickmode: 'array',
              tickvals: {ticks},
              ticktext: {ticktext},
              gridcolor: VB.css('--line'),
              zeroline: false,
              showline: false,
              tickfont: {{ color: ink3 }}
            }},
            margin: {{ t: 28, l: 48, r: 12, b: 48 }},
            bargap: 0.55
          }}, {{responsive: true, displayModeBar: false}});
        }})();
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

    header_html = run_header(
        folder.name,
        facts=[
            ("Total VMs", creation_total_info.get("total_vms", "?")),
            ("Creation time", fmt_secs(creation_total_time)) if creation_total_time else None,
            ("Boot storm time", fmt_secs(boot_total_time)) if boot_total_time else None,
        ],
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

    header_html = run_header(
        folder.name,
        facts=[
            ("Total VMs", migration_total_info.get("total_vms", "?")),
            ("Migration time", fmt_secs(total_time)) if total_time else None,
        ],
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

    header_html = run_header(
        folder.name,
        facts=[
            ("VMs created", total_vms),
            ("PVCs created", total_pvcs),
            ("Iterations", iterations),
            ("Duration", fmt_secs(total_time)) if total_time else None,
        ],
        tags=[("Storage class", config.get("storage_classes", "N/A"))],
    )

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
    <table class="table w-100">
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
    <table class="table w-100">
      <tbody>{results_rows}</tbody>
    </table>
    """

    # Phases skipped
    phases_skipped = capacity_results.get("phases_skipped", []) if capacity_results else []
    phases_html = ", ".join(phases_skipped) if phases_skipped else "None"

    return f"""
    <div class="mb-4">
      {header_html}
      <p class="mb-0">{status_html}</p>

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

    header_html = run_header(
        folder.name,
        facts=[
            ("VMs tested", total_vms, f'<span class="ok">{successful} passed</span> · <span class="bad">{failed} failed</span>'),
            ("Test duration", fmt_secs(duration)),
        ],
        tags=[
            ("Pattern", config.get("rw")),
            ("Block size", config.get("bs")),
            ("I/O depth", config.get("iodepth")),
            ("Jobs", config.get("numjobs")),
            ("Runtime", f"{config.get('runtime')}s" if config.get("runtime") else None),
            ("File size", config.get("size")),
        ],
    )

    # Config table
    config_rows = "".join([
        f"<tr><th>Block Size</th><td>{config.get('bs', 'N/A')}</td></tr>",
        f"<tr><th>I/O Pattern</th><td>{config.get('rw', 'N/A')}</td></tr>",
        f"<tr><th>I/O Depth</th><td>{config.get('iodepth', 'N/A')}</td></tr>",
        f"<tr><th>Num Jobs</th><td>{config.get('numjobs', 'N/A')}</td></tr>",
        f"<tr><th>Runtime</th><td>{config.get('runtime', 'N/A')} s</td></tr>",
        f"<tr><th>File Size</th><td>{config.get('size', 'N/A')}</td></tr>",
    ])
    config_table = f'<table class="table w-100"><tbody>{config_rows}</tbody></table>'

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
    <table class="table w-100">
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
                    datasets: [{{ data: [vm.read_iops, vm.write_iops], backgroundColor: ['#5598e7', '#f08a5d'] }}]
                  }},
                  options: {{ responsive: true, plugins: {{ legend: {{ display: false }}, title: {{ display: true, text: 'IOPS' }} }} }}
                }});

                // Bandwidth Chart
                vmBwChartInstance_{uid} = new Chart(document.getElementById('vmBwChart_{uid}'), {{
                  type: 'bar',
                  data: {{
                    labels: ['Read', 'Write'],
                    datasets: [{{ data: [vm.read_bw, vm.write_bw], backgroundColor: ['#5598e7', '#f08a5d'] }}]
                  }},
                  options: {{ responsive: true, plugins: {{ legend: {{ display: false }}, title: {{ display: true, text: 'Bandwidth (MiB/s)' }} }} }}
                }});

                // Latency Chart
                vmLatChartInstance_{uid} = new Chart(document.getElementById('vmLatChart_{uid}'), {{
                  type: 'bar',
                  data: {{
                    labels: ['Read Latency', 'Write Latency'],
                    datasets: [{{ data: [vm.read_lat, vm.write_lat], backgroundColor: ['#5598e7', '#f08a5d'] }}]
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

    chart_html = f"""
    <div class="row g-3 mb-4">
      {split_card("IOPS", "IOPS", read_iops, write_iops, 0)}
      {split_card("Bandwidth", "MiB/s", read_bw, write_bw, 1)}
      {latency_card("Mean latency", read_lat, write_lat)}
    </div>
    """

    return f"""
    <div class="mb-4">
      {header_html}
      {chart_html}
      <h4>Aggregated results across all VMs</h4>
      {metrics_table}
      <h4 class="mt-4">Per-VM details</h4>
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

    status = data.get("status", "completed")
    status_pill = (
        '<span class="badge bg-success">Completed</span>'
        if status == "completed"
        else f'<span class="badge bg-info">{status}</span>'
    )
    header_html = run_header(
        folder.name,
        facts=[("Test", test_name), ("Runtime", f"{runtime} s")],
        status_html=status_pill,
    )

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

    config_table = f'<table class="table w-100"><tbody>{config_rows}</tbody></table>'

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

    perf_table = f'<table class="table w-100"><tbody>{perf_rows}</tbody></table>' if perf_rows else "<p>No metrics.</p>"

    status = data.get("status", "completed")
    status_html = '<span class="badge bg-success">Completed</span>' if status == "completed" else f'<span class="badge bg-info">{status}</span>'

    return f"""
    <div class="mb-4">
      {header_html}
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
    header_html = run_header(
        folder.name,
        facts=[
            ("VMs with results", vms_with_results, f"{vms_skipped} skipped" if vms_skipped else ""),
            ("Collection time", fmt_secs(elapsed_seconds)),
        ],
    )

    kpi_html = f"""
    <div class="row g-3 mb-4">
      {split_card("IOPS", "IOPS", read_iops, write_iops, 0)}
      {split_card("Throughput", "MB/s", read_throughput_mbs, write_throughput_mbs, 1)}
      {single_latency_card("Latency", avg_latency_ms, min_latency_ms, max_latency_ms)}
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
    <table class="table w-100">
      <thead class="table-dark"><tr><th>Metric</th><th>Value</th></tr></thead>
      <tbody>{metrics_rows}</tbody>
    </table>
    """

    chart_html = ""

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
    header_html = run_header(
        folder.name,
        facts=[("Total VMs", total_vms), ("Total operations", total_ops)],
        tags=[
            ("Operation", config.get("operation", "N/A").upper()),
            ("Disks per VM", config.get("disks_per_vm")),
            ("Disk size", config.get("disk_size")),
            ("Storage class", config.get("storage_class")),
            ("Unplug tested", "Yes" if config.get("test_unplug") else "No"),
            ("Validation", "skipped" if config.get("skip_validation") else "on"),
        ],
        show_date=False,
    )

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
    config_table = f'<table class="table w-100"><tbody>{config_rows}</tbody></table>'

    # One card per operation: the headline time, how many succeeded, and the phases drawn against the total.
    def op_card(title, color, d, phases, note):
        if not d:
            return ""
        count = d.get("count", 0)
        success = d.get("success_count", 0)
        badge = "bg-success" if success == count else ("bg-warning" if success > 0 else "bg-danger")
        total = d.get("avg_total_time", 0) or 0
        bars = ""
        for label, value in phases:
            width = max(2.0, min(100.0, 100.0 * value / total)) if total else 0
            bars += (
                f'<div class="ph"><span>{label}</span>'
                f'<span class="track"><span class="fill" style="width:{width:.1f}%;background:{color}"></span></span>'
                f'<span class="val">{value:.1f} s</span></div>'
            )
        return f"""
        <div class="col-md-4">
          <div class="card h-100 op-card">
            <div class="op-top">
              <span class="op-name"><span class="dot" style="background:{color}"></span>{title}</span>
              <span class="badge {badge}">{success}/{count} succeeded</span>
            </div>
            <div class="op-big">{total:.1f} <small>s average total</small></div>
            <div class="op-sub">{note}</div>
            {f'<div class="phases">{bars}</div>' if bars else ""}
          </div>
        </div>
        """

    hp_card = op_card(
        "Hotplug", "#5598e7", hotplug,
        [("API attach", hotplug.get("avg_api_attach_time", 0)),
         ("Volume ready", hotplug.get("avg_volume_ready_time", 0)),
         ("Validation", hotplug.get("avg_validation_time", 0))],
        f"{hotplug.get('total_disks_attached', 0)} disks attached · {hotplug.get('total_disks_validated', 0)} validated",
    ) if hotplug else ""
    cp_card = op_card(
        "Coldplug", "#4fbf9a", coldplug,
        [("API attach", coldplug.get("avg_api_attach_time", 0)),
         ("VM boot", coldplug.get("avg_vm_boot_time", 0)),
         ("Validation", coldplug.get("avg_validation_time", 0))],
        f"{coldplug.get('total_disks_attached', 0)} disks attached · {coldplug.get('total_disks_validated', 0)} validated",
    ) if coldplug else ""
    up_card = op_card(
        "Unplug", "#f08a5d", unplug, [],
        f"{unplug.get('total_disks_removed', 0)} disks removed",
    ) if unplug else ""

    ops_html = f"""
    <div class="row g-3 mb-4">
      {hp_card}{cp_card}{up_card}
    </div>
    <p class="text-muted small mb-4">Phase bars are drawn against each operation's average total time.</p>
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
            bar_colors_vm   = ["#a9c9f3", "#82b2ee", "#5598e7"]
        elif op == "coldplug":
            chart_labels_vm = ["API Attach", "VM Boot", "Total"]
            chart_vals_vm   = [r.get("api_attach_time", 0), r.get("vm_boot_time", 0), r.get("total_time", 0)]
            bar_colors_vm   = ["#a8e0cd", "#7fd0b3", "#4fbf9a"]
        else:  # unplug
            chart_labels_vm = ["Total"]
            chart_vals_vm   = [r.get("total_time", 0)]
            bar_colors_vm   = ["#f08a5d"]

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
      <h4>Operations</h4>
      {ops_html}
      <h4 class="mt-4">Per-VM Details <small class="text-muted fs-6">(click a row to view timing chart)</small></h4>
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
        build_bar_chart_mmss(creation_rec, "Creation Duration", "#5598e7", f"chart_{px_version}_{disk_name}_creation"),
        build_bar_chart_mmss(boot_rec, "Boot Storm Duration", "#4fbf9a", f"chart_{px_version}_{disk_name}_boot"),
        build_bar_chart_mmss(mig_rec, "Live Migration Duration", "#f08a5d", f"chart_{px_version}_{disk_name}_mig"),
    ]
    if cap_rec:
        chart_items.append(build_bar_chart_mmss(cap_rec, "Chaos Benchmark Duration", "#9a8fe0", f"chart_{px_version}_{disk_name}_cap"))

    chart_items = [item for item in chart_items if item]
    charts_html = (
        f"""
    <div class="mt-3">
      <div class="row g-3">
        {''.join(chart_items)}
      </div>
    </div>
    """
        if chart_items
        else ""
    )

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

        disk_ops_folders = [f for f in by_vms[vm_count] if (f / "disk_ops_results.json").exists()]
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
                            data-bs-toggle="collapse" data-bs-target="#diskops_acc_{fold_uid}" title="{f.name}">
                      {friendly_date(f.name)}
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

        # Offer only the benchmark types this VM size has results for, and open on the first one.
        folders_here = by_vms[vm_count]

        def has_any(*names):
            return any((f / name).exists() for f in folders_here for name in names)

        sections = [
            ("cb", "Creation + Boot Storm", cb_sections, has_any("summary_vm_creation_results.json")),
            ("mig", "Live Migration", mig_sections, has_any("summary_migration_results.json")),
            ("cap", "Chaos Benchmark", cap_sections, has_any("summary_chaos_benchmark.json")),
            ("fio", "FIO Benchmark", fio_sections, has_any("summary_fio_benchmark.json", "fio_benchmark_results.json")),
            ("elbencho", "Elbencho Benchmark", elbencho_sections, has_any("aggregated_results.json")),
            ("diskops", "Disk Ops", disk_ops_sections, has_any("disk_ops_results.json")),
        ]
        present = [item for item in sections if item[3]]
        if not present:
            vm_tabs_body.append(
                f"""
            <div class="tab-pane fade {show_cls} {active_cls}" id="tab_{vm_id}" role="tabpanel">
              <p class="text-muted mt-3">No result files were saved for this VM size. The run may not have completed.</p>
            </div>
            """
            )
            continue

        tab_nav_items, tab_content_items = [], []
        for pos, (key, label, html, _present) in enumerate(present):
            on = pos == 0
            tab_nav_items.append(
                f'<li class="nav-item"><button class="nav-link {"active" if on else ""}" id="tab-{vm_id}_{key}-tab" '
                f'data-bs-toggle="tab" data-bs-target="#tab_{vm_id}_{key}" type="button" role="tab">{label}</button></li>'
            )
            tab_content_items.append(
                f'<div class="tab-pane fade {"show active" if on else ""}" id="tab_{vm_id}_{key}" role="tabpanel">{html}</div>'
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
# ---------------- VirtualMachinePool ----------------
POOL_COLOR = "#5598e7"


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def build_vm_pool_run(folder: Path, uid: str) -> str:
    """One VirtualMachinePool run: how long each clone strategy took to bring the pool fully ready."""
    data = load_json(folder / "results.json") or {}
    config = data.get("configuration", {})
    rows = data.get("results", []) or []
    if not rows:
        return ""

    order = []
    for row in rows:
        name = row.get("requested_strategy", "?")
        if name not in order:
            order.append(name)

    summary = []
    for name in order:
        mine = [r for r in rows if r.get("requested_strategy") == name]
        ok = [r for r in mine if r.get("status") == "succeeded"]
        unsupported = all(r.get("status") == "unsupported" for r in mine)
        summary.append({
            "name": name,
            "runs": len(ok),
            "failed": len([r for r in mine if r.get("status") not in ("succeeded", "unsupported")]),
            "unsupported": unsupported,
            "reason": next((r.get("error") for r in mine if r.get("error")), None),
            "objects": _median([r.get("all_vm_objects_seconds") for r in ok]),
            "disks": _median([r.get("all_root_disks_seconds") for r in ok]),
            "half": _median([r.get("ready_50_seconds") for r in ok]),
            "ready": _median([r.get("all_ready_seconds") for r in ok]),
            "recovery": _median([(r.get("recovery") or {}).get("seconds") for r in ok]),
        })

    image = config.get("golden_image") or "{}/{}".format(
        config.get("datasource_namespace", "?"), config.get("datasource_name", "?"))
    best = [x for x in summary if x["ready"] is not None]
    fastest = min(best, key=lambda x: x["ready"]) if len(best) > 1 else None
    header = run_header(
        folder.name[:15],
        facts=[
            ("VMs in pool", config.get("replicas", "?")),
            ("Storage class", config.get("storage_class") or "default"),
            ("Runs per strategy", config.get("iterations", 1)),
        ],
        tags=[("Image", image), ("Ready when", config.get("ready_when"))],
    )

    names = [x["name"] for x in best]
    values = [round(x["ready"], 2) for x in best]
    labels = [fmt_secs(x["ready"]) for x in best]
    chart_id = f"pool_chart_{uid}"
    chart = ""
    if best:
        chart = f"""
        <div class="card mb-3">
          <div class="card-header"><strong>Time until every VM is ready</strong> <span class="hint">median per clone strategy</span></div>
          <div class="card-body p-2"><div id="{chart_id}" class="plotly-chart" style="height:300px; width:100%;"></div></div>
        </div>
        <script>
          (function () {{
            var ink2 = VB.css('--ink-2'), ink3 = VB.css('--ink-3');
            Plotly.newPlot('{chart_id}', [{{
              x: {names}, y: {values}, type: 'bar',
              marker: {{ color: '{POOL_COLOR}', cornerradius: 4 }},
              text: {labels}, textposition: 'outside', cliponaxis: false, textfont: {{ color: ink2, size: 12 }},
              hovertemplate: '%{{x}}<br>%{{text}}<extra></extra>'
            }}], {{
              paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
              font: {{ family: VB.font, color: ink2, size: 12 }},
              xaxis: {{ showgrid: false, linecolor: VB.css('--axis'), tickfont: {{ color: ink2 }} }},
              yaxis: {{ title: {{ text: 'seconds', font: {{ color: ink3, size: 12 }} }}, gridcolor: VB.css('--line'),
                       zeroline: false, tickfont: {{ color: ink3 }}, rangemode: 'tozero' }},
              margin: {{ t: 28, l: 56, r: 12, b: 40 }}, bargap: 0.65
            }}, {{responsive: true, displayModeBar: false}});
          }})();
        </script>"""

    show_recovery = any(x["recovery"] is not None for x in summary)
    body = ""
    for x in summary:
        if x["unsupported"]:
            body += (f'<tr><td>{x["name"]}</td><td colspan="{6 if show_recovery else 5}" class="text-muted">'
                     f'Not supported on this cluster{": " + x["reason"] if x["reason"] else ""}</td></tr>')
            continue
        note = f' <span class="text-muted">({x["failed"]} failed)</span>' if x["failed"] else ""
        body += (
            f'<tr><td>{x["name"]}{note}</td><td>{fmt_secs(x["objects"])}</td><td>{fmt_secs(x["disks"])}</td>'
            f'<td>{fmt_secs(x["half"])}</td><td><strong>{fmt_secs(x["ready"])}</strong></td>'
            + (f'<td>{fmt_secs(x["recovery"])}</td>' if show_recovery else "")
            + "</tr>"
        )
    table = f"""
    <div class="card">
      <div class="card-body p-0">
        <table class="table mb-0">
          <thead><tr><th>Clone strategy</th><th>VM objects created</th><th>Root disks ready</th>
            <th>50% of VMs ready</th><th>All VMs ready</th>{"<th>Pool recovery</th>" if show_recovery else ""}</tr></thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </div>"""
    return f'<div class="mb-5">{header}{chart}{table}</div>'


def find_vm_pool_runs(base_dir: Path, cutoff=None):
    root = base_dir / "vm-pool"
    runs = []
    if root.is_dir():
        for run in root.iterdir():
            if not (run / "results.json").exists():
                continue
            try:
                started = datetime.strptime(run.name.split("_")[0].split("-vm-pool")[0], "%Y%m%d-%H%M%S")
            except ValueError:
                started = datetime.fromtimestamp(run.stat().st_mtime)
            if cutoff is None or started >= cutoff:
                runs.append(run)
    return sorted(runs, key=lambda p: p.name, reverse=True)


def build_vm_pool_tab(runs) -> str:
    return "".join(build_vm_pool_run(run, f"pool{i}") for i, run in enumerate(runs))


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>KubeVirt Performance Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {
  color-scheme: light;
  --page: #f6f6f3; --surface: #fcfcfb; --soft: #f1f0ec;
  --ink: #1f1f1d; --ink-2: #52514e; --ink-3: #898781;
  --line: #e6e5df; --axis: #c3c2b7; --border: rgba(11,11,11,.08);
  --bar: #5598e7; --link: #2a78d6;
  --good: #00704a; --bad: #c4443f; --warn: #eda100; --warn-ink: #8a5a00;
  --icon-filter: none;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #141413; --surface: #1c1c1a; --soft: #242422;
    --ink: #ecebe6; --ink-2: #c3c2b7; --ink-3: #898781;
    --line: #2c2c2a; --axis: #3d3d39; --border: rgba(255,255,255,.10);
    --bar: #3987e5; --link: #6ea8f0;
    --good: #3fbf8a; --bad: #e66767; --warn-ink: #e3b341;
    --icon-filter: invert(1) opacity(.7);
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1280px; margin: 0 auto; padding: 28px 24px 56px; }
a { color: var(--link); }
.top { display: flex; align-items: center; gap: 14px; margin-bottom: 22px; }
.top .logo { height: 46px; width: auto; display: block; }
.top h1 { font-size: 22px; font-weight: 600; margin: 0; letter-spacing: -.01em; }
.top .sub { color: var(--ink-3); font-size: 13px; }
h3 { font-size: 18px; font-weight: 600; margin-top: 28px; }
h4 { font-size: 16px; font-weight: 600; }
h5, h6 { font-size: 14px; font-weight: 600; }
.text-muted { color: var(--ink-3) !important; }
hr { border-color: var(--line); opacity: 1; }
.hint { color: var(--ink-3); font-size: 12px; font-weight: 400; margin-left: 6px; }

/* tabs: underline for the nested levels, segmented control for the benchmark type */
.nav-tabs { border-bottom: 1px solid var(--line); gap: 4px; }
.nav-tabs .nav-link { border: 0; border-bottom: 2px solid transparent; border-radius: 0; margin-bottom: -1px;
  background: none; color: var(--ink-3); padding: 8px 14px; }
.nav-tabs .nav-link:hover { color: var(--ink); border-bottom-color: var(--axis); }
.nav-tabs .nav-link.active { background: none; color: var(--ink); font-weight: 600; border-bottom-color: var(--bar); }
#pxTab .nav-link { font-size: 15px; padding: 10px 16px; }
.nav-pills { display: inline-flex; flex-wrap: wrap; width: fit-content; max-width: 100%; background: var(--soft);
  border-radius: 10px; padding: 3px; gap: 2px; }
.nav-pills .nav-link { color: var(--ink-2); border-radius: 8px; padding: 6px 14px; background: none; }
.nav-pills .nav-link:hover { color: var(--ink); }
.nav-pills .nav-link.active, .nav-pills .show > .nav-link { background: var(--surface); color: var(--ink); font-weight: 600;
  box-shadow: 0 1px 2px rgba(0,0,0,.08); }

/* run header card */
.run-head { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px 20px; margin-bottom: 16px; }
.run-top { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; }
.run-date { font-size: 17px; font-weight: 600; letter-spacing: -.01em; }
.run-folder { color: var(--ink-3); font-size: 12px; }
.run-facts { display: flex; flex-wrap: wrap; gap: 10px 44px; margin-top: 14px; }
.fact .k { color: var(--ink-3); font-size: 12px; }
.fact .v { font-size: 18px; font-weight: 600; line-height: 1.3; }
.fact .s { color: var(--ink-3); font-size: 12px; }
.fact .s .ok { color: var(--good); } .fact .s .bad { color: var(--bad); }
.run-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); }
.pill { background: var(--soft); color: var(--ink); border-radius: 999px; padding: 3px 11px; font-size: 12px; font-weight: 500; }
.pill .k { color: var(--ink-3); font-weight: 400; margin-right: 2px; }
.mb-4 > h4.mt-4, .mb-4 > h4 { font-size: 15px; margin-top: 24px !important; margin-bottom: 10px; }

/* disk-ops operation cards */
.op-card { padding: 16px 18px; }
.op-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.op-name { font-weight: 600; display: flex; align-items: center; gap: 8px; }
.op-name .dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.op-big { font-size: 30px; font-weight: 600; letter-spacing: -.02em; line-height: 1.15; margin-top: 12px; }
.op-big small { font-size: 13px; font-weight: 500; color: var(--ink-3); letter-spacing: 0; }
.op-sub { color: var(--ink-3); font-size: 12px; margin-top: 2px; }
.phases { display: grid; gap: 10px; margin-top: 16px; }
.ph { display: grid; grid-template-columns: 92px 1fr 56px; align-items: center; gap: 10px; font-size: 12px; color: var(--ink-2); }
.ph .track { height: 8px; border-radius: 4px; background: var(--soft); display: block; overflow: hidden; }
.ph .fill { display: block; height: 100%; border-radius: 4px; }
.ph .val { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }

/* read / write split */
.split { display: flex; gap: 2px; height: 10px; border-radius: 5px; overflow: hidden; margin-top: 16px; background: var(--soft); }
.split span { display: block; height: 100%; }
.split-leg { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 14px; margin-top: 10px; font-size: 12px; color: var(--ink-2); }
.split-leg b { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.split-leg em { font-style: normal; color: var(--ink-3); }
.dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; margin-right: 6px; }

/* cards and stat tiles */
.card { --bs-card-bg: var(--surface); --bs-card-border-color: var(--border); --bs-card-cap-bg: transparent;
  --bs-card-color: var(--ink); color: var(--ink); background: var(--surface); border: 1px solid var(--border); border-radius: 12px; box-shadow: none; }
.card-header { background: transparent; border-bottom: 1px solid var(--line); padding: 12px 16px; font-size: 14px; color: var(--ink); }
.card-header strong { font-weight: 600; }
.stat-card { text-align: left !important; }
.stat-card .card-body { padding: 14px 16px !important; }
.stat-k { color: var(--ink-3); font-size: 12px; }
.stat-v { font-size: 26px; font-weight: 600; letter-spacing: -.02em; line-height: 1.25; color: var(--ink); }
.stat-v small { font-size: 13px; font-weight: 500; color: var(--ink-3); letter-spacing: 0; }

/* tables */
.table { --bs-table-bg: transparent; --bs-table-color: var(--ink); --bs-table-border-color: var(--line);
  --bs-table-striped-bg: transparent; --bs-table-striped-color: var(--ink); --bs-table-hover-bg: var(--soft);
  --bs-table-hover-color: var(--ink); --bs-border-color: var(--line); color: var(--ink); font-size: 13px; }
.table { border: 1px solid var(--border); border-collapse: separate; border-spacing: 0; border-radius: 10px; overflow: hidden; background: var(--surface); }
.table > :not(caption) > * > * { padding: 9px 12px; border-width: 0 0 1px; border-color: var(--line); }
.table > tbody > tr:last-child > * { border-bottom: 0; }
.table-dark, .table-light, thead.table-dark, thead.table-light {
  --bs-table-bg: var(--soft); --bs-table-color: var(--ink-2); --bs-table-border-color: var(--line); }
.table thead th { font-size: 12px; font-weight: 500; color: var(--ink-2); }
.table tbody th { font-weight: 500; color: var(--ink-2); width: 30%; }
.vm-row-clickable:hover > * { background: var(--soft); }
table.dataTable { border-collapse: collapse !important; font-size: 13px; }
table.dataTable thead th, table.dataTable thead td { background: var(--soft); color: var(--ink-2); font-size: 12px; font-weight: 500;
  border-bottom: 1px solid var(--line) !important; padding: 9px 12px; }
table.dataTable tbody tr, table.dataTable.display tbody tr > .sorting_1, table.dataTable.display tbody tr.odd > * { background: transparent !important; box-shadow: none !important; }
table.dataTable tbody td { border-top: 1px solid var(--line) !important; padding: 9px 12px; color: var(--ink); }
table.dataTable.display tbody tr:hover > * { background: var(--soft) !important; }
.dataTables_wrapper { width: 100%; margin: 0 auto; color: var(--ink-3); font-size: 13px; background: var(--surface);
  border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; }
.dataTables_wrapper .dataTables_filter input, .dataTables_wrapper .dataTables_length select {
  background: var(--surface); color: var(--ink); border: 1px solid var(--border); border-radius: 8px; padding: 4px 8px; }
.dataTables_wrapper .dataTables_paginate .paginate_button { color: var(--ink-2) !important; border-radius: 8px; border: 1px solid transparent !important; }
.dataTables_wrapper .dataTables_paginate .paginate_button.current, .dataTables_wrapper .dataTables_paginate .paginate_button:hover {
  background: var(--soft) !important; border: 1px solid var(--border) !important; color: var(--ink) !important; }
.dataTables_wrapper .dataTables_scrollBody, .dataTables_wrapper .dataTables_scrollHead { background: transparent !important; }
table.dataTable { width: 100% !important; }

/* soft status pills */
.badge { font-weight: 500; border-radius: 999px; padding: .3em .75em; }
.badge.bg-success { background: color-mix(in srgb, var(--good) 14%, var(--surface)) !important; color: var(--good) !important; }
.badge.bg-danger { background: color-mix(in srgb, var(--bad) 14%, var(--surface)) !important; color: var(--bad) !important; }
.badge.bg-warning { background: color-mix(in srgb, var(--warn) 22%, var(--surface)) !important; color: var(--warn-ink) !important; }
.badge.bg-info { background: color-mix(in srgb, var(--bar) 16%, var(--surface)) !important; color: var(--link) !important; }
.badge.bg-secondary { background: var(--soft) !important; color: var(--ink-2) !important; }

/* accordions and modals */
.accordion { --bs-accordion-bg: var(--surface); --bs-accordion-border-color: var(--border); --bs-accordion-color: var(--ink);
  --bs-accordion-btn-bg: var(--surface); --bs-accordion-btn-color: var(--ink); --bs-accordion-active-bg: var(--soft);
  --bs-accordion-active-color: var(--ink); --bs-accordion-border-radius: 12px; --bs-accordion-inner-border-radius: 11px; }
.accordion-button { font-size: 14px; font-weight: 500; }
.accordion-button:focus { box-shadow: none; }
.accordion-button::after, .btn-close { filter: var(--icon-filter); }
.accordion-button:not(.collapsed)::after { filter: var(--icon-filter); }
.modal-content { background: var(--surface); color: var(--ink); border: 1px solid var(--border); border-radius: 14px; }
.modal-header { border-bottom: 1px solid var(--line); }
@media (max-width: 640px) { .wrap { padding: 18px 14px 40px; } }
</style>
<script>
const VB = {
  css: (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
  font: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  read: '#5598e7', write: '#f08a5d',
};
if (window.Chart) {
  Chart.defaults.font.family = VB.font;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = VB.css('--ink-2');
  Chart.defaults.borderColor = VB.css('--line');
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.legend.labels.useBorderRadius = true;
  Chart.defaults.plugins.legend.labels.borderRadius = 3;
}
</script>
</head>
<body>
<div class="wrap">
<div class="top">
  __LOGO__
  <div>
    <h1>KubeVirt performance dashboard</h1>
    <div class="sub">Generated __NOW__</div>
  </div>
</div>

<ul class="nav nav-tabs" id="pxTab" role="tablist">__NAV__</ul>
<div class="tab-content" id="pxTabContent" style="margin-top: 1rem;">__BODY__</div>
</div>

<script>
$(document).ready(function() {
  $('table.display').DataTable({ scrollX: true, autoWidth: false, pageLength: 25 });

  $('a[data-bs-toggle="tab"], button[data-bs-toggle="tab"]').on('shown.bs.tab', function () {
    $.fn.dataTable.tables({visible: true, api: true}).columns.adjust();
    setTimeout(function () {
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
    document.querySelectorAll('.plotly-chart').forEach(function (el) {
      try { Plotly.Plots.resize(el); } catch(e) {}
    });
  }, 400);
});
</script>
</body>
</html>
"""


def build_html_page(px_nav, px_body):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logo = (
        f'<img class="logo" alt="virtbench" src="data:image/png;base64,{LOGO_PNG_BASE64}">'
        if LOGO_PNG_BASE64
        else ""
    )
    return (
        PAGE_TEMPLATE.replace("__LOGO__", logo)
        .replace("__NOW__", now)
        .replace("__NAV__", "".join(px_nav))
        .replace("__BODY__", "".join(px_body))
    )


# ---------------- Discover + Build ----------------
REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve(path, default: Path) -> Path:
    """Paths are taken as given; a relative one that is not under the current folder is looked up in the repo."""
    if not path:
        return default
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p
    return REPO_ROOT / p


def generate(base_dir=None, days=None, output=None, cluster_info=None, manual_results=None):
    """Build the dashboard and return its path, or None when there are no results to show.

    With no arguments it reads <repo>/results, includes every run, and writes <repo>/results_dashboard.html.
    """
    base_dir = _resolve(base_dir, REPO_ROOT / "results")
    out = _resolve(output, REPO_ROOT / "results_dashboard.html")
    cutoff = datetime.now() - timedelta(days=days) if days else None
    px_map = {}

    if not base_dir.is_dir():
        return None
    for px in sorted([d for d in base_dir.iterdir() if d.is_dir() and d.name != "vm-pool"]):
        disk_map = defaultdict(list)
        for disk_dir in sorted([d for d in px.iterdir() if d.is_dir()]):
            for run in sorted([r for r in disk_dir.iterdir() if r.is_dir()], key=lambda p: p.name):
                try:
                    started = datetime.strptime(run.name.split("_")[0], "%Y%m%d-%H%M%S")
                except ValueError:
                    continue
                if (cutoff is None or started >= cutoff) and has_results(run):
                    disk_map[disk_dir.name].append(run)
        if disk_map:
            px_map[px.name] = disk_map

    pool_runs = find_vm_pool_runs(base_dir, cutoff)
    if not px_map and not pool_runs:
        return None

    px_nav, px_body = [], []
    for idx, (px_version, disk_map) in enumerate(sorted(px_map.items())):
        px_id = f"px_{px_version}".replace(".", "_").replace("-", "_")
        active_cls = "active" if idx == 0 else ""
        show_cls = "show" if idx == 0 else ""
        px_nav.append(f'<li class="nav-item"><button class="nav-link {active_cls}" id="tab-{px_id}-tab" data-bs-toggle="tab" data-bs-target="#tab_{px_id}" type="button" role="tab">{px_version}</button></li>')
        px_body.append(f'<div class="tab-pane fade {show_cls} {active_cls}" id="tab_{px_id}" role="tabpanel">{build_px_tab(px_version, disk_map)}</div>')

    if pool_runs:
        first = not px_nav
        px_nav.append(
            f'<li class="nav-item"><button class="nav-link {"active" if first else ""}" id="tab-vmpool-tab" '
            'data-bs-toggle="tab" data-bs-target="#tab_vmpool" type="button" role="tab">VM Pool</button></li>')
        px_body.append(
            f'<div class="tab-pane fade {"show active" if first else ""}" id="tab_vmpool" role="tabpanel">'
            f'{build_vm_pool_tab(pool_runs)}</div>')

    info = load_cluster_info(str(_resolve(cluster_info, REPO_ROOT))) if cluster_info else None
    if info:
        px_nav.append(
            '<li class="nav-item"><button class="nav-link" id="tab-clusterinfo-tab" data-bs-toggle="tab" data-bs-target="#tab_clusterinfo" type="button" role="tab">Cluster Info</button></li>')
        px_body.append(f'<div class="tab-pane fade" id="tab_clusterinfo" role="tabpanel">{build_cluster_info_tab(info)}</div>')

    manual = load_manual_results(str(_resolve(manual_results, REPO_ROOT))) if manual_results else None
    if manual:
        px_nav.append(
            '<li class="nav-item"><button class="nav-link" id="tab-manualresults-tab" '
            'data-bs-toggle="tab" data-bs-target="#tab_manualresults" type="button" role="tab">Manual Results</button></li>'
        )
        px_body.append(f'<div class="tab-pane fade" id="tab_manualresults" role="tabpanel">{build_manual_results_tab(manual)}</div>')

    out.write_text(build_html_page(px_nav, px_body), encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Generate the KubeVirt performance dashboard. With no options it reads ./results "
                    "and writes results_dashboard.html in the repo root."
    )
    parser.add_argument("--days", type=int, default=None, help="Only include runs from the last N days (default: all)")
    parser.add_argument("--base-dir", default=None, help="Results folder (default: <repo>/results)")
    parser.add_argument("--output-html", default=None, help="Where to write the page (default: <repo>/results_dashboard.html)")
    parser.add_argument("--cluster-info", default=None, help="Optional cluster_info.yaml to add a Cluster Info tab")
    parser.add_argument("--manual-results", default=None, help="Optional manual_results.yaml to add a Manual Results tab")
    args = parser.parse_args()

    out = generate(args.base_dir, args.days, args.output_html, args.cluster_info, args.manual_results)
    if out is None:
        print("No results found to show.")
        return
    print(f"Dashboard generated: {out}")


if __name__ == "__main__":
    main()
