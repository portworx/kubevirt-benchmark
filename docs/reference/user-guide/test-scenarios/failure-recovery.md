# Failure and Recovery Testing

Tests VM recovery time after node failures to validate high availability and disaster recovery capabilities.

**Use Case**: Validates that VMs can recover and restart on healthy nodes after a node failure.

This guide covers two approaches:

1. **[Automated FAR Testing](#automated-far-testing)** - Uses Fence Agents Remediation (FAR) operator for automated node fencing
2. **[Manual Failure Testing](#manual-failure-testing-without-far)** - Manual node power-off without FAR operator (simpler setup)

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

### Using Python Script Directly

The `recovery-test.py` script orchestrates the complete FAR test workflow when
run with `--mode far-operator`:

**Prerequisites**:
- VMs already created and running on the target node
- FAR operator installed and configured
- `far-template.yaml` configured with the target node name

**Steps**:

```bash
cd failure-recovery

# 1. Edit far-template.yaml with your node details
vim far-template.yaml

# 2. Run the complete FAR test
python3 recovery-test.py \
  --mode far-operator \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --far-config far-template.yaml \
  --save-results

# With custom options
python3 recovery-test.py \
  --mode far-operator \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --far-config my-far-template.yaml \
  --concurrency 128 \
  --log-file recovery-test.log
```

**What the script does**:

1. Detects VMIs running on the target node
2. Applies FAR configuration to trigger node failure
3. Waits for node to become NotReady/Unknown
4. Measures VM recovery time
5. Cleans up FAR configuration on exit

### Monitor-Only Mode

If you trigger node failure separately (for example via your own automation or
manual BMC action), run `recovery-test.py` in monitor mode to only measure
recovery time:

```bash
cd failure-recovery

# Auto-detect VMs by node
python3 recovery-test.py \
  --mode monitor \
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

### Using Python Script

```bash
cd failure-recovery
python3 recovery-test.py \
  --mode monitor \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --cleanup \
  --far-name my-far-resource \
  --failed-node worker-node-1 \
  --yes
```

---

## Manual Failure Testing (Without FAR)

If you don't have the FAR operator installed or want to test manual node failure scenarios, you can use `recovery-test.py` in `--mode manual`.

### Prerequisites

**Required**:
- kubectl configured and connected to your cluster
- VMs already created and running on the target node
- Access to node BMC/IPMI or cloud console to manually power off the node

**Optional**:
- Use `--remove-node-selector` to allow VMs to reschedule onto healthy nodes

### How It Works

In `--mode manual`, `recovery-test.py`:

1. **Detects VMs on the target node** - Builds the list of VMIs to monitor
2. **(Optional) Removes node selectors** - Allows VMs to reschedule to other nodes
3. **Waits for manual node failure** - You power off the node via BMC/IPMI/cloud console
4. **Monitors node status** - Detects when node becomes NotReady/Unknown
5. **Measures VM recovery** - Tracks how long VMs take to recover and become Running

### Running Manual Failure Tests

#### Step 1: Create VMs on Target Node

First, create VMs on the node you want to test:

```bash
# Create VMs with node selector to pin them to specific node
virtbench datasource-clone \
  --start 1 \
  --end 60 \
  --storage-class YOUR-STORAGE-CLASS \
  --node-name worker-node-1 \
  --save-results
```

#### Step 2: Run the Manual Failure Test

```bash
cd failure-recovery

# Basic test - you'll manually power off the node
python3 recovery-test.py \
  --mode manual \
  --node worker-node-1 \
  --vm-name rhel-9-vm

# With node selector removal (allows VMs to reschedule)
python3 recovery-test.py \
  --mode manual \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --remove-node-selector

# With logging and custom options
python3 recovery-test.py \
  --mode manual \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --remove-node-selector \
  --concurrency 128 \
  --log-file recovery-$(date +%Y%m%d-%H%M%S).log

# Fast test - skip ping validation (only check VMI Running state)
python3 recovery-test.py \
  --mode manual \
  --node worker-node-1 \
  --vm-name rhel-9-vm \
  --skip-ping
```

#### Step 3: Power Off the Node

When the script starts monitoring, manually power off the target node:

**Using IPMI/BMC**:
```bash
# Example using ipmitool
ipmitool -I lanplus -H <bmc-ip> -U <username> -P <password> power off
```

**Using Cloud Provider Console**:
- AWS: Stop the EC2 instance
- Azure: Stop the VM
- GCP: Stop the Compute Engine instance
- vSphere: Power off the VM

**Using Physical Server**:
- Press and hold the power button, or
- Use the BMC web interface

#### Step 4: Monitor Recovery

The script will:

1. Detect when the node becomes NotReady/Unknown
2. Start measuring VM recovery time
3. Monitor VMs until they reach Running state (and optionally ping-ready)
4. Display recovery statistics

### Example Output

```
[INFO] Manual Node Failure Recovery Test Configuration:
  Node name:          worker-node-1
  Namespace range:    kubevirt-perf-test-1 to kubevirt-perf-test-60
  VM name:            rhel-9-vm
  Concurrency:        128
  Poll interval:      1s

[SUCCESS] Found 60 VMs in test namespaces
[SUCCESS] Prerequisites check passed

[STEP] Step 1: Removing node selectors from VMs to allow rescheduling...
[SUCCESS] Node selectors removed

[STEP] Step 2: Monitoring Node Status (Waiting for Power-Off)
[INFO] Monitoring node worker-node-1 for failure (power off manually)...
[INFO] Waiting for node to become NotReady or Unknown...
[SUCCESS] Node worker-node-1 is down (Status: NotReady/Unknown) after 45s

[STEP] Step 3: Monitoring VM Recovery
[INFO] Starting recovery monitoring...

VM Recovery Summary:
  Total VMs:           60
  Recovered:           60
  Failed:              0
  Average Recovery:    4m 32s
  Min Recovery:        3m 15s
  Max Recovery:        6m 45s
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `--mode` | **Required**. `manual`, `far-operator`, or `monitor` | - |
| `--node` | **Required**. Target node name | - |
| `--vm-name` | VM name to monitor | rhel-9-vm |
| `--namespace-prefix` | Namespace prefix | perf-test |
| `--concurrency` | Monitoring concurrency | 128 |
| `--poll-interval` | Poll interval in seconds | 2 |
| `--log-file` | Log file path | None |
| `--remove-node-selector` | Remove node selectors before test | false |
| `--skip-ping` | Skip ping tests (faster) | false |
| `--far-config` | FAR YAML manifest (`far-operator` mode) | far-template.yaml |

### When to Use Manual vs FAR Testing

**Use Manual Testing When**:
- You don't have FAR operator installed
- Testing in a simple lab environment
- You want full control over the failure timing
- Learning about VM recovery behavior
- Testing specific failure scenarios

**Use FAR Testing When**:
- You have production HA requirements
- Need automated node remediation
- Testing at scale with multiple nodes
- Validating production HA configuration
- Need repeatable automated tests

### Cleanup

After testing, clean up the FAR resources and (optionally) the VMs.

```bash
# Using virtbench CLI — removes FAR resources and uncordons the node
virtbench failure-recovery \
  --node worker-1 \
  --cleanup \
  --yes

# Using the Python script — also delete VMs, DataVolumes, PVCs, and namespaces
cd failure-recovery
python3 recovery-test.py \
  --mode monitor \
  --node worker-1 \
  --cleanup \
  --cleanup-vms \
  --yes

# Or delete the namespaces manually
for i in {1..60}; do
  kubectl delete namespace kubevirt-perf-test-$i
done
```

If the node is still powered off, power it back on:

```bash
# Using IPMI
ipmitool -I lanplus -H <bmc-ip> -U <username> -P <password> power on

# Or use your cloud provider console/BMC interface
```

---

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

