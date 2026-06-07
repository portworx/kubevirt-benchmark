# Elbencho Storage I/O Benchmark

Manages [elbencho](https://github.com/breuner/elbencho) storage benchmark workloads on VMs across many namespaces. Supports deploying VMs, running rate-limited or max-throughput workloads, and gathering aggregated results.

**Use Case**: Sustain controlled IO load (constant IOPS or read/write mix at line rate) across many VMs and measure aggregated IOPS, throughput, and latency.

## How It Works

Each VM runs an `elbencho` systemd service against one or more block devices. The script drives the workload by SSHing into each VM through a helper pod and supports two workload modes:

- **IOPS mode** (`--iops`) - Fixed total IOPS, split 50/50 between read and write.
- **RWMIX mode** (`--rwmixpct`) - Maximum throughput at a specified read/write ratio.

The full workflow (`run-all`) deploys VMs, starts the workload, waits for `--duration` seconds, gathers results, and optionally cleans up.

## Prerequisites

- `kubectl` configured against the target cluster.
- An SSH helper pod with `sshpass` deployed in the cluster (default: `ssh-test-pod` in `default`).
- A VM template whose image has `elbencho` installed at `/root/elbencho/bin/elbencho` and a configured systemd service. Use any KubeVirt `VirtualMachine` template that exposes the disks you want to drive.
- The VM image must allow SSH login as the `root` user with password authentication (default credentials: `--vm-user root` / `--vm-password Password1`), and `root` must be able to run `elbencho` directly (the binary on `PATH` or invokable at `/root/elbencho/bin/elbencho`).

## Basic Usage

### virtbench CLI

```bash
# Full workflow: deploy 10 VMs, run for 5 minutes, gather + save results
virtbench elbencho \
  --namespace-prefix perf-test --start 1 --end 10 \
  --vm-name rhel-elbencho-1 --action run-all \
  --vm-template examples/vm-templates/YOUR-ELBENCHO-VM.yaml \
  --iops 1000 --block-size 4K --duration 300 \
  --save-results --storage-driver portworx-3.6

# RWMIX mode: 70% read at max throughput for 10 minutes
virtbench elbencho \
  --namespace-prefix perf-test --start 1 --end 10 \
  --vm-name rhel-elbencho-1 --action run-all \
  --vm-template examples/vm-templates/YOUR-ELBENCHO-VM.yaml \
  --rwmixpct 70 --block-size 32K --iodepth 2 --threads 12 \
  --duration 600 --save-results
```

## Step-by-Step Workflow

```bash
# 1. Deploy VMs
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 -a deploy \
  --vm-template examples/vm-templates/YOUR-ELBENCHO-VM.yaml

# 2. Check status
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 -a status

# 3. Start a workload (1000 IOPS total, 4K blocks)
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \
  -a change-workload --iops 1000 --block-size 4K

# 4. Switch workload mid-run (70% read at max throughput)
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \
  -a change-workload --rwmixpct 70 --block-size 32K --threads 12

# 5. Stop IO and gather results
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \
  -a gather-results --save-results --storage-driver portworx-3.6

# 6. Cleanup VMs and namespaces
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 -a cleanup
```

## Actions

| Action | Description |
|--------|-------------|
| `run-all` | Full workflow: deploy + workload + wait + gather (requires `--duration`) |
| `deploy` | Deploy VMs (requires `--vm-template`) |
| `start` | Start the default 1 IOPS systemd service |
| `stop` | Stop the systemd service |
| `restart` | Restart the systemd service |
| `status` | Check elbencho service status |
| `stop-all` | Stop all elbencho processes (service + manual) |
| `change-workload` | Stop current workload and start a new one |
| `gather-results` | Stop IO and collect results from all VMs |
| `cleanup` | Delete VMs and namespaces |

## Configuration Options

### Required

| Option | Description |
|--------|-------------|
| `--namespace-prefix`, `-p` | Namespace prefix (e.g., `perf-test` &rarr; `perf-test-1`, `perf-test-2`, ...) |
| `--start`, `-s` | Start namespace index |
| `--end`, `-e` | End namespace index |
| `--vm-name`, `-n` | VM name in each namespace |
| `--action`, `-a` | Action to perform (see table above) |

### Deploy / run-all

| Option | Default | Description |
|--------|---------|-------------|
| `--vm-template` | (required for `deploy`/`run-all`) | Path to VM template YAML |
| `--secret-yaml` | (none) | Path to cloud-init secret YAML |
| `--ping-timeout` | `300` | Seconds to wait for VMs to become reachable |

### Workload

| Option | Default | Description |
|--------|---------|-------------|
| `--iops` | `0` | IOPS mode: total IOPS (must be even, split 50/50 read/write). Mutually exclusive with `--rwmixpct`. |
| `--rwmixpct` | `0` | RWMIX mode: read percentage (0-100), max throughput |
| `--block-size` | `4K` | Block size (e.g., `4K`, `32K`, `1M`) |
| `--num-disks` | `0` | Number of disks (0 = all available) |
| `--iodepth` | `1` | IO depth per thread |
| `--threads` | `0` | Threads per VM (0 = same as `num-disks`) |
| `--duration` | `0` | Duration in seconds (0 = infinite; **required** for `run-all`) |

### Results

| Option | Default | Description |
|--------|---------|-------------|
| `--save-results` | `false` | Save aggregated JSON summary |
| `--results-dir` | `./results` | Base directory for results |
| `--storage-driver` | `Not-Specified` | Storage driver label (folder component) |
| `--disks-per-vm` | `auto` | Disks-per-VM label (folder component); auto-detected from VM spec |
| `--run-name` | (auto) | Override run folder name (default: `{timestamp}_elbencho_{N}vms`) |
| `--output-dir` | (none) | **Deprecated**: full output path that bypasses the structured layout |

### SSH

| Option | Default | Description |
|--------|---------|-------------|
| `--ssh-pod` | `ssh-test-pod` | SSH helper pod name (must have `sshpass`) |
| `--ssh-pod-ns` | `default` | SSH helper pod namespace |
| `--vm-user` | `root` | VM SSH user |
| `--vm-password` | `Password1` | VM SSH password |

### Execution

| Option | Default | Description |
|--------|---------|-------------|
| `--concurrency`, `-c` | `20` | Max concurrent operations |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-file` | (auto) | Log file path. With `--save-results`, defaults to `elbencho_<action>_<timestamp>.log` inside the run result folder. |


## Workload Mode Examples

```bash
# IOPS mode: 2000 total IOPS (1000 read + 1000 write), 8K blocks, 4 disks
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \
  -a change-workload --iops 2000 --block-size 8K --num-disks 4

# IOPS mode: 500 IOPS for 5 minutes
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \
  -a change-workload --iops 500 --block-size 4K \
  --threads 4 --iodepth 2 --duration 300

# RWMIX mode: 100% read at max throughput
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \
  -a change-workload --rwmixpct 100 --block-size 64K \
  --iodepth 4 --threads 8

# RWMIX mode: 70% read / 30% write at max throughput
virtbench elbencho -p perf-test -s 1 -e 10 -n rhel-elbencho-1 \
  -a change-workload --rwmixpct 70 --block-size 32K \
  --iodepth 2 --threads 12 --num-disks 2
```

## Results

`gather-results` (and `run-all`) produce per-VM raw output plus an aggregated JSON summary:

- **IOPS** - total / read / write
- **Throughput** - total / read / write (bytes/sec)
- **Latency** - average / min / max (microseconds)

### Output Structure

```
{results-dir}/{storage-driver}/{disks-per-vm}/{run-name}/
├── aggregated_results.json        # Summary across all VMs
├── elbencho_gather.log            # Execution log
└── perf-test-1/                   # Per-VM raw output
    ├── rwmix_<timestamp>.json
    ├── rwmix_<timestamp>.txt
    ├── rwmix_<timestamp>.csv
    └── rwmix_<timestamp>_live.csv
```

Default `run-name`: `{timestamp}_elbencho_{N}vms`.

### Aggregated JSON

```json
{
  "timestamp": "2026-03-13T16:39:23.123456",
  "vms_with_results": 10,
  "vms_skipped": 0,
  "elapsed_seconds": 15.61,
  "aggregated": {
    "total_iops": 45000,
    "read_iops": 31500,
    "write_iops": 13500,
    "total_throughput_bytes": 1474560000,
    "read_throughput_bytes": 1032192000,
    "write_throughput_bytes": 442368000,
    "avg_latency_us": 4500,
    "min_latency_us": 500,
    "max_latency_us": 50000
  },
  "per_vm_results": [ ... ]
}
```

### View in Dashboard

Generate the dashboard from the saved results directory after the run.

## Troubleshooting

**No JSON files generated.** elbencho only writes JSON output when stopped gracefully via SIGINT. If files are missing, rerun `gather-results` with `--log-level DEBUG` and verify the elbencho process responds to signals.

**VMs skipped in aggregation.** A VM is skipped if no elbencho process is running, the VM IP cannot be retrieved, or SSH fails. Check `virtbench elbencho ... -a status` first.

**Connection issues.** Confirm the SSH helper pod is running (`kubectl get pod ssh-test-pod -n default`), the VMs are reachable from that pod, and `--vm-user` / `--vm-password` are correct.
