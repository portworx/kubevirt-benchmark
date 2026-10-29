"""HTML dashboard for ``virtbench fio --action compare-profiles`` results.

The page shows what was run and how each KubeVirt storage profile compared with
``baseline`` under the same VM, volume, node and FIO settings. It reports
measurements only: it does not rank profiles or recommend one.

Usage:
    python3 -m virtbench.profile_dashboard --base-dir results
    python3 -m virtbench.profile_dashboard --run results/sc-fc/profile-comparison/20261020-120000
"""

import argparse
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from virtbench.dashboard_logo import LOGO_PNG_BASE64

RESULT_FILE = "fio_profile_comparison.json"
DASHBOARD_FILE = "profile_comparison_dashboard.html"
# A change is only coloured as better/worse when it exceeds the run-to-run
# spread of the runs being compared, and never below this floor.
MIN_NOISE_PCT = 1.0
NOISY_CV_PCT = 5.0
DEFAULT_WORKLOAD = "default"


# --- loading and normalisation ------------------------------------------------


def _block_bytes(block_size: str) -> int:
    text = str(block_size).strip().lower()
    scale = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
    try:
        if text and text[-1] in scale:
            return int(float(text[:-1]) * scale[text[-1]])
        return int(float(text))
    except ValueError:
        return 4096


def _workloads(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Workloads in run order; older runs only recorded one ad-hoc pattern."""
    recorded = document.get("workloads")
    if recorded:
        return [
            {
                "name": item["name"],
                "label": item.get("label", item["name"]),
                "primary": item.get("primary", "iops"),
                "rw": item.get("rw"),
                "bs": item.get("bs"),
            }
            for item in recorded
        ]
    config = document.get("fio_config", {})
    rw, block = config.get("rw", "unknown"), config.get("bs", "")
    return [
        {
            "name": f"{rw}-{block}" if block else rw,
            "label": f"{block} {rw}".strip(),
            "primary": "bw" if _block_bytes(block) >= 64 * 1024 else "iops",
            "rw": rw,
            "bs": block,
        }
    ]


def _spread_pct(values: List[float]) -> Optional[float]:
    """Run-to-run spread (coefficient of variation, %); None with fewer than two runs."""
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return round(statistics.pstdev(values) / mean * 100, 2) if mean else 0.0


def _change(value: Optional[float], base: Optional[float]) -> Optional[float]:
    if value is None or not base:
        return None
    return round((value - base) / base * 100, 2)


def _state(change: Optional[float], noise: float, higher_is_better: bool) -> str:
    """'better', 'worse', or 'same' (within run-to-run spread); 'n/a' without a baseline."""
    if change is None:
        return "n/a"
    if abs(change) <= noise:
        return "same"
    return "better" if (change > 0) == higher_is_better else "worse"


def _applied(info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not info:
        return {"verified": None}
    if not info.get("verified"):
        return {"verified": False, "reason": info.get("reason")}
    disk = info.get("benchmark_disk") or {}
    return {
        "verified": True,
        "iothreads": info.get("iothreads"),
        "disk_iothread": disk.get("iothread"),
        "queues": disk.get("queues"),
        "cache": disk.get("cache"),
        "notes": info.get("notes", []),
    }


def build_workload_view(
    document: Dict[str, Any], workload: Dict[str, Any], single_workload: bool
) -> Dict[str, Any]:
    """Rows for one workload, in the order the profiles were requested."""
    name = workload["name"]
    metric = "total_bw_mibps" if workload["primary"] == "bw" else "total_iops"

    def belongs(item: Dict[str, Any]) -> bool:
        return item.get("workload", name if single_workload else DEFAULT_WORKLOAD) == name

    summaries = {item["profile"]: item for item in document.get("profiles", []) if belongs(item)}
    iterations = [item for item in document.get("iterations", []) if belongs(item)]
    failures = {item["profile"]: item.get("error", "") for item in document.get("failures", [])}
    applied = document.get("applied_configuration", {})

    order = list(document.get("requested_profiles") or summaries)
    order += [profile for profile in summaries if profile not in order]

    rows = []
    for profile in order:
        summary = summaries.get(profile)
        if summary is None:
            error = failures.get(profile, "no result recorded")
            rows.append({"profile": profile, "status": "failed",
                         "error": (error.strip().splitlines() or ["no result recorded"])[0][:300]})
            continue
        values = [item[metric] for item in iterations if item["profile"] == profile]
        p99_values = [
            max(item.get("read_p99_ms", 0.0), item.get("write_p99_ms", 0.0))
            for item in iterations
            if item["profile"] == profile
        ]
        # Computed from the individual runs: older result files did not store it.
        spread = _spread_pct([item["total_iops"] for item in iterations if item["profile"] == profile])
        read_p99, write_p99 = summary["median_read_p99_ms"], summary["median_write_p99_ms"]
        rows.append(
            {
                "profile": profile,
                "status": "ok",
                "description": summary.get("description", ""),
                "runs": summary.get("iterations", len(values)),
                "primary": summary["median_total_bw_mibps" if metric == "total_bw_mibps" else "median_total_iops"],
                "iops": summary["median_total_iops"],
                "bw": summary["median_total_bw_mibps"],
                "read_iops": summary["median_read_iops"],
                "write_iops": summary["median_write_iops"],
                "read_p99": read_p99,
                "write_p99": write_p99,
                "p99": max(read_p99, write_p99),
                "cv": spread,
                "threads": summary.get("thread_budget"),
                "iops_per_thread": summary.get("median_iops_per_thread"),
                "values": values,
                "p99_values": p99_values,
                "applied": _applied(applied.get(profile)),
            }
        )

    baseline = next((row for row in rows if row["profile"] == "baseline" and row["status"] == "ok"), None)
    flags: List[str] = []
    for row in rows:
        if row["status"] != "ok":
            continue
        if baseline is None:
            row.update(primary_change=None, p99_change=None,
                       primary_state="n/a", p99_state="n/a")
        else:
            noise = max(MIN_NOISE_PCT, row["cv"] or 0.0, baseline["cv"] or 0.0)
            row["primary_change"] = _change(row["primary"], baseline["primary"])
            row["p99_change"] = _change(row["p99"], baseline["p99"])
            row["noise"] = round(noise, 2)
            row["primary_state"] = _state(row["primary_change"], noise, True)
            row["p99_state"] = _state(row["p99_change"], noise, False)
        notes = []
        if row["runs"] <= 1:
            notes.append("single iteration, no spread measured")
        if row["cv"] is not None and row["cv"] > NOISY_CV_PCT:
            notes.append(f"run-to-run spread {row['cv']:.1f}%")
        if row["applied"].get("verified") is False:
            notes.append("not verified against the running VM")
        notes.extend(row["applied"].get("notes", []))
        row["notes"] = notes
    if baseline is None:
        flags.append("No successful baseline run: changes versus baseline cannot be shown.")
    for row in rows:
        if row["status"] == "failed":
            flags.append(f"{row['profile']} failed: {row['error']}")

    return {
        "name": name,
        "label": workload["label"],
        "primary": workload["primary"],
        "unit": "MiB/s" if workload["primary"] == "bw" else "IOPS",
        "rows": rows,
        "baseline": baseline["profile"] if baseline else None,
        "flags": flags,
    }


def load_run(result_file: Path) -> Dict[str, Any]:
    """Read one ``fio_profile_comparison.json`` into a display-ready structure."""
    document = json.loads(result_file.read_text(encoding="utf-8"))
    workloads = _workloads(document)
    config = document.get("fio_config", {})
    folder = result_file.parent
    return {
        "id": f"{folder.parent.parent.name}-{folder.name}".replace(".", "-"),
        "driver": folder.parent.parent.name,
        "run": folder.name,
        "timestamp": document.get("timestamp"),
        "storage_class": document.get("storage_class"),
        "node": document.get("node_name"),
        "vm_cpus": document.get("vm_cpus"),
        "fio": {
            "iodepth": config.get("iodepth"),
            "numjobs": config.get("numjobs"),
            "runtime": config.get("runtime"),
            "ramp_time": config.get("ramp_time"),
            "size": config.get("size"),
        },
        "requested_profiles": document.get("requested_profiles", []),
        "workloads": [
            build_workload_view(document, workload, len(workloads) == 1) for workload in workloads
        ],
    }


def find_runs(base_dir: Path, days: Optional[int] = None) -> List[Path]:
    """Every comparison result under ``base_dir``, newest first."""
    cutoff = datetime.now() - timedelta(days=days) if days else None
    found = []
    for path in base_dir.glob(f"*/profile-comparison/*/{RESULT_FILE}"):
        if cutoff:
            try:
                if datetime.strptime(path.parent.name, "%Y%m%d-%H%M%S") < cutoff:
                    continue
            except ValueError:
                pass
        found.append(path)
    return sorted(found, key=lambda item: item.parent.name, reverse=True)


# --- page ----------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Storage Profile Comparison</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    color-scheme: light;
    --page: #f6f6f3; --surface: #fcfcfb; --soft: #f1f0ec;
    --ink: #1f1f1d; --ink-2: #52514e; --ink-3: #898781;
    --line: #e6e5df; --axis: #c3c2b7; --border: rgba(11,11,11,.08);
    --bar: #5598e7; --bar-base: #c3c2b7; --whisker: #52514e; --ref: #a9a89e;
    --good: #00704a; --bad: #c4443f; --flat: #898781; --warn: #eda100;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --page: #141413; --surface: #1c1c1a; --soft: #242422;
      --ink: #ecebe6; --ink-2: #c3c2b7; --ink-3: #898781;
      --line: #2c2c2a; --axis: #3d3d39; --border: rgba(255,255,255,.10);
      --bar: #3987e5; --bar-base: #5d5c56; --whisker: #c3c2b7; --ref: #6b6a64;
      --good: #3fbf8a; --bad: #e66767; --flat: #898781;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--ink);
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  .wrap { max-width: 1240px; margin: 0 auto; padding: 32px 24px 48px; }
  header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; }
  .brand { display: flex; align-items: center; gap: 14px; }
  .logo { flex: none; height: 46px; width: auto; display: block; }
  h1 { font-size: 22px; font-weight: 600; margin: 0; letter-spacing: -.01em; }
  .sub { color: var(--ink-3); font-size: 13px; margin-top: 2px; }
  .pick { position: relative; }
  .pick-btn { display: flex; align-items: center; gap: 10px; font: inherit; color: var(--ink); background: var(--surface);
    border: 1px solid var(--border); border-radius: 10px; padding: 7px 12px; cursor: pointer; text-align: left;
    box-shadow: 0 1px 2px rgba(0,0,0,.04); }
  .pick-btn:hover, .pick-btn[aria-expanded="true"] { border-color: var(--axis); }
  .pick-btn:focus-visible, .pick-item:focus-visible { outline: 2px solid var(--bar); outline-offset: 2px; }
  .pick-t { font-weight: 400; line-height: 1.3; }
  .pick-m { color: var(--ink-3); font-size: 12px; line-height: 1.25; white-space: nowrap; }
  .pick-btn svg { flex: none; color: var(--ink-3); transition: transform .15s; }
  .pick-btn[aria-expanded="true"] svg { transform: rotate(180deg); }
  .pick-menu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 10; width: max-content; min-width: 320px; max-width: 92vw;
    max-height: 340px; overflow-y: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 6px; box-shadow: 0 8px 24px rgba(0,0,0,.10); }
  .pick-menu[hidden] { display: none; }
  .pick-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; font: inherit;
    color: var(--ink); background: transparent; border: 0; border-radius: 8px; padding: 8px 10px; cursor: pointer; text-align: left; }
  .pick-item:hover { background: var(--soft); }
  .pick-cap { color: var(--ink-3); font-size: 11px; margin: 0 0 3px 2px; }
  .pick-item svg { flex: none; color: var(--bar); visibility: hidden; }
  .pick-item[aria-selected="true"] svg { visibility: visible; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 18px 0 22px; }
  .chip { background: var(--soft); color: var(--ink-2); border-radius: 999px; padding: 3px 11px; font-size: 12px; }
  .chip b { color: var(--ink); font-weight: 600; }
  .seg { display: inline-flex; background: var(--soft); border-radius: 10px; padding: 3px; gap: 2px; margin-bottom: 18px; flex-wrap: wrap; }
  .seg button { font: inherit; border: 0; background: transparent; color: var(--ink-2); padding: 6px 14px;
    border-radius: 8px; cursor: pointer; }
  .seg button[aria-pressed="true"] { background: var(--surface); color: var(--ink); font-weight: 600;
    box-shadow: 0 1px 2px rgba(0,0,0,.08); }
  .tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
  .tile, .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
  .tile { padding: 14px 16px; }
  .tile .k { color: var(--ink-3); font-size: 12px; }
  .tile .v { font-size: 26px; font-weight: 600; letter-spacing: -.02em; margin-top: 2px; line-height: 1.2; }
  .tile .v small { font-size: 13px; font-weight: 500; color: var(--ink-3); letter-spacing: 0; }
  .tile .n { color: var(--ink-3); font-size: 12px; margin-top: 2px; }
  .notes { background: var(--soft); border-radius: 12px; padding: 10px 14px; margin-bottom: 14px;
    border-left: 3px solid var(--warn); color: var(--ink-2); font-size: 13px; }
  .notes div + div { margin-top: 3px; }
  .charts { display: grid; grid-template-columns: 1fr; gap: 12px; margin-bottom: 14px; }
  .card { padding: 16px 18px; }
  .card h3 { margin: 0; font-size: 14px; font-weight: 600; }
  .card .hint { color: var(--ink-3); font-size: 12px; margin: 1px 0 10px; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; color: var(--ink-2); font-size: 12px; margin-bottom: 6px; }
  .legend i { display: inline-block; vertical-align: -1px; margin-right: 5px; }
  .sw { width: 10px; height: 10px; border-radius: 3px; }
  .rf { width: 14px; height: 0; border-top: 1px dashed var(--ref); position: relative; top: -3px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-weight: 500; color: var(--ink-3); font-size: 12px; padding: 8px 10px;
    border-bottom: 1px solid var(--line); white-space: nowrap; }
  td { padding: 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  tr:last-child td { border-bottom: 0; }
  th.r, td.r { text-align: right; font-variant-numeric: tabular-nums; }
  .pn { font-weight: 600; }
  .tag { font-size: 11px; font-weight: 500; color: var(--ink-2); background: var(--soft); border-radius: 6px;
    padding: 1px 7px; margin-left: 6px; }
  .desc { color: var(--ink-3); font-size: 12px; margin-top: 1px; max-width: 360px; }
  .sub2 { color: var(--ink-3); font-size: 12px; }
  .d { white-space: nowrap; font-weight: 600; }
  .d.good { color: var(--good); } .d.bad { color: var(--bad); } .d.flat, .d.n { color: var(--flat); font-weight: 500; }
  .fail td { color: var(--ink-3); } .fail .why { color: var(--bad); }
  footer { color: var(--ink-3); font-size: 12px; margin-top: 18px; }
  .empty { padding: 40px; text-align: center; color: var(--ink-3); }
  @media (max-width: 960px) { }
  @media (max-width: 520px) { .wrap { padding: 20px 14px 36px; } .card { overflow-x: auto; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <img class="logo" alt="virtbench" src="data:image/png;base64,__LOGO__">
      <div>
        <h1>Storage profile comparison</h1>
        <div class="sub">What was run, and how each KubeVirt profile compared with baseline.</div>
      </div>
    </div>
    <div id="runPick"></div>
  </header>
  <div id="chips" class="chips"></div>
  <div id="wlPick"></div>
  <div id="view"></div>
  <footer>Generated __NOW__. Measurements only: this page does not rank or recommend profiles.</footer>
</div>
<script>
const RUNS = __DATA__;
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const fmt = (v, d = 0) => v === null || v === undefined || Number.isNaN(v) ? '–'
  : Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const pct = (v) => v === null || v === undefined ? 'n/a'
  : `${v > 0 ? '+' : v < 0 ? '−' : ''}${fmt(Math.abs(v), 1)}%`;
const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
const state = {run: 0, wl: 0};
let charts = [];

function delta(change, kind) {
  if (change === null || change === undefined) return '<span class="d n">n/a</span>';
  const cls = kind === 'better' ? 'good' : kind === 'worse' ? 'bad' : 'flat';
  const arrow = change > 0 ? '▲' : change < 0 ? '▼' : '•';
  const why = kind === 'same' ? 'within the run-to-run spread' : kind;
  return `<span class="d ${cls}" title="${why}">${arrow} ${pct(change)}</span>`;
}
const deltaColor = (kind) => ({better: css('--good'), worse: css('--bad')}[kind] || css('--flat'));

function summary(w) {
  const ok = w.rows.filter((r) => r.status === 'ok');
  const base = ok.find((r) => r.profile === 'baseline');
  const others = ok.filter((r) => r.profile !== 'baseline' && r.primary_change !== null && r.primary_change !== undefined);
  const digits = w.primary === 'bw' ? 1 : 0;
  const winners = others.filter((r) => r.primary_state === 'better');
  const better = winners.length;
  return [
    ['Baseline', base ? `${fmt(base.primary, digits)} <small>${w.unit}</small>` : '–'],
    [`Profiles above baseline (${w.unit})`, `<span style="color:var(--good)">${better}</span> <small>of ${others.length}</small>`,
      better ? winners.map((r) => esc(r.profile)).join(', ') : 'none'],
  ].map(([k, v, n]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div>${n ? `<div class="n">${n}</div>` : ''}</div>`).join('');
}

function notes(w) {
  const ok = w.rows.filter((r) => r.status === 'ok');
  const out = [];
  w.flags.forEach((f) => out.push(esc(f)));
  const failed = w.rows.length - ok.length;
  if (failed) out.push(`${failed} profile${failed === 1 ? ' was' : 's were'} not measured; see the table.`);
  if (ok.length && ok.every((r) => r.runs <= 1))
    out.push('One run per profile, so small differences may just be run-to-run noise.');
  ok.forEach((r) => ((r.applied && r.applied.notes) || []).forEach((n) => out.push(`${esc(r.profile)}: ${esc(n)}.`)));
  return out.length ? `<div class="notes">${out.map((t) => `<div>${t}</div>`).join('')}</div>` : '';
}

function table(w) {
  const rows = w.rows.map((r) => {
    if (r.status !== 'ok') {
      return `<tr class="fail"><td><span class="pn">${esc(r.profile)}</span><div class="why">Not measured: ${esc(r.error)}</div></td><td colspan="4"></td></tr>`;
    }
    const base = r.profile === 'baseline';
    const rw = r.read_p99 && r.write_p99 ? `<div class="sub2">read ${fmt(r.read_p99, 2)} · write ${fmt(r.write_p99, 2)}</div>` : '';
    return `<tr><td><span class="pn">${esc(r.profile)}</span>${base ? '<span class="tag">baseline</span>' : ''}<div class="desc">${esc(r.description)}</div></td>
      <td class="r">${fmt(r.primary, w.primary === 'bw' ? 1 : 0)}</td>
      <td class="r">${base ? '<span class="d n">–</span>' : delta(r.primary_change, r.primary_state)}</td>
      <td class="r">${fmt(r.p99, 2)}${rw}</td>
      <td class="r">${base ? '<span class="d n">–</span>' : delta(r.p99_change, r.p99_state)}</td></tr>`;
  }).join('');
  return `<table><thead><tr><th>Profile</th><th class="r">${w.unit}</th><th class="r">vs baseline</th>
    <th class="r">p99 ms</th><th class="r">vs baseline</th></tr></thead><tbody>${rows}</tbody></table>`;
}

const legend = '<div class="legend"><span><i class="sw" style="background:var(--bar-base)"></i>baseline</span>' +
  '<span><i class="sw" style="background:var(--bar)"></i>profile</span>' +
  '<span><i class="rf"></i>baseline level</span></div>';

function barChart(canvas, ok, opts) {
  const base = ok.find((r) => r.profile === 'baseline');
  const baseValue = base ? opts.value(base) : null;
  const items = ok.map((r) => {
    const change = r.profile === 'baseline' ? null : opts.change(r);
    return {valueText: fmt(opts.value(r), opts.digits),
      deltaText: change === null || change === undefined ? '' : `${change > 0 ? '▲' : change < 0 ? '▼' : '•'} ${pct(change)}`,
      deltaColor: deltaColor(opts.state(r))};
  });
  const annotate = {
    id: 'annotate',
    beforeDatasetsDraw(chart, _, o) {
      if (o.baseline === null) return;
      const {ctx, chartArea, scales: {y}} = chart;
      const py = y.getPixelForValue(o.baseline);
      ctx.save(); ctx.strokeStyle = css('--ref'); ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(chartArea.left, py); ctx.lineTo(chartArea.right, py); ctx.stroke(); ctx.restore();
    },
    afterDatasetsDraw(chart, _, o) {
      const {ctx} = chart;
      ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      const halo = (t, x, y) => { ctx.lineWidth = 5; ctx.lineJoin = 'round'; ctx.strokeStyle = css('--surface'); ctx.strokeText(t, x, y); };
      chart.getDatasetMeta(0).data.forEach((bar, i) => {
        const item = o.items[i];
        let y = bar.y - 8;
        if (item.deltaText) {
          ctx.font = `600 13px ${FONT}`; halo(item.deltaText, bar.x, y); ctx.fillStyle = item.deltaColor; ctx.fillText(item.deltaText, bar.x, y); y -= 17;
        }
        ctx.font = `12px ${FONT}`; halo(item.valueText, bar.x, y); ctx.fillStyle = css('--ink-2'); ctx.fillText(item.valueText, bar.x, y);
      });
      ctx.restore();
    },
  };
  charts.push(new Chart(canvas, {
    type: 'bar',
    data: {labels: ok.map((r) => r.profile.split('-')), datasets: [{
      data: ok.map(opts.value),
      backgroundColor: ok.map((r) => r.profile === 'baseline' ? css('--bar-base') : css('--bar')),
      borderRadius: {topLeft: 4, topRight: 4}, borderSkipped: false, maxBarThickness: 24,
      barPercentage: .7, categoryPercentage: 1}]},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      layout: {padding: {top: 44}},
      plugins: {legend: {display: false}, annotate: {items, baseline: baseValue},
        tooltip: {displayColors: false, backgroundColor: css('--surface'), titleColor: css('--ink'),
          bodyColor: css('--ink-2'), borderColor: css('--line'), borderWidth: 1, padding: 10,
          callbacks: {title: (c) => ok[c[0].dataIndex].profile,
            label: (c) => `${fmt(opts.value(ok[c.dataIndex]), opts.digits)} ${opts.unit}`}}},
      scales: {
        y: {beginAtZero: true, grid: {color: css('--line'), drawTicks: false}, border: {display: false},
          ticks: {color: css('--ink-3'), font: {family: FONT, size: 11}, maxTicksLimit: 5,
            callback: (v) => v >= 10000 ? `${v / 1000}k` : fmt(v)}},
        x: {grid: {display: false}, border: {color: css('--axis')},
          ticks: {color: css('--ink-2'), font: {family: FONT, size: 12}, maxRotation: 0, autoSkip: false}}},
    },
    plugins: [annotate],
  }));
}

const CHEVRON = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></svg>';
const CHECK = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-7"/></svg>';
function runWhen(r) {
  const d = new Date(r.timestamp);
  return Number.isNaN(d.getTime()) ? r.run
    : d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}
function runMeta(r) {
  const n = Math.max(0, ...r.workloads.map((w) => w.rows.length));
  const wl = r.workloads.length === 1 ? r.workloads[0].label : `${r.workloads.length} workloads`;
  return `Storage class: ${r.storage_class || r.driver} · ${n} profile${n === 1 ? '' : 's'} · ${wl}`;
}
function runPicker(run) {
  const head = `<span><div class="pick-t">${esc(runWhen(run))}</div><div class="pick-m">${esc(runMeta(run))}</div></span>`;
  if (RUNS.length < 2) return `<div class="pick-btn" style="cursor:default">${head}</div>`;
  const items = RUNS.map((r, i) => `<button class="pick-item" role="option" data-run="${i}" aria-selected="${i === state.run}">
      <span><div class="pick-t">${esc(runWhen(r))}</div><div class="pick-m">${esc(runMeta(r))}</div></span>${CHECK}</button>`).join('');
  return `<div class="pick"><div class="pick-cap">Run</div><button class="pick-btn" id="pickBtn" aria-haspopup="listbox" aria-expanded="false" aria-label="Choose run">${head}${CHEVRON}</button>
    <div class="pick-menu" id="pickMenu" role="listbox" hidden>${items}</div></div>`;
}
function setMenu(open) {
  const btn = document.getElementById('pickBtn'), menu = document.getElementById('pickMenu');
  if (!btn) return;
  menu.hidden = !open; btn.setAttribute('aria-expanded', String(open));
}

function render() {
  charts.forEach((c) => c.destroy()); charts = [];
  const run = RUNS[state.run];
  if (!run) { document.getElementById('view').innerHTML = '<div class="empty">No profile comparison results found.</div>'; return; }
  const w = run.workloads[state.wl];
  const fio = run.fio || {};
  const secs = (v) => (v === null || v === undefined || v === '' ? null : v + 's');
  const runsPer = Math.max(0, ...w.rows.map((r) => r.runs || 0)) || null;
  const chips = [['Storage class', run.storage_class], ['Node', run.node || 'scheduler-selected'], ['VM vCPUs', run.vm_cpus],
    ['I/O depth', fio.iodepth], ['Jobs', fio.numjobs], ['Runtime', secs(fio.runtime)], ['Ramp', secs(fio.ramp_time)],
    ['File/job', fio.size], ['Runs per profile', runsPer]]
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `<span class="chip">${esc(k)} <b>${esc(v)}</b></span>`).join('');
  document.getElementById('chips').innerHTML = chips;
  document.getElementById('runPick').innerHTML = runPicker(run);
  document.getElementById('wlPick').innerHTML = run.workloads.length > 1
    ? `<div class="seg">${run.workloads.map((x, i) => `<button aria-pressed="${i === state.wl}" data-wl="${i}">${esc(x.label)}</button>`).join('')}</div>`
    : `<h2 style="font-size:16px;font-weight:600;margin:0 0 14px">${esc(w.label)}</h2>`;
  const ok = w.rows.filter((r) => r.status === 'ok');
  document.getElementById('view').innerHTML = `
    <div class="tiles">${summary(w)}</div>${notes(w)}
    <div class="charts">
      <div class="card"><h3>${w.unit} by profile</h3><div class="hint">Higher is better. Green is an improvement over baseline.</div>${legend}
        <div style="height:340px"><canvas id="cMain" role="img" aria-label="${w.unit} by profile"></canvas></div></div>
      <div class="card"><h3>p99 latency (ms) by profile</h3><div class="hint">Lower is better. Green is an improvement over baseline.</div>${legend}
        <div style="height:340px"><canvas id="cLat" role="img" aria-label="p99 latency by profile"></canvas></div></div>
    </div>
    <div class="card" style="padding:6px 8px 4px"><div style="overflow-x:auto">${table(w)}</div></div>`;
  if (ok.length) {
    barChart(document.getElementById('cMain'), ok, {digits: w.primary === 'bw' ? 1 : 0, unit: w.unit,
      value: (r) => r.primary, change: (r) => r.primary_change, state: (r) => r.primary_state});
    barChart(document.getElementById('cLat'), ok, {digits: 2, unit: 'ms',
      value: (r) => r.p99, change: (r) => r.p99_change, state: (r) => r.p99_state});
  }
}

document.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-wl]'); if (b) { state.wl = +b.dataset.wl; render(); }
});
document.addEventListener('click', (e) => {
  const item = e.target.closest('.pick-item');
  if (item) { state.run = +item.dataset.run; state.wl = 0; render(); return; }
  const btn = e.target.closest('#pickBtn');
  if (btn) { setMenu(btn.getAttribute('aria-expanded') !== 'true'); return; }
  setMenu(false);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setMenu(false); });
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
render();
</script>
</body>
</html>
"""


def render(runs: List[Dict[str, Any]]) -> str:
    """Return the dashboard page for the given normalised runs."""
    data = json.dumps(runs).replace("</", "<\\/")
    return (
        PAGE.replace("__LOGO__", LOGO_PNG_BASE64)
        .replace("__NOW__", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        .replace("__DATA__", data)
    )


def write_run_dashboard(run_dir: Path) -> Path:
    """Write the dashboard for a single run folder next to its results."""
    output = run_dir / DASHBOARD_FILE
    output.write_text(render([load_run(run_dir / RESULT_FILE)]), encoding="utf-8")
    return output


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-dir", default="results",
                        help="Results directory containing <driver>/profile-comparison/<run>/")
    parser.add_argument("--run", action="append", default=[],
                        help="A single profile-comparison run folder (repeatable); "
                             "used instead of --base-dir")
    parser.add_argument("--days", type=int, help="Only include runs from the last N days")
    parser.add_argument("--output-html", default=DASHBOARD_FILE)
    args = parser.parse_args(argv)

    if args.run:
        files = [Path(item) / RESULT_FILE for item in args.run]
        missing = [str(path) for path in files if not path.exists()]
        if missing:
            parser.error(f"no {RESULT_FILE} in: {', '.join(missing)}")
    else:
        files = find_runs(Path(args.base_dir), args.days)
    if not files:
        print("No profile comparison results found.")
        return 1
    runs = [load_run(path) for path in files]
    output = Path(args.output_html)
    output.write_text(render(runs), encoding="utf-8")
    print(f"Dashboard generated: {output.absolute()} ({len(runs)} run(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
