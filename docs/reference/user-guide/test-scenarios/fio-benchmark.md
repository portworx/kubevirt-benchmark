# FIO Storage I/O Benchmark

Tests storage I/O performance by running FIO benchmarks across multiple VMs in parallel.

**Use Case**: Measure storage IOPS, bandwidth, and latency under various workload patterns.

## Basic Usage

### virtbench CLI

```bash
# Run FIO benchmark on 10 VMs
virtbench fio \
  --start 1 --end 10 \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results

# Custom FIO parameters
virtbench fio \
  --start 1 --end 20 \
  --storage-class YOUR-STORAGE-CLASS \
  --fio-runtime 300 \
  --fio-rw randrw \
  --fio-bs 4k \
  --fio-iodepth 64 \
  --save-results \
  --cleanup
```

### Python Script

```bash
cd fio-benchmark

python3 measure-fio-performance.py \
  --start 1 --end 20 \
  --storage-class YOUR-STORAGE-CLASS \
  --fio-runtime 300 \
  --fio-rw randrw \
  --fio-bs 4k \
  --fio-iodepth 64 \
  --fio-numjobs 4 \
  --save-results \
  --results-dir ../results/my-test \
  --cleanup
```

## Configuration Options

### FIO Parameters

| Option | Default | Description |
|--------|---------|-------------|
| `--start` | (required) | Starting namespace index |
| `--end` | (required) | Ending namespace index |
| `--storage-class` | (required) | Storage class name |
| `--namespace-prefix` | `fio-benchmark` | Namespace prefix (creates `fio-benchmark-1`, etc.) |
| `--fio-runtime` | `300` | FIO test duration in seconds |
| `--fio-rw` | `randwrite` | I/O pattern: `read`, `write`, `randread`, `randwrite`, `randrw`, `rw` |
| `--fio-bs` | `4k` | Block size (e.g., `4k`, `8k`, `64k`, `1M`) |
| `--fio-iodepth` | `64` | I/O queue depth |
| `--fio-numjobs` | `4` | Number of parallel FIO jobs per VM |
| `--fio-size` | `10G` | Test file size per job |

### Result Collection Options

| Option | Default | Description |
|--------|---------|-------------|
| `--collect-retries` | `8` | Max retries for collecting results from VMs |
| `--collect-retry-delay` | `20` | Delay (seconds) between collection retries |
| `--collect-concurrency` | `5` | Max concurrent result collections |

### Output Options

| Option | Default | Description |
|--------|---------|-------------|
| `--save-results` | `false` | Save results to JSON/CSV files |
| `--results-dir` | `results` | Base directory for results |
| `--cleanup` | `false` | Delete VMs and namespaces after test |

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
results/{timestamp}_fio_benchmark_{start}-{end}/
├── aggregated_results.json    # Summary across all VMs
├── aggregated_results.csv     # CSV format
└── per-vm-results/            # Individual VM results
    ├── fio-benchmark-1/
    │   └── fio_raw.json
    └── ...
```

### View in Dashboard

```bash
cd dashboard
python3 generate_dashboard.py --base-dir ../results
open output/dashboard.html
```

The dashboard displays:

- KPI summary cards (Total IOPS, Bandwidth, Latency)
- IOPS + Latency dual-axis chart
- Per-VM breakdown with clickable namespaces

