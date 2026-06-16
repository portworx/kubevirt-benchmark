#!/usr/bin/env python3
"""
virtbench - KubeVirt Benchmark Suite CLI

Main entry point for the virtbench command-line interface.
"""
import click
import json
import os
import sys
from pathlib import Path

from virtbench.common import find_repo_root
from virtbench.commands import (
    datasource_clone,
    migration,
    chaos,
    failure_recovery,
    fio,
    elbencho,
    disk_ops,
    validate,
    version,
    vm_ops,
)


class Context:
    """Global context for sharing state between commands"""
    
    def __init__(self):
        self.log_level = 'info'
        self.log_file = None
        self.kubeconfig = None
        self.timeout = '4h'
        self.uuid = None
        self.repo_root = None
    
    def initialize(self):
        """Initialize context (find repo root)"""
        try:
            self.repo_root = find_repo_root()
        except RuntimeError as e:
            click.echo(f"Error: {e}", err=True)
            raise click.Abort()


@click.group(context_settings={'help_option_names': ['-h', '--help']})
@click.version_option(version='2.0.0', prog_name='virtbench')
@click.option('--log-level',
              default='info',
              type=click.Choice(['debug', 'info', 'warn', 'error'], case_sensitive=False),
              help='Log level')
@click.option('--log-file', 
              type=click.Path(),
              help='Log file path (auto-generated if not specified)')
@click.option('--kubeconfig', 
              type=click.Path(exists=True),
              help='Path to kubeconfig file')
@click.option('--timeout', 
              default='4h',
              help='Benchmark timeout (default: 4h)')
@click.option('--uuid', 
              help='Benchmark UUID (auto-generated if not specified)')
@click.pass_context
def cli(ctx, log_level, log_file, kubeconfig, timeout, uuid):
    """
    virtbench - KubeVirt Benchmark Suite
    
    Performance testing toolkit for KubeVirt virtual machines running on
    OpenShift Container Platform (OCP).
    
    \b
    Available Commands:
      datasource-clone     Run DataSource clone benchmark
      migration            Run VM migration benchmark
      chaos-benchmark      Run chaos benchmark (concurrent VM/volume operations)
      failure-recovery     Run failure recovery benchmark
      fio                  Run FIO benchmark across VMs
      elbencho             Manage elbencho workloads on VMs
      disk-ops             Run disk hotplug/coldplug benchmark
      vm-ops               VM operations (drain, rebalance, snapshot, blkdiscard, power)
      validate-cluster     Validate cluster prerequisites
      version              Print version information

    \b
    Examples:
      # Validate cluster
      virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS

      # Run datasource clone test
      virtbench datasource-clone --start 1 --end 10 --storage-class YOUR-STORAGE-CLASS

      # Run migration test
      virtbench migration --start 1 --end 5 --source-node worker-1

      # Manage elbencho workloads
      virtbench elbencho -p datasource-clone -s 1 -e 10 -n rhel-elbencho-1 -a status
    
    \b
    Global Flags:
      --log-level          Log level: debug, info, warn, error (default: info)
      --log-file           Log file path (auto-generated if not specified)
      --kubeconfig         Path to kubeconfig file
      --timeout            Benchmark timeout (default: 4h)
      --uuid               Benchmark UUID (auto-generated if not specified)
    """
    # Create context object
    ctx.obj = Context()
    ctx.obj.log_level = log_level.lower()
    ctx.obj.log_file = log_file
    ctx.obj.kubeconfig = kubeconfig
    ctx.obj.timeout = timeout
    ctx.obj.uuid = uuid

    if kubeconfig:
        os.environ['KUBECONFIG'] = kubeconfig

    os.environ['VIRTBENCH_COMMAND_ARGS'] = json.dumps(['virtbench'] + sys.argv[1:])
    
    # Initialize context (find repo root)
    ctx.obj.initialize()


# Register subcommands
cli.add_command(datasource_clone.datasource_clone)
cli.add_command(migration.migration)
cli.add_command(chaos.chaos_benchmark)
cli.add_command(failure_recovery.failure_recovery)
cli.add_command(fio.fio)
cli.add_command(elbencho.elbencho)
cli.add_command(disk_ops.disk_ops)
cli.add_command(vm_ops.vm_ops)
cli.add_command(validate.validate_cluster)
cli.add_command(version.version)


def main():
    """Entry point for CLI"""
    cli(obj=None)


if __name__ == '__main__':
    main()
