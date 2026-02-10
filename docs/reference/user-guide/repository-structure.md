# Repository Structure

This page describes the organization and structure of the virtbench repository.

## Directory Layout

```
kubevirt-benchmark/
├── virtbench/                    # Main CLI package
│   ├── __init__.py              # Package initialization
│   ├── cli.py                   # CLI entry point and command definitions
│   ├── commands/                # Individual command implementations
│   │   ├── datasource_clone.py # DataSource clone benchmark
│   │   ├── migration.py         # Migration benchmark
│   │   ├── chaos.py              # Chaos benchmark
│   │   ├── failure_recovery.py  # Failure recovery benchmark
│   │   └── validate_cluster.py  # Cluster validation
│   └── utils/                   # Shared utilities
│       ├── logger.py            # Logging utilities
│       ├── k8s_utils.py         # Kubernetes helpers
│       └── results.py           # Results processing
│
├── scripts/                     # Legacy Python scripts (deprecated)
│   ├── measure-vm-creation-time.py
│   ├── measure-migration-time.py
│   ├── measure-chaos.py
│   └── measure-failure-recovery.py
│
├── dashboard/                   # Dashboard generation
│   ├── generate_dashboard.py   # Dashboard generator script
│   ├── cluster_info.yaml       # Cluster metadata template
│   └── manual_results.yaml     # Manual results template
│
├── templates/                   # VM and resource templates
│   ├── vm-template.yaml        # Base VM template
│   ├── datasource-template.yaml# DataSource template
│   └── far-template.yaml       # FAR template
│
├── docs/                        # Documentation
│   ├── index.md                # Landing page
│   ├── install.md              # Installation guide
│   ├── reference/              # Reference documentation
│   │   ├── user-guide/         # User guides
│   │   │   └── test-scenarios/ # Test scenario guides
│   │   ├── configuration.md    # Configuration reference
│   │   ├── output-and-results.md
│   │   └── results-dashboard.md
│   └── community/              # Community docs
│
├── results/                     # Test results (auto-generated)
│   └── {storage-version}/
│       └── {num-disks}-disk/
│           └── {timestamp}_{test}_{vms}vms/
│
├── setup.py                     # Python package setup
├── requirements.txt             # Python dependencies
├── install.sh                   # Installation script
├── mkdocs.yml                   # Documentation configuration
├── README.md                    # Repository README
├── LICENSE                      # Apache 2.0 license
└── CHANGELOG.md                 # Version history
```

## Key Components

### virtbench CLI Package

The `virtbench/` directory contains the main CLI application:

- **cli.py**: Main entry point using Click framework
- **commands/**: Individual benchmark command implementations
- **utils/**: Shared utility functions for Kubernetes operations, logging, and results processing

### Templates

The `templates/` directory contains YAML templates for:

- **VM templates**: Base VM configurations for different scenarios
- **DataSource templates**: For VM cloning operations
- **FAR templates**: For failure and recovery testing

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
├── {storage-version}/          # e.g., "3.2.0" or "default"
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

