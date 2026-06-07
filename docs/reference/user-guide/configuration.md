# Configuration Options

This page provides a comprehensive reference for the `virtbench` CLI.

## VM Creation Tests

Configuration options for DataSource-based VM provisioning and boot storm tests.

| Option                       | Description                                                                            | Default                                          |
|------------------------------|----------------------------------------------------------------------------------------|--------------------------------------------------|
| `--start`                    | Starting namespace index                                                               | 1                                                |
| `--end`                      | Ending namespace index                                                                 | 10                                               |
| `--vm-name`                  | VM resource name                                                                       | rhel-9-vm                                        |
| `--concurrency`              | Max parallel monitoring threads                                                        | 50                                               |
| `--ssh-pod`                  | Pod name for ping tests                                                                | ssh-test-pod                                     |
| `--ssh-pod-ns`               | Namespace of SSH pod                                                                   | default                                          |
| `--poll-interval`            | Seconds between status checks                                                          | 1                                                |
| `--ping-timeout`             | Ping timeout in seconds                                                                | 300                                              |
| `--log-file`                 | Output log file path. With `--save-results`, the log is written into the run result folder unless explicitly overridden. | auto-generated |
| `--namespace-prefix`         | Prefix for test namespaces                                                             | datasource-clone                                 |
| `--namespace-batch-size`     | Namespaces to create in parallel                                                       | 20                                               |
| `--boot-storm`               | Enable boot storm testing                                                              | false                                            |
| `--skip-vm-creation`         | Reuse existing VMs (boot-storm only)                                                   | false                                            |
| `--skip-namespace-creation`  | Skip namespace creation step                                                           | false                                            |
| `--single-node`              | Run all VMs on a single node                                                           | false                                            |
| `--node-name`                | Specific node to use (requires `--single-node`)                                        | auto-select                                      |
| `--num-disks`                | Override number of data disks in the VM template                                       | template default                                 |
| `--cleanup`                  | Delete resources and namespaces after test                                             | false                                            |
| `--cleanup-on-failure`       | Clean up even if tests fail                                                            | false                                            |
| `--dry-run-cleanup`          | Show what would be deleted without deleting                                            | false                                            |
| `--yes`                      | Skip confirmation prompt for cleanup                                                   | false                                            |
| `--save-results`             | Save log, detailed JSON/CSV, and summary JSON/CSV inside a timestamped run folder      | false                                            |
| `--results-folder`           | Base directory to store test results                                                   | results                                          |
| `--storage-driver`           | Storage driver label to include in results path, such as `portworx-3.6` or `ceph` | -                                             |

Saved DataSource clone and boot-storm runs use this structure:

```text
results/{storage-driver}/{num-disks}-disk/{timestamp}_{namespace-prefix}_{start}-{end}/
├── datasource-clone.log
├── vm_creation_results.json
├── vm_creation_results.csv
├── summary_vm_creation_results.json
├── summary_vm_creation_results.csv
├── boot_storm_results.json
├── boot_storm_results.csv
├── summary_boot_storm_results.json
└── summary_boot_storm_results.csv
```

## Live Migration Tests

Configuration options for VM live migration testing.

| Option | Description | Default |
|--------|-------------|---------|
| `--start`, `-s` | Starting namespace index | 1 |
| `--end`, `-e` | Ending namespace index | 10 |
| `--vm-name`, `-n` | VM resource name | rhel-9-vm |
| `--namespace-prefix` | Prefix for test namespaces | migration |
| `--create-vms` | Create VMs before migration | false |
| `--vm-template` | VM template YAML file | ../examples/vm-templates/vm-template.yaml |
| `--storage-class` | Storage class name (required with --create-vms) | None |
| `--source-node` | Source node name for migration | None |
| `--source-nodes` | Comma-separated list of source nodes, repeatable if needed, or `all` for every worker | None |
| `--target-node` | Target node name for migration | auto-select |
| `--parallel` | Migrate VMs in parallel | false |
| `--evacuate` | Evacuate all VMs from source node | false |
| `--concurrency`, `-c` | Number of concurrent migrations | 50 |
| `--migration-timeout` | Timeout for each migration in seconds | 600 |
| `--max-migration-retries` | Maximum retries for failed migrations | 3 |
| `--vm-startup-timeout` | Timeout waiting for VMs to reach Running state | 3600 (1 hour) |
| `--ssh-pod` | SSH test pod name for ping tests | ssh-test-pod |
| `--ssh-pod-ns` | SSH test pod namespace | default |
| `--ping-timeout` | Timeout for ping validation in seconds | 3600 (1 hour) |
| `--skip-ping` | Skip ping validation after migration | false |
| `--log-file` | Output log file path | auto-generated |
| `--cleanup / --no-cleanup` | Delete VMs, VMIMs, and namespaces after test | false |
| `--yes`, `-y` | Skip confirmation prompts | false |
| `--save-results` | Save detailed migration results (JSON and CSV) under results/ | false |
| `--storage-driver` | Storage driver to include in results path (optional) | - |
| `--results-folder` | Base directory to store test results | ../results |

## Failure Recovery Tests

Configuration options for failure and recovery testing with FAR.

The `virtbench failure-recovery` wrapper auto-discovers VMs by node, so it
does **not** take `--start`/`--end`.

| Option | Description | Default |
|--------|-------------|---------|
| `--node` *(required)* | Node name to auto-detect VMs from | — |
| `--vm-name`, `-n` | VM resource name | rhel-9-vm |
| `--vm-template` | VM template YAML file | ../examples/vm-templates/vm-template.yaml |
| `--storage-class` | Storage class name (overrides template value) | None |
| `--namespace-prefix` | Prefix for test namespaces | failure-recovery |
| `--concurrency`, `-c` | Max parallel threads | 10 |
| `--poll-interval` | Seconds between polls | 5 |
| `--recovery-timeout` | Timeout for recovery in seconds | 600 |
| `--cleanup / --no-cleanup` | Delete test resources after completion | false |
| `--yes`, `-y` | Skip confirmation prompts | false |
| `--save-results` | Save detailed results to results folder | false |
| `--results-folder` | Base directory to store test results | ../results |
| `--storage-driver` | Storage driver to include in results path (optional) | - |
| `--log-file` | Log file path | auto-generated |

## Chaos Benchmark Tests

Configuration options for chaos benchmark testing.

### Required Options

| Option | Description |
|--------|-------------|
| `--storage-class` | Storage class name (comma-separated for multiple) |
| `--concurrency` | Number of concurrent operations (**REQUIRED**) |

### Test Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--namespace` | `virt-chaos-benchmark` | Namespace for test resources |
| `--max-iterations` | `0` (unlimited) | Maximum number of iterations |
| `--vms` | `5` | Number of VMs per iteration |
| `--data-volume-count` | `1` | Number of data volumes per VM |
| `--min-vol-size` | `30Gi` | Initial volume size (must include unit, e.g., `30Gi`) |
| `--min-vol-inc-size` | `10Gi` | Volume size increment for resize (must include unit, e.g., `10Gi`) |

### VM Template Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--vm-yaml` | `examples/vm-templates/vm-template.yaml` | Path to VM YAML template |
| `--vm-name` | `rhel-9-vm` | Base VM name |
| `--datasource-name` | `rhel9` | DataSource name |
| `--datasource-namespace` | `openshift-virtualization-os-images` | DataSource namespace |
| `--vm-memory` | `2048M` | VM memory |
| `--vm-cpu-cores` | `1` | VM CPU cores |

### Skip Options

| Option | Description |
|--------|-------------|
| `--skip-resize` | Skip volume resize phase |
| `--skip-clone` | Skip volume clone phase |
| `--skip-snapshot` | Skip snapshot phase |
| `--skip-restart` | Skip restart phase |

### Execution Options

| Option | Default | Description |
|--------|---------|-------------|
| `--concurrency` | `50`    | Number of concurrent operations |
| `--poll-interval` | `1`     | Polling interval in seconds |
| `--scheduling-timeout` | `120`   | Seconds to wait in Scheduling state before declaring capacity reached |

### Cleanup Options

| Option | Description |
|--------|-------------|
| `--cleanup` | Cleanup resources after test completion |
| `--cleanup-only` | Only cleanup resources from previous runs |

### Results Options

| Option | Default | Description |
|--------|---------|-------------|
| `--save-results` | `false` | Save results to JSON/CSV files |
| `--results-dir` | `results` | Directory to save results |
| `--storage-driver` | `default` | Storage driver for folder hierarchy (e.g., portworx-3.6) |

Results are saved in the standard folder structure:
```
results/{storage-driver}/{num-disks}-disk/{timestamp}_chaos_benchmark_{total_vms}vms/
```

### Logging Options

| Option | Default | Description |
|--------|---------|-------------|
| `--log-file` | Auto-generated | Log file path |
| `--log-level` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Common Options

These options are available across multiple test types:

### Logging

- `--log-file`: Path to save log output (default: stdout)
- `--log-level`: Set logging verbosity (DEBUG, INFO, WARNING, ERROR)

### Cleanup

- `--cleanup`: Remove all test resources after completion
- `--cleanup-on-failure`: Clean up even if tests fail
- `--dry-run-cleanup`: Preview what would be deleted without actually deleting
- `--yes`: Skip confirmation prompts

### Results

- `--save-results`: Save detailed results to JSON and CSV files
- `--storage-driver`: Organize DataSource clone and boot-storm results by storage driver
- `--results-folder` / `--results-dir`: Base directory for results

### Network Testing

- `--ssh-pod`: Name of SSH test pod for ping validation
- `--ssh-pod-ns`: Namespace of SSH test pod
- `--ping-timeout`: Timeout for network reachability tests
- `--skip-ping`: Skip network validation (faster but less comprehensive)

### Concurrency

- `--concurrency`: Number of parallel operations
- `--poll-interval`: Seconds between status checks

## Environment Variables

### VIRTBENCH_REPO

When using the virtbench CLI from any directory, set this variable to point to the repository root:

```bash
export VIRTBENCH_REPO=/path/to/kubevirt-benchmark-suite
```

Add to your shell profile for persistence:

```bash
echo 'export VIRTBENCH_REPO=/path/to/kubevirt-benchmark-suite' >> ~/.bashrc
source ~/.bashrc
```

### KUBECONFIG

For direct Python script execution, set `KUBECONFIG` so the underlying
`kubectl` calls use the intended cluster:

```bash
export KUBECONFIG=/path/to/kubeconfig
```

When using the `virtbench` CLI, you can either set `KUBECONFIG` or pass the
global option:

```bash
virtbench --kubeconfig /path/to/kubeconfig validate-cluster --storage-class YOUR-STORAGE-CLASS
```

## Configuration Files

### VM Templates

VM templates use placeholder variables that can be replaced:

| Variable | Description | Example Values |
|----------|-------------|----------------|
| `{{VM_NAME}}` | VM name | `rhel-9-vm`, `my-test-vm` |
| `{{STORAGE_CLASS_NAME}}` | Storage class name | `standard`, `gp2`, `ceph-rbd` |
| `{{DATASOURCE_NAME}}` | DataSource name | `rhel9`, `fedora`, `centos` |
| `{{DATASOURCE_NAMESPACE}}` | DataSource namespace | `openshift-virtualization-os-images` |
| `{{STORAGE_SIZE}}` | Root disk storage size | `30Gi`, `50Gi`, `100Gi` |
| `{{VM_MEMORY}}` | VM memory allocation | `2048M`, `4Gi`, `8Gi` |
| `{{VM_CPU_CORES}}` | Number of CPU cores | `1`, `2`, `4`, `8` |

See the [Installation Guide](../../install.md) for details on using the template helper script.

## See Also

- [User Guide Overview](test-scenarios/overview.md) - Getting started with benchmarks
- [Output and Results](output-and-results.md) - Understanding test output
- [Installation Guide](../../install.md) - Setup and configuration
