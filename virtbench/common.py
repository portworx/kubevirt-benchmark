#!/usr/bin/env python3
"""
Common utilities for virtbench CLI
"""
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from rich.console import Console
from rich.panel import Panel

console = Console()


def find_repo_root() -> Path:
    """
    Find the repository root directory.
    
    Checks in order:
    1. VIRTBENCH_REPO environment variable
    2. Current working directory
    3. Parent of current working directory
    4. Directory containing this script
    
    Returns:
        Path to repository root
        
    Raises:
        RuntimeError: If repository root cannot be found
    """
    # Check VIRTBENCH_REPO env var
    repo = os.getenv('VIRTBENCH_REPO')
    if repo:
        repo_path = Path(repo)
        if repo_path.exists() and (repo_path / 'chaos-benchmark').exists():
            return repo_path.resolve()

    # Check current directory
    cwd = Path.cwd()
    if (cwd / 'chaos-benchmark').exists():
        return cwd.resolve()

    # Check parent directory
    if (cwd.parent / 'chaos-benchmark').exists():
        return cwd.parent.resolve()

    # Check script location
    script_dir = Path(__file__).parent.parent
    if (script_dir / 'chaos-benchmark').exists():
        return script_dir.resolve()
    
    raise RuntimeError(
        "Could not find repository root directory.\n"
        "Please set VIRTBENCH_REPO environment variable or run from the repository directory."
    )


def print_banner(title: str) -> None:
    """
    Print a formatted banner.
    
    Args:
        title: Banner title text
    """
    console.print()
    console.print("=" * 80)
    console.print(f"  {title}")
    console.print("=" * 80)
    console.print()


def build_python_command(script_path: Path, args: Dict[str, Any]) -> List[str]:
    """
    Build Python command with arguments.
    
    Args:
        script_path: Path to Python script
        args: Dictionary of arguments (key-value pairs)
    
    Returns:
        List of command arguments suitable for subprocess
    """
    cmd = [sys.executable, str(script_path)]
    
    for key, value in args.items():
        if isinstance(value, bool):
            # Boolean flags - only add if True
            if value:
                cmd.append(f'--{key}')
        elif isinstance(value, (list, tuple)):
            # nargs='+' arguments: emit "--key v1 v2 v3"
            if len(value) > 0:
                cmd.append(f'--{key}')
                cmd.extend(str(v) for v in value)
        elif value is not None:
            # Regular arguments
            cmd.extend([f'--{key}', str(value)])

    return cmd


def generate_log_filename(prefix: str) -> str:
    """
    Generate a timestamped log filename.
    
    Args:
        prefix: Prefix for the log file (e.g., 'datasource-clone')
    
    Returns:
        Log filename with timestamp
    """
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return f"{prefix}-{timestamp}.log"

