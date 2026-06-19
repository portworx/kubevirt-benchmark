"""
Version command
"""

import click
from rich.console import Console
from rich.table import Table

import virtbench


console = Console()


@click.command("version")
def version():
    """
    Print version information

    Displays version information for virtbench and its components.
    """
    table = Table(title="virtbench Version Information", show_header=True, header_style="bold cyan")
    table.add_column("Component", style="cyan")
    table.add_column("Version", style="green")

    table.add_row("virtbench", virtbench.__version__)
    table.add_row("Python", get_python_version())
    table.add_row("Click", get_package_version("click"))
    table.add_row("Rich", get_package_version("rich"))
    table.add_row("PyYAML", get_package_version("yaml"))
    table.add_row("Pandas", get_package_version("pandas"))

    console.print()
    console.print(table)
    console.print()


def get_python_version() -> str:
    """Get Python version"""
    import sys

    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def get_package_version(package_name: str) -> str:
    """Get package version"""
    try:
        if package_name == "yaml":
            import yaml

            return getattr(yaml, "__version__", "unknown")
        else:
            import importlib.metadata

            return importlib.metadata.version(package_name)
    except Exception:
        return "not installed"
