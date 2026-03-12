# User Guide

## Testing Scenarios

The benchmark suite supports the following testing scenarios:

### 1. DataSource-Based VM Provisioning
Tests VM creation using KubeVirt DataSource cloning for efficient VM provisioning.

**Use Case**: Measure VM provisioning performance with your storage backend.

[Learn more →](datasource-clone.md)

### 2. Live Migration Testing
Tests VM live migration with existing VMs or by creating new VMs across different scenarios.

**Use Case**: Validates migration performance for node maintenance, load balancing, and disaster recovery scenarios.

[Learn more →](migration.md)

### 3. Chaos Benchmark Testing
Tests cluster resilience by running concurrent chaos operations including VM creation, volume resize, volume clone, VM restart, and snapshots.

**Use Case**: Stress-test the cluster with concurrent operations, validate volume cloning, and measure performance under load.

[Learn more →](chaos-benchmark.md)

### 4. Failure and Recovery Testing
Tests VM recovery time after simulated node failures using Fence Agents Remediation (FAR).

**Use Case**: Validates high availability and disaster recovery capabilities.

[Learn more →](failure-recovery.md)

### 5. Cluster Validation
Validates that your OpenShift cluster is properly configured and ready to run KubeVirt performance tests.

**Use Case**: Pre-flight checks before running benchmarks.

[Learn more →](cluster-validation.md)

### 6. FIO Storage I/O Benchmark
Tests storage I/O performance by running FIO benchmarks across multiple VMs in parallel.

**Use Case**: Measure storage IOPS, bandwidth, and latency under various workload patterns.

[Learn more →](fio-benchmark.md)

## Next Steps

1. [Install virtbench](../../../install.md) - Get started with installation
2. [Configure your environment](../configuration.md) - Set up storage classes and templates
3. [Run your first test](datasource-clone.md) - Start with a simple VM creation test
4. [View results](../output-and-results.md) - Understand test output and metrics

