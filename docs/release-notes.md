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

Each operation is a thin Click wrapper around a script in `vm-ops/` that
can also be invoked directly.

#### New: Boot Storm Testing Scenario

Boot storm functionality (`--boot-storm` on `datasource-clone`) is now
documented as a first-class scenario with single-node, multi-node, and
existing-VM variants. See the dedicated [Boot Storm](reference/user-guide/test-scenarios/boot-storm.md) guide.

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

### Breaking Changes

- `fio-benchmark/measure-fio-performance.py` → `io-benchmark/fio/measure-fio-performance.py`
- `failure-recovery/measure-recovery-time.py`, `run-far-test.sh`,
  `run-manual-failure-test.sh`, `patch-vms.sh` → replaced by
  `failure-recovery/recovery-test.py`
- Some default values changed for `datasource-clone` and `migration` —
  see [Configuration Options](reference/user-guide/configuration.md) for
  the per-flag defaults of both the wrapper and the underlying scripts.

### Notes

- The `virtbench` CLI is the recommended entry point. The underlying
  scripts in `datasource-clone/`, `migration/`, `failure-recovery/`,
  `io-benchmark/`, `vm-ops/`, and `chaos-benchmark/` remain available for
  direct invocation and continue to expose script-only flags documented
  alongside each scenario.
