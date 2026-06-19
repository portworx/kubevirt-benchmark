# FIO Storage I/O Benchmark

Tests storage I/O performance by running FIO benchmarks across multiple VMs in parallel.

**Use Case**: Measure storage IOPS, bandwidth, and latency under various workload patterns.

## How It Works

FIO is baked into the VM template and runs on boot. The benchmark workflow has four stages, each runnable on its own:

1. **deploy** - Create namespaces and VMs; FIO starts automatically.
2. **status** - Poll VM and FIO state.
3. **gather-results** - Collect FIO JSON output from each VM via SSH.
4. **cleanup** - Delete VMs and namespaces.

`run-all` (default) chains all four with a wait between deploy and gather.

## Basic Usage

### virtbench CLI

```bash
# Full workflow (deploy + wait + gather + save)
virtbench fio --action run-all \
  --start 1 --end 10 \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results

# Custom FIO parameters
virtbench fio --action run-all \
  --start 1 --end 20 \
  --storage-class YOUR-STORAGE-CLASS \
  --fio-runtime 300 --fio-rw randrw --fio-bs 4k --fio-iodepth 64 \
  --save-results --cleanup

# Step-by-step workflow
virtbench fio --action deploy -s 1 -e 10 --storage-class YOUR-STORAGE-CLASS
virtbench fio --action status -s 1 -e 10
virtbench fio --action gather-results -s 1 -e 10 --save-results
virtbench fio --action cleanup -s 1 -e 10
```

!!! note "`--save-results` vs `--action gather-results`"
    `--save-results` is a **flag** that writes summary JSON/CSV files to the output directory. `--action gather-results` is an **action** that collects raw FIO output from VMs over SSH. Use them together (`--action gather-results --save-results`) to both fetch and persist.

## Configuration Options

### Action and Targeting

| Option | Default | Description |
|--------|---------|-------------|
| `--action`, `-a` | `run-all` | One of: `deploy`, `status`, `gather-results`, `cleanup`, `run-all` |
| `--start`, `-s` | (required) | Starting namespace index |
| `--end`, `-e` | (required) | Ending namespace index |
| `--storage-class` | (required for `deploy`/`run-all`) | Storage class name |
| `--namespace-prefix` | `fio-benchmark` | Namespace prefix (creates `fio-benchmark-1`, ...) |
| `--vm-name` | `fio-vm` | VM resource name in each namespace |
| `--vm-template` | `../examples/vm-templates/fio-vm-template.yaml` | Path to VM template YAML |
| `--concurrency`, `-c` | `20` | Max parallel threads for deploy/status/cleanup |

### FIO Parameters

| Option | Default | Description |
|--------|---------|-------------|
| `--fio-runtime` | `300` | FIO test duration in seconds |
| `--fio-rw` | `randwrite` | I/O pattern: `read`, `write`, `randread`, `randwrite`, `randrw`, `rw` |
| `--fio-bs` | `4k` | Block size (e.g., `4k`, `8k`, `64k`, `1M`) |
| `--fio-iodepth` | `64` | I/O queue depth |
| `--fio-numjobs` | `4` | Number of parallel FIO jobs per VM |
| `--fio-size` | `10G` | Test file size per job |

### Result Collection

| Option | Default | Description |
|--------|---------|-------------|
| `--collect-retries` | `8` | Max retries for collecting results from VMs |
| `--collect-retry-delay` | `20` | Delay (seconds) between collection retries |
| `--collect-concurrency` | `5` | Max concurrent result collections |
| `--ssh-pod` | `ssh-test-pod` | SSH helper pod name (must have `sshpass`) |
| `--ssh-pod-ns` | `default` | SSH helper pod namespace |
| `--vm-user` | `cloud-user` | VM SSH user |
| `--vm-password` | `changeme` | VM SSH password |

### Output

| Option | Default | Description |
|--------|---------|-------------|
| `--save-results` | `false` | Save summary JSON/CSV to the output directory |
| `--results-dir` | `results` | Base directory for results |
| `--storage-driver` | `Not-Specified` | Storage driver label (folder component) |
| `--disks-per-vm` | `auto` | Disks-per-VM label (folder component); auto-detected from VM spec |
| `--cleanup` | `false` | Delete VMs and namespaces after `run-all` |

## Disk Space Requirements

FIO creates test files based on `--fio-size` and `--fio-numjobs`:

| Setting | Calculation | Example |
|---------|-------------|---------|
| Test files per VM | `fio-size × fio-numjobs` | 10G × 4 = **40GB** |
| Scratch disk size | Must be > test files | 50GB (in VM template) |

**Data written during test** depends on achieved IOPS:

```
Data Written = Write IOPS × Block Size × Runtime

Example: 21,000 IOPS × 4KB × 60s ≈ 5GB written
```

## Workload Examples

```bash
# Sequential read (max bandwidth)
virtbench fio --start 1 --end 10 --storage-class YOUR-SC \
  --fio-rw read --fio-bs 1M --fio-iodepth 32 --save-results

# Random write (database workload)
virtbench fio --start 1 --end 10 --storage-class YOUR-SC \
  --fio-rw randwrite --fio-bs 4k --fio-iodepth 64 --save-results

# Mixed read/write 50/50 (general workload)
virtbench fio --start 1 --end 10 --storage-class YOUR-SC \
  --fio-rw randrw --fio-bs 4k --fio-iodepth 64 --save-results
```

## Results

Results include per-VM and aggregated metrics:

- **IOPS**: Read/Write operations per second
- **Bandwidth**: Read/Write throughput in MiB/s
- **Latency**: Read/Write latency in milliseconds

### Output Structure

```
{results-dir}/{storage-driver}/{disks-per-vm}/{timestamp}_fio_benchmark_{N}vms/
├── summary_fio_benchmark.json     # Aggregated summary across all VMs
├── fio_benchmark_results.json     # Per-VM results (JSON array)
├── fio_benchmark_results.csv      # Per-VM results (CSV)
└── per-vm-results/                # Raw FIO output per VM
    ├── fio-benchmark-1/
    │   └── fio_raw.json
    └── ...
```

Example: `results/portworx-3.6/2-disk/20260308-141522_fio_benchmark_10vms/`. `--storage-driver` and `--disks-per-vm` are folder components only - they have no functional effect on the test.

### View in Dashboard

Generate the dashboard from the saved results directory after the run.

The dashboard displays:

- KPI summary cards (Total IOPS, Bandwidth, Latency)
- IOPS + Latency dual-axis chart
- Per-VM breakdown with clickable namespaces
