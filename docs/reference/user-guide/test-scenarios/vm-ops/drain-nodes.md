# Drain Nodes

Drain Kubernetes nodes and measure how long the drain takes, with optional
sequential or parallel execution across multiple nodes.

**Use Case**: Pre-stage node maintenance, simulate rolling upgrades, or
measure drain throughput as part of a benchmark run.

## How It Works

For each node the operation:

1. Cordons the node.
2. Issues `kubectl drain` with the configured grace period and pod-eviction
   options.
3. Waits up to `--timeout` seconds for the drain to complete.
4. Records which pods (if any) failed to evacuate.
5. Optionally uncordons the node afterwards.

When `--parallel` is set, drains run in worker threads; otherwise nodes are
drained one at a time with an optional `--wait-between` pause to simulate
reboot time.

## Basic Drain

### Using virtbench CLI

```bash
# Sequentially drain two nodes
virtbench vm-ops drain-nodes --nodes worker-1 --nodes worker-2

# Drain in parallel
virtbench vm-ops drain-nodes --nodes worker-1 --nodes worker-2 --parallel

# Drain with a custom timeout and grace period
virtbench vm-ops drain-nodes \
  --nodes worker-1 \
  --timeout 600 \
  --grace-period 60
```


## Full Example with All Options

```bash
virtbench vm-ops drain-nodes \
  --nodes worker-1 --nodes worker-2 --nodes worker-3 \
  --parallel \
  --timeout 600 \
  --grace-period 30 \
  --ignore-daemonsets \
  --delete-emptydir \
  --force \
  --uncordon-after \
  --wait-between 30 \
  --log-file drain-2026-05-08.log
```

## Options

| Option | Description |
| --- | --- |
| `--nodes` *(required, repeatable)* | Node names to drain. Repeat the flag for each node. |
| `--parallel` | Drain nodes in parallel (default: sequential). |
| `--timeout` | Drain timeout in seconds (default: 300). |
| `--grace-period` | Pod termination grace period (default: 30). |
| `--ignore-daemonsets` | Ignore DaemonSet pods. |
| `--delete-emptydir` | Delete pods that mount `emptyDir` volumes. |
| `--force` | Force drain even if pods are not managed by a controller. |
| `--uncordon-after` | Uncordon each node after drain completes. |
| `--wait-between` | Seconds to wait between sequential drains (simulates reboot, default: 0). |
| `--dry-run` | Print the actions without performing them. |
| `--log-file` | Path to a log file (auto-generated if omitted). |

## Output

At the end of the run a summary table shows, per node:

* Drain duration (seconds)
* Pods evacuated
* Pods that remained after the drain attempt
* Whether the node was uncordoned

## Notes

* `--ignore-daemonsets` and `--delete-emptydir` are typically required on
  most production clusters.
* When chaining with [`power-toggle-vms`](power-toggle-vms.md), drain
  *after* powering off VMs to avoid unnecessary live migrations.
