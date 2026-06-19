# Disk Operations Benchmark

Tests disk hotplug and coldplug performance for KubeVirt VMs.

**Use Case**: Measure disk attach/detach times for running and stopped VMs with in-VM validation.

## Operations

| Operation | Description | VM State |
|-----------|-------------|----------|
| `hotplug` | Attach disk to running VM | Running |
| `coldplug` | Attach disk to stopped VM, then start | Stopped |
| `all` | Run both hotplug and coldplug | Both |

## Basic Usage

### virtbench CLI

```bash
# Create VMs and hotplug 3 disks
virtbench disk-ops --start 1 --end 10 --storage-class px-csi --disks 3 \
  --create-vms --save-results

# Coldplug test (creates VMs, stops them, attaches disk, starts them)
virtbench disk-ops --start 1 --end 5 --storage-class px-csi \
  --operation coldplug --create-vms --save-results

# All operations with unplug test and cleanup
virtbench disk-ops --start 1 --end 10 --storage-class px-csi \
  --operation all --create-vms --test-unplug --cleanup
```

### Python Script

```bash
cd disk-ops-benchmark

python3 measure-disk-ops.py \
  --start 1 --end 10 \
  --storage-class px-csi \
  --operation hotplug \
  --disks 3 \
  --create-vms \
  --save-results
```

## Validation Modes

In-VM validation runs `lsblk` over SSH to confirm each disk is visible inside the
guest. SSH goes through a **persistent helper pod** (`--ssh-pod`, default
`ssh-test-pod` in the `default` namespace) that has `sshpass` installed — the pod
is auto-created if it doesn't exist and reused across runs. Authentication is
password-based (`--vm-user` / `--vm-password`).

- **`--create-vms`** — VMs are provisioned with `--vm-password` baked into their
  cloud-init, so validation works out of the box.
- **Pre-existing VMs** — pass `--vm-user` / `--vm-password` matching the password
  those VMs already accept (and ensure their `sshd` allows password auth).
- **`--skip-validation`** — skip the in-VM SSH check entirely and rely only on
  API-level volume status. No helper pod is created.

## Configuration Options

### Operation Settings

| Option | Default | Description |
|--------|---------|-------------|
| `--start` | (required) | Start namespace index |
| `--end` | (required) | End namespace index |
| `--storage-class` | (required) | Storage class for PVCs |
| `--operation` | `all` | Operation: `hotplug`, `coldplug`, `all` |
| `--disks` | `1` | Number of disks per VM |
| `--disk-size` | `10Gi` | Disk size |
| `--test-unplug` | `false` | Also test disk removal |

### VM Settings

| Option | Default | Description |
|--------|---------|-------------|
| `--namespace-prefix` | `disk-ops` | Namespace prefix |
| `--vm-name` | `disk-ops-vm` | VM name |
| `--vm-template` | `disk-ops-benchmark/vm-template.yaml` | VM template file (used with `--create-vms`) |
| `--vm-user` | `cloud-user` | VM SSH user for in-VM validation |
| `--vm-password` | `changeme` | VM SSH password for in-VM validation |
| `--ssh-pod` | `ssh-test-pod` | Persistent SSH helper pod with `sshpass` (auto-created if missing) |
| `--ssh-pod-ns` | `default` | SSH helper pod namespace |
| `--create-vms` | `false` | Create VMs before testing |

### Execution Settings

| Option | Default | Description |
|--------|---------|-------------|
| `--concurrency` | `10` | Max concurrent operations |
| `--attach-timeout` | `300` | Disk attach timeout (seconds) |
| `--skip-validation` | `false` | Skip in-VM validation |

### Output Settings

| Option | Default | Description |
|--------|---------|-------------|
| `--results-dir` | `results` | Base results directory |
| `--save-results` | `false` | Save results to JSON/CSV |
| `--px-version` | `px-unknown` | Storage driver/version label for the results folder |
| `--disk-type` | `1-disk` | Disk type label for the results folder |
| `--cleanup` | `false` | Remove hotplugged disks (and created VMs) after test |

## Metrics

### Hotplug Metrics

| Metric | Description |
|--------|-------------|
| `api_attach_time` | Time for `virtctl addvolume` API call |
| `volume_ready_time` | Time for volume to appear in VMI status |
| `validation_time` | Time to verify disk in VM via SSH |
| `total_time` | End-to-end hotplug time |

### Coldplug Metrics

| Metric | Description |
|--------|-------------|
| `api_attach_time` | Time to attach disk to stopped VM |
| `vm_boot_time` | Time for VM to boot with new disk |
| `validation_time` | Time to verify disk in VM via SSH |
| `total_time` | End-to-end coldplug time |

## Validation

The benchmark validates disk attachment at two levels:

1. **API Level**: Checks volume appears in `vmi.status.volumeStatus`
2. **VM Level**: SSH into VM and verify disk via `lsblk`

Validation ensures the disk is actually usable inside the VM, not just attached at the API level.

## Results

### Output Structure

```
results/{px-version}/{disk-type}/{timestamp}_disk_ops_benchmark_{start}-{end}/
├── disk_ops_results.json    # Full results with per-VM data
└── disk_ops_summary.csv     # CSV summary
```

The `{px-version}` and `{disk-type}` segments come from `--px-version` and
`--disk-type`, keeping the layout compatible with the results dashboard.

### Sample Output

```
======================================================================
DISK OPERATIONS BENCHMARK RESULTS
======================================================================

HOTPLUG:
  Success Rate:      10/10 (100.0%)
  Disks Attached:    30
  Disks Validated:   30
  Avg API Time:      1.25s
  Avg Volume Ready:  3.50s
  Avg Total Time:    7.20s

COLDPLUG:
  Success Rate:      10/10 (100.0%)
  Disks Attached:    30
  Disks Validated:   30
  Avg API Time:      0.80s
  Avg VM Boot Time:  45.00s
  Avg Total Time:    52.30s
======================================================================
```

### View in Dashboard

```bash
cd dashboard
python3 generate_dashboard.py --base-dir ../results
open output/dashboard.html
```

The dashboard shows:
- KPI cards (Hotplug/Coldplug avg time, success rate)
- Time breakdown chart (API, Volume Ready/Boot, Validation)
- Per-VM results table with validation status
