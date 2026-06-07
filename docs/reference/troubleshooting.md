# Troubleshooting

This guide helps you diagnose and resolve common issues when running virtbench performance tests.

## General Issues

### Python Version Too Old

**Symptoms**: Script exits with "Python 3.8+ is required"

**Solutions**:
- Upgrade Python to 3.8 or higher
- On RHEL/CentOS: `sudo yum install python3.8`
- On Ubuntu/Debian: `sudo apt-get install python3.8`
- On macOS: `brew install python@3.8`

### kubectl Not Found or Not Configured

**Symptoms**: "kubectl: command not found" or "The connection to the server was refused"

**Solutions**:
- Install kubectl: Follow [Kubernetes documentation](https://kubernetes.io/docs/tasks/tools/)
- Configure kubectl: `export KUBECONFIG=/path/to/kubeconfig`
- Test connection: `kubectl get nodes`

### virtbench Command Not Found

**Symptoms**: "virtbench: command not found" after installation

**Solutions**:
- Add `~/.local/bin` to PATH: `export PATH="$HOME/.local/bin:$PATH"`
- Make it permanent: Add to `~/.bashrc` or `~/.zshrc`
- If using venv: Activate it first: `source venv/bin/activate`
- Reinstall: `pip3 install -e .`

## VM Creation Issues

### DataSource Not Found

**Symptoms**: VM creation fails with "DataSource 'rhel9' not found"

**Solutions**:
- List available DataSources: `kubectl get datasource -n openshift-virtualization-os-images`
- Check DataSource name in template matches available DataSources
- Verify OpenShift Virtualization is properly installed
- Wait for DataSources to be created (may take a few minutes after installation)

### Storage Class Not Found

**Symptoms**: PVC creation fails with "StorageClass not found"

**Solutions**:
- List available storage classes: `kubectl get storageclass`
- Verify storage class name is correct
- Ensure storage class is properly configured
- Check if storage backend is healthy

### VMs Stuck in Provisioning

**Symptoms**: VMs remain in "Provisioning" state for extended time

**Solutions**:
- Check DataVolume status: `kubectl get dv -n <namespace>`
- Check CDI logs: `kubectl logs -n openshift-cnv -l app=cdi-deployment`
- Verify storage backend is healthy
- Check for resource constraints on nodes
- Increase timeout values if storage is slow

### VMs Not Reaching Running State

**Symptoms**: VMs stuck in "Starting" or other non-Running states

**Solutions**:
- Check VM events: `kubectl describe vm <vm-name> -n <namespace>`
- Check VMI status: `kubectl get vmi <vm-name> -n <namespace>`
- Check virt-launcher pod logs: `kubectl logs virt-launcher-<vm-name>-xxx -n <namespace>`
- Verify node has sufficient resources
- Check VM console for boot errors: `virtctl console <vm-name> -n <namespace>`

### Permission Denied Errors

**Symptoms**: Cannot create namespaces, VMs, or other resources

**Solutions**:
- Ensure your user has cluster-admin or equivalent permissions
- Check RBAC policies: `kubectl auth can-i create vm --all-namespaces`
- Verify service account permissions if running in a pod

### Golden Image PVCs Not Ready

**Symptoms**: DataSource or DataVolume not found

**Solutions**:
- Check DataVolume status: `kubectl get dv -n openshift-virtualization-os-images`
- Verify registry image stream exists: `kubectl get imagestream -n openshift-virtualization-os-images`
- Check CDI operator logs: `kubectl logs -n openshift-cnv -l name=cdi-operator`

## Chaos Benchmark Issues

### Volume Resize Fails

**Symptoms**: Resize phase fails with error

**Solutions**:
- Check if your storage class supports volume expansion:
  ```bash
  kubectl get storageclass YOUR-STORAGE-CLASS -o jsonpath='{.allowVolumeExpansion}'
  ```
- If `false`, use `--skip-resize` to skip this phase
- Check storage backend limits and quotas

### Snapshot Creation Fails

**Symptoms**: Snapshot phase fails

**Solutions**:
- Check if VolumeSnapshotClass is configured:
  ```bash
  kubectl get volumesnapshotclass
  ```
- If not available, use `--skip-snapshot` to skip this phase
- Verify storage backend supports CSI snapshots

### Out of Resources (VM Creation Fails)

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

## Migration Issues

### Migration Stuck or Timeout

**Symptoms**: Migration doesn't complete within timeout

**Solutions**:
- Increase `--migration-timeout` value
- Check network bandwidth between nodes
- Verify storage backend supports live migration
- Check virt-handler logs on source and target nodes

### Migration Fails Immediately

**Symptoms**: Migration fails right after starting

**Solutions**:
- Verify VM is in Running state before migration
- Check if VM has any conditions preventing migration
- Review VMIM resource for error details: `kubectl describe vmim -n <namespace>`

## Debug Mode

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

## Getting Help

If you're still experiencing issues:

1. **Check Logs**: Review test logs with `--log-level DEBUG`
2. **Search Issues**: [Search existing GitHub issues](https://github.com/portworx/kubevirt-benchmark/issues)
3. **Open an Issue**: [Create a new issue](https://github.com/portworx/kubevirt-benchmark/issues/new/choose) with:
   - virtbench version
   - Cluster details (OCP version, KubeVirt version)
   - Storage backend
   - Full error messages and logs
   - Steps to reproduce

## See Also

- [Cluster Validation](user-guide/test-scenarios/cluster-validation.md) - Pre-flight checks
- [Configuration Options](user-guide/configuration.md) - All available options
- [Best Practices](best-practices.md) - Recommended practices
