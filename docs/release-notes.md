# Release Notes

## Unreleased

### KubeVirt FIO Storage-Profile Comparison

Added `virtbench fio --action compare-profiles`. It measures baseline,
IOThreads policies, per-disk dedicated IOThreads, virtio block multiqueue, and
a combined profile against the same VM, PVC, worker node, CPU allocation, and
FIO workload. Results include repeated-run medians, tail latency, baseline
deltas, and an IOPS-per-thread efficiency view.

`--workloads` runs several named I/O patterns (`randread-4k`, `randwrite-4k`,
`randrw-4k`, `seqread-128k`, `seqwrite-128k`) per profile, and `--recommend`
prints a ranked table per workload plus an overall recommendation with a
"Best for" label for each profile.

Each run also writes `profile_comparison_dashboard.html`: per-workload charts and
a table showing every profile's change against baseline, the settings actually
applied, and data-quality notes. `python3 -m virtbench.profile_dashboard` combines
several runs into one page. The dashboard reports measurements only and does not
rank or recommend profiles.

Profiles follow the KubeVirt "High-performance features" guide. Each profile is
verified against the running libvirt domain, so a setting KubeVirt silently
ignored is reported as failed. Dedicated IOThreads and block multiqueue are
refused on non-virtio (for example SCSI) benchmark disks, and the
IOPS-per-thread figure uses the IOThread count actually observed.

### CDI Root-Disk Strategy Benchmark

Added `virtbench cdi-root-disk`. It takes a golden PVC or PVC-backed
DataSource and a VM specification, provisions the root disk through
`csi-clone`, `snapshot`, and `copy`, and reports root-disk, VMI, and readiness
timings. Requested and observed CDI strategies are recorded separately so
fallbacks cannot be misreported as optimized clones.

### VirtualMachinePool Benchmark

Added `virtbench vm-pool`. It scales a pool from zero to a requested replica
count with a unique cloned root disk per VM and measures VM creation, root-disk
completion, 50%/90%/100% readiness, failures, and peak replica counts. An
optional `--recovery-delete` phase measures reconciliation after managed VMs
are deleted.

### Chaos Benchmark Data Volume Options

`virtbench chaos-benchmark` gains `--data-volume-access-mode` (`RWX` or `RWO`) and
`--data-volume-mode` (`Filesystem` or `Block`). The extra data volumes used to be
created ReadWriteOnce whatever `--vm-yaml` said, which left those VMs unable to
live-migrate. **The data volumes are now ReadWriteMany by default.** Storage classes
that cannot provision RWX volumes need `--data-volume-access-mode RWO` to keep the
previous behavior.

### FIO Result Collection

`virtbench fio` (`gather-results` and `run-all`) now collects results in rounds: every
VM is tried once per round and the batch waits `--collect-retry-delay` between rounds.
Previously each worker held its slot through all retries, so with more VMs than
`--collect-concurrency` the first VMs could use up every retry before the rest started
and fail while later VMs succeeded. A VM that is `Running` but has no IP yet is now
retried instead of failing at once, and the saved summary reports the FIO settings the
VMs actually ran (read from their output) rather than the command-line defaults.

### Results Dashboard

`virtbench dashboard` builds the results dashboard with no options: it reads
`results/` and writes `results_dashboard.html` in the repository root (`--days N`
limits the window). The page was redesigned with the virtbench
logo, light and dark themes, a run summary card, read/write split cards for FIO and
Elbencho, and per-operation cards for disk operations. Runs that saved no result
files are no longer listed. The same command also adds a **VM Pool** tab
(`virtbench vm-pool` runs) and writes `profile_comparison_dashboard.html` when
FIO profile comparisons exist.

## v2.0.0

This is a major release that introduces a unified `virtbench` CLI, three new
test scenarios (Chaos, FIO, Elbencho), a `vm-ops` day-2 operations command
group, a rewritten failure-recovery test, and a full documentation site.

### New Features

#### Unified `virtbench` CLI

A single Click-based entry point that wraps every benchmark and operation
in the suite. Subcommands:

- `virtbench datasource-clone` — VM creation / boot storm
- `virtbench migration` — live migration testing
- `virtbench chaos-benchmark` — concurrent chaos workload
- `virtbench failure-recovery` — node-failure recovery with FAR
- `virtbench fio` — FIO storage I/O benchmark
- `virtbench elbencho` — Elbencho storage benchmark
- `virtbench validate-cluster` — pre-flight cluster validation
- `virtbench vm-ops <op>` — day-2 VM operations group
- `virtbench version` — version information

Top-level options (`--log-level`, `--log-file`, `--kubeconfig`, `--timeout`,
`--uuid`) apply across all subcommands.

The `virtbench` CLI is the documented entry point for user workflows.
Scenario examples now use `virtbench` commands instead of direct Python
script execution.

#### New: Chaos Benchmark

`virtbench chaos-benchmark` runs concurrent chaos operations — VM creation, volume
resize, volume clone, VM restart, and snapshots — to stress-test cluster
resilience under mixed concurrent load.

#### New: Elbencho Storage Benchmark

`virtbench elbencho` runs the elbencho storage micro-benchmark across
multiple VMs to measure file-system level throughput and latency, paired
with the existing FIO scenario.

#### New: `vm-ops` Day-2 Operations Group

A dedicated command group for VM lifecycle operations used during
benchmarking and validation:

- `vm-ops drain-nodes` — drain Kubernetes nodes and measure drain time
- `vm-ops rebalance-vms` — rebalance VMs across worker nodes
- `vm-ops vm-snapshot` — create `VirtualMachineSnapshots` in batches
- `vm-ops run-blkdiscard` — run `blkdiscard` on data disks inside VMs
- `vm-ops power-toggle-vms` — power VMs on or off

Each operation is exposed through the `virtbench vm-ops` command group.

`vm-ops rebalance-vms` now targets worker nodes by default, computes an
automatic target range that handles small VM counts, and only includes
master/control-plane nodes when `--include-master-nodes` is provided.

#### New: Boot Storm Testing Scenario

Boot storm functionality (`--boot-storm` on `datasource-clone`) is now
documented as a first-class scenario with single-node, multi-node, and
existing-VM variants. See the dedicated [Boot Storm](reference/user-guide/test-scenarios/boot-storm.md) guide.

Creation and boot-storm result paths now use the storage driver label and
disk count instead of the storage class name:

```text
results/{storage-driver}/{num-disks}-disk/{timestamp}_{namespace-prefix}_{start}-{end}/
```

Run logs are saved in the same timestamped run folder as the JSON and CSV
result files.

#### Live Migration Updates

`virtbench migration` now exposes the migration modes needed for common
operator workflows, including auto-select-busiest node evacuation,
round-robin migration, interleaved scheduling, and multi-source-node
migration.

Multi-source-node migration accepts a comma-separated `--source-nodes`
value, for example:

```bash
virtbench migration --source-nodes worker-1,worker-2,worker-3
```

It discovers matching VMIs directly from the cluster, removes VM/VMI
`nodeSelector` settings before migration, and submits migrations in an
interleaved order across source nodes.

The migration guide now includes an end-to-end workflow covering cluster
validation, VM creation, boot storm, rebalance, and multi-source-node live
migration against the same VM set.

#### Results and Logging

Saved benchmark runs now keep logs, detailed results, and summary results
together inside the same run directory. Commands executed through
`virtbench` are logged with sensitive values redacted where applicable.

The public result grouping option is standardized as `--storage-driver`
across documented workflows. The previous `--storage-version` spelling is
removed from the user documentation.

#### Rewritten: Failure Recovery

`failure-recovery/recovery-test.py` replaces the previous
`measure-recovery-time.py` and the `run-far-test.sh` /
`run-manual-failure-test.sh` / `patch-vms.sh` shell scripts. The wrapper
exposes node-targeted recovery testing via `virtbench failure-recovery`,
including `--mode monitor`, `--mode manual`, and `--mode far-operator`.

#### Documentation Site

Full MkDocs Material site under `docs/`, including:

- Repository Structure reference
- Test Scenarios index with per-scenario guides
- VM Operations sub-section
- Configuration Options reference
- Output and Results, Cleanup Guide, VM Template Guide
- Best Practices and Troubleshooting

### Structural Changes

- I/O benchmarks consolidated under `io-benchmark/{fio,elbencho}/`. The
  legacy `fio-benchmark/measure-fio-performance.py` location is removed.
- Failure-recovery shell scripts removed in favour of a single Python
  test driver.
- New top-level `vm-ops/` directory containing the day-2 operation
  scripts.
- Hotplug disk support removed from `vm-ops/` and from documentation.

### Breaking Changes

- `fio-benchmark/measure-fio-performance.py` → `io-benchmark/fio/measure-fio-performance.py`
- `failure-recovery/measure-recovery-time.py`, `run-far-test.sh`,
  `run-manual-failure-test.sh`, `patch-vms.sh` → replaced by
  `failure-recovery/recovery-test.py`
- `--storage-version` documentation replaced with `--storage-driver`.
- Some default values changed for `datasource-clone` and `migration` —
  see [Configuration Options](reference/user-guide/configuration.md) for
  the per-flag defaults of both the wrapper and the underlying scripts.

### Notes

- The `virtbench` CLI is the recommended and documented entry point for
  benchmark workflows. User-facing examples use `virtbench` commands.
