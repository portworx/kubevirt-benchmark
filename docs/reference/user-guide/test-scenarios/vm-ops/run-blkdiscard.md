# Run blkdiscard

Run `blkdiscard` against data disks inside KubeVirt VMs to release blocks
back to thin-provisioned storage.

**Use Case**: Reclaim space after destructive write tests (FIO, elbencho),
verify TRIM/UNMAP plumbing through the storage stack, or normalize a test
fleet before a fresh run.

## How It Works

For each VM in the namespace range the operation:

1. Connects to the VM through an SSH bastion pod
   (`--ssh-pod` in `--ssh-pod-ns`).
2. Auto-detects data disks (every block device except the OS disk) — or
   uses the explicit list from `--disks`.
3. Runs `blkdiscard /dev/<disk>` on each target disk.
4. Logs success / failure per disk.

## Basic Usage

### Using virtbench CLI

```bash
# Auto-detect data disks across 10 VMs
virtbench vm-ops run-blkdiscard \
  --namespace-prefix rhel-eb-filler \
  --start 1 --end 10 \
  --vm-name rhel-elbencho-1

# Target specific disks
virtbench vm-ops run-blkdiscard \
  --namespace-prefix rhel-eb-filler \
  --start 1 --end 10 \
  --vm-name rhel-elbencho-1 \
  --disks vdb --disks vdc

# Higher concurrency
virtbench vm-ops run-blkdiscard \
  --namespace-prefix rhel-eb-filler \
  --start 1 --end 50 \
  --vm-name rhel-elbencho-1 \
  --concurrency 25
```


## Full Example with All Options

```bash
virtbench vm-ops run-blkdiscard \
  --namespace-prefix rhel-eb-filler \
  --start 1 \
  --end 50 \
  --vm-name rhel-elbencho-1 \
  --disks vdb --disks vdc --disks vdd \
  --ssh-pod ssh-test-pod \
  --ssh-pod-ns default \
  --vm-user root \
  --vm-password Password1 \
  --concurrency 25 \
  --log-file blkdiscard-2026-05-08.log
```

## Options

| Option | Description |
| --- | --- |
| `--namespace-prefix` *(required)* | Namespace prefix (e.g., `rhel-eb-filler`). |
| `--start` *(required)* | Start namespace index. |
| `--end` *(required)* | End namespace index. |
| `--vm-name` *(required)* | VM name in each namespace. |
| `--disks` *(repeatable)* | Specific disks (e.g., `vdb`, `vdc`). Auto-detects all data disks if omitted. |
| `--ssh-pod` | SSH bastion pod name (default: `ssh-test-pod`). |
| `--ssh-pod-ns` | SSH pod namespace (default: `default`). |
| `--vm-user` | VM SSH user (default: `root`). |
| `--vm-password` | VM SSH password (default: `Password1`). |
| `--concurrency` | Max concurrent operations (default: 10). |
| `--dry-run` | Show what would be done without doing it. |
| `--log-file` | Path to a log file (auto-generated if omitted). |

## Prerequisites

* An SSH bastion pod is running in the cluster and reachable from the
  control-plane host. The pod must have `ssh` and `sshpass` available.
* The target VMs accept the `--vm-user` / `--vm-password` credentials.
* Each target disk is *not* mounted (or `blkdiscard` will refuse). Stop or
  unmount the filesystem before running this operation.

## Notes

* `blkdiscard` is destructive — it issues TRIM/UNMAP, which on most storage
  backends marks blocks as discarded. Do not run this against disks that
  hold data you want to keep.
* When omitting `--disks`, the operation skips the OS disk
  (typically `vda`).
* For large fleets, increase `--concurrency` cautiously — each parallel
  run opens a new SSH connection through the bastion pod.

## See Also

* [Cleanup Guide](../../cleanup-guide.md)
