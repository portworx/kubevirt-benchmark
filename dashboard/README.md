# KubeVirt Performance Dashboard Generator

## Overview

`generate_dashboard.py` is a Python script that generates an interactive HTML dashboard for visualizing KubeVirt performance test results. The dashboard organizes test results by PX version, disk configuration, and VM count, providing comprehensive charts and tables for analyzing VM creation, boot storm, and live migration performance metrics.

## Features

- **Multi-level Organization**: Results organized by PX Version → Disk Count → VM Size
- **Interactive Charts**: Plotly-based bar charts showing duration metrics in mm:ss format
- **Detailed Tables**: DataTables-powered interactive tables with sorting and filtering
- **Cluster Information**: Display cluster metadata and configuration details
- **Manual Results**: Include manually collected test results alongside automated ones
- **Time-based Filtering**: Filter results by date range (e.g., last N days)

## Requirements

### Python Dependencies

```bash
pip install pandas pyyaml
```

### Required Python Packages
- `pandas` - Data manipulation and analysis
- `pyyaml` - YAML file parsing

## Usage

### Basic Usage

From the repository root directory (`/Users/bnagar/Documents/GitHub/kubevirt-benchmark-suite/`):

```bash
python3 dashboard/generate_dashboard.py
```

This will use default parameters:
- Look for results in the `results/` directory
- Include results from the last 15 days
- Generate `results_dashboard.html` in the current directory

### Full Usage with All Parameters

```bash
python3 dashboard/generate_dashboard.py \
  --days 50 \
  --base-dir results \
  --cluster-info dashboard/cluster_info.yaml \
  --manual-results dashboard/manual_results.yaml \
  --output-html results_dashboard.html
```

### Command-Line Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--days` | int | 15 | Number of days to look back for results. Only folders with timestamps within this range will be included. |
| `--base-dir` | str | `results` | Base directory containing test result folders. |
| `--output-html` | str | `results_dashboard.html` | Output HTML file path. |
| `--cluster-info` | str | None | Path to cluster information YAML file (optional). |
| `--manual-results` | str | None | Path to manual results YAML file (optional). |

### Examples

**Example 1: Generate dashboard for last 30 days**
```bash
python3 dashboard/generate_dashboard.py --days 30
```

**Example 2: Custom output location**
```bash
python3 dashboard/generate_dashboard.py \
  --output-html /path/to/custom_dashboard.html
```

**Example 3: Different results directory**
```bash
python3 dashboard/generate_dashboard.py \
  --base-dir /path/to/test/results \
  --days 60
```

**Example 4: Your typical usage**
```bash
python3 dashboard/generate_dashboard.py \
  --days 50 \
  --base-dir results \
  --cluster-info dashboard/cluster_info.yaml \
  --manual-results dashboard/manual_results.yaml
```

## Input Data Structure

### Results Directory Structure

The script expects the following directory structure:

```
results/
├── <px_version>/
│   ├── <disk_count>/
│   │   ├── <timestamp>_<test_name>_<vm_range>/
│   │   │   ├── summary_vm_creation_results.json
│   │   │   ├── vm_creation_results.json
│   │   │   ├── summary_boot_storm_results.json
│   │   │   ├── boot_storm_results.json
│   │   │   ├── summary_migration_results.json
│   │   │   └── migration_results.json
```

**Example:**
```
results/
├── 3.5.0/
│   ├── 1-disk/
│   │   ├── 20251014-165952_kubevirt-perf-test_1-50/
│   │   │   ├── summary_vm_creation_results.json
│   │   │   ├── vm_creation_results.json
│   │   │   └── ...
│   ├── 2-disk/
│   ├── 4-disk/
```

### Folder Naming Convention

Result folders must follow this naming pattern and structure. Automatically generated with other `measure-vm-creation-time.py` and `measure-vm-migration-time.py` scripts are run with --save-results option.
```
<YYYYMMDD-HHMMSS>_<test_name>_<vm_range>
```

- **Timestamp**: `YYYYMMDD-HHMMSS` format (e.g., `20251014-165952`)
- **Test Name**: Any descriptive name (e.g., `kubevirt-perf-test`)
- **VM Range**: Either a single number (e.g., `50`) or a range (e.g., `1-50`)

**Examples:**
- `20251014-165952_kubevirt-perf-test_1-50` → 50 VMs
- `20251015-120000_boot-test_100` → 100 VMs
- `20251016-093000_migration-test_1-200` → 200 VMs

### Cluster Info YAML Format

The `cluster_info.yaml` file is provided as a template. Copy and customize it with your cluster's metadata. All key-value pairs under the `cluster` section will be displayed in the "Cluster Info" tab.

**Template File:** `dashboard/cluster_info.yaml`

**Notes:**
- Keys are automatically converted to labels (e.g., `px_version` → `PX VERSION`)
- Multi-line values (using `|`) are displayed in `<pre>` tags
- You can add any custom key-value pairs
- Customize the template with your cluster's specific information

### Manual Results YAML Format

The `manual_results.yaml` file contains manually collected test results. Whatever YAML structure you create under the `results` section will be converted to a table in the "Manual Results" tab.

**File:** `dashboard/manual_results.yaml`

**Format:**
```yaml
results:
  - field1: value1
    field2: value2
    field3: value3
  - field1: value4
    field2: value5
    field3: value6
```

**Notes:**
- Whatever YAML you create will be converted to a table in the Manual Results tab
- Each list item becomes a row in the table
- Keys become column headers
- Numeric values are automatically rounded to 2 decimal places

## Output

### Generated Dashboard

The script generates a single HTML file (`results_dashboard.html` by default) containing:

1. **Top-level PX Version Tabs**: One tab per PX version found in results
2. **Disk Count Tabs**: Nested tabs for each disk configuration (1-disk, 2-disk, 4-disk, etc.)
3. **Summary Charts**: Three compact bar charts showing:
   - VM Creation Duration
   - Boot Storm Duration
   - Live Migration Duration
4. **VM Size Tabs**: Further nested tabs for each VM count (50 VMs, 100 VMs, etc.)
5. **Detailed Tables**:
   - Creation + Boot Storm subtab with summary and detailed results
   - Live Migration subtab with summary and detailed results
6. **Cluster Info Tab**: (if `--cluster-info` provided) Displays cluster metadata
7. **Manual Results Tab**: (if `--manual-results` provided) Displays manual test results

### Dashboard Features

- **Interactive Charts**: Hover over bars to see detailed information
- **Sortable Tables**: Click column headers to sort
- **Searchable Tables**: Use the search box to filter results
- **Responsive Design**: Works on desktop and mobile devices
- **Bootstrap Styling**: Clean, professional appearance

## Metrics Displayed

### VM Creation Metrics
- Running Time
- Ping Time
- Clone Duration
- Total VMs, Successful, Failed counts
- Total Creation Duration

### Boot Storm Metrics
- Running Time
- Ping Time
- Total VMs, Successful, Failed counts
- Total Boot Storm Duration

### Live Migration Metrics
- Observed Time
- VMIM Time
- Difference (Observed - VMIM)
- Total VMs, Successful, Failed counts
- Total Migration Duration

## Troubleshooting

### No results appear in dashboard

**Possible causes:**
1. Results are older than the `--days` threshold
   - **Solution**: Increase `--days` value
2. Incorrect `--base-dir` path
   - **Solution**: Verify the path to your results directory
3. Folder naming doesn't match expected pattern
   - **Solution**: Ensure folders follow `YYYYMMDD-HHMMSS_name_vm-range` format

### Cluster Info or Manual Results not showing

**Possible causes:**
1. File path is incorrect
   - **Solution**: Verify the path to YAML files
2. YAML syntax errors
   - **Solution**: Validate YAML syntax using a YAML validator
3. Missing `cluster` or `results` top-level key
   - **Solution**: Ensure YAML has correct structure

### Charts not rendering

**Possible causes:**
1. Missing JSON summary files
   - **Solution**: Ensure `summary_*.json` files exist in result folders
2. Invalid JSON format
   - **Solution**: Validate JSON files

## Tips

1. **Regular Updates**: Run the script regularly to keep the dashboard up-to-date
2. **Version Control**: Consider version controlling your `cluster_info.yaml` and `manual_results.yaml` files
3. **Automation**: Add the script to your CI/CD pipeline for automatic dashboard generation
4. **Archiving**: Use different `--days` values to create historical snapshots
5. **Custom Styling**: The generated HTML can be customized by editing the embedded CSS

## Support

For issues or questions about the dashboard generator, please refer to the main project documentation or contact the development team.
