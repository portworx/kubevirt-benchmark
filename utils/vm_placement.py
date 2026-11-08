#!/usr/bin/env python3
"""
Balanced VM placement across worker nodes.

Spreads benchmark VMs evenly over the eligible worker nodes by pinning each VM
to one node (round-robin), then reads back where the VMIs actually landed.

A worker node is eligible when it is Ready, schedulable (not cordoned), does
not carry the exclude label, and has no NoSchedule/NoExecute taint that the VM
does not tolerate. To keep the benchmark off a node, label it:

    kubectl label node <node> virtbench.io/no-fio=true

(or pass its name in the exclude list, or taint it).
"""
import json
import logging
from collections import Counter, OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

from utils.common import run_kubectl_command

HOSTNAME_LABEL = 'kubernetes.io/hostname'
WORKER_LABEL = 'node-role.kubernetes.io/worker'
BLOCKING_EFFECTS = ('NoSchedule', 'NoExecute')
DEFAULT_EXCLUDE_LABEL = 'virtbench.io/no-fio'


def parse_label_selector(label: Optional[str]) -> Optional[Tuple[str, Optional[str]]]:
    """Parse 'key' or 'key=value' into (key, value-or-None). Empty -> None."""
    label = (label or '').strip()
    if not label:
        return None
    if '=' in label:
        key, value = label.split('=', 1)
        return key.strip(), value.strip()
    return label, None


def _has_label(labels: Dict[str, str], selector: Optional[Tuple[str, Optional[str]]]) -> bool:
    if not selector:
        return False
    key, value = selector
    if key not in labels:
        return False
    return value is None or labels[key] == value


# ---------------------------------------------------------------------------
# Node selection
# ---------------------------------------------------------------------------

def _toleration_matches(toleration: Dict, taint: Dict) -> bool:
    """Return True if a pod toleration tolerates a node taint (Kubernetes rules)."""
    effect = toleration.get('effect')
    if effect and effect != taint.get('effect'):
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
    for cond in node.get('status', {}).get('conditions', []):
        if cond.get('type') == 'Ready':
            return cond.get('status') == 'True'
    return False


def select_eligible_nodes(nodes: List[Dict], tolerations: Optional[List[Dict]] = None,
                          exclude: Optional[Iterable[str]] = None,
                          exclude_label: Optional[str] = DEFAULT_EXCLUDE_LABEL
                          ) -> Tuple[List[Dict], Dict[str, str]]:
    """
    Filter node objects (from `kubectl get nodes -o json`) down to eligible ones.

    Returns:
        (eligible, skipped) where eligible is a list of
        {'name': ..., 'hostname': ...} sorted by name, and skipped maps
        node name -> reason it was left out.
    """
    tolerations = tolerations or []
    exclude = set(exclude or [])
    label_selector = parse_label_selector(exclude_label)
    eligible, skipped = [], {}

    for node in nodes:
        meta = node.get('metadata', {})
        name = meta.get('name')
        if name in exclude:
            skipped[name] = 'excluded by --exclude-nodes'
            continue
        if _has_label(meta.get('labels', {}) or {}, label_selector):
            skipped[name] = f'has exclude label {exclude_label}'
            continue
        if not _is_ready(node):
            skipped[name] = 'NotReady'
            continue
        if node.get('spec', {}).get('unschedulable'):
            skipped[name] = 'cordoned (unschedulable)'
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
            skipped[name] = f'taint {desc}'
            continue
        eligible.append({
            'name': name,
            'hostname': meta.get('labels', {}).get(HOSTNAME_LABEL, name),
        })

    eligible.sort(key=lambda n: n['name'])
    return eligible, skipped


def get_eligible_worker_nodes(tolerations: Optional[List[Dict]] = None,
                              exclude: Optional[Iterable[str]] = None,
                              exclude_label: Optional[str] = DEFAULT_EXCLUDE_LABEL,
                              logger: Optional[logging.Logger] = None
                              ) -> Tuple[List[Dict], Dict[str, str]]:
    """Query worker nodes from the cluster and return (eligible, skipped)."""
    rc, stdout, stderr = run_kubectl_command(
        ['get', 'nodes', '-l', f'{WORKER_LABEL}=', '-o', 'json'],
        check=False, logger=logger
    )
    if rc != 0:
        raise RuntimeError(f"Failed to list worker nodes: {stderr.strip()}")
    nodes = json.loads(stdout or '{}').get('items', [])
    return select_eligible_nodes(nodes, tolerations, exclude, exclude_label)


# ---------------------------------------------------------------------------
# Planning and manifest injection
# ---------------------------------------------------------------------------

def plan_balanced_placement(vm_keys: List[str], nodes: List[Dict]) -> "OrderedDict[str, Dict]":
    """
    Assign VMs to nodes round-robin so per-node counts differ by at most one.

    Args:
        vm_keys: Ordered identifiers for the VMs (e.g. namespaces)
        nodes: Eligible nodes from select_eligible_nodes()

    Returns:
        OrderedDict vm_key -> node dict
    """
    if not nodes:
        raise ValueError("No eligible worker nodes for VM placement")
    return OrderedDict((key, nodes[i % len(nodes)]) for i, key in enumerate(vm_keys))


def count_per_node(assignment: Dict[str, str]) -> Dict[str, int]:
    """Count VMs per node from a vm_key -> node name mapping (sorted by node)."""
    return dict(sorted(Counter(assignment.values()).items()))


def get_vm_tolerations(vm_yaml: str) -> List[Dict]:
    """Return the tolerations declared in the VirtualMachine template."""
    for doc in yaml.safe_load_all(vm_yaml):
        if isinstance(doc, dict) and doc.get('kind') == 'VirtualMachine':
            return doc.get('spec', {}).get('template', {}).get('spec', {}).get('tolerations', []) or []
    return []


class _LiteralDumper(yaml.SafeDumper):
    """Keep multi-line strings (cloud-init user data) readable as literal blocks."""


def _str_representer(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


_LiteralDumper.add_representer(str, _str_representer)


def set_vm_cpus(vm_yaml: str, cores: int) -> str:
    """
    Return the manifest with every VirtualMachine given `cores` guest vCPUs
    (domain.cpu.cores, 1 socket / 1 thread) and a matching CPU request so the
    scheduler reserves real CPU for each vCPU. Memory is left unchanged.
    """
    if cores < 1:
        raise ValueError("vCPU count must be at least 1")
    docs = [d for d in yaml.safe_load_all(vm_yaml) if d is not None]
    found = False
    for doc in docs:
        if isinstance(doc, dict) and doc.get('kind') == 'VirtualMachine':
            domain = doc.setdefault('spec', {}).setdefault('template', {}) \
                .setdefault('spec', {}).setdefault('domain', {})
            cpu = domain.get('cpu') or {}
            cpu['cores'] = cores
            cpu.pop('sockets', None)
            cpu.pop('threads', None)
            domain['cpu'] = cpu
            resources = domain.get('resources') or {}
            requests = resources.get('requests') or {}
            requests['cpu'] = str(cores)
            resources['requests'] = requests
            limits = resources.get('limits') or {}
            if 'cpu' in limits:
                limits['cpu'] = str(cores)
            domain['resources'] = resources
            found = True
    if not found:
        raise ValueError("No VirtualMachine found in manifest to set vCPUs")
    return yaml.dump_all(docs, Dumper=_LiteralDumper, sort_keys=False, default_flow_style=False)


def pin_vm_to_node(vm_yaml: str, hostname: str) -> str:
    """
    Return the manifest with every VirtualMachine pinned to the given node via
    spec.template.spec.nodeSelector[kubernetes.io/hostname].
    """
    docs = [d for d in yaml.safe_load_all(vm_yaml) if d is not None]
    pinned = False
    for doc in docs:
        if isinstance(doc, dict) and doc.get('kind') == 'VirtualMachine':
            pod_spec = doc.setdefault('spec', {}).setdefault('template', {}).setdefault('spec', {})
            selector = pod_spec.get('nodeSelector') or {}
            selector[HOSTNAME_LABEL] = hostname
            pod_spec['nodeSelector'] = selector
            pinned = True
    if not pinned:
        raise ValueError("No VirtualMachine found in manifest to pin")
    return yaml.dump_all(docs, Dumper=_LiteralDumper, sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def get_vmi_node_map(namespaces: List[str], vm_name: str,
                     logger: Optional[logging.Logger] = None) -> Dict[str, Optional[str]]:
    """Return namespace -> node the VMI runs on (None if no VMI or not scheduled yet)."""
    wanted = set(namespaces)
    rc, stdout, stderr = run_kubectl_command(
        ['get', 'vmi', '--all-namespaces', '-o', 'json'], check=False, logger=logger
    )
    result = {ns: None for ns in namespaces}
    if rc != 0:
        if logger:
            logger.warning(f"Failed to list VMIs: {stderr.strip()}")
        return result
    for vmi in json.loads(stdout or '{}').get('items', []):
        meta = vmi.get('metadata', {})
        ns = meta.get('namespace')
        if ns in wanted and meta.get('name') == vm_name:
            result[ns] = vmi.get('status', {}).get('nodeName') or None
    return result


def verify_placement(planned: Dict[str, str], actual: Dict[str, Optional[str]]) -> Dict:
    """
    Compare planned vs actual placement.

    Args:
        planned: vm_key -> planned node name (empty dict if no plan)
        actual:  vm_key -> actual node name or None

    Returns:
        dict with per-node counts, unscheduled VMs, mismatches and balanced flag.
    """
    scheduled = {k: v for k, v in actual.items() if v}
    unscheduled = sorted(k for k, v in actual.items() if not v)
    mismatches = {
        k: {'planned': planned[k], 'actual': actual.get(k)}
        for k in planned if actual.get(k) and actual.get(k) != planned[k]
    }
    counts = count_per_node(scheduled)
    # When there is a plan, a node that should host VMs but has none still counts.
    for node in set(planned.values()):
        counts.setdefault(node, 0)
    counts = dict(sorted(counts.items()))
    balanced = bool(counts) and not unscheduled and not mismatches and \
        (max(counts.values()) - min(counts.values()) <= 1)
    return {
        'balanced': balanced,
        'per_node_counts': counts,
        'unscheduled': unscheduled,
        'mismatches': mismatches,
    }
