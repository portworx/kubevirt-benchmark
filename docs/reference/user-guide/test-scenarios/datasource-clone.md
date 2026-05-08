# VM Creation (DataSource Clone)

Tests VM creation using KubeVirt DataSource cloning for efficient VM provisioning.

**Use Case**: Measure VM provisioning performance with your storage backend.

For boot-storm scenarios (mass simultaneous VM startup), see the dedicated
[Boot Storm Testing](boot-storm.md) guide.

## Basic VM Creation Test

### Using virtbench CLI

```bash
# Run performance test using the vm template
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --vm-template ../examples/vm-templates/rhel9-vm-datasource.yaml \
  --save-results \
  --storage-version STORAGE-VERSION
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
  --storage-version STORAGE-VERSION
```

### Using Python Script

```bash
cd datasource-clone

# Run performance test (requires pre-configured template)
python3 measure-vm-creation-time.py \
  --start 1 \
  --end 100 \
  --vm-name VM-NAME \
  --namespace-prefix NS-PREFIX \
  --vm-template ../examples/vm-templates/rhel9-vm-datasource.yaml \
  --save-results \
  --storage-version STORAGE-VERSION
```

## Advanced Options

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
  --storage-version STORAGE-VERSION
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
  --storage-version STORAGE-VERSION
```

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
