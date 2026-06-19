# VM Template Guide

This guide explains how to use and customize VM templates for KubeVirt performance testing.

## Overview

VM templates are YAML files that define the configuration for KubeVirt VirtualMachine resources. The virtbench suite includes several pre-configured templates and supports custom templates.

## Available Templates

The repository includes the following templates in `examples/vm-templates/`:

### 1. vm-template.yaml (Recommended)

A flexible template with placeholder variables for easy customization.

**Template Variables:**
- `{{VM_NAME}}` - VM name
- `{{STORAGE_CLASS_NAME}}` - Storage class name
- `{{DATASOURCE_NAME}}` - DataSource name (e.g., rhel9, fedora)
- `{{DATASOURCE_NAMESPACE}}` - DataSource namespace
- `{{STORAGE_SIZE}}` - Root disk storage size (e.g., 30Gi)
- `{{VM_MEMORY}}` - VM memory (e.g., 2048M, 4Gi)
- `{{VM_CPU_CORES}}` - Number of CPU cores

### 2. rhel9-vm-datasource.yaml

Pre-configured template for RHEL 9 VMs using DataSource cloning.

**Features:**
- Uses RHEL 9 DataSource from `openshift-virtualization-os-images`
- 30Gi root disk
- 2Gi memory, 1 CPU core
- Cloud-init configuration included

### 3. rhel9-vm-registry.yaml

Template for RHEL 9 VMs using PVC cloning from golden images.

**Features:**
- Clones from golden image PVC
- Suitable for environments without DataSource support
- Same resource configuration as datasource template

## Using Templates

### With virtbench CLI

The virtbench CLI automatically handles template variable replacement:

```bash
virtbench datasource-clone \
  --start 1 \
  --end 10 \
  --storage-class YOUR-STORAGE-CLASS \
  --vm-template examples/vm-templates/vm-template.yaml
```

The CLI will automatically replace `{{STORAGE_CLASS_NAME}}` with your specified storage class.

### With Template Helper Script

For manual template customization, use the `apply_template.sh` script:

```bash
# Basic usage
./utils/apply_template.sh \
  -o my-vm.yaml \
  -n my-vm \
  -s YOUR-STORAGE-CLASS

# Full customization
./utils/apply_template.sh \
  -o custom-vm.yaml \
  -n custom-vm \
  -s YOUR-STORAGE-CLASS \
  -d fedora \
  --storage-size 50Gi \
  --memory 4Gi \
  --cpu-cores 2
```

### Manual Replacement

You can also manually replace placeholders using `sed`:

```bash
sed -i 's/{{STORAGE_CLASS_NAME}}/YOUR-STORAGE-CLASS/g' vm-template.yaml
sed -i 's/{{VM_NAME}}/my-vm/g' vm-template.yaml
kubectl apply -f vm-template.yaml -n my-namespace
```

## Template Structure

### Basic VM Template Structure

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: {{VM_NAME}}
spec:
  dataVolumeTemplates:
    - metadata:
        name: {{VM_NAME}}-volume
      spec:
        sourceRef:
          kind: DataSource
          name: {{DATASOURCE_NAME}}
          namespace: {{DATASOURCE_NAMESPACE}}
        storage:
          resources:
            requests:
              storage: {{STORAGE_SIZE}}
          storageClassName: {{STORAGE_CLASS_NAME}}
          volumeMode: Block
  runStrategy: Always
  template:
    spec:
      domain:
        cpu:
          cores: {{VM_CPU_CORES}}
        devices:
          disks:
            - name: rootdisk
              bootOrder: 1
              disk:
                bus: virtio
            - name: cloudinitdisk
              disk:
                bus: virtio
          interfaces:
            - name: default
              masquerade: {}
        resources:
          requests:
            cpu: {{VM_CPU_CORES}}
            memory: {{VM_MEMORY}}
      networks:
        - name: default
          pod: {}
      volumes:
        - dataVolume:
            name: {{VM_NAME}}-volume
          name: rootdisk
        - cloudInitNoCloud:
            userData: |
              #cloud-config
              chpasswd:
                expire: false
              password: Password1
              user: rhel
          name: cloudinitdisk
```

## Customizing Templates

### Changing VM Resources

Modify CPU and memory allocations:

```yaml
resources:
  requests:
    cpu: "4"        # 4 CPU cores
    memory: 8Gi     # 8GB memory
```

### Changing Storage Size

Adjust root disk size:

```yaml
storage:
  resources:
    requests:
      storage: 100Gi  # 100GB disk
```

### Using Different DataSources

Change the source image:

```yaml
sourceRef:
  kind: DataSource
  name: fedora      # Use Fedora instead of RHEL
  namespace: openshift-virtualization-os-images
```

### Adding Node Selectors

Pin VMs to specific nodes:

```yaml
spec:
  template:
    spec:
      nodeSelector:
        kubernetes.io/hostname: worker-node-1
```

## Best Practices

1. **Use Template Variables**: Prefer `vm-template.yaml` with placeholders for flexibility
2. **Version Control**: Keep custom templates in version control
3. **Test Templates**: Validate templates with a single VM before large-scale tests
4. **Resource Sizing**: Match VM resources to your test requirements
5. **Storage Class**: Ensure storage class supports required features (snapshots, resize, etc.)

## Troubleshooting

### Template Variable Not Replaced

**Problem**: Placeholder like `{{STORAGE_CLASS_NAME}}` appears in created resources

**Solution**: Ensure you're using the correct template application method or manually replace placeholders

### DataSource Not Found

**Problem**: VM creation fails with "DataSource not found"

**Solution**:
- Verify DataSource exists: `kubectl get datasource -n openshift-virtualization-os-images`
- Check DataSource name and namespace in template

### Storage Class Not Found

**Problem**: PVC creation fails with "StorageClass not found"

**Solution**:
- List available storage classes: `kubectl get storageclass`
- Update template with correct storage class name

## See Also

- [DataSource Clone Testing](test-scenarios/datasource-clone.md) - Using templates in tests
- [Configuration Options](configuration.md) - Template-related options
- [Cluster Validation](test-scenarios/cluster-validation.md) - Verify DataSource availability
