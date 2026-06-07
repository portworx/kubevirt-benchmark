# VM Operations

The `virtbench vm-ops` command group bundles day-2 VM lifecycle operations
used during benchmarking and cluster validation. Each operation is a thin
Click wrapper around a script in `vm-ops/` that you can also invoke directly.

**Use Case**: Drain nodes, rebalance VMs across hosts, snapshot VMs in
batches, run `blkdiscard` inside guests, and power VMs on or off — all
without leaving the unified CLI.

## Available Operations

| Operation | Purpose |
| --- | --- |
| [`drain-nodes`](drain-nodes.md) | Drain Kubernetes nodes and measure drain time |
| [`rebalance-vms`](rebalance-vms.md) | Rebalance VMs evenly across worker nodes |
| [`vm-snapshot`](vm-snapshot.md) | Create `VirtualMachineSnapshots` in batches |
| [`run-blkdiscard`](run-blkdiscard.md) | Run `blkdiscard` on data disks inside VMs |
| [`power-toggle-vms`](power-toggle-vms.md) | Power VMs on or off (`--action {on,off}`) |

## Quick Reference

```bash
# Drain two nodes in parallel
virtbench vm-ops drain-nodes --nodes worker-1 worker-2 --parallel

# Rebalance the cluster (dry-run)
virtbench vm-ops rebalance-vms --vm-name rhel-elbencho-1 --dry-run

# Snapshot 50 VMs in batches of 25, 5 minutes apart
virtbench vm-ops vm-snapshot \
  --namespace-prefix migration --start 1 --end 50 \
  --vm-name rhel-9-vm --batch-size 25 --interval 300

# Run blkdiscard on auto-detected data disks
virtbench vm-ops run-blkdiscard \
  --namespace-prefix rhel-eb-filler --start 1 --end 10 \
  --vm-name rhel-elbencho-1

# Power off 50% of running VMs on a node
virtbench vm-ops power-toggle-vms \
  --action off --node worker-1 --percentage 50

# Power them back on from the saved list
virtbench vm-ops power-toggle-vms \
  --action on --vm-list-file powered_off_vms_worker-1_<ts>.txt
```

## Common Patterns

### Discovery Modes

Most operations support two ways of selecting target VMs:

* **Range mode** — `--namespace-prefix <prefix> --start <i> --end <j>` plus
  `--vm-name <name>`. The CLI iterates `prefix-i` through `prefix-j` and
  acts on the named VM in each namespace.
* **Node mode** — `--node <node>` discovers VMs running on a given worker.
  Only available where the running VMI is bound to a node (for example,
  `power-toggle-vms --action off`).

### Concurrency

All long-running operations expose `--concurrency` (a few use `-c` as
shorthand) so you can dial parallelism up or down per operation. Sensible
defaults are wired into each script.

### Dry-Run

Every operation supports `--dry-run`. Use it to preview the work without
mutating cluster state.

### Logging

`drain-nodes`, `vm-snapshot`, and `run-blkdiscard` accept
`--log-file <path>` and the global `virtbench --log-file <path>`. When
neither is provided, a timestamped log file is generated automatically.

`rebalance-vms` and `power-toggle-vms` print to stdout/stderr and do not
write a log file.

## Prerequisites

* `kubectl` (or `oc`) on `$PATH`, with a working kubeconfig.
* `virtctl` (or the `kubectl virt` Krew plugin) for VM power operations.
* For `run-blkdiscard`: an SSH-capable pod in the cluster (default
  `ssh-test-pod` in the `default` namespace) with network access to the
  target VMs.

## See Also

* [Cleanup Guide](../../cleanup-guide.md) — clean up VMs/PVCs/namespaces after
  tests.
* [Migration Testing](../migration.md) — pairs well with
  `drain-nodes` and `power-toggle-vms` for evacuation scenarios.
