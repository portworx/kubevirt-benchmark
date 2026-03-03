# DataSource-Based VM Provisioning

Tests VM creation using KubeVirt DataSource cloning for efficient VM provisioning.

**Use Case**: Measure VM provisioning performance with your storage backend.

## Basic VM Creation Test

### Using virtbench CLI

```bash
# Run performance test using the vm template
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name rhel-9-vm \
  --vm-template ../examples/vm-templates/rhel9-vm-datasource.yaml \
  --save-results 
```

```bash
# Run performance test using your storage class using default templates
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results 
```

### Using Python Script

```bash
cd datasource-clone

# Run performance test (requires pre-configured template)
python3 measure-vm-creation-time.py \
  --start 1 \
  --end 100 \
  --vm-name rhel-9-vm \
  --vm-template ../examples/vm-templates/rhel9-vm-datasource.yaml \
  --save-results 
```

## Boot Storm Testing

A "boot storm" occurs when many VMs start simultaneously, creating high demand on storage I/O, network resources, compute resources, and hypervisor scheduling.

### What is Boot Storm Testing?

This test helps you understand:
1. How your infrastructure handles concurrent VM startups
2. Performance degradation under load
3. Bottlenecks in storage, network, or compute
4. Realistic recovery time objectives (RTO)

### How It Works

The boot storm test follows this workflow:

**Phase 1: Initial VM Creation**
1. Creates all test namespaces in parallel batches
2. Creates and starts all VMs simultaneously
3. Measures time to Running state
4. Measures time to network readiness (ping)
5. Displays initial creation performance results

**Phase 2: Shutdown All VMs**
1. Issues stop commands to all VMs in parallel
2. Waits for all VMIs to be deleted (VMs fully stopped)
3. Confirms all VMs are in stopped state

**Phase 3: Boot Storm (Simultaneous Startup)**
1. Issues start commands to ALL VMs at once
2. Creates maximum load on infrastructure
3. Measures time to Running state for each VM
4. Measures time to network readiness for each VM
5. Displays boot storm performance results

**Phase 4: Comparison**
Compare initial creation vs boot storm metrics to understand:
- Performance differences between cold start and warm start
- Impact of concurrent operations
- Storage backend behavior under load

## Single Node Boot Storm Testing

Tests VM startup performance on a single node when powering on multiple VMs simultaneously.

**Use Case**: Validates node-level capacity and boot storm performance (e.g., how many VMs can a single node handle during boot storm).

### Using virtbench CLI

```bash
# Run test on a single node (auto-selected) with your storage class
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --single-node \
  --boot-storm \
  --save-results \ 
  --log-file single-node-boot-storm-$(date +%Y%m%d-%H%M%S).log

# Or specify a specific node
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --single-node \
  --node-name worker-node-1 \
  --boot-storm \
  --log-file single-node-boot-storm-$(date +%Y%m%d-%H%M%S).log
```

```bash
# Run bootstorm test using vm template
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name rhel-9-vm \
  --vm-template ../examples/vm-templates/rhel9-vm-datasource.yaml \
  --boot-storm
  --save-results 
```


### Using Python Script

```bash
cd datasource-clone

# Run test on a single node (auto-selected)
python3 measure-vm-creation-time.py \
  --start 1 \
  --end 50 \
  --vm-name rhel-9-vm \
  --single-node \
  --boot-storm \
  --save-results \
  --log-file single-node-boot-storm-$(date +%Y%m%d-%H%M%S).log

# Or specify a specific node
python3 measure-vm-creation-time.py \
  --start 1 \
  --end 50 \
  --vm-name rhel-9-vm \
  --single-node \
  --node-name worker-node-1 \
  --boot-storm \
  --save-results \
  --log-file single-node-boot-storm-$(date +%Y%m%d-%H%M%S).log
```

**What it does**:
1. Selects a single node (random or specified)
2. Creates and starts all VMs on that node (initial test)
3. Stops all VMs and waits for complete shutdown
4. Starts all VMs simultaneously on the same node (boot storm)
5. Measures time to Running state and time to ping for each VM
6. Provides separate statistics for initial creation and boot storm

## Multi-Node Boot Storm Testing

Tests VM startup performance across all nodes when powering on multiple VMs simultaneously.

**Use Case**: Validates cluster-wide performance under boot storm conditions (e.g., after maintenance, power outage recovery).

### Using virtbench CLI

```bash
# Run test with boot storm (VMs distributed across all nodes)
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --storage-class YOUR-STORAGE-CLASS \
  --boot-storm \
  --log-file boot-storm-$(date +%Y%m%d-%H%M%S).log
```

### Using Python Script

```bash
cd datasource-clone

# Run test with boot storm (VMs distributed across all nodes)
python3 measure-vm-creation-time.py \
  --start 1 \
  --end 100 \
  --vm-name rhel-9-vm \
  --boot-storm \
  --save-results \
  --log-file boot-storm-$(date +%Y%m%d-%H%M%S).log
```

**What it does**:
1. Creates and starts all VMs (distributed across nodes)
2. Stops all VMs and waits for complete shutdown
3. Starts all VMs simultaneously (boot storm)
4. Measures time to Running state and time to ping for each VM
5. Provides separate statistics for initial creation and boot storm

## Interpreting Boot Storm Results

### Key Metrics
- **Time to Running**: How long until VM reaches Running state
- **Time to Ping**: How long until VM is network-reachable
- **Max Times**: Worst-case performance

### What to Look For

| Performance Level | Boot Storm vs Initial | Recommendation |
|-------------------|----------------------|----------------|
| Good | 1.5-2x slower | Infrastructure handles load well |
| Concerning | 3x slower | Investigate bottlenecks |
| Critical | 5x+ slower | Major infrastructure issues |

### Performance Tuning

If boot storm performance is poor:

1. **Storage Bottleneck**: Increase storage IOPS, use faster storage tier, enable caching
2. **Network Bottleneck**: Check DHCP server capacity, verify network bandwidth
3. **Compute Bottleneck**: Add more worker nodes, increase node resources

## Advanced Options

### Namespace Batch Creation

```bash
# Create namespaces in batches of 30 for faster setup
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --storage-class YOUR-STORAGE-CLASS \
  --namespace-batch-size 30 \
  --boot-storm
```

### Custom Namespace Prefix

```bash
# Use custom namespace prefix
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --namespace-prefix my-test \
  --boot-storm
```

### Save Results

```bash
# Save results to JSON and CSV files
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --storage-class YOUR-STORAGE-CLASS \
  --boot-storm \
  --save-results \
  --storage-version 3.2.0
```

## Cleanup

```bash
# Cleanup resources after test
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --cleanup

# Cleanup even if tests fail
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --cleanup-on-failure

# Dry run to see what would be deleted
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --dry-run-cleanup
```

## See Also

- [Configuration Options](../configuration.md) - Detailed configuration reference
- [Output and Results](../output-and-results.md) - Understanding test output
- [Migration Testing](migration.md) - Test VM live migration
