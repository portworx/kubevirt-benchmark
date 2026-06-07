# VM Snapshot

Create `VirtualMachineSnapshot` objects across many namespaces in
configurable batches, with a fixed delay between batches to throttle
load on the snapshot controller.

**Use Case**: Snapshot large fleets of VMs at controlled rates, or measure
snapshot performance under burst conditions.

## How It Works

For each VM in the namespace range the operation:

1. Builds a `VirtualMachineSnapshot` named
   `<snapshot-prefix>-<timestamp>-<index>`.
2. Submits up to `--batch-size` snapshots concurrently (bounded by
   `--concurrency`).
3. Sleeps `--interval` seconds between batches.
4. Records per-snapshot success/failure and total run time in the log.

## Basic Usage

### Using virtbench CLI

```bash
# Snapshot 50 VMs in default 50-VM batches, 15 minutes apart
virtbench vm-ops vm-snapshot \
  --namespace-prefix migration \
  --start 1 --end 50 \
  --vm-name rhel-9-vm

# Snapshot in smaller batches with a tighter interval
virtbench vm-ops vm-snapshot \
  --namespace-prefix migration \
  --start 1 --end 100 \
  --vm-name rhel-9-vm \
  --batch-size 25 \
  --interval 300

# Custom snapshot prefix and concurrency
virtbench vm-ops vm-snapshot \
  --namespace-prefix perf-test \
  --start 1 --end 200 \
  --vm-name rhel-9-vm \
  --snapshot-prefix nightly-snap \
  --concurrency 25
```


## Full Example with All Options

```bash
virtbench vm-ops vm-snapshot \
  --namespace-prefix migration \
  --start 1 \
  --end 200 \
  --vm-name rhel-9-vm \
  --batch-size 50 \
  --interval 600 \
  --concurrency 50 \
  --snapshot-prefix bench-snap \
  --log-file vm-snapshot-2026-05-08.log
```

## Options

| Option | Description |
| --- | --- |
| `--namespace-prefix` *(required)* | Namespace prefix (e.g., `perf-test`). |
| `--start` *(required)* | Start namespace index. |
| `--end` *(required)* | End namespace index. |
| `--vm-name` *(required)* | VM name in each namespace. |
| `--batch-size` | VMs per batch (default: 50). |
| `--interval` | Seconds between batches (default: 900). |
| `--concurrency` | Max concurrent snapshot operations within a batch (default: 50). |
| `--snapshot-prefix` | Snapshot name prefix (default: `vm-snap`). |
| `--dry-run` | Show what would be done without doing it. |
| `--log-file` | Path to a log file (auto-generated if omitted). |

## Output

The log captures, per batch:

* Batch number, size, and start time
* Per-VM snapshot creation result
* Wait time before the next batch
* Final summary with success / failure counts and total elapsed time

## Notes

* Snapshots are stored as `VirtualMachineSnapshot` resources; they are not
  deleted automatically. Use `kubectl delete vmsnapshot -n <ns> --all` or
  the [Cleanup Guide](../../cleanup-guide.md) to remove them.
* `--interval` is wall-clock — the script does not subtract the time spent
  inside a batch.
* For sustained load tests, set `--batch-size` and `--concurrency` to the
  same value so each batch is fully parallel.

## See Also

* [Cleanup Guide](../../cleanup-guide.md) — for removing snapshots after the
  test.

