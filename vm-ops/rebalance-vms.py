#!/usr/bin/env python3
"""
rebalance-vms.py  –  Rebalance KubeVirt VMs to a target range per node.

Queries the live cluster for node and VMI data, computes the minimum set
of moves to bring every node into the target range, then for each move:

  1. oc patch vm   rhel-elbencho-1 -n <namespace>  (pin nodeSelector first)
  2. virtctl stop  rhel-elbencho-1 -n <namespace>
  3. oc wait vmi   rhel-elbencho-1 -n <namespace>  --for=delete
  4. virtctl start rhel-elbencho-1 -n <namespace>

Usage:
  python3 rebalance-vms.py                          # execute all moves
  python3 rebalance-vms.py --dry-run                # print commands only
  python3 rebalance-vms.py --target-min 15 --target-max 16
  python3 rebalance-vms.py --vm-name my-vm --namespace-label my-label
"""

import argparse
import subprocess
import sys

VM_NAME    = "rhel-elbencho-1"
TARGET_MIN = 16
TARGET_MAX = 17


# --------------------------------------------------------------------------- #
# Live cluster queries                                                         #
# --------------------------------------------------------------------------- #

def _oc(args):
    """Run an oc command, return stdout. Exits on error."""
    cmd = ["oc"] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ERROR running: {' '.join(cmd)}\n{res.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return res.stdout


def fetch_nodes():
    """Return list of all Ready worker/master node names via 'oc get nodes'."""
    out = _oc(["get", "nodes", "--no-headers", "-o",
                "custom-columns=NAME:.metadata.name"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def fetch_vmi(all_nodes, vm_name):
    """Return {node: [namespace, ...]} from live 'oc get vmi -A'."""
    out = _oc(["get", "vmi", "-A", "--no-headers", "-o",
               "custom-columns="
               "NS:.metadata.namespace,"
               "NAME:.metadata.name,"
               "NODE:.status.nodeName"])
    node_vms = {nd: [] for nd in all_nodes}   # seed every node (incl. empty)
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 3:
            continue
        ns, name, node = cols[0], cols[1], cols[2]
        if name != vm_name:
            continue
        if node in node_vms:
            node_vms[node].append(ns)
        else:
            # VMI is on a node not in our node list — include it anyway
            node_vms[node] = [ns]
    return node_vms


# --------------------------------------------------------------------------- #
# Balancing logic                                                              #
# --------------------------------------------------------------------------- #

def assign_targets(node_vms, target_min, target_max):
    """
    Assign target_max or target_min to each node so that:
      - total VMs is preserved exactly
      - nodes already closest to target_max are preferred for the higher quota
        (minimises overall movement)
    """
    total  = sum(len(v) for v in node_vms.values())
    n      = len(node_vms)
    n_high = total - n * target_min          # how many nodes get target_max

    ranked = sorted(node_vms, key=lambda nd: abs(len(node_vms[nd]) - target_max))
    targets, assigned = {}, 0
    for nd in ranked:
        if assigned < n_high:
            targets[nd] = target_max
            assigned += 1
        else:
            targets[nd] = target_min
    return targets


def compute_moves(node_vms, targets):
    """
    Return list of (src_node, dst_node, namespace) tuples.
    Donors: most-loaded nodes first.
    Receivers: least-loaded nodes first.
    """
    donors = []
    for nd in sorted(node_vms, key=lambda x: -len(node_vms[x])):
        excess = node_vms[nd][targets[nd]:]      # namespaces beyond quota
        donors.extend((nd, ns) for ns in excess)

    receivers = []
    for nd in sorted(node_vms, key=lambda x: len(node_vms[x])):
        for _ in range(targets[nd] - len(node_vms[nd])):
            receivers.append(nd)

    return [(src, dst, ns) for dst, (src, ns) in zip(receivers, donors)]


# --------------------------------------------------------------------------- #
# Execution                                                                    #
# --------------------------------------------------------------------------- #

def run(cmd, dry_run):
    print("  $", " ".join(cmd))
    if not dry_run:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"  ERROR: {res.stderr.strip()}", file=sys.stderr)
        return res
    return None


def wait_for_stop(ns, vm_name, dry_run, timeout=120):
    """Block until the VMI is fully gone (VM is Stopped)."""
    cmd = ["oc", "wait", "vmi", vm_name,
           "-n", ns,
           "--for=delete",
           f"--timeout={timeout}s"]
    print("  $", " ".join(cmd))
    if not dry_run:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # vmi may already be gone — that's fine
            msg = res.stderr.strip()
            if "not found" not in msg.lower():
                print(f"  WARN: {msg}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Rebalance KubeVirt VMs evenly across nodes")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print commands without executing them")
    ap.add_argument("--vm-name", default=VM_NAME,
                    help=f"VM name to rebalance (default: {VM_NAME})")
    ap.add_argument("--target-min", type=int, default=TARGET_MIN,
                    help=f"Minimum VMs per node (default: {TARGET_MIN})")
    ap.add_argument("--target-max", type=int, default=TARGET_MAX,
                    help=f"Maximum VMs per node (default: {TARGET_MAX})")
    args = ap.parse_args()

    if args.target_min > args.target_max:
        ap.error("--target-min cannot be greater than --target-max")

    print("Fetching node list from cluster...")
    all_nodes = fetch_nodes()
    print(f"  {len(all_nodes)} nodes found")

    print(f"Fetching VMI list for '{args.vm_name}'...")
    node_vms  = fetch_vmi(all_nodes, args.vm_name)
    total_vms = sum(len(v) for v in node_vms.values())
    print(f"  {total_vms} VMIs found\n")

    targets = assign_targets(node_vms, args.target_min, args.target_max)
    moves   = compute_moves(node_vms, targets)

    # ---- Summary table ---------------------------------------------------- #
    print(f"\n{'NODE':<22} {'NOW':>4}  {'TARGET':>6}  {'DELTA':>5}")
    print("-" * 48)
    for nd in sorted(node_vms):
        cur, tgt = len(node_vms[nd]), targets[nd]
        marker = "  ← move" if cur != tgt else ""
        print(f"  {nd:<20} {cur:>4}  {tgt:>6}  {tgt-cur:>+5}{marker}")
    total = sum(len(v) for v in node_vms.values())
    print(f"\n  Total VMs : {total}   Nodes : {len(node_vms)}   Moves : {len(moves)}\n")

    if not moves:
        print("Cluster is already balanced. Nothing to do.")
        return

    # ---- Move plan --------------------------------------------------------- #
    print(f"{'NAMESPACE':<18}  {'FROM':<22}  TO")
    print("-" * 68)
    for src, dst, ns in moves:
        print(f"  {ns:<16}  {src:<22}  {dst}")

    # ---- Execute ----------------------------------------------------------- #
    patch_tpl = ('{{"spec":{{"template":{{"spec":{{"nodeSelector":'
                 '{{"kubernetes.io/hostname":"{node}"}}}}}}}}}}')

    tag = " (DRY RUN)" if args.dry_run else ""
    print(f"\n=== Executing {len(moves)} moves{tag} ===\n")

    vm = args.vm_name
    for src, dst, ns in moves:
        print(f"--- {ns} : {src}  →  {dst} ---")
        # 1. Patch nodeSelector first (safe while VM is still running)
        run(["oc", "patch", "vm", vm, "-n", ns,
             "--type", "merge",
             "-p", patch_tpl.format(node=dst)], args.dry_run)
        # 2. Stop the VM
        run(["virtctl", "stop", vm, "-n", ns], args.dry_run)
        # 3. Wait until VMI is fully gone before starting
        wait_for_stop(ns, vm, args.dry_run)
        # 4. Start the VM — it will land on the patched node
        run(["virtctl", "start", vm, "-n", ns], args.dry_run)
        print()

    print("=== Done ===")


if __name__ == "__main__":
    main()

