# Failure and Recovery Testing

Tests VM recovery time after node failures to validate high availability and disaster recovery capabilities.

**Use Case**: Validates that VMs can recover and restart on healthy nodes after a node failure.

This guide covers the supported `virtbench failure-recovery` workflow for
monitoring VM recovery after a node failure. Fence Agents Remediation (FAR)
can be used to automate node fencing.

---

## Automated FAR Testing

### Prerequisites for FAR Testing

!!! warning "Important"
    Before running failure and recovery tests, you must have the following operators installed and configured on your cluster.

### Required Operators

#### 1. Node Health Check Operator (NHC)

The Node Health Check Operator monitors node health and automatically creates remediation CRs when nodes become unhealthy. NHC is responsible for:

- Detecting unhealthy nodes based on configurable conditions
- Creating FenceAgentsRemediation CRs to trigger remediation
- Deleting remediation CRs after nodes recover

#### 2. Fence Agents Remediation Operator (FAR)

The Fence Agents Remediation Operator performs the actual node fencing using fence agents (e.g., IPMI, AWS, etc.). FAR is responsible for:

- Tainting unhealthy nodes to prevent workload scheduling
- Executing fence agent commands to reboot or power off nodes
- Evicting workloads from unhealthy nodes

Both operators are part of the [MedIK8s](https://www.medik8s.io/) project for Kubernetes node remediation.

### Installation & Configuration

1. Install both operators via OperatorHub (OpenShift) or follow the MedIK8s installation guides
2. Create a `FenceAgentsRemediationTemplate` CR with your fence agent configuration (IPMI, AWS, etc.)
3. Create a `NodeHealthCheck` CR that references your FAR template
4. Configure fence agent credentials (BMC/IPMI credentials, cloud provider credentials, etc.)

!!! info "Documentation"
    Configuration is environment-specific and depends on your fencing method (IPMI, AWS, etc.). Please refer to the official MedIK8s documentation for detailed setup instructions:
    
    - [Node Health Check Operator](https://www.medik8s.io/remediation/node-healthcheck-operator/node-healthcheck-operator/)
    - [Fence Agents Remediation](https://www.medik8s.io/remediation/fence-agents-remediation/fence-agents-remediation/)

### Verify Installation

```bash
# Verify CRDs are available
kubectl get crd nodehealthchecks.remediation.medik8s.io
kubectl get crd fenceagentsremediations.fence-agents-remediation.medik8s.io

# Verify your FenceAgentsRemediationTemplate exists
kubectl get fenceagentsremediationtemplates -A

# Verify your NodeHealthCheck is configured
kubectl get nodehealthchecks -A
```

## Running FAR Tests

### Using virtbench CLI

```bash
# Run failure recovery test (auto-detects VMs on the node)
virtbench failure-recovery \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --save-results

# With a different VM name
virtbench failure-recovery \
  --node worker-node-1 \
  --vm-name debian-vm \
  --save-results
```


### Monitor-Only Mode

If you trigger node failure separately (for example via your own automation or
manual BMC action), run the wrapper to auto-detect VMIs on the affected node
and monitor recovery:

```bash
virtbench failure-recovery \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --save-results
```

## What the Test Measures

The failure recovery test measures:

1. **Detection Time**: Time to detect node failure
2. **Remediation Time**: Time to execute fence agent and taint node
3. **VM Recovery Time**: Time for VMs to restart on healthy nodes
4. **Network Recovery Time**: Time for VMs to become network-reachable
5. **Total Recovery Time**: End-to-end recovery duration

## Understanding Results

### Key Metrics

- **Time to VMI Deletion**: How long until failed VMIs are deleted
- **Time to VM Restart**: How long until VMs restart on new nodes
- **Time to Running**: How long until VMs reach Running state
- **Time to Ping**: How long until VMs are network-reachable
- **Total Recovery Time**: Complete recovery duration

### Recovery Time Objectives (RTO)

| RTO Level | Total Recovery Time | Status |
|-----------|---------------------|--------|
| Excellent | < 5 minutes | HA working optimally |
| Good | 5-10 minutes | Acceptable for most workloads |
| Concerning | 10-20 minutes | Review configuration |
| Critical | > 20 minutes | HA issues need attention |

## Cleanup

### Using virtbench CLI

```bash
# Clean up FAR resources after a recovery test
virtbench failure-recovery \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --cleanup \
  --yes
```



## Troubleshooting

### FAR-Specific Issues

#### FAR CR Not Created

**Cause**: NodeHealthCheck not detecting node failure

**Solution**:
- Verify NodeHealthCheck CR is configured correctly
- Check node conditions match NHC configuration
- Review NHC operator logs

### VMs Not Recovering

**Cause**: Fence agent not executing or node not being fenced

**Solution**:
- Verify FenceAgentsRemediationTemplate is correct
- Check fence agent credentials
- Review FAR operator logs
- Verify node is actually being fenced (check BMC/cloud console)

### Slow Recovery Times

**Cause**: Various factors can slow recovery

**Solution**:
- Reduce NHC detection timeout
- Optimize fence agent timeout settings
- Ensure sufficient resources on healthy nodes
- Check storage backend performance

### Manual Testing Issues

#### Script Can't Detect Node Failure

**Symptoms**: Script times out waiting for node to become NotReady

**Solutions**:
- Verify you actually powered off the node
- Check node status manually: `kubectl get node <node-name>`
- Increase the `--node-timeout` value (default: 600s)
- Ensure kubectl can still reach the cluster

#### VMs Not Rescheduling

**Symptoms**: VMs remain in pending state after node failure

**Solutions**:
- Use `--remove-node-selector` flag to allow rescheduling
- Verify other nodes have sufficient resources
- Check if VMs have other constraints (affinity, taints)
- Review VM events: `kubectl describe vm <vm-name> -n <namespace>`

#### Recovery Takes Too Long

**Symptoms**: VMs take more than 10-15 minutes to recover

**Solutions**:
- Check if Kubernetes detected the node failure (node should be NotReady)
- Verify pod eviction timeout settings
- Ensure sufficient resources on healthy nodes
- Check storage backend performance and availability
- Review kubelet logs on healthy nodes

## See Also

- [Configuration Options](../configuration.md) - Detailed configuration reference
- [Output and Results](../output-and-results.md) - Understanding test output
- [MedIK8s Documentation](https://www.medik8s.io/) - Official operator documentation
