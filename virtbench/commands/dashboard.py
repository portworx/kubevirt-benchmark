#!/usr/bin/env python3
"""
Dashboard command - build the results dashboard from the saved runs
"""
import importlib.util
from pathlib import Path

import click
from rich.console import Console

from virtbench.profile_dashboard import DASHBOARD_FILE, find_runs, load_run, render

console = Console()


def _load_generator(repo_root: Path):
    script = Path(repo_root) / "dashboard" / "generate_dashboard.py"
    if not script.exists():
        raise click.ClickException(f"Dashboard generator not found at {script}")
    spec = importlib.util.spec_from_file_location("generate_dashboard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@click.command('dashboard')
@click.option('--days', type=click.IntRange(min=1), help='Only include runs from the last N days (default: all)')
@click.option('--output', type=click.Path(path_type=Path),
              help='Where to write the results page (default: results_dashboard.html in the repo root)')
@click.pass_context
def dashboard(ctx, days, output):
    """
    Build the dashboards from everything under results/

    \b
    Writes results_dashboard.html (FIO, VM pool, creation, migration, chaos, ...)
    and, when profile comparisons exist, profile_comparison_dashboard.html.

    \b
    Examples:
      virtbench dashboard
      virtbench dashboard --days 30
    """
    repo_root = Path(ctx.obj.repo_root)
    generator = _load_generator(repo_root)
    written = []

    results_page = generator.generate(days=days, output=str(output) if output else None)
    if results_page:
        written.append(("Results dashboard", results_page))

    runs = find_runs(repo_root / "results", days)
    if runs:
        profile_page = repo_root / DASHBOARD_FILE
        profile_page.write_text(render([load_run(path) for path in runs]), encoding="utf-8")
        written.append(("Profile comparison", profile_page))

    if not written:
        console.print("[yellow]No results found to show.[/yellow] Run a benchmark first, or check the results/ folder.")
        raise click.exceptions.Exit(1)
    for label, path in written:
        console.print(f"{label}: [cyan]{path}[/cyan]")
