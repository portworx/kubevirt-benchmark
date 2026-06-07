# Results Dashboard

The virtbench suite includes an interactive HTML dashboard generator to visualize and analyze your performance test results.

## Overview

After running tests with `--save-results`, you can generate a rich, interactive dashboard that provides:

- **Multi-level Organization**: Results organized by Storage Driver → Disk Count → VM Size
- **Interactive Charts**: Plotly-based bar charts showing duration metrics
- **Detailed Tables**: Sortable and searchable DataTables for all test results
- **Cluster Information**: Display cluster metadata and configuration
- **Manual Results**: Include manually collected test results
- **Time-series Visualization**: Track performance trends over time

## Generating the Dashboard

### Basic Usage

Generate the dashboard from the saved `results/` directory after benchmark
runs complete. This will:
1. Scan the `results/` directory for test results
2. Process all results from the last 15 days
3. Generate `results_dashboard.html` in the current directory

### Custom Configuration

Use the dashboard options below to select the results directory, time range,
cluster information file, manual results file, and output HTML path.

## Dashboard Options

| Option | Description | Default |
|--------|-------------|---------|
| `--days` | Number of days of results to include | 15 |
| `--base-dir` | Base directory containing test results | `results` |
| `--cluster-info` | Path to cluster information YAML file | `dashboard/cluster_info.yaml` |
| `--manual-results` | Path to manual results YAML file | `dashboard/manual_results.yaml` |
| `--output-html` | Output HTML file path | `results_dashboard.html` |

## Dashboard Features

### VM Creation Performance

- **Charts**: Bar charts showing average time to Running and time to Ping
- **Tables**: Detailed results for each test run with sortable columns
- **Metrics**: Success rate, average times, max times, total VMs tested

### Boot Storm Performance

- **Comparison Charts**: Initial creation vs boot storm performance
- **Impact Analysis**: Performance degradation metrics
- **Statistics**: Separate metrics for initial and boot storm phases

### Live Migration Performance

- **Duration Charts**: Migration time analysis
- **Success Rates**: Migration completion statistics
- **Detailed Metrics**: Observed vs VMIM timestamps, downtime measurements

### Chaos Benchmark Results

- **Chaos Operations**: Concurrent VM operations and their results
- **Phase Analysis**: Performance of each phase (create, resize, restart, snapshot)
- **Iteration Metrics**: Results across multiple iterations

## Cluster Information

Create a `cluster_info.yaml` file to include cluster metadata in the dashboard:

```yaml
cluster_name: "Production OCP Cluster"
ocp_version: "4.14.8"
kubevirt_version: "4.14.2"
storage_backend: "Portworx Enterprise"
storage_driver: "portworx-3.6"
node_count: 6
worker_nodes:
  - name: "worker-1"
    cpu: "32 cores"
    memory: "128 GB"
    storage: "2TB NVMe"
  - name: "worker-2"
    cpu: "32 cores"
    memory: "128 GB"
    storage: "2TB NVMe"
network: "10 Gbps"
notes: "Production cluster with HA configuration"
```

## Manual Results

Include manually collected results in `manual_results.yaml`:

```yaml
manual_tests:
  - test_type: "vm_creation"
    date: "2024-01-15"
    storage_driver: "portworx-3.6"
    disk_count: 10
    vm_count: 100
    avg_time_to_running: 12.5
    avg_time_to_ping: 18.3
    notes: "Baseline test before upgrade"
  
  - test_type: "migration"
    date: "2024-01-16"
    storage_driver: "portworx-3.6"
    vm_count: 50
    avg_migration_duration: 25.4
    success_rate: 100
    notes: "Sequential migration test"
```

## Dashboard Sections

### 1. Cluster Overview

Displays cluster configuration and metadata from `cluster_info.yaml`.

### 2. Test Summary

High-level statistics across all test types:
- Total tests run
- Date range of results
- Success rates
- Performance trends

### 3. VM Creation Results

Organized by storage driver and disk count:
- Interactive charts for time to Running and time to Ping
- Detailed tables with all test runs
- Filtering and sorting capabilities

### 4. Boot Storm Results

Comparison between initial creation and boot storm:
- Side-by-side performance charts
- Performance degradation metrics
- Statistical analysis

### 5. Migration Results

Migration performance analysis:
- Duration charts
- Success rate tracking
- Detailed migration metrics

### 6. Chaos Benchmark Results

Chaos testing outcomes:
- Concurrent operations performance
- Phase-by-phase performance
- Failure point analysis

## Using the Dashboard

### Navigation

- Use the table of contents to jump to specific sections
- Click on chart elements for detailed information
- Use table search and sort features to find specific results

### Filtering Results

- Filter by storage driver
- Filter by disk count
- Filter by date range
- Filter by test type

### Exporting Data

- Tables can be copied to clipboard
- Export to CSV or Excel
- Print-friendly view available

## Best Practices

1. **Regular Generation**: Generate dashboard after each test run to track trends
2. **Driver Tracking**: Use `--storage-driver` to organize DataSource clone and boot-storm results by backend driver, such as `portworx-3.6` or `ceph`
3. **Cluster Info**: Keep `cluster_info.yaml` updated with current configuration
4. **Manual Results**: Document baseline tests and special scenarios
5. **Archive Dashboards**: Save dashboard HTML files for historical reference

## Troubleshooting

### No results found

**Cause**: Results directory is empty or doesn't contain recent results

**Solution**:
- Verify results directory path with `--base-dir`
- Increase `--days` to include older results
- Ensure tests were run with `--save-results` flag

### Dashboard generation fails

**Cause**: Missing dependencies or malformed result files

**Solution**:
- Install required Python packages: `pip install -r requirements.txt`
- Check result JSON files for syntax errors
- Review dashboard script logs for specific errors

### Charts not displaying

**Cause**: JavaScript errors or missing Plotly library

**Solution**:
- Open browser console to check for errors
- Ensure internet connection (Plotly loads from CDN)
- Try a different web browser

## See Also

- [Output and Results](output-and-results.md) - Understanding test output
- [Configuration Options](configuration.md) - Test configuration reference
- [User Guide](test-scenarios/overview.md) - Running performance tests
