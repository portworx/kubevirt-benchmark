# User Guide

## Testing Scenarios

The benchmark suite supports the following testing scenarios:

### 1. VM Creation (DataSource Clone)
Tests VM creation using KubeVirt DataSource cloning for efficient VM provisioning.

**Use Case**: Measure VM provisioning performance with your storage backend.

[Learn more →](datasource-clone.md)

### 2. Boot Storm Testing
Mass simultaneous VM startup test to validate concurrent startup performance,
storage/network/compute bottlenecks, and disaster recovery RTO.

**Use Case**: Validate cluster behaviour during power-outage recovery, post-maintenance
cold starts, or any scenario where many VMs start at once.

[Learn more →](boot-storm.md)

### 3. Live Migration Testing
Tests VM live migration with existing VMs or by creating new VMs across different scenarios.

**Use Case**: Validates migration performance for node maintenance, load balancing, and disaster recovery scenarios.

[Learn more →](migration.md)

### 4. Chaos Benchmark Testing
Tests cluster resilience by running concurrent chaos operations including VM creation, volume resize, volume clone, VM restart, and snapshots.

**Use Case**: Stress-test the cluster with concurrent operations, validate volume cloning, and measure performance under load.

[Learn more →](chaos-benchmark.md)

### 5. Failure and Recovery Testing
Tests VM recovery time after simulated node failures using Fence Agents Remediation (FAR).

**Use Case**: Validates high availability and disaster recovery capabilities.

[Learn more →](failure-recovery.md)

### 6. Cluster Validation (Openshift Only)
Validates that your OpenShift cluster is properly configured and ready to run KubeVirt performance tests.

**Use Case**: Pre-flight checks before running benchmarks.

[Learn more →](cluster-validation.md)

### 7. FIO Storage I/O Benchmark
Tests storage I/O performance by running FIO benchmarks across multiple VMs in parallel.

**Use Case**: Measure storage IOPS, bandwidth, and latency under various workload patterns.

[Learn more →](fio-benchmark.md)

### 8. Elbencho Storage Benchmark
Runs the elbencho storage micro-benchmark across multiple VMs to measure
file-system level throughput and latency.

**Use Case**: Compare storage performance for file-based workloads alongside FIO results.

[Learn more →](elbencho-benchmark.md)

### 9. VM Operations (Day-2)
Day-2 lifecycle operations for benchmarking and validation: drain nodes,
rebalance VMs, snapshot in batches, run `blkdiscard`, and power VMs on/off.

**Use Case**: Exercise VM lifecycle paths individually or as building blocks
for larger test plans.

[Learn more →](vm-ops/overview.md)

## Next Steps

1. [Configure your environment](../configuration.md) - Set up storage classes and templates
2. [Run your first test](datasource-clone.md) - Start with a simple VM creation test
3. [View results](../output-and-results.md) - Understand test output and metrics
