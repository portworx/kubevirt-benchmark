# VirtualMachinePool Scale and Recovery Benchmark

`virtbench vm-pool` measures how quickly a KubeVirt `VirtualMachinePool`
creates a requested fleet of VMs. Every replica receives a unique CDI-cloned
root disk from the supplied golden image.

## Scale-out benchmark

```bash
virtbench vm-pool \
  --datasource-name rhel9 \
  --datasource-namespace openshift-virtualization-os-images \
  --vm-spec vm.yaml \
  --replicas 50 \
  --strategy all
```

The golden image is selected the same way as in `chaos-benchmark`: by
`--datasource-name` and `--datasource-namespace` (defaults `rhel9` and
`openshift-virtualization-os-images`). The DataSource must point to a PVC.
To use a specific PVC instead, pass `--golden-image <namespace>/<pvc>`.

For each clone strategy, VirtBench creates a pool at zero replicas and then
scales it to the requested size. This makes the scale request the common start
time for all measurements.

The command records:

- first and all managed VM objects created;
- first and all root DataVolumes ready;
- first, 50%, 90%, and 100% of VMIs ready;
- peak VM and VMI counts, including any temporary overshoot;
- failed root DataVolumes;
- requested and observed CDI strategies for every root disk.

Only DataVolumes marked as generated root disks are included in storage
timings. Additional disks in the user-provided VM specification do not distort
the root-disk count.

## Recovery benchmark

Use `--recovery-delete` to remove managed VMs after the pool reaches full
readiness and measure how quickly the pool restores capacity:

```bash
virtbench vm-pool \
  --datasource-name rhel9 \
  --vm-spec vm.yaml \
  --replicas 50 \
  --recovery-delete 10 \
  --strategy csi-clone
```

Recovery completes only after the pool has returned to the requested ready
replica count and at least the requested number of replacement VM UIDs has
appeared. This prevents a stale pool status from being counted as recovery.

## Prerequisites

- KubeVirt and CDI must be installed.
- The `virtualmachinepools.pool.kubevirt.io` CRD must exist.
- The golden image must resolve to a bound PVC.
- The caller must be able to create VMs, pools, DataVolumes, PVCs, and the
  selected namespace.
- Cross-namespace cloning requires permission to create
  `datavolumes/source` in the source namespace.
- Snapshot testing requires a `VolumeSnapshotClass` whose driver matches the
  golden PVC's StorageClass.

Resources are cleaned between strategy runs by default so an earlier pool
does not consume capacity or affect a later measurement. Use
`--keep-resources` only when retained objects are needed for investigation.

## Results

JSON and CSV are written by default to:

```text
results/vm-pool/<timestamp>-<run-id>/
```

The JSON includes complete configuration, preflight decisions, per-iteration
measurements, recovery results, and P50/P95/P99 summaries.

