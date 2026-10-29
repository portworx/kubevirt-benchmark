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

`compare-profiles` is a separate, controlled experiment for KubeVirt storage
tuning. It provisions one VM and scratch volume, pins the VM to one node, then
restarts that same VM for each profile. Reusing the volume avoids comparing
different PVCs or running profiles concurrently against the backend.

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

# Compare KubeVirt high-performance storage profiles
virtbench fio --action compare-profiles \
  --storage-class YOUR-STORAGE-CLASS \
  --node-name YOUR-WORKER \
  --fio-rw randrw --fio-rwmixread 70 \
  --fio-runtime 60 --fio-ramp-time 15 \
  --profile-iterations 3

# Run several I/O patterns per profile and emit a ranked recommendation
virtbench fio --action compare-profiles \
  --storage-class YOUR-STORAGE-CLASS \
  --profiles baseline,iothreads-auto,block-multiqueue,combined \
  --workloads randread-4k,randwrite-4k,seqread-128k \
  --vm-cpus 8 \
  --recommend
```

!!! note "`--save-results` vs `--action gather-results`"
    `--save-results` is a **flag** that writes summary JSON/CSV files to the output directory. `--action gather-results` is an **action** that collects raw FIO output from VMs over SSH. Use them together (`--action gather-results --save-results`) to both fetch and persist.

## Configuration Options

### Action and Targeting

| Option | Default | Description |
|--------|---------|-------------|
| `--action`, `-a` | `run-all` | One of: `deploy`, `status`, `gather-results`, `cleanup`, `run-all`, `compare-profiles` |
| `--start`, `-s` | required except profile comparison | Starting namespace index |
| `--end`, `-e` | required except profile comparison | Ending namespace index |
| `--storage-class` | (required for `deploy`/`run-all`/`compare-profiles`) | Storage class name |
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
| `--fio-ramp-time` | `15` | Warm-up time excluded from every measured iteration |
| `--fio-rwmixread` | `70` | Read percentage for `randrw`/`rw` |

### KubeVirt Profile Comparison

The profiles exercise the settings in the KubeVirt user guide section
[High-performance features](https://kubevirt.io/user-guide/storage/disks_and_volumes/#high-performance-features).
The default comparison includes:

| Profile | KubeVirt configuration |
|---------|------------------------|
| `baseline` | No `ioThreadsPolicy`, `dedicatedIOThread`, or `blockMultiQueue` |
| `iothreads-shared` | `ioThreadsPolicy: shared`: one IOThread shared by all disks |
| `iothreads-auto` | `ioThreadsPolicy: auto`: disks are spread round-robin over a pool of up to 2x vCPUs |
| `iothreads-supplemental` | `ioThreadsPolicy: supplementalPool` with `supplementalPoolThreadCount` |
| `dedicated-iothread` | Shared policy plus `dedicatedIOThread: true` on the benchmark disk |
| `block-multiqueue` | `blockMultiQueue: true`: one virtio-blk queue per vCPU |
| `combined` | Shared policy, dedicated benchmark-disk IOThread, and block multiqueue |

| Option | Default | Description |
|--------|---------|-------------|
| `--profiles` | All profiles above | Comma-separated profiles to run |
| `--profile-iterations` | `3` | Measured repetitions per profile |
| `--vm-cpus` | `4` | Equal guest CPUs, CPU request, and CPU limit for every profile |
| `--node-name` | scheduler-selected | Pin all restarts to one worker; recommended for comparisons |
| `--supplemental-pool-threads` | `4` | Supplemental IOThread count |
| `--benchmark-disk` | `scratchdisk` | Disk receiving per-disk tuning and FIO traffic |
| `--workloads` | the `--fio-rw`/`--fio-bs` pattern | Comma-separated I/O patterns run against every profile (see below) |
| `--recommend` | off | Print ranked per-workload tables and an overall recommendation |
| `--keep-profile-resources` | `false` | Retain the isolated namespace for debugging |

#### Workload patterns and the ranked recommendation

`--workloads` runs each pattern against every profile in the same VM boot, so
all patterns see the same VM, volume, node, and CPU allocation:

| Workload | Pattern | Ranked by |
|----------|---------|-----------|
| `randread-4k` | 4K random read | IOPS |
| `randwrite-4k` | 4K random write | IOPS |
| `randrw-4k` | 4K random 70/30 read/write | IOPS |
| `seqread-128k` | 128K sequential read | MiB/s |
| `seqwrite-128k` | 128K sequential write | MiB/s |

Total measurement time is `profiles x workloads x iterations x (runtime +
ramp)`; the command prints this estimate before it starts. Without
`--workloads` the single pattern from `--fio-rw`/`--fio-bs` is used.

With `--recommend` the command prints one ranking per workload and then an
overall table:

```text
OVERALL RECOMMENDATION
Rank  Profile            Score   randread-4k    seqread-128k   Worst p99  Best for
1     combined            100%   173,254 IOPS   5,993 MiB/s    9.634ms    Database / latency-sensitive / Throughput-heavy / multi-vCPU
2     baseline             91%   153,252 IOPS   5,562 MiB/s    9.896ms    Dev / light workloads (defaults)
```

How the table is built, so the result can be audited rather than trusted:

- **Per-workload rank** orders profiles by the workload's primary metric
  (IOPS or MiB/s). Profiles within 1% of the leader are treated as tied and
  ordered by lower tail latency, so a noise-level IOPS gap cannot hide a clearly
  better p99.
- **Score** is the geometric mean, across workloads, of each profile's result
  relative to the best profile for that workload. 100% means best everywhere.
- **Best for** comes from fixed rules: lowest tail latency across workloads
  gives *Database / latency-sensitive*; the top score gives *Throughput-heavy /
  multi-vCPU*; the best IOPS per thread gives *Cost-efficient / few threads*; the
  baseline is the *Dev / light workloads* reference; anything else is *General
  production*.
- A profile that failed on any requested workload is listed as *Not ranked*
  instead of being compared on partial data.

The ranking is saved in `fio_profile_comparison.json` (under `recommendation`)
and `fio_profile_recommendation.csv`.

#### Dashboard

Each `compare-profiles` run writes `profile_comparison_dashboard.html` next to
its results. Open it in a browser (it loads Bootstrap and Chart.js from a CDN).
It reports what was run and how each profile compared with `baseline`; it does
not rank profiles or recommend one.

For each workload it shows the throughput of every profile (median bar with
each individual run as a dot, and a baseline reference line), the percentage
change from baseline for throughput and p99 latency, p99 latency by profile, and
a table with what each profile changes, what was actually applied in the VM,
medians, change vs baseline, run-to-run spread, runs, and IOPS per thread.
Changes are coloured green (higher throughput or lower latency) or red (the
opposite) only when they are larger than the run-to-run spread of the runs being
compared; smaller changes are grey. Runs with one iteration, a spread above 5%,
failed or unverified profiles, and a missing baseline are called out on the page.

To combine several runs (one tab per run), or to rebuild a page:

```bash
python3 -m virtbench.profile_dashboard --base-dir results --output-html profile_comparison_dashboard.html
python3 -m virtbench.profile_dashboard --run results/sc-fc/profile-comparison/20261020-120000 --run results/sc-iscsi/profile-comparison/20260923-081102
```

#### Requirements and checks

- **Block multiqueue needs an explicit CPU allocation.** KubeVirt ignores
  `blockMultiQueue` unless a specific CPU count is requested, so every profile
  sets the guest CPUs and an equal CPU request and limit from `--vm-cpus`.
- **The benchmark disk must be a virtio disk** for `dedicated-iothread`,
  `block-multiqueue`, and `combined`. KubeVirt does not support dedicated
  IOThreads on a SCSI bus, so the command stops with an error instead of
  measuring a profile that cannot apply.
- **Each profile is verified against the running VM.** After a profile's VM
  starts, VirtBench reads the libvirt domain from the `virt-launcher` pod
  (`virsh dumpxml`) and compares what QEMU is actually running (IOThread count,
  and the benchmark disk's IOThread and queue count) with what the
  profile asked for. A profile KubeVirt did not apply is reported as failed
  instead of being measured as if it were tuned. If the domain cannot be read, for
  example without `pods/exec` permission, the profile is still measured and
  marked unverified in the results.

The applied values are printed for each profile and saved under
`applied_configuration` in `fio_profile_comparison.json`. Two behaviours are
visible there that the settings alone do not predict: `iothreads-auto` creates
at most one IOThread per disk (not the full 2x vCPU pool), and
`iothreads-supplemental` creates its pool but may leave the benchmark disk
unassigned to any IOThread; VirtBench prints a note when that happens.

The comparison reports median IOPS, bandwidth, read/write p95, p99, and p99.9
latency, percentage change from `baseline`, IOPS coefficient of variation, and
IOPS per host-thread budget. Treat a high coefficient of variation as a noisy
run that needs longer runtimes, more repetitions, or a quieter backend.
The thread budget is the vCPU count plus the IOThreads observed in the running
domain, so profiles that create more IOThreads are charged for them.
Raw FIO JSON and iteration/summary CSV files are retained under
`{results-dir}/{storage-driver}/profile-comparison/{timestamp}/`.

### Result Collection

| Option | Default | Description |
|--------|---------|-------------|
| `--collect-retries` | `8` | Attempts per VM when collecting results. Every VM is tried once per round, so all VMs get the same number of attempts regardless of `--collect-concurrency` |
| `--collect-retry-delay` | `20` | Delay (seconds) between collection rounds |
| `--collect-concurrency` | `5` | Max VMs read at the same time within a round |
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
- **Latency**: Read/Write latency in milliseconds; profile comparisons include p95, p99, and p99.9

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
