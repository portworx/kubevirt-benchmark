# Chaos Benchmark Testing

Tests cluster resilience by running concurrent chaos operations including VM creation, volume resize, volume clone, VM restart, and snapshots.

**Use Case**: Stress-test the cluster with concurrent operations, validate volume cloning, and measure performance under load.

## How It Works

The chaos benchmark test runs in iterations, with each iteration performing:

1. **Phase 1: Create VMs** - Creates VMs with data volumes (concurrent)
2. **Phase 2: Resize Volumes** - Expands volume sizes (concurrent)
3. **Phase 3: Clone Volumes** - Clones PVCs from source volumes (concurrent)
4. **Phase 4: Restart VMs** - Restarts VMs and waits for Running state (concurrent)
5. **Phase 5: Create Snapshots** - Creates VM snapshots (concurrent)

Repeats until failure or max iterations reached.

## Basic Chaos Test

### Using virtbench CLI

```bash
# Run chaos test with mandatory concurrency
virtbench chaos-benchmark --storage-class YOUR-STORAGE-CLASS --concurrency 2

# Run with custom VM count
virtbench chaos-benchmark --storage-class YOUR-STORAGE-CLASS --concurrency 5 --vms 10

# Run with maximum iterations limit
virtbench chaos-benchmark --storage-class YOUR-STORAGE-CLASS --concurrency 2 --max-iterations 5
```


## Full Example with All Options

```bash
virtbench chaos-benchmark \
  --storage-class px-fa-direct-access \
  --concurrency 2 \
  --vms 5 \
  --data-volume-count 1 \
  --max-iterations 1 \
  --min-vol-size 40Gi \
  --min-vol-inc-size 10Gi \
  --vm-memory 2Gi \
  --vm-cpu-cores 1 \
  --datasource-name rhel9 \
  --datasource-namespace openshift-virtualization-os-images \
  --save-results
```

> **Note**: Volume sizes must include units (e.g., `40Gi`, `100Mi`). Using just `40` without a unit will fail.

## Skip Specific Phases

```bash
# Skip volume clone phase
virtbench chaos-benchmark \
  --storage-class YOUR-STORAGE-CLASS \
  --concurrency 2 \
  --skip-clone

# Skip multiple phases
virtbench chaos-benchmark \
  --storage-class YOUR-STORAGE-CLASS \
  --concurrency 2 \
  --skip-resize \
  --skip-clone \
  --skip-snapshot
```

## Save Results to Files

```bash
# Run chaos test and save results
virtbench chaos-benchmark \
  --storage-class YOUR-STORAGE-CLASS \
  --concurrency 2 \
  --vms 5 \
  --save-results \
  --storage-driver portworx-3.6
```

Results will be saved to:
```
results/{storage-driver}/{num-disks}-disk/{timestamp}_chaos_benchmark_{total_vms}vms/
```

## Cleanup

### Using virtbench CLI

```bash
# Cleanup resources after test
virtbench chaos-benchmark --cleanup-only --concurrency 1
```


## Troubleshooting

### Volume Resize Failures

**Cause**: Storage class doesn't support volume expansion

**Solution**:
- Verify `AllowVolumeExpansion: true` in StorageClass
- Use `--skip-resize` to skip this phase

### Volume Clone Failures

**Cause**: Storage class doesn't support cloning

**Solution**:
- Use `--skip-clone` to skip this phase

### Snapshot Failures

**Cause**: Storage provisioner doesn't support CSI snapshots

**Solution**:
- Verify VolumeSnapshotClass exists
- Use `--skip-snapshot` to skip this phase

## See Also

- [Configuration Options](../configuration.md) - Detailed configuration reference
- [Output and Results](../output-and-results.md) - Understanding test output
- [Results Dashboard](../results-dashboard.md) - Visualize chaos benchmark results
