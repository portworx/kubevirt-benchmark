# FIO Storage I/O Benchmark

Tests storage I/O performance by running FIO benchmarks across multiple VMs in parallel.

**Use Case**: Measure storage IOPS, bandwidth, and latency under various workload patterns.

## How It Works

FIO is baked into the VM template and runs on boot. The benchmark workflow has four stages, each runnable on its own:

1. **deploy** - Create namespaces and VMs; FIO starts automatically.
2. **status** - Poll VM and FIO state.
3. **gather-results** - Collect FIO JSON output from each VM via SSH.
4. **cleanup** - Delete VMs and namespaces.

`run-all` (default) chains all four with a wait between deploy and gather.

## VM Size

The shipped FIO templates (`fio-vm-template.yaml`, `fio-vm-template-containerdisk.yaml`) give each VM **4 guest vCPUs** with a **CPU request of 4**, so the four FIO jobs, guest I/O completion and virtio device processing are not squeezed onto fewer CPUs. Memory and FIO parameters are unchanged.

By default the harness uses whatever the template says. To override it:

```bash
# 2 vCPUs, CPU request 2
virtbench fio -a run-all -s 1 -e 10 --storage-class YOUR-STORAGE-CLASS --vm-cpus 2
# Reproduce the older template (2 vCPUs, CPU request 1)
virtbench fio -a run-all -s 1 -e 10 --storage-class YOUR-STORAGE-CLASS --vm-cpus 2 --vm-cpu-request 1
```

`--vm-cpus` sets `domain.cpu.cores` (1 socket, 1 thread) and the CPU request (default: equal to `--vm-cpus`; set it separately with `--vm-cpu-request`). An existing CPU limit in the template is kept consistent. It cannot be used with templates that size the VM through `spec.instancetype`; choose a different instancetype instead.

The vCPU count and CPU request of the **running** VMs are recorded as `vm_cpus` and `vm_cpu_request` in `summary_fio_benchmark.json` and shown in the dashboard. Results from before this change have no `vm_cpus` (those VMs had 2 vCPUs and a CPU request of 1).

!!! warning "Capacity"
    A CPU request of 4 per VM needs real CPU: each node needs about `ceil(VMs / nodes) x CPU request` free allocatable CPU (plus memory for the guest and launcher overhead). With balanced placement the harness checks this before deploying and stops if a node cannot fit its share. Use fewer VMs, a smaller `--vm-cpus`/`--vm-cpu-request`, `--exclude-nodes` for busy nodes, `--no-balance`, or `--skip-capacity-check` to deploy anyway. A namespace `LimitRange` with a default CPU limit below the request will also reject the VMs.

## VM Placement

By default the FIO VMs are spread **evenly across the eligible nodes**. Each VM is pinned to one node (round-robin, via `nodeSelector` on `kubernetes.io/hostname`), so with 10 VMs and 5 nodes every node runs 2 VMs. If the VM count does not divide evenly, per-node counts differ by at most one. Assignment follows the namespace index, so deploying `-s 1 -e 10` and then `-s 11 -e 20` gives the same spread as `-s 1 -e 20`.

### Which nodes are used

Candidate nodes are, in order of preference:

1. nodes matching `--node-selector` (e.g. `--node-selector pool=fio` or `--node-selector disktype=nvme,zone-group=a`), if given;
2. otherwise nodes with the `node-role.kubernetes.io/worker` label, any value (OpenShift and some installers set this);
3. otherwise nodes without a `control-plane`/`master` role label (kubeadm, kind, k3s, RKE2, EKS, GKE, AKS usually do not label workers);
4. otherwise all nodes (single-node and compact clusters).

A candidate is skipped when it:

- is not `Ready` or is cordoned;
- is not schedulable for KubeVirt (`kubevirt.io/schedulable` is not `true`, e.g. virt-handler is not running there);
- does not match the VM template's own `nodeSelector`;
- carries the exclude label (default `virtbench.io/no-fio`) or is listed in `--exclude-nodes`;
- has a `NoSchedule`/`NoExecute` taint the VM template does not tolerate (a `NoExecute` toleration with `tolerationSeconds` counts as not tolerated, since the VM would be evicted mid-run). The shipped templates tolerate the `master`/`control-plane` `NoSchedule` taints.

The candidate pool, every skipped node with its reason, and the planned VMs per node are printed before anything is created. Names in `--exclude-nodes` that match no node are reported.

### Keeping FIO off nodes

Label them:

```bash
kubectl label node worker-3 virtbench.io/no-fio=true
# remove afterwards
kubectl label node worker-3 virtbench.io/no-fio-
```

Any node carrying the `virtbench.io/no-fio` label (any value) is skipped. To use a label you already have, pass `--exclude-node-label`, e.g. `--exclude-node-label pool=database` (`key` alone matches any value; `""` disables label exclusion). You can also list nodes by name:

```bash
virtbench fio -a run-all -s 1 -e 10 --storage-class YOUR-STORAGE-CLASS \
  --exclude-nodes worker-3,worker-4 --save-results
```

### Verification and results

After `deploy` and `run-all`, the harness waits up to `--placement-timeout` seconds (default 600) for every VMI to be scheduled, then prints the VMs per node and reports VMs that were not created, are not scheduled, have failed, run on a different node than planned, or need a restart (`RestartRequired`: the running VM does not match the applied spec). If balanced placement is not achieved, the command exits with status 1 (the run still collects and saves results). A failed `kubectl apply` also exits with status 1.

`gather-results` reads the placement and vCPUs from the running VMs, so a separate `deploy` and `gather-results` record what was actually deployed, not the flags of the gather command. With `--save-results`, the VM-to-node map is saved to `vm_node_placement.json`, under `placement` in the summary (`balanced` is `true`/`false` for balanced placement and `null` when the scheduler placed the VMs), and as a `node` column at the end of the CSV.

Reading the cluster state lists nodes, pods, VMs and VMIs cluster-wide when allowed, and falls back to per-namespace reads for users without cluster-wide list rights (the capacity check is then skipped).

### Limitations

- **Existing VMs:** `kubectl apply` over running VMs does not move or resize them, and old FIO results may be collected. The harness warns when VMs already exist; run `--action cleanup` first.
- **Storage topology:** pinning decides the node before the volume exists. With a `WaitForFirstConsumer` StorageClass the volume follows the VM. With an `Immediate` StorageClass and zonal storage (EBS, GCE PD, Azure Disk, local volumes), a volume can be created where the pinned node cannot reach it, leaving the VM Pending. The harness warns when an `Immediate` StorageClass is used with nodes in more than one zone. Use a `WaitForFirstConsumer` class, `--node-selector topology.kubernetes.io/zone=<zone>`, or `--no-balance`.
- **Template placement rules:** required node affinity, pod anti-affinity or topology spread constraints in the template are not evaluated when choosing nodes; the harness warns when they are present.

Use `--no-balance` to skip pinning and let the Kubernetes scheduler place the VMs.

## Basic Usage

### virtbench CLI

```bash
# Full workflow (deploy + wait + gather + save)
virtbench fio --action run-all \
  --start 1 --end 10 \
  --storage-class YOUR-STORAGE-CLASS \
  --save-results

# Custom FIO parameters
virtbench fio --action run-all \
  --start 1 --end 20 \
  --storage-class YOUR-STORAGE-CLASS \
  --fio-runtime 300 --fio-rw randrw --fio-bs 4k --fio-iodepth 64 \
  --save-results --cleanup

# Step-by-step workflow
virtbench fio --action deploy -s 1 -e 10 --storage-class YOUR-STORAGE-CLASS
virtbench fio --action status -s 1 -e 10
virtbench fio --action gather-results -s 1 -e 10 --save-results
virtbench fio --action cleanup -s 1 -e 10
```

!!! note "`--save-results` vs `--action gather-results`"
    `--save-results` is a **flag** that writes summary JSON/CSV files to the output directory. `--action gather-results` is an **action** that collects raw FIO output from VMs over SSH. Use them together (`--action gather-results --save-results`) to both fetch and persist.

## Configuration Options

### Action and Targeting

| Option | Default | Description |
|--------|---------|-------------|
| `--action`, `-a` | `run-all` | One of: `deploy`, `status`, `gather-results`, `cleanup`, `run-all` |
| `--start`, `-s` | (required) | Starting namespace index |
| `--end`, `-e` | (required) | Ending namespace index |
| `--storage-class` | (required for `deploy`/`run-all`) | Storage class name |
| `--namespace-prefix` | `fio-benchmark` | Namespace prefix (creates `fio-benchmark-1`, ...) |
| `--vm-name` | `fio-vm` | VM resource name in each namespace |
| `--vm-template` | `examples/vm-templates/fio-vm-template.yaml` | Path to VM template YAML |
| `--concurrency`, `-c` | `20` | Max parallel threads for deploy/status/cleanup |
| `--vm-cpus` | (template) | Override guest vCPUs per FIO VM; the CPU request matches unless `--vm-cpu-request` is set. Not allowed with `spec.instancetype` templates |
| `--vm-cpu-request` | (= `--vm-cpus`) | CPU request per VM when `--vm-cpus` is set |
| `--node-selector` | (none) | Label selector (`key` or `key=value`, comma-separated) choosing candidate nodes |
| `--exclude-node-label` | `virtbench.io/no-fio` | Node label (`key` or `key=value`) marking nodes FIO VMs must not use; `""` disables |
| `--exclude-nodes` | (none) | Comma-separated node names to keep FIO VMs off |
| `--no-balance` | off | Don't spread VMs evenly; let the scheduler place them |
| `--skip-capacity-check` | off | Deploy even if planned VMs exceed a node's free CPU/memory |
| `--placement-timeout` | `600` | Seconds to wait for VMIs to be scheduled before checking placement |

### FIO Parameters

| Option | Default | Description |
|--------|---------|-------------|
| `--fio-runtime` | `300` | FIO test duration in seconds |
| `--fio-rw` | `randwrite` | I/O pattern: `read`, `write`, `randread`, `randwrite`, `randrw`, `rw` |
| `--fio-bs` | `4k` | Block size (e.g., `4k`, `8k`, `64k`, `1M`) |
| `--fio-iodepth` | `64` | I/O queue depth |
| `--fio-numjobs` | `4` | Number of parallel FIO jobs per VM |
| `--fio-size` | `10G` | Test file size per job |

### Result Collection

| Option | Default | Description |
|--------|---------|-------------|
| `--collect-retries` | `8` | Max retries for collecting results from VMs |
| `--collect-retry-delay` | `20` | Delay (seconds) between collection retries |
| `--collect-concurrency` | `5` | Max concurrent result collections |
| `--ssh-pod` | `ssh-test-pod` | SSH helper pod name (must have `sshpass`) |
| `--ssh-pod-ns` | `default` | SSH helper pod namespace |
| `--vm-user` | `cloud-user` | VM SSH user |
| `--vm-password` | `changeme` | VM SSH password |

### Output

| Option | Default | Description |
|--------|---------|-------------|
| `--save-results` | `false` | Save summary JSON/CSV to the output directory |
| `--results-dir` | `results` | Base directory for results |
| `--storage-driver` | `Not-Specified` | Storage driver label (folder component) |
| `--disks-per-vm` | `auto` | Disks-per-VM label (folder component); auto-detected from VM spec |
| `--cleanup` | `false` | Delete VMs and namespaces after `run-all` |

## Disk Space Requirements

FIO creates test files based on `--fio-size` and `--fio-numjobs`:

| Setting | Calculation | Example |
|---------|-------------|---------|
| Test files per VM | `fio-size × fio-numjobs` | 10G × 4 = **40GB** |
| Scratch disk size | Must be > test files | 50GB (in VM template) |

**Data written during test** depends on achieved IOPS:

```
Data Written = Write IOPS × Block Size × Runtime

Example: 21,000 IOPS × 4KB × 60s ≈ 5GB written
```

## Workload Examples

```bash
# Sequential read (max bandwidth)
virtbench fio --start 1 --end 10 --storage-class YOUR-SC \
  --fio-rw read --fio-bs 1M --fio-iodepth 32 --save-results

# Random write (database workload)
virtbench fio --start 1 --end 10 --storage-class YOUR-SC \
  --fio-rw randwrite --fio-bs 4k --fio-iodepth 64 --save-results

# Mixed read/write 50/50 (general workload)
virtbench fio --start 1 --end 10 --storage-class YOUR-SC \
  --fio-rw randrw --fio-bs 4k --fio-iodepth 64 --save-results
```

## Results

Results include per-VM and aggregated metrics:

- **IOPS**: Read/Write operations per second
- **Bandwidth**: Read/Write throughput in MiB/s
- **Latency**: Read/Write latency in milliseconds

### Output Structure

```
{results-dir}/{storage-driver}/{disks-per-vm}/{timestamp}_fio_benchmark_{N}vms/
├── summary_fio_benchmark.json     # Aggregated summary across all VMs
├── fio_benchmark_results.json     # Per-VM results (JSON array)
├── fio_benchmark_results.csv      # Per-VM results (CSV, node as last column)
├── vm_node_placement.json         # VM-to-node map and per-node counts
└── per-vm-results/                # Raw FIO output per VM
    ├── fio-benchmark-1/
    │   └── fio_raw.json
    └── ...
```

Example: `results/portworx-3.6/2-disk/20260308-141522_fio_benchmark_10vms/`. `--storage-driver` and `--disks-per-vm` are folder components only - they have no functional effect on the test.

### View in Dashboard

Generate the dashboard from the saved results directory after the run.

The dashboard displays:

- KPI summary cards (Total IOPS, Bandwidth, Latency)
- IOPS + Latency dual-axis chart
- Per-VM breakdown with clickable namespaces

