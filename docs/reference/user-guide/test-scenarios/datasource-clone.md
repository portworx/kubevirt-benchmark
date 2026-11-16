# VM Creation (DataSource Clone)

Tests VM creation using KubeVirt DataSource cloning for efficient VM provisioning.

**Use Case**: Measure VM provisioning performance with your storage backend.

For boot-storm scenarios (mass simultaneous VM startup), see the dedicated
[Boot Storm Testing](boot-storm.md) guide.

## Basic VM Creation Test

```bash
# Run performance test using the vm template
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --vm-template ../examples/vm-templates/rhel9-vm-datasource.yaml \
  --save-results \
  --storage-driver STORAGE-DRIVER
```

```bash
# Run performance test using your storage class using default templates
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results \
  --storage-driver STORAGE-DRIVER
```

## Advanced Options

### Monitoring Deadlines

`--vm-ready-timeout` defaults to 600 seconds per monitoring worker, starting when
that worker begins clone tracking and covering the wait for Running.
`--ip-timeout` defaults to 300 seconds, starting after Running is observed.
Both options require positive integers and also apply to boot-storm monitoring.
Increase the readiness timeout when large clones or heavy contention need more time.

```bash
virtbench datasource-clone --start 1 --end 100 \
  --vm-ready-timeout 1800 --ip-timeout 120 --ping-timeout 300 \
  --save-results --cleanup-on-failure --yes
```

Status polling and clone-tracking commands are bounded by the remaining deadline.
Transient scheduling failures may recover within the readiness deadline. A timeout
marks that VM as failed while retaining any timings already measured. Other workers
continue, and the benchmark prints the summary, saves results when requested,
performs the requested cleanup, and exits with status 1 if any VM failed.

`--ping-timeout` continues to apply only to the ping phase after IP acquisition.

### Namespace Batch Creation

```bash
# Create namespaces in batches of 30 for faster setup
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --storage-class YOUR-STORAGE-CLASS \
  --namespace-batch-size 30 \
  --save-results \
  --storage-driver STORAGE-DRIVER
```

### Save Results

```bash
# Save results to JSON and CSV files
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results \
  --storage-driver STORAGE-DRIVER
```

Saved runs are written under:

```text
results/{storage-driver}/{num-disks}-disk/{timestamp}_{namespace-prefix}_{start}-{end}/
```

The run log is saved in the same folder as the JSON and CSV result files.

Detailed VM results include `clone_duration_sec`. When a clone start time is
inferred, the run log marks the duration as inferred. Captured clone durations,
including inferred durations, are included in summary metrics regardless of VM
success. VM readiness and IP acquisition timeouts are configurable with
`--clone-timeout` (1800 seconds by default), `--vm-ready-timeout` (600 seconds
after clone completion), and `--ip-timeout` (300 seconds by default), so slow
clones and incomplete readiness checks have independent budgets.

## Cleanup

```bash
# Cleanup resources after test
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --cleanup

# Cleanup even if tests fail
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --cleanup-on-failure

# Dry run to see what would be deleted
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --dry-run-cleanup
```

## See Also

- [Boot Storm Testing](boot-storm.md) - Mass simultaneous VM startup
- [Configuration Options](../configuration.md) - Detailed configuration reference
- [Output and Results](../output-and-results.md) - Understanding test output
- [Migration Testing](migration.md) - Test VM live migration
