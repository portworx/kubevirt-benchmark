# Power Toggle VMs

Power VMs on or off in bulk, with `--action {on, off}` selecting the
direction. Designed to be symmetric: a single power-off run produces a
snapshot file that a subsequent power-on run can consume to restore the
exact same set of VMs.

**Use Case**: Free up node capacity before a benchmark, simulate partial
node failure, or repeatedly power-cycle subsets of a fleet.

## How It Works

### `--action off`

1. Lists all *running* VMIs on `--node`.
2. Randomly samples `--percentage` of them.
3. Saves the selection to
   `powered_off_vms_<node>_<YYYYMMDD_HHMMSS>.txt` (one
   `namespace/name` per line).
4. Issues `virtctl stop` (or `kubectl virt stop`) to every selected VM in
   parallel.
5. Waits up to `--wait-timeout` for each VMI to disappear or reach
   `Succeeded`/`Failed`.

### `--action on`

Powered-off VMs do not have a `nodeName` on their VMI (they have no VMI at
all), so node-only discovery is not possible. You must specify a namespace
range or a list file:

* **Range mode** — iterate `<namespace-prefix>-<i>` for `i` in
  `[start, end]`, looking for `--vm-name` in each namespace. Optionally
  filter by `--node` (matches the VM's
  `spec.template.spec.nodeSelector.kubernetes.io/hostname`).
* **List-file mode** — read `namespace/name` lines from `--vm-list-file`
  (typically the file produced by a previous `--action off` run).

The command then issues `virtctl start` to every target in parallel and
waits for each VMI to reach `Running`.

## Basic Usage

### Using virtbench CLI

```bash
# Power off 50% of running VMs on a node
virtbench vm-ops power-toggle-vms \
  --action off --node worker-1 --percentage 50

# Power them back on from the saved list
virtbench vm-ops power-toggle-vms \
  --action on --vm-list-file powered_off_vms_worker-1_20260508_120000.txt

# Power on a namespace range (no list file required)
virtbench vm-ops power-toggle-vms \
  --action on \
  --namespace-prefix migration --start 1 --end 50 \
  --vm-name rhel-9-vm

# Power on only those VMs whose nodeSelector pins them to worker-1
virtbench vm-ops power-toggle-vms \
  --action on \
  --namespace-prefix migration --start 1 --end 50 \
  --vm-name rhel-9-vm \
  --node worker-1
```


## Full Example

```bash
# Aggressive power-off: 75% of node, higher concurrency
virtbench vm-ops power-toggle-vms \
  --action off \
  --node worker-2 \
  --percentage 75 \
  --concurrency 100 \
  --wait-timeout 600
```

```bash
# Symmetric power-on from the same list, dry-run first
virtbench vm-ops power-toggle-vms \
  --action on \
  --vm-list-file powered_off_vms_worker-2_20260508_120000.txt \
  --concurrency 100 \
  --wait-timeout 600 \
  --dry-run
```

## Options

| Option | Description |
| --- | --- |
| `--action` *(required)* | `on` or `off`. |
| `--node` | Required for `--action off` (unless `--vm-list-file`). Optional `nodeSelector` filter for `--action on`. |
| `--percentage` | Percentage of running VMs to power off (default: 50, `--action off` only). |
| `--namespace-prefix`, `--start`, `--end`, `--vm-name` | Range-based discovery for `--action on`. |
| `--vm-list-file` | File of `namespace/name` lines to act on (works with both actions). |
| `--concurrency` | Max concurrent operations (default: 50). |
| `--wait-timeout` | Timeout waiting for VMs to reach the target phase (default: 300s). |
| `--dry-run` | Show what would be done without doing it. |

## Output

The summary at the end of each run reports:

* VMs targeted
* `start` / `stop` commands sent vs. failed
* VMs that reached the target phase (`Running` for `on`, `Succeeded`/
  `Failed`/missing for `off`)
* Wall-clock breakdown (command-send time vs. wait time vs. total)

For `--action off`, the snapshot file path is also logged so you can feed
it back into `--action on` later.

## Notes

* `virtctl` (or the `kubectl virt` Krew plugin) must be on `$PATH`. The
  script tries the Krew plugin first, then the standalone binary.
* `--vm-list-file` lines look like `namespace/name`; blank lines and lines
  without a `/` are ignored.
* `--action off` always saves a snapshot file before issuing any stop
  commands. If the run is interrupted, you can still resume with
  `--action on --vm-list-file <that file>`.

## See Also

* [Drain Nodes](drain-nodes.md) — combine to evacuate workloads then drain.
* [Cleanup Guide](../../cleanup-guide.md)
