# Repository Structure

This page describes the organization and structure of the virtbench repository.

## Directory Layout

```
kubevirt-benchmark/
├── virtbench/                    # Main CLI package
│   ├── __init__.py
│   ├── cli.py                    # CLI entry point and command definitions
│   ├── commands/                 # Individual command implementations
│   │   ├── chaos.py              # Chaos benchmark
│   │   ├── datasource_clone.py   # DataSource clone benchmark
│   │   ├── disk_ops.py           # Disk hotplug/coldplug benchmark
│   │   ├── elbencho.py           # elbencho IO benchmark
│   │   ├── failure_recovery.py   # Failure recovery benchmark
│   │   ├── fio.py                # FIO IO benchmark
│   │   ├── migration.py          # Migration benchmark
│   │   ├── validate.py           # Cluster validation
│   │   ├── version.py            # Version subcommand
│   │   └── vm_ops.py             # vm-ops command group
│   └── utils/                    # Shared utilities (logger, k8s helpers, results)
│
├── chaos-benchmark/              # Chaos benchmark Python script
│   └── measure-chaos.py
├── datasource-clone/             # DataSource-clone benchmark Python script
│   └── measure-vm-creation-time.py
├── disk-ops-benchmark/           # Disk hotplug/coldplug benchmark
│   ├── measure-disk-ops.py
├── migration/                    # Migration benchmark Python script
│   └── measure-vm-migration-time.py
├── failure-recovery/             # Failure-recovery Python script and FAR template
│   ├── recovery-test.py
│   └── far-template.yaml
├── io-benchmark/                 # IO benchmark scripts
│   ├── fio/
│   └── elbencho/
├── vm-ops/                       # VM operations scripts
│   ├── drain-nodes.py
│   ├── power-toggle-vms.py
│   ├── rebalance-vms.py
│   ├── run-blkdiscard.py
│   └── snapshot-vms.py
│
├── utils/                        # Shared shell/python helpers
│   ├── apply_template.sh         # VM template helper
│   ├── replace-storage-class.sh
│   ├── common.py
│   └── validate_cluster.py       # Cluster validation Python script
│
├── dashboard/                    # Dashboard generation
│   ├── generate_dashboard.py
│   ├── cluster_info.yaml         # Cluster metadata template
│   ├── manual_results.yaml       # Manual results template
│   └── README.md
│
├── examples/                     # Reference YAML and shell examples
│   ├── vm-templates/             # VM templates (vm-template.yaml, fio-vm-template.yaml, …)
│   ├── benchmarks/               # Sample benchmark resources
│   ├── scripts/                  # Reference shell scripts (migration scenarios, …)
│   └── utilities/                # Helper resources (e.g. ssh-pod.yaml)
│
├── docs/                         # Documentation (MkDocs)
│   ├── index.md                  # Landing page
│   ├── install.md                # Installation guide
│   ├── reference/                # Reference documentation
│   │   ├── user-guide/
│   │   │   └── test-scenarios/
│   │   ├── configuration.md
│   │   ├── output-and-results.md
│   │   └── results-dashboard.md
│   └── community/                # Community docs
│
├── results/                      # Test results (auto-generated)
│   └── {storage-driver}/
│       └── {num-disks}-disk/
│           └── {timestamp}_{test}_{vms}vms/
│
├── setup.py                      # Python package setup
├── requirements.txt              # Python dependencies
├── install.sh                    # Installation script
├── mkdocs.yml                    # Documentation configuration
├── README.md                     # Repository README
└── LICENSE                       # Apache 2.0 license
```

## Key Components

### virtbench CLI Package

The `virtbench/` directory contains the main CLI application:

- **cli.py**: Main entry point using Click framework
- **commands/**: Individual benchmark command implementations
- **utils/**: Shared utility functions for Kubernetes operations, logging, and results processing

### Templates

VM and resource templates live under `examples/vm-templates/` (with the
exception of `failure-recovery/far-template.yaml`, which sits next to the
script that consumes it):

- **VM templates** (`examples/vm-templates/`): Base VM configurations
  (`vm-template.yaml`, `fio-vm-template.yaml`, `rhel9-vm-datasource.yaml`,
  `rhel9-vm-registry.yaml`)
- **FAR template** (`failure-recovery/far-template.yaml`): For failure and
  recovery testing

### Dashboard

The `dashboard/` directory contains tools for generating interactive HTML dashboards from test results.

### Documentation

The `docs/` directory contains all documentation in Markdown format, organized for MkDocs:

- **Getting Started**: Installation and quick start guides
- **Reference**: Detailed guides and configuration reference
- **Community**: Contributing guidelines and support information

### Results Directory

The `results/` directory is auto-generated when running tests with `--save-results`. It follows a hierarchical structure:

```
results/
├── {storage-driver}/           # e.g., "portworx-3.6", "ceph"
│   ├── {num-disks}-disk/       # e.g., "1-disk", "2-disk"
│   │   ├── {timestamp}_{test}_{vms}vms/
│   │   │   ├── *_results.json  # Detailed results
│   │   │   ├── *_results.csv   # CSV format
│   │   │   └── summary_*.json  # Summary statistics
```

## File Naming Conventions

### Test Results

- **Timestamp format**: `YYYYMMDD-HHMMSS`
- **Test names**: `vm_creation`, `boot_storm`, `migration`, `chaos_benchmark`, `failure_recovery`
- **VM range**: `{start}-{end}` (e.g., `1-50`)

Example: `20250105-143052_vm_creation_1-50vms/`

### Configuration Files

- **YAML files**: Use `.yaml` extension
- **Python files**: Follow PEP 8 naming conventions
- **Shell scripts**: Use `.sh` extension

## Package Structure

### Python Package

The virtbench package is installed as an editable package using `pip install -e .`:

- **Entry point**: `virtbench` command
- **Version**: Defined in `virtbench/__init__.py`
- **Dependencies**: Listed in `requirements.txt` and `setup.py`

### Dependencies

Core dependencies:
- **click**: CLI framework
- **rich**: Terminal formatting and progress bars
- **pandas**: Data processing and CSV generation
- **pyyaml**: YAML file parsing

## Configuration Files

### mkdocs.yml

MkDocs configuration for documentation site:
- Site metadata
- Navigation structure
- Theme configuration
- Plugin settings

### setup.py

Python package configuration:
- Package metadata
- Entry points for CLI commands
- Dependency specifications
- Python version requirements

### requirements.txt

Python dependencies with version constraints:
- Core libraries
- CLI dependencies
- Dashboard dependencies

## See Also

- [Installation Guide](../../install.md) - How to install virtbench
- [Configuration Options](configuration.md) - Configuration reference
- [Output and Results](output-and-results.md) - Results structure and format
