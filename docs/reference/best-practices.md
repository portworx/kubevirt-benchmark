# Best Practices

This guide provides recommendations for running effective and reliable performance tests with virtbench.

> **Important Notice:**
>
> - Do not run these benchmarks directly in your production environment without thorough testing first.
> - Always test in a non-production environment to understand the impact and behavior.
> - Test results will vary significantly based on your underlying infrastructure, including hardware specifications, storage backend, network configuration, and cluster resources.
> - **Use at your own risk.**

## General Testing Practices

### 1. Start Small, Scale Gradually

- Begin with 5-10 VMs to validate your setup
- Gradually increase to 50, 100, 200+ VMs
- Identify bottlenecks at each scale
- Understand your infrastructure limits before large-scale tests

### 2. Run Multiple Tests

- Run each test at least 3 times for consistency
- Average results across multiple runs
- Identify and investigate outliers
- Account for cluster variability

### 3. Save Results Consistently

Always use `--save-results` to track performance over time:

```bash
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results \
  --storage-driver portworx-3.6
```

### 4. Use Meaningful Test Names

Organize results with a storage driver label:

```bash
--storage-driver "portworx-3.6"
--storage-driver "ceph"
```

### 5. Monitor Cluster Resources

Watch cluster resources during tests:

```bash
# In a separate terminal
watch kubectl top nodes

# Check storage backend metrics
# (specific to your storage solution)
```

## VM Creation Testing

### 1. Validate Cluster First

Always run cluster validation before testing:

```bash
virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS
```

### 2. Use Appropriate Concurrency

- Default concurrency (50) works for most scenarios
- Increase for large-scale tests (100-200 VMs)
- Decrease if experiencing resource contention

```bash
virtbench datasource-clone \
  --start 1 \
  --end 200 \
  --concurrency 200 \
  --storage-class YOUR-STORAGE-CLASS
```

### 3. Namespace Batch Creation

Create namespaces in batches for faster setup:

```bash
virtbench datasource-clone \
  --start 1 \
  --end 100 \
  --namespace-batch-size 50 \
  --storage-class YOUR-STORAGE-CLASS
```

### 4. Boot Storm Testing

- Test both single-node and multi-node boot storms
- Start with smaller VM counts (20-30)
- Gradually increase to find capacity limits
- Compare initial creation vs boot storm performance

## Migration Testing

### 1. Verify VMs Before Migration

Ensure VMs are healthy before starting migration tests:

```bash
# Check VM status
kubectl get vm -n kubevirt-perf-test-1

# Verify network connectivity
kubectl exec -it ssh-test-pod -- ping <vm-ip>
```

### 2. Choose Appropriate Migration Scenario

- **Sequential**: For baseline performance
- **Parallel**: For stress testing
- **Evacuation**: For node maintenance scenarios
- **Round-robin**: For load balancing validation

### 3. Set Realistic Timeouts

Adjust timeouts based on VM size and network:

```bash
virtbench migration \
  --start 1 \
  --end 10 \
  --migration-timeout 600 \  # 10 minutes for large VMs
  --source-node worker-1
```

## Chaos Benchmark Testing

### 1. Understand Your Goals

- **Stress Test Cluster**: Run concurrent chaos operations
- **Test Specific Scenarios**: Use `--max-iterations` to limit test duration
- **Skip Unsupported Features**: Use `--skip-resize`, `--skip-clone`, or `--skip-snapshot` if needed

### 2. Start with Conservative Settings

```bash
virtbench chaos-benchmark \
  --storage-class YOUR-STORAGE-CLASS \
  --concurrency 2 \
  --vms 5 \
  --max-iterations 5
```

### 3. Monitor for Failures

- Watch for resource exhaustion
- Check storage backend health
- Monitor node resources
- Review logs for errors

## Failure Recovery Testing

### 1. Test in Non-Production First

- Validate FAR configuration in test environment
- Understand recovery behavior before production use
- Document expected recovery times

### 2. Use Appropriate Timeouts

Set timeouts based on your RTO requirements:

```bash
virtbench failure-recovery \
  --node worker-1 \
  --recovery-timeout 600  # 10 minutes
```

### 3. Clean Up FAR Resources

Always clean up FAR resources after testing:

```bash
virtbench failure-recovery \
  --node worker-1 \
  --cleanup \
  --yes
```

## Logging and Debugging

### 1. Use Appropriate Log Levels

- **INFO**: Normal operation (default)
- **DEBUG**: Detailed troubleshooting
- **WARNING**: Important issues only
- **ERROR**: Critical errors only

### 2. Save Logs to Files

```bash
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --log-file test-$(date +%Y%m%d-%H%M%S).log
```

### 3. Review Logs After Tests

- Check for errors and warnings
- Identify performance bottlenecks
- Validate test completion

## Cleanup Practices

### 1. Always Clean Up After Tests

```bash
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --cleanup
```

### 2. Use Dry Run First

Preview cleanup before executing:

```bash
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --dry-run-cleanup
```

### 3. Clean Up on Failure

Ensure resources are cleaned up even if tests fail:

```bash
virtbench datasource-clone \
  --start 1 \
  --end 50 \
  --storage-class YOUR-STORAGE-CLASS \
  --cleanup-on-failure
```

## Results Management

### 1. Organize Results by Version

Use `--storage-driver` to organize results:

```bash
--storage-driver "portworx-3.6"
```

### 2. Generate Dashboards Regularly

Create dashboards after each test run:

```bash
Generate the results dashboard after each run and archive the produced HTML.
```

### 3. Archive Important Results

- Save dashboard HTML files
- Keep JSON/CSV results for historical comparison
- Document test conditions and configurations

## Performance Optimization

### 1. Tune for Your Environment

- Adjust concurrency based on cluster size
- Optimize namespace batch size
- Configure appropriate timeouts

### 2. Minimize External Load

- Run tests when cluster is not under load
- Avoid running multiple tests simultaneously
- Ensure storage backend is not saturated

### 3. Use Consistent Test Conditions

- Same time of day
- Same cluster state
- Same resource availability

## See Also

- [Configuration Options](user-guide/configuration.md) - All available options
- [Troubleshooting](troubleshooting.md) - Common issues and solutions
- [Output and Results](user-guide/output-and-results.md) - Understanding test output
- [Cleanup Guide](user-guide/cleanup-guide.md) - Cleanup procedures
