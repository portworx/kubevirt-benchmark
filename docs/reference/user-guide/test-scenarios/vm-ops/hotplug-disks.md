# Hotplug Disks

Hotplug Portworx (or any CSI) DataVolumes onto running KubeVirt VMs using
`virtctl addvolume`, with optional automatic IO generation via elbencho.

**Use Case**: Stress-test hotplug paths, expand VM capacity at runtime, or
seed data disks before running benchmarks.

## How It Works

For each target VM, the operation:

1. Creates `--disk-count` `DataVolume` objects in the VM's namespace using
   `--storage-class` and `--volume-mode`.
2. Hotplugs each volume into the VM with `virtctl addvolume` (persisted to
   the VM spec when `--persist` is set).
3. Optionally launches `elbencho` IO against the new disks via an SSH pod
   when `--start-io` is provided.

## Discovery Modes

### Mode 1 — Namespace Range

Iterate `<namespace-prefix>-<i>` for `i` in `[start, end]`.

```bash
virtbench vm-ops hotplug-disks \
  --start 1 --end 10 \
  --namespace-prefix datasource-clone \
  --vm-name rhel-9-vm \
  --disk-count 2 --disk-size 10Gi \
  --storage-class YOUR-STORAGE-CLASS
```

### Mode 2 — Node-Based

Pick `--num-vms` running VMs from a given node.

```bash
virtbench vm-ops hotplug-disks \
  --node worker-1 --num-vms 5 \
  --disk-count 1 --disk-size 20Gi \
  --storage-class YOUR-STORAGE-CLASS
```

## Basic Hotplug

### Using virtbench CLI

```bash
# Hotplug one 10Gi disk to VMs in namespaces 1-10
virtbench vm-ops hotplug-disks \
  -s 1 -e 10 \
  --storage-class YOUR-STORAGE-CLASS

# Hotplug 2x20Gi disks (filesystem mode) onto 5 VMs of a node
virtbench vm-ops hotplug-disks \
  --node worker-2 --num-vms 5 \
  --disk-count 2 --disk-size 20Gi \
  --volume-mode Filesystem \
  --storage-class YOUR-STORAGE-CLASS
```

### Using Python Script

```bash
cd vm-ops

python3 hotplug-disks.py -s 1 -e 10 --disk-count 2 --disk-size 10Gi \
  --storage-class YOUR-STORAGE-CLASS
```

## Full Example with IO

```bash
virtbench vm-ops hotplug-disks \
  --start 1 --end 20 \
  --namespace-prefix migration \
  --vm-name rhel-9-vm \
  --disk-count 2 \
  --disk-size 10Gi \
  --storage-class px-raw-sc \
  --volume-mode Block \
  --persist \
  --concurrency 10 \
  --start-io \
  --ssh-pod ssh-test-pod \
  --ssh-pod-ns default \
  --vm-user root \
  --vm-password Password1
```

## Options

| Option | Description |
| --- | --- |
| `-s, --start`, `-e, --end` | Namespace index range (Mode 1). |
| `--namespace-prefix` | Namespace prefix (default: `datasource-clone`). |
| `--vm-name` | VM name in each namespace (default: `rhel-9-vm`). |
| `--node`, `--num-vms` | Node-based discovery (Mode 2). |
| `--disk-count` | Disks to hotplug per VM (default: 1). |
| `--disk-size` | Size of each disk (default: 10Gi). |
| `--storage-class` *(required)* | Storage class for the new DataVolumes. |
| `--volume-mode` | `Block` (default) or `Filesystem`. |
| `--persist / --no-persist` | Persist the volumes to the VM spec (default: persist). |
| `-c, --concurrency` | Max parallel operations (default: 10). |
| `--start-io` | Run elbencho IO against the new disks. |
| `--ssh-pod`, `--ssh-pod-ns` | SSH bastion pod used to reach VMs. |
| `--vm-user`, `--vm-password` | VM credentials for SSH. |
| `--dry-run` | Show what would be done without making changes. |
| `--log-file` | Path to log file (auto-generated if omitted). |

## Notes

* `--storage-class` is required. There is no default — pick a class with
  enough free capacity for `disk-count × disk-size × VM count`.
* `--persist` is recommended so volumes survive VM restarts.
* `--start-io` requires the SSH bastion pod and credentials to be valid;
  the script falls back gracefully when `elbencho` is missing on a guest.

## See Also

* [VM Template Guide](../../vm-template-guide.md)
* [Run blkdiscard](run-blkdiscard.md) — pairs well after IO runs to
  reclaim space on thin-provisioned backends.

