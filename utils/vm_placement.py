#!/usr/bin/env python3
"""
Balanced VM placement across nodes, for any Kubernetes distribution.

Spreads benchmark VMs evenly over the eligible nodes by pinning each VM to one
node (round-robin by namespace index), then reads back where the VMIs actually
landed.

Candidate nodes:
  * nodes matching --node-selector, if given; otherwise
  * nodes with the node-role.kubernetes.io/worker label (any value), if any
    node has it (OpenShift, RKE1, some installers); otherwise
  * nodes without a control-plane/master role label (kubeadm, kind, k3s,
    RKE2, EKS/GKE/AKS); and if that leaves none (single-node or compact
    clusters), all nodes.

A candidate is eligible when it is Ready, not cordoned, schedulable for
KubeVirt (kubevirt.io/schedulable=true), matches the VM template's own
nodeSelector, does not carry the exclude label, is not excluded by name, and
has no NoSchedule/NoExecute taint the VM does not tolerate.

To keep the benchmark off a node, label it:

    kubectl label node <node> virtbench.io/no-fio=true
"""
import json
import logging
import math
import re
from collections import Counter, OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

from utils.common import run_kubectl_command

HOSTNAME_LABEL = 'kubernetes.io/hostname'
ZONE_LABEL = 'topology.kubernetes.io/zone'
WORKER_LABEL = 'node-role.kubernetes.io/worker'
CONTROL_PLANE_LABELS = ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master')
KUBEVIRT_SCHEDULABLE_LABEL = 'kubevirt.io/schedulable'
BLOCKING_EFFECTS = ('NoSchedule', 'NoExecute')
DEFAULT_EXCLUDE_LABEL = 'virtbench.io/no-fio'
TERMINAL_VMI_PHASES = ('Succeeded', 'Failed')

# KubeVirt defaults used to estimate a virt-launcher pod's requests when the
# template does not set them.
KUBEVIRT_CPU_ALLOCATION_RATIO = 10
VIRT_LAUNCHER_MEMORY_OVERHEAD = 300 * 1024 ** 2  # conservative estimate, bytes


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def parse_label_selector(selector: Optional[str]) -> List[Tuple[str, Optional[str]]]:
    """
    Parse a simple label selector: comma-separated 'key' or 'key=value' terms.
    Returns a list of (key, value-or-None). Empty input -> [].
    """
    terms = []
    for part in (selector or '').split(','):
        part = part.strip()
        if not part:
            continue
        if '=' in part:
            key, value = part.split('=', 1)
            terms.append((key.strip().rstrip('='), value.strip()))
        else:
            terms.append((part, None))
    return terms


def labels_match(labels: Dict[str, str], terms: List[Tuple[str, Optional[str]]]) -> bool:
    """True if labels satisfy every (key, value) term (value None = key exists)."""
    for key, value in terms:
        if key not in labels:
            return False
        if value is not None and labels[key] != value:
            return False
    return True


def parse_quantity(value) -> float:
    """Parse a Kubernetes resource quantity ('500m', '2', '4Gi', '1e3') into a float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    binary = {'Ki': 2 ** 10, 'Mi': 2 ** 20, 'Gi': 2 ** 30, 'Ti': 2 ** 40, 'Pi': 2 ** 50, 'Ei': 2 ** 60}
    decimal = {'n': 1e-9, 'u': 1e-6, 'm': 1e-3, 'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9,
               'T': 1e12, 'P': 1e15, 'E': 1e18}
    for suffix, mult in binary.items():
        if s.endswith(suffix):
            return float(s[:-2]) * mult
    if s and s[-1] in decimal and not re.fullmatch(r'[-+]?[0-9.]+[eE][-+]?[0-9]+', s):
        return float(s[:-1]) * decimal[s[-1]]
    return float(s)


def _kubectl_json(args: List[str], logger: Optional[logging.Logger] = None
                  ) -> Tuple[Optional[Dict], str]:
    """Run kubectl and parse JSON. Returns (data, error); never raises."""
    try:
        rc, stdout, stderr = run_kubectl_command(args + ['-o', 'json'], check=False, logger=logger)
    except Exception as e:  # timeouts, missing kubectl
        return None, str(e)
    if rc != 0:
        return None, (stderr or '').strip() or f'kubectl exited {rc}'
    try:
        return json.loads(stdout or '{}'), ''
    except ValueError as e:
        return None, f'invalid JSON from kubectl: {e}'


def _vm_docs(vm_yaml: str) -> Tuple[List, List[Dict]]:
    docs = [d for d in yaml.safe_load_all(vm_yaml) if d is not None]
    vms = [d for d in docs if isinstance(d, dict) and d.get('kind') == 'VirtualMachine']
    return docs, vms


def _pod_template(vm: Dict) -> Dict:
    return vm.setdefault('spec', {}).setdefault('template', {}).setdefault('spec', {})


# ---------------------------------------------------------------------------
# Reading the VM template
# ---------------------------------------------------------------------------

def get_vm_template_info(vm_yaml: str) -> Dict:
    """
    Describe the first VirtualMachine in a rendered manifest:
    tolerations, nodeSelector, placement constraints, instancetype, vCPUs and
    estimated per-VM CPU/memory requests (cores, bytes).
    Raises ValueError if the manifest has no VirtualMachine.
    """
    _, vms = _vm_docs(vm_yaml)
    if not vms:
        raise ValueError("VM template contains no 'kind: VirtualMachine' document")
    vm = vms[0]
    spec = vm.get('spec', {}) or {}
    pod = (spec.get('template', {}) or {}).get('spec', {}) or {}
    domain = pod.get('domain', {}) or {}
    cpu = domain.get('cpu', {}) or {}
    resources = domain.get('resources', {}) or {}
    requests = resources.get('requests', {}) or {}

    vcpus = None
    if any(k in cpu for k in ('cores', 'sockets', 'threads')):
        vcpus = int(cpu.get('cores', 1) or 1) * int(cpu.get('sockets', 1) or 1) * \
            int(cpu.get('threads', 1) or 1)

    if 'cpu' in requests:
        cpu_request = parse_quantity(requests['cpu'])
    elif vcpus:
        cpu_request = vcpus / KUBEVIRT_CPU_ALLOCATION_RATIO
    else:
        cpu_request = 0.1
    memory = requests.get('memory') or (domain.get('memory', {}) or {}).get('guest')
    memory_request = parse_quantity(memory) + VIRT_LAUNCHER_MEMORY_OVERHEAD if memory else None

    affinity = pod.get('affinity', {}) or {}
    constraints = []
    if (affinity.get('nodeAffinity', {}) or {}).get('requiredDuringSchedulingIgnoredDuringExecution'):
        constraints.append('required nodeAffinity')
    if (affinity.get('podAntiAffinity', {}) or {}).get('requiredDuringSchedulingIgnoredDuringExecution'):
        constraints.append('required podAntiAffinity')
    if pod.get('topologySpreadConstraints'):
        constraints.append('topologySpreadConstraints')

    return {
        'tolerations': pod.get('tolerations', []) or [],
        'node_selector': {k: str(v) for k, v in (pod.get('nodeSelector', {}) or {}).items()},
        'constraints': constraints,
        'instancetype': (spec.get('instancetype') or {}).get('name') if spec.get('instancetype') else None,
        'vcpus': vcpus,
        'cpu_request': cpu_request,
        'memory_request': memory_request,
    }


# ---------------------------------------------------------------------------
# Node selection
# ---------------------------------------------------------------------------

def _toleration_matches(toleration: Dict, taint: Dict) -> bool:
    """Return True if a pod toleration tolerates a node taint (Kubernetes rules)."""
    effect = toleration.get('effect')
    if effect and effect != taint.get('effect'):
        return False
    # A NoExecute toleration with tolerationSeconds only delays eviction: the
    # VM would be evicted mid-run, so treat it as not tolerated.
    if taint.get('effect') == 'NoExecute' and toleration.get('tolerationSeconds') is not None:
        return False
    key = toleration.get('key')
    operator = toleration.get('operator', 'Equal')
    if not key:
        # Empty key with Exists tolerates every taint
        return operator == 'Exists'
    if key != taint.get('key'):
        return False
    if operator == 'Exists':
        return True
    return (toleration.get('value') or '') == (taint.get('value') or '')


def _is_ready(node: Dict) -> bool:
    for cond in node.get('status', {}).get('conditions', []) or []:
        if cond.get('type') == 'Ready':
            return cond.get('status') == 'True'
    return False


def _labels(node: Dict) -> Dict[str, str]:
    return node.get('metadata', {}).get('labels', {}) or {}


def candidate_nodes(nodes: List[Dict], node_selector: Optional[str] = None
                    ) -> Tuple[List[Dict], str]:
    """
    Pick the candidate pool (before eligibility filters).
    Returns (nodes, description of how they were chosen).
    """
    terms = parse_label_selector(node_selector)
    if terms:
        return [n for n in nodes if labels_match(_labels(n), terms)], f'nodes matching {node_selector}'
    workers = [n for n in nodes if WORKER_LABEL in _labels(n)]
    if workers:
        return workers, f'nodes labeled {WORKER_LABEL}'
    non_cp = [n for n in nodes if not any(l in _labels(n) for l in CONTROL_PLANE_LABELS)]
    if non_cp:
        return non_cp, 'nodes without a control-plane role (no worker role labels in this cluster)'
    return list(nodes), 'all nodes (every node is a control-plane node)'


def select_eligible_nodes(nodes: List[Dict], tolerations: Optional[List[Dict]] = None,
                          exclude: Optional[Iterable[str]] = None,
                          exclude_label: Optional[str] = DEFAULT_EXCLUDE_LABEL,
                          template_node_selector: Optional[Dict[str, str]] = None,
                          node_selector: Optional[str] = None) -> Dict:
    """
    Filter node objects (from `kubectl get nodes -o json`) down to eligible ones.

    Returns a dict with:
        eligible: list of {'name', 'hostname', 'zone', 'allocatable_cpu',
                  'allocatable_memory'} sorted by name
        skipped: node name -> reason it was left out (candidates only)
        pool: how candidates were chosen
        unknown_excludes: --exclude-nodes names that match no node
    """
    tolerations = tolerations or []
    exclude = set(exclude or [])
    exclude_terms = parse_label_selector(exclude_label)
    template_selector = {k: v for k, v in (template_node_selector or {}).items() if k != HOSTNAME_LABEL}
    # Only enforce KubeVirt's schedulable label when KubeVirt labels nodes at all.
    kubevirt_labels_nodes = any(KUBEVIRT_SCHEDULABLE_LABEL in _labels(n) for n in nodes)

    all_names = {n.get('metadata', {}).get('name') for n in nodes}
    pool, pool_desc = candidate_nodes(nodes, node_selector)
    eligible, skipped = [], {}

    for node in pool:
        meta = node.get('metadata', {})
        name = meta.get('name')
        labels = _labels(node)
        if name in exclude:
            skipped[name] = 'excluded by --exclude-nodes'
            continue
        if exclude_terms and labels_match(labels, exclude_terms):
            skipped[name] = f'has exclude label {exclude_label}'
            continue
        if not _is_ready(node):
            skipped[name] = 'NotReady'
            continue
        if node.get('spec', {}).get('unschedulable'):
            skipped[name] = 'cordoned (unschedulable)'
            continue
        if kubevirt_labels_nodes and labels.get(KUBEVIRT_SCHEDULABLE_LABEL) != 'true':
            skipped[name] = f'not schedulable for KubeVirt ({KUBEVIRT_SCHEDULABLE_LABEL} != true)'
            continue
        mismatched = [f'{k}={v}' for k, v in template_selector.items() if labels.get(k) != v]
        if mismatched:
            skipped[name] = f"does not match VM template nodeSelector {', '.join(mismatched)}"
            continue
        blocking = [
            t for t in node.get('spec', {}).get('taints', []) or []
            if t.get('effect') in BLOCKING_EFFECTS
            and not any(_toleration_matches(tol, t) for tol in tolerations)
        ]
        if blocking:
            desc = ', '.join(
                f"{t.get('key')}{'=' + t['value'] if t.get('value') else ''}:{t.get('effect')}"
                for t in blocking
            )
            skipped[name] = f'taint not tolerated by the VM: {desc}'
            continue
        allocatable = node.get('status', {}).get('allocatable', {}) or {}
        eligible.append({
            'name': name,
            'hostname': labels.get(HOSTNAME_LABEL, name),
            'zone': labels.get(ZONE_LABEL),
            'allocatable_cpu': parse_quantity(allocatable.get('cpu')),
            'allocatable_memory': parse_quantity(allocatable.get('memory')),
        })

    eligible.sort(key=lambda n: n['name'])
    return {
        'eligible': eligible,
        'skipped': skipped,
        'pool': pool_desc,
        'unknown_excludes': sorted(exclude - all_names),
    }


def get_eligible_nodes(tolerations: Optional[List[Dict]] = None,
                       exclude: Optional[Iterable[str]] = None,
                       exclude_label: Optional[str] = DEFAULT_EXCLUDE_LABEL,
                       template_node_selector: Optional[Dict[str, str]] = None,
                       node_selector: Optional[str] = None,
                       logger: Optional[logging.Logger] = None) -> Dict:
    """Query nodes from the cluster and return select_eligible_nodes() output."""
    data, err = _kubectl_json(['get', 'nodes'], logger)
    if data is None:
        raise RuntimeError(f"Failed to list nodes: {err}")
    return select_eligible_nodes(data.get('items', []), tolerations, exclude, exclude_label,
                                 template_node_selector, node_selector)


# ---------------------------------------------------------------------------
# Planning, capacity and topology checks
# ---------------------------------------------------------------------------

def plan_balanced_placement(vm_keys: List[str], nodes: List[Dict],
                            start_index: int = 0) -> "OrderedDict[str, Dict]":
    """
    Assign VMs to nodes round-robin so per-node counts differ by at most one.

    start_index is the position of vm_keys[0] in the whole test range (for
    namespace prefix-N, use N-1). Deploying 1-10 and then 11-20 therefore gives
    the same spread as deploying 1-20 at once.
    """
    if not nodes:
        raise ValueError("No eligible nodes for VM placement")
    return OrderedDict(
        (key, nodes[(start_index + i) % len(nodes)]) for i, key in enumerate(vm_keys)
    )


def count_per_node(assignment: Dict[str, str]) -> Dict[str, int]:
    """Count VMs per node from a vm_key -> node name mapping (sorted by node)."""
    return dict(sorted(Counter(assignment.values()).items()))


def pod_requests_by_node(pods: List[Dict]) -> Dict[str, Dict[str, float]]:
    """Sum CPU/memory requests of non-terminated pods per node."""
    used: Dict[str, Dict[str, float]] = {}
    for pod in pods:
        node = pod.get('spec', {}).get('nodeName')
        if not node or pod.get('status', {}).get('phase') in TERMINAL_VMI_PHASES:
            continue
        spec = pod.get('spec', {})
        cpu = mem = 0.0
        for c in spec.get('containers', []) or []:
            req = (c.get('resources', {}) or {}).get('requests', {}) or {}
            cpu += parse_quantity(req.get('cpu'))
            mem += parse_quantity(req.get('memory'))
        for c in spec.get('initContainers', []) or []:
            req = (c.get('resources', {}) or {}).get('requests', {}) or {}
            cpu = max(cpu, parse_quantity(req.get('cpu')))
            mem = max(mem, parse_quantity(req.get('memory')))
        overhead = spec.get('overhead', {}) or {}
        cpu += parse_quantity(overhead.get('cpu'))
        mem += parse_quantity(overhead.get('memory'))
        slot = used.setdefault(node, {'cpu': 0.0, 'memory': 0.0})
        slot['cpu'] += cpu
        slot['memory'] += mem
    return used


def check_capacity(plan: Dict[str, Dict], nodes: List[Dict], used: Dict[str, Dict[str, float]],
                   cpu_request: float, memory_request: Optional[float]) -> List[str]:
    """
    Return one problem string per node that cannot fit its planned VMs, given
    allocatable minus what running pods already request.
    """
    counts = Counter(n['name'] for n in plan.values())
    problems = []
    for node in nodes:
        count = counts.get(node['name'], 0)
        if not count:
            continue
        u = used.get(node['name'], {'cpu': 0.0, 'memory': 0.0})
        free_cpu = node['allocatable_cpu'] - u['cpu']
        need_cpu = count * cpu_request
        if need_cpu > free_cpu + 1e-9:
            problems.append(f"{node['name']}: {count} VM(s) need {need_cpu:g} CPU, "
                            f"{max(free_cpu, 0):.2f} free of {node['allocatable_cpu']:g} allocatable")
        if memory_request:
            free_mem = node['allocatable_memory'] - u['memory']
            need_mem = count * memory_request
            if need_mem > free_mem:
                problems.append(f"{node['name']}: {count} VM(s) need ~{need_mem / 2**30:.1f}Gi memory, "
                                f"{max(free_mem, 0) / 2**30:.1f}Gi free")
    return problems


def get_pod_requests_by_node(logger: Optional[logging.Logger] = None
                             ) -> Tuple[Optional[Dict[str, Dict[str, float]]], str]:
    data, err = _kubectl_json(['get', 'pods', '--all-namespaces',
                               '--field-selector=status.phase!=Succeeded,status.phase!=Failed'], logger)
    if data is None:
        return None, err
    return pod_requests_by_node(data.get('items', [])), ''


def storage_topology_warning(storage_class: Optional[Dict], nodes: List[Dict]) -> Optional[str]:
    """
    Warn when hard pinning can conflict with where volumes are provisioned:
    an Immediate-binding StorageClass with eligible nodes in more than one zone.
    """
    if not storage_class:
        return None
    mode = storage_class.get('volumeBindingMode', 'Immediate')
    zones = sorted({n['zone'] for n in nodes if n.get('zone')})
    if mode == 'Immediate' and len(zones) > 1:
        name = storage_class.get('metadata', {}).get('name')
        return (f"StorageClass {name} binds volumes Immediately and the chosen nodes span zones "
                f"{', '.join(zones)}. A zonal volume may be created in a different zone than the "
                f"node its VM is pinned to, leaving the VM Pending. Use a WaitForFirstConsumer "
                f"StorageClass, restrict nodes to one zone with --node-selector, or use --no-balance.")
    return None


def get_storage_class(name: str, logger: Optional[logging.Logger] = None) -> Optional[Dict]:
    data, _ = _kubectl_json(['get', 'storageclass', name], logger)
    return data


# ---------------------------------------------------------------------------
# Manifest changes
# ---------------------------------------------------------------------------

class _LiteralDumper(yaml.SafeDumper):
    """Keep multi-line strings (cloud-init user data) readable as literal blocks."""


# Strings PyYAML (YAML 1.1) would write unquoted but Kubernetes' YAML 1.2
# parser reads as numbers, e.g. '1e3'.
_YAML12_NUMBER = re.compile(r'[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?')


def _str_representer(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    if _YAML12_NUMBER.fullmatch(data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style="'")
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


_LiteralDumper.add_representer(str, _str_representer)


def _dump(docs: List) -> str:
    return yaml.dump_all(docs, Dumper=_LiteralDumper, sort_keys=False, default_flow_style=False)


def set_vm_cpus(vm_yaml: str, cores: int, cpu_request: Optional[str] = None) -> str:
    """
    Return the manifest with every VirtualMachine given `cores` guest vCPUs
    (domain.cpu.cores, 1 socket / 1 thread) and a CPU request of `cpu_request`
    (default: equal to cores). Existing CPU limits are kept consistent.
    Memory is left unchanged.

    Raises ValueError for invalid values, manifests without a VirtualMachine,
    or VMs that use an instancetype (KubeVirt rejects domain.cpu there).
    """
    if cores is None or int(cores) < 1:
        raise ValueError("--vm-cpus must be at least 1")
    request = str(cpu_request) if cpu_request is not None else str(cores)
    if parse_quantity(request) <= 0:
        raise ValueError("--vm-cpu-request must be greater than 0")
    docs, vms = _vm_docs(vm_yaml)
    if not vms:
        raise ValueError("VM template contains no 'kind: VirtualMachine' document")
    for vm in vms:
        if (vm.get('spec', {}) or {}).get('instancetype'):
            raise ValueError("VM template uses spec.instancetype, which sets the CPU count; "
                             "--vm-cpus cannot be used with it (choose a different instancetype instead)")
        domain = _pod_template(vm).setdefault('domain', {})
        cpu = domain.get('cpu') or {}
        cpu['cores'] = int(cores)
        cpu.pop('sockets', None)
        cpu.pop('threads', None)
        domain['cpu'] = cpu
        resources = domain.get('resources') or {}
        requests = resources.get('requests') or {}
        requests['cpu'] = request
        resources['requests'] = requests
        limits = resources.get('limits') or {}
        if 'cpu' in limits:
            # Keep an existing limit consistent: never below the request, and
            # equal to the vCPU count (as dedicated-CPU VMs require).
            limits['cpu'] = request if parse_quantity(request) > int(cores) else str(cores)
        domain['resources'] = resources
    return _dump(docs)


def pin_vm_to_node(vm_yaml: str, hostname: str) -> str:
    """
    Return the manifest with every VirtualMachine pinned to the given node via
    spec.template.spec.nodeSelector[kubernetes.io/hostname].
    """
    docs, vms = _vm_docs(vm_yaml)
    if not vms:
        raise ValueError("VM template contains no 'kind: VirtualMachine' document")
    for vm in vms:
        pod_spec = _pod_template(vm)
        selector = pod_spec.get('nodeSelector') or {}
        selector[HOSTNAME_LABEL] = hostname
        pod_spec['nodeSelector'] = selector
    return _dump(docs)


# ---------------------------------------------------------------------------
# Reading back what is deployed and running
# ---------------------------------------------------------------------------

def _list_in_namespaces(kind: str, namespaces: List[str], logger: Optional[logging.Logger] = None
                        ) -> Tuple[List[Dict], List[str]]:
    """
    List objects of `kind` in the given namespaces. Tries one cluster-wide list
    first and falls back to per-namespace lists (for users without cluster-wide
    list rights). Returns (items, errors).
    """
    wanted = set(namespaces)
    data, err = _kubectl_json(['get', kind, '--all-namespaces'], logger)
    if data is not None:
        return [o for o in data.get('items', [])
                if o.get('metadata', {}).get('namespace') in wanted], []
    items, errors = [], []
    for ns in namespaces:
        d, e = _kubectl_json(['get', kind, '-n', ns], logger)
        if d is None:
            if 'NotFound' not in e and 'not found' not in e:
                errors.append(f'{ns}: {e}')
            continue
        items.extend(d.get('items', []))
    return items, errors


def _cpu_count(cpu: Dict) -> Optional[int]:
    if not cpu or not any(k in cpu for k in ('cores', 'sockets', 'threads')):
        return None
    return int(cpu.get('cores', 1) or 1) * int(cpu.get('sockets', 1) or 1) * int(cpu.get('threads', 1) or 1)


def read_vmi_state(vmis: List[Dict], vms: List[Dict], namespaces: List[str], vm_name: str
                   ) -> Dict[str, Dict]:
    """
    Combine VMI (what is running) and VM (what was applied) objects into
    namespace -> {
        'exists': VM object exists,
        'phase': VMI phase or None,
        'node': node the VMI runs on (None if not running / terminal),
        'cores': vCPUs of the running VMI (falls back to VM spec),
        'cpu_request': CPU request of the running VMI (falls back to VM spec),
        'hostname': pinned hostname of the running VMI (falls back to VM spec),
        'restart_required': VM spec changed but the VMI was not restarted,
    }
    """
    wanted = set(namespaces)
    state = {ns: {'exists': False, 'phase': None, 'node': None, 'cores': None,
                  'cpu_request': None, 'hostname': None, 'restart_required': False}
             for ns in namespaces}

    for vm in vms:
        meta = vm.get('metadata', {})
        ns = meta.get('namespace')
        if ns not in wanted or meta.get('name') != vm_name:
            continue
        pod = (vm.get('spec', {}).get('template', {}) or {}).get('spec', {}) or {}
        domain = pod.get('domain', {}) or {}
        s = state[ns]
        s['exists'] = True
        s['cores'] = _cpu_count(domain.get('cpu', {}) or {})
        req = ((domain.get('resources', {}) or {}).get('requests', {}) or {}).get('cpu')
        s['cpu_request'] = str(req) if req is not None else None
        s['hostname'] = (pod.get('nodeSelector') or {}).get(HOSTNAME_LABEL)
        s['restart_required'] = any(
            c.get('type') == 'RestartRequired' and c.get('status') == 'True'
            for c in vm.get('status', {}).get('conditions', []) or []
        )

    for vmi in vmis:
        meta = vmi.get('metadata', {})
        ns = meta.get('namespace')
        if ns not in wanted or meta.get('name') != vm_name:
            continue
        spec = vmi.get('spec', {}) or {}
        domain = spec.get('domain', {}) or {}
        status = vmi.get('status', {}) or {}
        s = state[ns]
        s['exists'] = True
        s['phase'] = status.get('phase')
        s['node'] = None if s['phase'] in TERMINAL_VMI_PHASES else (status.get('nodeName') or None)
        cores = _cpu_count(domain.get('cpu', {}) or {})
        if cores is not None:
            s['cores'] = cores
        req = ((domain.get('resources', {}) or {}).get('requests', {}) or {}).get('cpu')
        if req is not None:
            s['cpu_request'] = str(req)
        s['hostname'] = (spec.get('nodeSelector') or {}).get(HOSTNAME_LABEL)
    return state


def plan_from_state(state: Dict[str, Dict], hostname_to_node: Dict[str, str]
                    ) -> Optional["OrderedDict[str, Dict]"]:
    """
    Rebuild the placement plan from what is deployed. Returns None unless every
    existing VM is pinned to a hostname (i.e. it was deployed with balanced placement).
    """
    present = {ns: s for ns, s in state.items() if s.get('exists')}
    if not present or any(not s.get('hostname') for s in present.values()):
        return None
    return OrderedDict(
        (ns, {'name': hostname_to_node.get(s['hostname'], s['hostname']), 'hostname': s['hostname']})
        for ns, s in sorted(present.items())
    )


def summarize_values(values: Iterable):
    """Single value if all agree, sorted list if they differ, None if unknown."""
    distinct = sorted({v for v in values if v is not None}, key=str)
    if not distinct:
        return None
    return distinct[0] if len(distinct) == 1 else distinct


def get_deployed_state(namespaces: List[str], vm_name: str,
                       logger: Optional[logging.Logger] = None) -> Dict:
    """
    Read VMs, VMIs and nodes from the cluster. Never raises.
    Returns {'state', 'plan', 'vm_cpus', 'vm_cpu_request', 'errors'}.
    """
    vmis, err_vmi = _list_in_namespaces('vmi', namespaces, logger)
    vms, err_vm = _list_in_namespaces('vm', namespaces, logger)
    state = read_vmi_state(vmis, vms, namespaces, vm_name)

    hostname_to_node = {}
    nodes, err = _kubectl_json(['get', 'nodes'], logger)
    errors = err_vmi + err_vm
    if nodes is not None:
        for n in nodes.get('items', []):
            meta = n.get('metadata', {})
            hostname_to_node[(meta.get('labels', {}) or {}).get(HOSTNAME_LABEL, meta.get('name'))] = \
                meta.get('name')
    else:
        errors.append(f'nodes: {err} (assuming hostname == node name)')

    present = [s for s in state.values() if s['exists']]
    return {
        'state': state,
        'plan': plan_from_state(state, hostname_to_node),
        'vm_cpus': summarize_values(s['cores'] for s in present),
        'vm_cpu_request': summarize_values(s['cpu_request'] for s in present),
        'errors': errors,
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_placement(planned: Dict[str, str], state: Dict[str, Dict],
                     failed_to_apply: Iterable[str] = ()) -> Dict:
    """
    Compare planned vs actual placement.

    Args:
        planned: vm_key -> planned node name (empty dict if no plan)
        state:   vm_key -> {'exists', 'phase', 'node', 'restart_required'} (see read_vmi_state);
                 a plain vm_key -> node-name mapping is also accepted
        failed_to_apply: VMs whose manifest could not be applied

    Returns:
        dict with balanced (True/False, or None when nothing was planned),
        per-node counts, not_created, unscheduled, terminal, mismatches,
        restart_required.
    """
    norm = {}
    for k, v in state.items():
        norm[k] = v if isinstance(v, dict) else {'exists': True, 'phase': 'Running' if v else None,
                                                  'node': v, 'restart_required': False}
    failed = set(failed_to_apply)
    not_created = sorted(k for k, s in norm.items() if k in failed or not s.get('exists'))
    terminal = sorted(k for k, s in norm.items() if s.get('phase') in TERMINAL_VMI_PHASES)
    unscheduled = sorted(k for k, s in norm.items()
                         if k not in not_created and k not in terminal and not s.get('node'))
    restart_required = sorted(k for k, s in norm.items() if s.get('restart_required'))
    scheduled = {k: s['node'] for k, s in norm.items() if s.get('node')}
    mismatches = {
        k: {'planned': planned[k], 'actual': scheduled[k]}
        for k in planned if k in scheduled and scheduled[k] != planned[k]
    }
    counts = count_per_node(scheduled)
    for node in set(planned.values()):
        counts.setdefault(node, 0)
    counts = dict(sorted(counts.items()))

    if not planned:
        balanced = None  # nothing was planned or enforced
    else:
        balanced = bool(counts) and not (not_created or unscheduled or terminal or mismatches) and \
            (max(counts.values()) - min(counts.values()) <= 1)
    return {
        'balanced': balanced,
        'per_node_counts': counts,
        'not_created': not_created,
        'unscheduled': unscheduled,
        'terminal': terminal,
        'mismatches': mismatches,
        'restart_required': restart_required,
    }
