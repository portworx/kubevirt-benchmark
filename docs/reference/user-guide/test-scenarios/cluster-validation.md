# Cluster Validation

The cluster validation script checks that your OpenShift cluster is properly configured and ready to run KubeVirt performance tests.

## Validation Checks

The script validates:

- kubectl access and cluster connectivity
- OpenShift Virtualization installation and health
  - KubeVirt resource status (Deployed phase)
  - Critical deployments: virt-api, virt-controller, virt-operator
  - virt-handler daemonset on all nodes
- Storage class availability
- Worker node readiness
- DataSource availability
- User permissions
- Node resource utilization

## Running Validation

### Using virtbench CLI

The `virtbench validate-cluster` wrapper exposes a small surface — for the
DataSource override, minimum-node count, or "run all checks" flags, use the
Python script directly.

```bash
# Basic validation
virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS

# Quick validation (skip slower checks)
virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS --quick
```

### Using Python Script

```bash
cd utils

# Basic validation
python3 validate_cluster.py --storage-class YOUR-STORAGE-CLASS

# Comprehensive validation
python3 validate_cluster.py --all --storage-class YOUR-STORAGE-CLASS

# With custom DataSource
python3 validate_cluster.py \
  --storage-class YOUR-STORAGE-CLASS \
  --datasource fedora \
  --datasource-namespace openshift-virtualization-os-images

# Require minimum worker nodes
python3 validate_cluster.py \
  --storage-class YOUR-STORAGE-CLASS \
  --min-worker-nodes 5
```

## Validation Options

Flags marked **(script-only)** are exposed only by `utils/validate_cluster.py`,
not by the `virtbench validate-cluster` wrapper.

| Option | Description | Default |
|--------|-------------|---------|
| `--storage-class NAME` | Storage class name to validate | (required) |
| `--quick` | Skip the slower checks (wrapper-only fast path) | false |
| `--datasource NAME` *(script-only)* | DataSource name to validate | rhel9 |
| `--datasource-namespace NS` *(script-only)* | DataSource namespace | openshift-virtualization-os-images |
| `--min-worker-nodes NUM` *(script-only)* | Minimum worker nodes required | 1 |
| `--all` *(script-only)* | Run all validation checks | false |
| `--log-level LEVEL` *(script-only)* | Logging level | INFO |

## Exit Codes

- `0` - All checks passed, cluster is ready
- `1` - One or more checks failed, cluster not ready

## Understanding Validation Output

### Successful Validation

```
✓ kubectl access and cluster connectivity
✓ OpenShift Virtualization installed and healthy
✓ Storage class available
✓ Worker nodes ready (5 nodes)
✓ DataSource available
✓ User permissions verified

Cluster validation passed! Ready to run benchmarks.
```

### Failed Validation

```
✓ kubectl access and cluster connectivity
✗ OpenShift Virtualization not found or not healthy
  - KubeVirt resource not in Deployed phase
✓ Storage class available
✓ Worker nodes ready (3 nodes)
✗ DataSource 'rhel9' not found
  - Check namespace: openshift-virtualization-os-images

Cluster validation failed. Please fix the issues above.
```

## Troubleshooting Validation Failures

### OpenShift Virtualization Not Found

**Check if KubeVirt resource exists:**

```bash
kubectl get kubevirt -A
# Expected: NAMESPACE openshift-cnv, PHASE Deployed
```

**Solution**: Install OpenShift Virtualization operator or KubeVirt

### Components Not Ready

**Check deployment status:**

```bash
# Check deployment status
kubectl get deployment -n openshift-cnv | grep -E "virt-api|virt-controller|virt-operator"

# Check pod logs for errors
kubectl logs -n openshift-cnv deployment/virt-api
```

**Solution**: Review operator logs and ensure all components are running

### Storage Class Not Found

**List all storage classes:**

```bash
kubectl get storageclass
```

**Solution**: Create a storage class appropriate for your storage backend. Refer to your storage provider's documentation.

### DataSource Not Found

**Check available DataSources:**

```bash
kubectl get datasource -n openshift-virtualization-os-images
```

**Solution**: 
- Verify the DataSource name and namespace
- Create DataSource if missing
- Use `--datasource` flag to specify a different DataSource

### Insufficient Worker Nodes

**Check worker node count:**

```bash
kubectl get nodes -l node-role.kubernetes.io/worker
```

**Solution**:
- Add more worker nodes to the cluster
- Adjust `--min-worker-nodes` if fewer nodes are acceptable

### Permission Denied

**Check user permissions:**

```bash
# Test namespace creation
kubectl auth can-i create namespaces

# Test VM creation
kubectl auth can-i create virtualmachines -n default

# Test pod exec
kubectl auth can-i create pods/exec -n default
```

**Solution**: Request cluster-admin or appropriate RBAC permissions from cluster administrator

## Pre-Flight Checklist

Before running benchmarks, ensure:

- [ ] Cluster validation passes
- [ ] Storage class supports dynamic provisioning
- [ ] Storage class is compatible with KubeVirt DataVolumes
- [ ] SSH test pod is running (for network tests)
- [ ] Sufficient cluster resources available
- [ ] DataSource exists and is ready

## See Also

- [Installation Guide](../../../install.md) - Install virtbench and dependencies
- [Configuration Options](../configuration.md) - Configure storage and templates
- [User Guide Overview](overview.md) - Start running benchmarks

