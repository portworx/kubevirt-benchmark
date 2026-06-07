# Cleanup Guide

All test scripts support comprehensive cleanup with multiple options for different scenarios.

## Cleanup Options

| Option | Description |
|--------|-------------|
| `--cleanup` | Delete resources and namespaces after test completes |
| `--cleanup-on-failure` | Clean up resources even if tests fail |
| `--dry-run-cleanup` | Show what would be deleted without actually deleting |
| `--yes` | Skip confirmation prompt for cleanup |

## What Gets Cleaned Up

### VM Creation Tests

- All VMs created during the test
- All DataVolumes (DVs) associated with the VMs
- All PersistentVolumeClaims (PVCs)
- All test namespaces (kubevirt-perf-test-1 through kubevirt-perf-test-N)

### Migration Tests

- VirtualMachineInstanceMigration (VMIM) resources
- Optionally: VMs, DataVolumes, PVCs, and namespaces (if `--create-vms` was used)

### Failure Recovery Tests

- FenceAgentsRemediation (FAR) custom resources
- FAR annotations from VMs
- Uncordon nodes that were marked as failed
- Optionally: VMs, DataVolumes, PVCs, and namespaces — only via the Python
  script with `--cleanup-vms` (the `virtbench failure-recovery` wrapper
  cleans up FAR resources only)

### Chaos Benchmark Tests

- All VMs in the test namespace
- All DataVolumes and PVCs
- All VolumeSnapshots
- The entire test namespace

## Cleanup Examples

### Clean up after VM Creation Tests

**virtbench CLI:**
```bash
# Clean up after test
virtbench datasource-clone --start 1 --end 50 --storage-class YOUR-STORAGE-CLASS --cleanup

# Dry run to see what would be deleted
virtbench datasource-clone --start 1 --end 50 --storage-class YOUR-STORAGE-CLASS --dry-run-cleanup

# Clean up even if tests fail
virtbench datasource-clone --start 1 --end 50 --storage-class YOUR-STORAGE-CLASS --cleanup-on-failure
```

### Clean up after Migration Tests

**virtbench CLI:**
```bash
# Clean up VMIMs only (VMs were pre-existing)
virtbench migration --start 1 --end 10 --source-node worker-1 --cleanup

# Clean up everything (VMs were created by test)
virtbench migration --start 1 --end 10 --source-node worker-1 --create-vms --cleanup
```

### Clean up after Failure Recovery Tests

**virtbench CLI:**
```bash
# Clean up FAR resources after a recovery test
virtbench failure-recovery --node worker-1 --cleanup --yes
```

### Clean up after Chaos Benchmark

**virtbench CLI:**
```bash
# Cleanup only (from previous run)
virtbench chaos-benchmark --cleanup-only --concurrency 1
```

## Manual Cleanup

If automated cleanup fails or you need to clean up manually:

### Delete Test Namespaces

```bash
# Delete all test namespaces
kubectl delete namespace -l app=kubevirt-perf-test

# Delete specific range
for i in {1..50}; do
  kubectl delete namespace kubevirt-perf-test-$i --ignore-not-found=true
done
```

### Delete Specific Resources

```bash
# Delete VMs in a namespace
kubectl delete vm --all -n kubevirt-perf-test-1

# Delete DataVolumes
kubectl delete dv --all -n kubevirt-perf-test-1

# Delete PVCs
kubectl delete pvc --all -n kubevirt-perf-test-1

# Delete VMIMs
kubectl delete vmim --all -n kubevirt-perf-test-1
```

### Delete FAR Resources

```bash
# Delete FAR custom resource
kubectl delete fenceagentsremediation <far-name> -n <namespace>

# Remove FAR annotations from VMs
kubectl annotate vm <vm-name> fence.agents.remediation.medik8s.io/fence-agent- -n <namespace>

# Uncordon nodes
kubectl uncordon <node-name>
```

## Best Practices

1. **Use Dry Run First**: Always use `--dry-run-cleanup` to preview what will be deleted
2. **Confirm Deletions**: Review the confirmation prompt carefully before proceeding
3. **Save Results First**: Ensure results are saved before cleanup if needed
4. **Check Dependencies**: Verify no other processes are using the resources
5. **Monitor Cleanup**: Watch for errors during cleanup process

## Troubleshooting

### Namespace Stuck in Terminating

**Problem**: Namespace remains in "Terminating" state

**Solution**:
```bash
# Check for finalizers
kubectl get namespace kubevirt-perf-test-1 -o yaml | grep finalizers

# Remove finalizers if stuck
kubectl patch namespace kubevirt-perf-test-1 -p '{"metadata":{"finalizers":[]}}' --type=merge
```

### PVC Not Deleting

**Problem**: PVC stuck in "Terminating" state

**Solution**:
```bash
# Check if PVC is in use
kubectl describe pvc <pvc-name> -n <namespace>

# Delete associated pods/VMs first
kubectl delete vm --all -n <namespace>

# Force delete if needed
kubectl patch pvc <pvc-name> -p '{"metadata":{"finalizers":[]}}' --type=merge -n <namespace>
```

### Cleanup Fails with Permission Errors

**Problem**: Insufficient permissions to delete resources

**Solution**:
- Ensure your user has cluster-admin or equivalent permissions
- Check RBAC policies: `kubectl auth can-i delete namespace`
- Contact cluster administrator for required permissions

## See Also

- [Configuration Options](configuration.md) - All cleanup-related options
- [Output and Results](output-and-results.md) - Saving results before cleanup
- [Best Practices](../best-practices.md) - Cleanup best practices

