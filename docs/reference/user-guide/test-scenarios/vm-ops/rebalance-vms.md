# Rebalance VMs

Rebalance KubeVirt VMs evenly across worker nodes by live-migrating VMs
from over-loaded nodes to under-loaded ones.

**Use Case**: Restore even VM distribution after node drains, scale events,
or skewed scheduling decisions.

## How It Works

The operation:

1. Counts how many of the named VM (`--vm-name`) are currently running on
   each worker node.
2. Identifies nodes that are above `--target-max` and below `--target-min`.
3. Issues `virtctl migrate` commands to shuffle VMs from hot nodes to cold
   nodes until every node holds between `target-min` and `target-max` VMs.

The operation does *not* create or delete VMs — it only migrates existing
ones.

## Basic Usage

### Using virtbench CLI

```bash
# Show the migration plan without executing it
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1 \
  --dry-run

# Rebalance with default targets (16-17 VMs per node)
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1

# Custom targets for a smaller cluster
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1 \
  --target-min 8 \
  --target-max 10
```

### Using Python Script

```bash
cd vm-ops

python3 rebalance-vms.py --vm-name rhel-elbencho-1 --dry-run
```

## Options

| Option | Description |
| --- | --- |
| `--vm-name` | VM name to rebalance (default: `rhel-elbencho-1`). |
| `--target-min` | Minimum VMs per node (default: 16). |
| `--target-max` | Maximum VMs per node (default: 17). |
| `--dry-run` | Print migration commands without executing them. |

## Output

The script prints, for each step:

* Source node → target node
* VM namespace and name
* Migration command issued (or simulated under `--dry-run`)

When the run completes, it shows a final per-node count.

## Notes

* The script targets a single VM name across all namespaces. Run it once
  per VM name if you have multiple workloads to rebalance.
* `target-min` and `target-max` should differ by at least 1; otherwise the
  rebalance loop cannot converge.
* Migrations are issued sequentially; tune cluster-side migration
  concurrency through KubeVirt configuration if needed.

## See Also

* [Migration Testing](../migration.md) — for measuring
  migration time itself.
* [Drain Nodes](drain-nodes.md) — pairs well when you need to evacuate a
  node, then rebalance the survivors.

