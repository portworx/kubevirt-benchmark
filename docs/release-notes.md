# Release Notes

## Unreleased

### FIO benchmark

- **Balanced VM placement (default).** FIO VMs are pinned evenly across the
  eligible nodes and the placement is verified and saved with the results
  (`vm_node_placement.json`, `placement` in the summary, `node` CSV column).
  Nodes can be excluded with a label (`virtbench.io/no-fio`, or
  `--exclude-node-label`), by name (`--exclude-nodes`), or chosen with
  `--node-selector`. `--no-balance` restores scheduler placement.
- **4 vCPUs per FIO VM.** The shipped FIO templates now use 4 vCPUs with a CPU
  request of 4 (was 2 vCPUs, request 1). This needs about 4x the CPU per VM;
  balanced placement checks free capacity before deploying.
  `--vm-cpus` now defaults to 4 for any template (instancetype templates keep
  their own size); `--vm-cpus 2 --vm-cpu-request 1` reproduces the old size.
- **Failed FIO runs are no longer reported as successful.** A VM now fails
  when FIO exits nonzero, any FIO job reports a nonzero `error`, or the output
  has no jobs. The guest templates record the FIO exit status in
  `/tmp/fio_exit_code`. Failed VMs are excluded from the metrics, listed under
  `failures` in the summary, and make `gather-results` and `run-all` exit
  with status 1.
- `deploy` and `run-all` exit with status 1 when a VM fails to apply or
  balanced placement is not achieved.
- The FIO CSV gains a `node` column at the end. The summary records
  `vm_cpus` and `vm_cpu_request`, and the dashboard shows them.

## v2.0.0

This is a major release that introduces a unified `virtbench` CLI, four new
test scenarios (Chaos, FIO, Elbencho, Disk Operations), a `vm-ops` day-2
operations command group, a rewritten failure-recovery test, an optional
AI-assisted workflow for adaptive validation runs, and a full documentation
site.

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
- `virtbench disk-ops` — disk hotplug/coldplug benchmark
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

#### New: FIO Storage Benchmark

`virtbench fio` runs FIO inside multiple VMs in parallel to measure storage
IOPS, bandwidth, and latency, with configurable block size, I/O pattern,
I/O depth, job count, and runtime. The workflow stages (`deploy`, `status`,
`gather-results`, `cleanup`) can run individually or together with `run-all`.
See the [FIO](reference/user-guide/test-scenarios/fio-benchmark.md) guide.

#### New: Elbencho Storage Benchmark

`virtbench elbencho` runs the elbencho storage micro-benchmark across
multiple VMs to measure file-system level throughput and latency, paired
with the FIO scenario.

#### New: Disk Operations Benchmark

`virtbench disk-ops` measures disk hotplug (attach to a running VM) and
coldplug (attach to a stopped VM, then start it) times, with optional in-VM
validation that the new disks are visible. See the
[Disk Operations](reference/user-guide/test-scenarios/disk-ops-benchmark.md)
guide.

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

#### New: Optional AI-Assisted Workflow

An optional VirtBench skill for Codex or Claude Code turns natural-language
prompts, such as
`@virtbench vm-create, bootstorm and migrate 4 rounds max VMs 120`,
into adaptive validation runs. The skill file and its installer are not
shipped in the repository; the AI-assisted workflow guide includes both for
you to copy and install.

The deterministic engine lives in the new `adaptive/` package
(`python -m adaptive.planner`). The planner, not the language model, estimates
a VM ceiling from node allocatable resources and current pod requests (15%
headroom by default), spreads VM totals across the requested rounds, and runs
existing `virtbench` commands. An optional max-VM cap is reported as
user-capped when the best stable result reaches it.

- Adaptive scenarios: VM creation, boot storm, migration, the combined
  create, boot storm, rebalance and migrate workflow, FIO max-IOPS and
  max-bandwidth searches, and disk operations.
- Assisted scenarios: Elbencho, chaos, failure recovery, and cluster
  validation run through the skill without adaptive planning.
- Each run writes `virtbench_ai_rounds.json` and a JSON and Markdown analysis
  under `<results-folder>/ai-runs/`, and the results dashboard shows the round
  decisions next to the matching results.

See the [Optional AI-Assisted Workflow](reference/user-guide/ai-workflow.md) guide.

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
- New top-level `disk-ops-benchmark/` directory containing the disk
  hotplug/coldplug benchmark script.
- New top-level `adaptive/` package containing the planner and run analysis
  used by the optional AI-assisted workflow.
- Hotplug disk support removed from `vm-ops/`. Disk hotplug and coldplug
  testing is now provided by the dedicated `virtbench disk-ops` benchmark.

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
