# Output and Results

This page explains how to interpret test output, understand results, and troubleshoot common issues.

## Console Output

Tests provide real-time progress updates:

```
[INFO] 2024-01-15 10:30:00 - Starting VM creation test
[INFO] 2024-01-15 10:30:00 - Creating namespaces kubevirt-perf-test-1 to kubevirt-perf-test-100
[INFO] 2024-01-15 10:30:05 - Dispatching VM creation in parallel
[INFO] 2024-01-15 10:30:15 - [kubevirt-perf-test-1] VM Running at 8.45s
[INFO] 2024-01-15 10:30:18 - [kubevirt-perf-test-1] Ping success at 11.23s
...
```

## Summary Report

At completion, a summary table is displayed:

```
Performance Test Summary
================================================================================
Namespace                Running(s)      Ping(s)         Status
--------------------------------------------------------------------------------
kubevirt-perf-test-1     8.45           11.23           Success
kubevirt-perf-test-2     9.12           12.45           Success
kubevirt-perf-test-3     8.89           11.98           Success
...
================================================================================
Statistics:
  Total VMs:              100
  Successful:             98
  Failed:                 2
  Avg Time to Running:    9.23s
  Avg Time to Ping:       12.45s
  Max Time to Running:    15.67s
  Max Time to Ping:       18.92s
  Total Test Duration:    125.34s
================================================================================
```

## Log Files

Detailed logs are saved to the specified log file with:

- Timestamps for all operations
- Error messages and stack traces
- Resource creation/deletion events
- Performance metrics

### Example Log Entry

```
[INFO] 2024-01-15 10:30:15 - [kubevirt-perf-test-1] VM created successfully
[INFO] 2024-01-15 10:30:18 - [kubevirt-perf-test-1] VM reached Running state at 8.45s
[INFO] 2024-01-15 10:30:21 - [kubevirt-perf-test-1] Ping successful at 11.23s
```

## Saved Results

When using `--save-results`, tests generate structured output files. For
DataSource clone and boot-storm runs, the log file is saved in the same
timestamped folder as the JSON and CSV files.

### File Structure

```
results/
├── {storage-driver}/
│   ├── {num-disks}-disk/
│   │   ├── {timestamp}_vm_creation_{num_vms}vms/
│   │   │   ├── datasource-clone.log
│   │   │   ├── vm_creation_results.json
│   │   │   ├── vm_creation_results.csv
│   │   │   └── summary_vm_creation.json
│   │   ├── {timestamp}_migration_{num_vms}vms/
│   │   │   ├── migration_results.json
│   │   │   ├── migration_results.csv
│   │   │   └── summary_migration.json
│   │   └── {timestamp}_chaos_benchmark_{total_vms}vms/
│   │       ├── chaos_benchmark_results.json
│   │       ├── chaos_benchmark_results.csv
│   │       └── summary_chaos_benchmark.json
```

### JSON Results Format

```json
{
  "test_type": "vm_creation",
  "timestamp": "2024-01-15T10:30:00",
  "total_vms": 100,
  "successful": 98,
  "failed": 2,
  "avg_time_to_running": 9.23,
  "avg_time_to_ping": 12.45,
  "max_time_to_running": 15.67,
  "max_time_to_ping": 18.92,
  "results": [
    {
      "namespace": "kubevirt-perf-test-1",
      "vm_name": "rhel-9-vm",
      "time_to_running": 8.45,
      "time_to_ping": 11.23,
      "status": "Success"
    }
  ]
}
```

### CSV Results Format

```csv
namespace,vm_name,time_to_running,time_to_ping,status
kubevirt-perf-test-1,rhel-9-vm,8.45,11.23,Success
kubevirt-perf-test-2,rhel-9-vm,9.12,12.45,Success
kubevirt-perf-test-3,rhel-9-vm,8.89,11.98,Success
```

## Understanding Metrics

### VM Creation Metrics

- **Time to Running**: Duration from VM creation to Running state
  - Includes: DataVolume provisioning, VM scheduling, VM startup
  - Good: < 30s, Acceptable: 30-60s, Slow: > 60s

- **Time to Ping**: Duration from VM creation to network reachability
  - Includes: Time to Running + cloud-init + network configuration
  - Good: < 60s, Acceptable: 60-120s, Slow: > 120s

### Migration Metrics

- **Migration Duration (Observed)**: Time measured by the test script
- **Migration Duration (VMIM)**: Time recorded in VirtualMachineInstanceMigration resource
- **Downtime**: Time VM is unavailable during migration (if measured)

### Capacity Metrics

- **VMs Created**: Total VMs successfully created across all iterations
- **Iterations Completed**: Number of successful iteration cycles
- **Failure Point**: Which phase failed (creation, resize, restart, snapshot)
- **Time per Phase**: Duration of each operation phase

## Troubleshooting

### Common Issues

#### VMs fail to reach Running state

**Symptoms**: VMs stuck in Scheduling or Pending state

**Solutions**:
- Check storage class is available: `kubectl get sc`
- Verify sufficient cluster resources: `kubectl top nodes`
- Check VM events: `kubectl describe vm <vm-name> -n <namespace>`
- Review PVC status: `kubectl get pvc -n <namespace>`

#### Ping tests timeout

**Symptoms**: VMs reach Running state but ping fails

**Solutions**:
- Verify SSH pod exists and is running: `kubectl get pod <ssh-pod> -n <namespace>`
- Check network policies allow pod-to-pod communication
- Verify VM has cloud-init configured correctly
- Check VM console for boot errors: `virtctl console <vm-name> -n <namespace>`

#### Permission denied errors

**Symptoms**: Cannot create namespaces, VMs, or other resources

**Solutions**:
- Ensure your user has cluster-admin or equivalent permissions
- Check RBAC policies: `kubectl auth can-i create vm --all-namespaces`
- Verify service account permissions if running in a pod

#### Golden image PVCs not ready

**Symptoms**: DataSource or DataVolume not found

**Solutions**:
- Check DataVolume status: `kubectl get dv -n openshift-virtualization-os-images`
- Verify registry image stream exists: `kubectl get imagestream -n openshift-virtualization-os-images`
- Check CDI operator logs: `kubectl logs -n openshift-cnv -l name=cdi-operator`

### Chaos Benchmark Issues

#### Volume resize fails

**Symptoms**: Resize phase fails with error

**Solutions**:
- Check if your storage class supports volume expansion:
  ```bash
  kubectl get storageclass YOUR-STORAGE-CLASS -o jsonpath='{.allowVolumeExpansion}'
  ```
- If `false`, use `--skip-resize` to skip this phase
- Check storage backend limits and quotas

#### Snapshot creation fails

**Symptoms**: Snapshot phase fails

**Solutions**:
- Check if VolumeSnapshotClass is configured:
  ```bash
  kubectl get volumesnapshotclass
  ```
- If not available, use `--skip-snapshot` to skip this phase
- Verify storage backend supports CSI snapshots

#### Out of resources (VM creation fails)

**Symptoms**: VMs stuck in Scheduling state, capacity limit reached

**Solutions**:
- This indicates you've reached capacity limits. Check:
  ```bash
  # Check node resources
  kubectl top nodes

  # Check node status
  kubectl describe node node-name
  ```
- Review cluster resource quotas
- Add more worker nodes or increase node resources

### Migration Issues

#### Migration stuck or timeout

**Symptoms**: Migration doesn't complete within timeout

**Solutions**:
- Increase `--migration-timeout` value
- Check network bandwidth between nodes
- Verify storage backend supports live migration
- Check virt-handler logs on source and target nodes

#### Migration fails immediately

**Symptoms**: Migration fails right after starting

**Solutions**:
- Verify VM is in Running state before migration
- Check if VM has any conditions preventing migration
- Review VMIM resource for error details: `kubectl describe vmim -n <namespace>`

### Debug Mode

Enable debug logging for detailed troubleshooting:

```bash
# Using virtbench CLI
virtbench datasource-clone --log-level DEBUG --start 1 --end 5

virtbench datasource-clone --log-level DEBUG --start 1 --end 5
```

## Performance Baselines

### Expected Performance Ranges

These are general guidelines. Actual performance depends on your infrastructure:

#### VM Creation (DataSource Clone)

| Storage Type | Time to Running | Time to Ping |
|--------------|-----------------|--------------|
| Local SSD | 10-20s | 30-45s |
| Network SSD (Portworx, Ceph) | 15-30s | 40-60s |
| Network HDD | 30-60s | 60-120s |

#### Live Migration

| VM Size | Migration Duration |
|---------|-------------------|
| Small (2GB RAM) | 10-30s |
| Medium (4-8GB RAM) | 30-60s |
| Large (16GB+ RAM) | 60-180s |

#### Boot Storm Impact

Expect 1.5-3x slower performance during boot storm compared to sequential creation.

## Best Practices

1. **Run Multiple Tests**: Run tests multiple times to get consistent baselines
2. **Save Results**: Always use `--save-results` to track performance over time
3. **Monitor Resources**: Watch cluster resources during tests
4. **Start Small**: Begin with small VM counts to validate setup
5. **Use Logging**: Enable appropriate log levels for troubleshooting
6. **Clean Up**: Always clean up test resources after completion

## See Also

- [Configuration Options](configuration.md) - All available configuration options
- [Results Dashboard](results-dashboard.md) - Visualize test results
- [User Guide](test-scenarios/overview.md) - Running different test scenarios
