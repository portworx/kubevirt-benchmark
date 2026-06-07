# Release Notes

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
exposes node-targeted recovery testing via `virtbench failure-recovery
--node <name>`.

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
