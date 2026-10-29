# CDI Root-Disk Strategy Benchmark

`virtbench cdi-root-disk` provisions the root disk for the same virtual
machine through each CDI clone strategy and measures how long the VM takes to
become usable.

The user supplies:

- a CDI DataSource (`--datasource-name`, `--datasource-namespace`, as in
  `chaos-benchmark`) that points to a bound PVC, or a golden PVC passed with
  `--golden-image <namespace>/<name>`;
- a `VirtualMachine` YAML specification;
- optionally, the name of the root volume when it cannot be inferred from
  `bootOrder: 1`.

VirtBench changes only the generated resource names, benchmark labels, run
strategy, and root DataVolume. Other disks, networks, resources, cloud-init,
and scheduling settings remain from the supplied VM specification.

The generated root disk inherits its size, access modes, volume mode, and—by
default—StorageClass from the golden PVC. This keeps the three strategy runs
comparable and prevents an older root-disk definition in the VM YAML from
silently changing the clone geometry.

## Run all strategies

```bash
virtbench cdi-root-disk \
  --datasource-name rhel9 \
  --datasource-namespace openshift-virtualization-os-images \
  --vm-spec examples/vm-templates/rhel9-vm-datasource.yaml \
  --root-volume rootdisk \
  --strategy all \
  --iterations 5
```

`--strategy all` runs `csi-clone`, `snapshot`, and `copy` sequentially. The
strategy is requested on each generated DataVolume, so VirtBench does not
change the cluster-wide CDI configuration or the StorageProfile.

To test one strategy:

```bash
virtbench cdi-root-disk \
  --datasource-name rhel9 \
  --vm-spec vm.yaml \
  --strategy snapshot
```

## Measurements

Each iteration records:

- DataVolume creation to `Succeeded` (`root_disk_seconds`);
- VM creation to VMI `Running`;
- VM creation to the selected readiness condition;
- the requested and CDI-observed clone strategy;
- any CDI clone fallback reason.

Readiness can be selected with:

```text
--ready-when vmi-running
--ready-when vmi-ready
--ready-when guest-agent
```

`vmi-ready` is the default. Use `guest-agent` only when the image starts and
connects the QEMU guest agent.

## Safety and validation

The golden PVC is never modified. By default, the command refuses to run when
a live pod mounts the golden PVC because optimized CDI cloning requires the
source not to be in use. `--allow-mounted-source` overrides this protection,
but the clone may wait, fail, or fall back.

VirtBench validates snapshot support before testing that strategy. CSI clone
support is confirmed by the operation itself because Kubernetes does not
expose CSI clone capability through a standard discovery API. A requested
strategy that CDI does not actually use is reported as `fallback`, not as a
successful result for that strategy.

CSI clone and snapshot require the source and target to use the same
StorageClass. When `--storage-class` selects a different class, those two
strategies are reported as unsupported; host-assisted `copy` can still run.

Resources and the benchmark-created namespace are removed by default. Pass
`--keep-resources` for debugging.

## Results

JSON and CSV are written by default to:

```text
results/cdi-root-disk/<timestamp>-<run-id>/
```

Use `--no-save-results` to print results without writing files.
