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
- DataSource availability (optional — only needed for datasource-clone and chaos tests)
- User permissions
- Node resource utilization

## Running Validation

```bash
# Basic validation
virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS

# Quick validation (skip slower checks)
virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS --quick

# Use a specific kubeconfig
virtbench --kubeconfig /path/to/kubeconfig validate-cluster \
  --storage-class YOUR-STORAGE-CLASS

# Comprehensive validation
virtbench validate-cluster --all --storage-class YOUR-STORAGE-CLASS

# With custom DataSource
virtbench validate-cluster \
  --storage-class YOUR-STORAGE-CLASS \
  --datasource fedora \
  --datasource-namespace openshift-virtualization-os-images

# Require minimum worker nodes
virtbench validate-cluster \
  --storage-class YOUR-STORAGE-CLASS \
  --min-worker-nodes 5
```

## Validation Options

| Option | Description | Default |
|--------|-------------|---------|
| `--storage-class NAME` | Storage class name to validate | (required) |
| `--quick` | Skip DataSource, SSH pod, and node resource checks | false |
| `--datasource NAME` | DataSource name to validate | rhel9 |
| `--datasource-namespace NS` | DataSource namespace | openshift-virtualization-os-images |
| `--ssh-pod NAME` | SSH pod name to validate | ssh-test-pod |
| `--ssh-pod-namespace NS` | SSH pod namespace | default |
| `--min-worker-nodes NUM` | Minimum worker nodes required | 1 |
| `--all` | Run all validation checks | false |
| `--kubeconfig PATH` | Global `virtbench` option for kubeconfig path | `KUBECONFIG` environment variable or kubectl default |

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
✗ Storage class 'YOUR-STORAGE-CLASS' not found
✓ Worker nodes ready (3 nodes)
⚠ DataSource 'rhel9' exists but is not ready - WARNING (only needed for datasource-clone/chaos tests)

Cluster validation failed. Please fix the issues above.
```

A missing or not-ready DataSource is reported as a **warning**, not a failure — it
only blocks the datasource-clone and chaos tests. Validation fails only on the
hard checks above (connectivity, virtualization, storage class, worker nodes,
permissions).

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
- [ ] DataSource exists and is ready (only for datasource-clone / chaos tests)

## See Also

- [Installation Guide](../../../install.md) - Install virtbench and dependencies
- [Configuration Options](../configuration.md) - Configure storage and templates
- [User Guide Overview](overview.md) - Start running benchmarks
