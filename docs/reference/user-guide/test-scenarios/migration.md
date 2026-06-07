# Live Migration Testing

Tests VM live migration with existing VMs or by creating new VMs across different scenarios.

**Use Case**: Validates migration performance for node maintenance, load balancing, and disaster recovery scenarios.

**Supported scenarios**:

- **Sequential** — migrate VMs one at a time from a source to a target node.
- **Parallel** — migrate many VMs concurrently between two nodes.
- **Node evacuation** — drain every VM from a single source node (explicit or auto-selected busiest).
- **Round-robin** — distribute VMs across all worker nodes for load balancing.
- **Multi-source-node** — drain VMs from a comma-separated list of nodes (or every worker via `--source-nodes all`)
  in parallel, with discovery driven by `kubectl` so no `--start`/`--end` range is required.
  See [Multi-Source-Node Migration](#multi-source-node-migration).

## Prerequisites

Live migration tests require VMs to already exist. You have two options:

### Option 1: Create VMs as part of the migration test (Recommended)

Use `--create-vms` with `--storage-class` to create VMs on the source node before migration:

#### Using virtbench CLI

```bash
# Create 10 VMs on source node and migrate them to target node
virtbench migration \
  --start 1 \
  --end 10 \
  --source-node worker-1 \
  --target-node worker-2 \
  --create-vms \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results

# Create VMs, migrate, and cleanup after test
virtbench migration \
  --start 1 \
  --end 10 \
  --source-node worker-1 \
  --target-node worker-2 \
  --create-vms \
  --storage-class YOUR-STORAGE-CLASS \
  --cleanup \
  --save-results
```


### Option 2: Use existing VMs

If VMs already exist (e.g., created by datasource-clone tests), you can migrate them directly:

```bash
# Migrate existing VMs created by datasource-clone
virtbench migration \
  --start 1 --end 3 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```

## Recommended Workflow: Creation, Boot Storm, Rebalance, Multi-Source Migration

Use this workflow when you want to validate VM provisioning, boot storm, and
live migration against the same VM set. The example below creates 10 VMs,
runs a boot storm against those existing VMs, rebalances the VMIs across
worker nodes, and then migrates all VMs using multi-source-node migration.

Replace these placeholders before running the commands:

- `KUBECONFIG` — path to the kubeconfig for the target cluster.
- `VM_TEMPLATE` — path to the VM template YAML.
- `SECRET_YAML` — path to cloud-init or secret YAML required by the VM.
- `STORAGE_CLASS` — storage class to validate.
- `STORAGE_DRIVER` — result grouping label, such as `portworx-3.6` or `ceph`.
- `WORKER_1,WORKER_2,...` — comma-separated worker nodes currently hosting
  the VMs before migration.

Set the kubeconfig once before running the workflow:

```bash
export KUBECONFIG=/path/to/kubeconfig
```

### 1. Validate the cluster

```bash
virtbench validate-cluster \
  --storage-class STORAGE_CLASS \
  --datasource rhel9 \
  --datasource-namespace openshift-virtualization-os-images \
  --min-worker-nodes 4
```

### 2. Create 10 VMs

```bash
virtbench datasource-clone \
  --start 1 \
  --end 10 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --vm-template VM_TEMPLATE \
  --secret-yaml SECRET_YAML \
  --concurrency 10 \
  --namespace-batch-size 10 \
  --ping-timeout 300 \
  --num-disks 4 \
  --save-results \
  --results-folder results \
  --storage-driver STORAGE_DRIVER
```

### 3. Run boot storm against the existing VMs

```bash
virtbench datasource-clone \
  --start 1 \
  --end 10 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --vm-template VM_TEMPLATE \
  --secret-yaml SECRET_YAML \
  --concurrency 10 \
  --ping-timeout 300 \
  --num-disks 4 \
  --boot-storm \
  --skip-vm-creation \
  --skip-namespace-creation \
  --save-results \
  --results-folder results \
  --storage-driver STORAGE_DRIVER
```

### 4. Rebalance the VMs across worker nodes

```bash
virtbench vm-ops rebalance-vms \
  --vm-name rhel-elbencho-1
```

By default, rebalance uses worker nodes only. For example, 10 VMs across
4 workers results in a target range of 2-3 VMs per worker. Rebalance moves
VMs by setting `nodeSelector`, stopping the VM, and starting it again on the
target worker.

### 5. Run multi-source-node migration

```bash
virtbench migration \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --source-nodes WORKER_1,WORKER_2,WORKER_3,WORKER_4 \
  --concurrency 10 \
  --poll-interval 1 \
  --migration-timeout 600 \
  --ping-timeout 300 \
  --save-results \
  --results-folder results \
  --storage-driver STORAGE_DRIVER
```

The `--source-nodes` value is a comma-separated list. Multi-source migration
discovers the matching VMIs on those nodes, clears VM/VMI `nodeSelector`
settings left by rebalance, and then submits migrations in an interleaved
order across the source nodes. If you want to target every worker that has
matching VMIs, you can use `--source-nodes all`.

Each saved run writes its log, JSON, and CSV files into one run folder:

```text
results/{storage-driver}/{num-disks}-disk/{timestamp}_{test-name}/
```

## Migration Scenarios

### Sequential Migration

Migrate VMs one by one from source to target node.

#### Using virtbench CLI

```bash
# Migrate existing VMs one by one
virtbench migration \
  --start 1 --end 3 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --poll-interval 1 \
  --migration-timeout 600 \
  --ping-timeout 300 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```


### Parallel Migration

Migrate multiple VMs simultaneously with configurable concurrency.

#### Using virtbench CLI

```bash
# Migrate existing VMs in parallel with three concurrent migrations
virtbench migration \
  --start 1 --end 3 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --parallel \
  --concurrency 3 \
  --poll-interval 1 \
  --migration-timeout 600 \
  --ping-timeout 300 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```


### Parallel Migration with Advanced Options

#### Using virtbench CLI

```bash
# High-scale parallel migration with custom timeout and no ping validation
virtbench migration \
  --start 1 \
  --end 200 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --parallel \
  --concurrency 50 \
  --interleaved-scheduling \
  --migration-timeout 1000 \
  --skip-ping \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```


### Node Evacuation (Specific Node)

Evacuate all VMs from a specific node before maintenance.

#### Using virtbench CLI

```bash
# Evacuate all VMs from worker-3 before maintenance
virtbench migration \
  --start 1 \
  --end 100 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --source-node worker-3 \
  --evacuate \
  --concurrency 20 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```


### Node Evacuation (Auto-Select Busiest)

Automatically find and evacuate the node currently hosting the most matching
VMs in the namespace range.

#### Using virtbench CLI

```bash
virtbench migration \
  --start 1 \
  --end 100 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --evacuate \
  --auto-select-busiest \
  --concurrency 20 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```


### Round-Robin Migration

Distribute VMs across worker nodes for load balancing. The command chooses a
different target worker for each VM and runs migrations concurrently up to
`--concurrency`.

#### Using virtbench CLI

```bash
virtbench migration \
  --start 1 --end 3 \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --round-robin \
  --concurrency 3 \
  --poll-interval 1 \
  --migration-timeout 600 \
  --ping-timeout 300 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```


### Multi-Source-Node Migration

Discover and migrate **all** VMs running on a list of source nodes in parallel,
without having to specify a `--start`/`--end` range. VMs are listed directly
from the cluster (`kubectl get vmi -A`) and filtered by `.status.nodeName`,
which makes this the right choice when:

- The original `migration-N` namespace numbering no longer matches what is
  actually running on each node (e.g. after several rounds of migrations).
- You want to drain multiple nodes simultaneously (rolling node maintenance,
  cordon-then-evacuate workflows, full cluster rebalancing).

VMs are submitted to the worker pool in an **interleaved** order — `VM1` from
node1, `VM1` from node2, `VM1` from node3, then `VM2` from node1, and so on —
so concurrent migrations are spread evenly across source nodes from the very
first batch instead of draining one node at a time.

#### Using virtbench CLI

```bash
# Drain three specific nodes in parallel
virtbench migration \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --source-nodes worker-1,worker-2,worker-3 \
  --concurrency 20 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6

# Pin every migration to a single target node
virtbench migration \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --source-nodes worker-1,worker-2 \
  --target-node worker-5 \
  --concurrency 15 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6

# Evacuate every worker node in the cluster
virtbench migration \
  --vm-name rhel-elbencho-1 \
  --namespace-prefix datasource-clone \
  --source-nodes all \
  --concurrency 20 \
  --save-results \
  --results-folder results \
  --storage-driver portworx-3.6
```


## What the Test Measures

1. Validates VMs are running
2. Triggers live migration (sequential, parallel, evacuation, or round-robin)
3. Monitors migration progress
4. Measures migration duration (both observed and VMIM timestamps)
5. Validates network connectivity after migration
6. Provides detailed statistics with dual timing measurements

## Cleanup

### Clean up VMIMs only (VMs remain)

#### Using virtbench CLI

```bash
virtbench migration --start 1 --end 100 --cleanup
```


### Clean up everything if VMs were created by the test

#### Using virtbench CLI

```bash
virtbench migration --start 1 --end 100 --create-vms --cleanup
```


## See Also

- [Configuration Options](../configuration.md) - Detailed configuration reference
- [Output and Results](../output-and-results.md) - Understanding test output
- [DataSource Clone Testing](datasource-clone.md) - Create VMs for migration tests
