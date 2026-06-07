# Rebalance VMs

Rebalance KubeVirt VMs evenly across worker nodes by pinning selected VMs to
target workers, stopping them, and starting them again.

**Use Case**: Restore even VM distribution after node drains, scale events,
or skewed scheduling decisions.

## How It Works

The operation:

1. Counts how many of the named VM (`--vm-name`) are currently running on
   each worker node.
2. Computes an even target range automatically from the current VM count and
   worker count, unless `--target-min` or `--target-max` is provided.
3. Builds the minimum movement plan needed to bring target workers into range.
4. For each move, patches the VM `nodeSelector`, stops the VM, waits for the
   VMI to disappear, and starts the VM again on the selected worker.

The operation does *not* create or delete VMs. It relocates existing VMs by
changing their node selector and restarting them.

## Basic Usage

### Using virtbench CLI

```bash
# Show the migration plan without executing it
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1 \
  --dry-run

# Rebalance with auto targets
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1

# Custom targets for a smaller cluster
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1 \
  --target-min 8 \
  --target-max 10

# Include master/control-plane nodes as possible targets
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1 \
  --include-master-nodes
```


## Options

| Option | Description |
| --- | --- |
| `--vm-name` | VM name to rebalance (default: `rhel-elbencho-1`). |
| `--target-min` | Minimum VMs per target node (default: auto). |
| `--target-max` | Maximum VMs per target node (default: auto). |
| `--include-master-nodes` | Include master/control-plane nodes as rebalance targets. Worker nodes are used by default. |
| `--dry-run` | Print migration commands without executing them. |

## Output

The script prints, for each step:

* Source node -> target node
* VM namespace and name
* Patch, stop, wait, and start commands issued (or simulated under `--dry-run`)

When the run completes, it shows a final per-node count.

## Notes

* The script targets a single VM name across all namespaces. Run it once
  per VM name if you have multiple workloads to rebalance.
* Master/control-plane nodes are never used as targets unless
  `--include-master-nodes` is provided.
* For small runs, the default target range is computed automatically. For
  example, 3 VMs across 4 workers results in a `0-1` target range: three
  workers hold one VM each and one worker holds none.
* Moves are executed sequentially.

## See Also

* [Drain Nodes](drain-nodes.md) — pairs well when you need to evacuate a
  node, then rebalance the survivors.
