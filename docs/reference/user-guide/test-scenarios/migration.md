# Live Migration Testing

Tests VM live migration with existing VMs or by creating new VMs across different scenarios.

**Use Case**: Validates migration performance for node maintenance, load balancing, and disaster recovery scenarios.

**Supported scenarios**:

- **Sequential** — migrate VMs one at a time from a source to a target node.
- **Parallel** — migrate many VMs concurrently between two nodes.
- **Node evacuation** — drain every VM from a single source node (explicit or auto-selected busiest).
- **Round-robin** — distribute VMs across all worker nodes for load balancing.
- **Multi-source-node** — drain VMs from a list of nodes (or every worker via `--source-nodes all`)
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

#### Using Python Script

```bash
cd migration

# Create 10 VMs on source node and migrate them to target node
python3 measure-vm-migration-time.py \
  --start 1 \
  --end 10 \
  --source-node worker-1 \
  --target-node worker-2 \
  --create-vms \
  --vm-template ../examples/vm-templates/rhel9-vm-datasource.yaml \
  --save-results
```

!!! warning "Important"
    When using `--create-vms`, you must either:
    
    - Provide `--storage-class YOUR-STORAGE-CLASS` to specify the storage class at runtime, OR
    - Pre-configure your VM template with the correct storage class before running the test

### Option 2: Use existing VMs

If VMs already exist (e.g., created by datasource-clone tests), you can migrate them directly:

```bash
# Migrate existing VMs (assumes VMs exist in migration-1 through migration-10 namespaces)
virtbench migration \
  --start 1 \
  --end 10 \
  --namespace-prefix migration \
  --source-node worker-1 \
  --save-results
```

## Migration Scenarios

### Sequential Migration

Migrate VMs one by one from source to target node.

#### Using virtbench CLI

```bash
# Migrate 10 VMs one by one from worker-1 to worker-2
virtbench migration \
  --start 1 \
  --end 10 \
  --source-node worker-1 \
  --target-node worker-2 \
  --create-vms \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results
```

#### Using Python Script

```bash
cd migration

# Migrate 10 VMs one by one from worker-1 to worker-2
python3 measure-vm-migration-time.py \
  --start 1 \
  --end 10 \
  --source-node worker-1 \
  --target-node worker-2 \
  --create-vms \
  --save-results
```

### Parallel Migration

Migrate multiple VMs simultaneously with configurable concurrency.

#### Using virtbench CLI

```bash
# Migrate 50 VMs in parallel with 10 concurrent migrations
virtbench migration \
  --start 1 \
  --end 50 \
  --source-node worker-1 \
  --target-node worker-2 \
  --create-vms \
  --storage-class YOUR-STORAGE-CLASS \
  --parallel \
  --concurrency 10 \
  --save-results
```

#### Using Python Script

```bash
cd migration

# Migrate 50 VMs in parallel with 10 concurrent migrations
python3 measure-vm-migration-time.py \
  --start 1 \
  --end 50 \
  --source-node worker-1 \
  --target-node worker-2 \
  --create-vms \
  --parallel \
  --concurrency 10 \
  --save-results
```

### Parallel Migration with Advanced Options

#### Using virtbench CLI

```bash
# High-scale parallel migration with custom timeout
virtbench migration \
  --start 1 \
  --end 200 \
  --parallel \
  --concurrency 50 \
  --skip-ping \
  --save-results \
  --migration-timeout 1000
```

#### Using Python Script

The `--interleaved-scheduling` flag (distribute parallel migration threads
across nodes in an interleaved pattern) is only available on the Python
script:

```bash
cd migration

# High-scale parallel migration with interleaved scheduling and custom timeout
python3 measure-vm-migration-time.py \
  --start 1 \
  --end 200 \
  --parallel \
  --concurrency 50 \
  --skip-ping \
  --save-results \
  --migration-timeout 1000 \
  --interleaved-scheduling
```

### Node Evacuation (Specific Node)

Evacuate all VMs from a specific node before maintenance.

#### Using virtbench CLI

```bash
# Evacuate all VMs from worker-3 before maintenance
virtbench migration \
  --start 1 \
  --end 100 \
  --source-node worker-3 \
  --evacuate \
  --concurrency 20 \
  --save-results
```

#### Using Python Script

```bash
cd migration

# Evacuate all VMs from worker-3 before maintenance
python3 measure-vm-migration-time.py \
  --start 1 \
  --end 100 \
  --source-node worker-3 \
  --evacuate \
  --concurrency 20 \
  --save-results
```

### Node Evacuation (Auto-Select Busiest)

Automatically find and evacuate the busiest node. The
`--auto-select-busiest` flag is only available on the Python script.

#### Using Python Script

```bash
cd migration

# Automatically find and evacuate the busiest node
python3 measure-vm-migration-time.py \
  --start 1 \
  --end 100 \
  --evacuate \
  --auto-select-busiest \
  --concurrency 20 \
  --save-results
```

### Round-Robin Migration

Distribute VMs across all nodes for load balancing. The `--round-robin`
flag is only available on the Python script.

#### Using Python Script

```bash
cd migration

# Distribute VMs across all nodes for load balancing
python3 measure-vm-migration-time.py \
  --start 1 \
  --end 100 \
  --round-robin \
  --concurrency 20 \
  --save-results
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
  --source-nodes worker-1 \
  --source-nodes worker-2 \
  --source-nodes worker-3 \
  --concurrency 20 \
  --save-results

# Pin every migration to a single target node
virtbench migration \
  --source-nodes worker-1 \
  --source-nodes worker-2 \
  --target-node worker-5 \
  --concurrency 15 \
  --save-results

# Evacuate every worker node in the cluster
virtbench migration \
  --source-nodes all \
  --concurrency 20 \
  --save-results
```

#### Using Python Script

```bash
cd migration

# Drain three specific nodes in parallel
python3 measure-vm-migration-time.py \
  --source-nodes worker-1 worker-2 worker-3 \
  --concurrency 20 \
  --save-results

# Evacuate every worker node in the cluster
python3 measure-vm-migration-time.py \
  --source-nodes all \
  --concurrency 20 \
  --save-results
```

!!! note "Differences vs `--evacuate`"
    `--evacuate` operates on the namespace range `--start`..`--end` and a single
    `--source-node`. `--source-nodes` ignores any namespace range, accepts
    multiple nodes at once, and discovers VMs directly from the cluster, so it
    keeps working even when VMs no longer match their original namespace
    numbering.

!!! tip "Target node selection"
    When `--target-node` is omitted, KubeVirt auto-selects a target for each
    migration. The script prefers non-source nodes as targets; if every worker
    is listed as a source (e.g. `--source-nodes all`), all workers become
    eligible targets and KubeVirt's scheduler avoids migrating a VM back to
    its current node.

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

#### Using Python Script

```bash
cd migration
python3 measure-vm-migration-time.py --start 1 --end 100 --cleanup
```

### Clean up everything if VMs were created by the test

#### Using virtbench CLI

```bash
virtbench migration --start 1 --end 100 --create-vms --cleanup
```

#### Using Python Script

```bash
cd migration
python3 measure-vm-migration-time.py --start 1 --end 100 --create-vms --cleanup
```

## See Also

- [Configuration Options](../configuration.md) - Detailed configuration reference
- [Output and Results](../output-and-results.md) - Understanding test output
- [DataSource Clone Testing](datasource-clone.md) - Create VMs for migration tests
