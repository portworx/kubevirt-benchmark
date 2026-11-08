import re
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.vm_placement import (  # noqa: E402
    count_per_node,
    get_vm_tolerations,
    pin_vm_to_node,
    set_vm_cpus,
    plan_balanced_placement,
    select_eligible_nodes,
    verify_placement,
)

TEMPLATE = Path(__file__).resolve().parent.parent / "examples" / "vm-templates" / "fio-vm-template.yaml"


def node(name, ready=True, unschedulable=False, taints=None, hostname=None, labels=None):
    return {
        "metadata": {"name": name, "labels": {"kubernetes.io/hostname": hostname or name, **(labels or {})}},
        "spec": {"unschedulable": unschedulable, "taints": taints or []},
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }


class SelectEligibleNodesTest(unittest.TestCase):
    def test_filters_notready_cordoned_tainted_and_excluded(self):
        nodes = [
            node("w1"),
            node("w2", ready=False),
            node("w3", unschedulable=True),
            node("w4", taints=[{"key": "virtbench.io/no-fio", "value": "true", "effect": "NoSchedule"}]),
            node("w5"),
            node("w6", taints=[{"key": "soft", "effect": "PreferNoSchedule"}]),
        ]
        eligible, skipped = select_eligible_nodes(nodes, exclude=["w5"])
        self.assertEqual([n["name"] for n in eligible], ["w1", "w6"])
        self.assertEqual(skipped["w2"], "NotReady")
        self.assertIn("cordoned", skipped["w3"])
        self.assertIn("virtbench.io/no-fio=true:NoSchedule", skipped["w4"])
        self.assertIn("exclude", skipped["w5"])

    def test_default_exclude_label_any_value(self):
        nodes = [node("w1"), node("w2", labels={"virtbench.io/no-fio": "true"}),
                 node("w3", labels={"virtbench.io/no-fio": ""})]
        eligible, skipped = select_eligible_nodes(nodes)
        self.assertEqual([n["name"] for n in eligible], ["w1"])
        self.assertIn("exclude label", skipped["w2"])
        self.assertIn("w3", skipped)

    def test_custom_exclude_label_key_value(self):
        nodes = [node("w1", labels={"pool": "db"}), node("w2", labels={"pool": "fio"}), node("w3")]
        eligible, skipped = select_eligible_nodes(nodes, exclude_label="pool=db")
        self.assertEqual([n["name"] for n in eligible], ["w2", "w3"])
        self.assertEqual(list(skipped), ["w1"])

    def test_empty_exclude_label_disables(self):
        nodes = [node("w1", labels={"virtbench.io/no-fio": "true"})]
        eligible, _ = select_eligible_nodes(nodes, exclude_label="")
        self.assertEqual([n["name"] for n in eligible], ["w1"])

    def test_tolerated_taint_keeps_node(self):
        tol = [{"key": "node-role.kubernetes.io/master", "operator": "Exists", "effect": "NoSchedule"}]
        nodes = [node("w1", taints=[{"key": "node-role.kubernetes.io/master", "effect": "NoSchedule"}])]
        eligible, _ = select_eligible_nodes(nodes, tolerations=tol)
        self.assertEqual([n["name"] for n in eligible], ["w1"])


class PlanTest(unittest.TestCase):
    def test_ten_vms_five_nodes_two_each(self):
        nodes = [{"name": f"n{i}", "hostname": f"n{i}"} for i in range(5)]
        plan = plan_balanced_placement([f"ns-{i}" for i in range(10)], nodes)
        counts = count_per_node({k: v["name"] for k, v in plan.items()})
        self.assertEqual(counts, {f"n{i}": 2 for i in range(5)})

    def test_uneven_differs_by_at_most_one(self):
        nodes = [{"name": f"n{i}", "hostname": f"n{i}"} for i in range(3)]
        plan = plan_balanced_placement([f"ns-{i}" for i in range(7)], nodes)
        counts = count_per_node({k: v["name"] for k, v in plan.items()})
        self.assertEqual(sorted(counts.values()), [2, 2, 3])

    def test_no_nodes_raises(self):
        with self.assertRaises(ValueError):
            plan_balanced_placement(["ns-1"], [])


class PinTest(unittest.TestCase):
    def test_pins_vm_and_keeps_cloud_init(self):
        content = re.sub(r"\{\{[A-Z_]+\}\}", "x", TEMPLATE.read_text())
        pinned = pin_vm_to_node(content, "worker-2.example")
        vm = yaml.safe_load(pinned)
        pod_spec = vm["spec"]["template"]["spec"]
        self.assertEqual(pod_spec["nodeSelector"], {"kubernetes.io/hostname": "worker-2.example"})
        original = yaml.safe_load(content)["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(pod_spec["volumes"], original)
        self.assertEqual(len(get_vm_tolerations(content)), 2)


class VcpuTest(unittest.TestCase):
    def test_sets_cores_and_request_keeps_memory(self):
        content = re.sub(r"\{\{[A-Z_]+\}\}", "x", TEMPLATE.read_text())
        domain = yaml.safe_load(set_vm_cpus(content, 4))["spec"]["template"]["spec"]["domain"]
        self.assertEqual(domain["cpu"], {"cores": 4})
        self.assertEqual(domain["resources"]["requests"]["cpu"], "4")
        self.assertEqual(domain["memory"], yaml.safe_load(content)["spec"]["template"]["spec"]["domain"]["memory"])

    def test_rejects_zero(self):
        with self.assertRaises(ValueError):
            set_vm_cpus("kind: VirtualMachine\n", 0)


class VerifyTest(unittest.TestCase):
    def test_balanced(self):
        planned = {"a": "n1", "b": "n2"}
        r = verify_placement(planned, {"a": "n1", "b": "n2"})
        self.assertTrue(r["balanced"])
        self.assertEqual(r["per_node_counts"], {"n1": 1, "n2": 1})

    def test_unscheduled_and_mismatch(self):
        planned = {"a": "n1", "b": "n2", "c": "n1"}
        r = verify_placement(planned, {"a": "n1", "b": None, "c": "n2"})
        self.assertFalse(r["balanced"])
        self.assertEqual(r["unscheduled"], ["b"])
        self.assertEqual(r["mismatches"], {"c": {"planned": "n1", "actual": "n2"}})

    def test_no_plan_uses_actual_spread(self):
        r = verify_placement({}, {"a": "n1", "b": "n1", "c": "n1", "d": "n2"})
        self.assertFalse(r["balanced"])
        self.assertEqual(r["per_node_counts"], {"n1": 3, "n2": 1})


if __name__ == "__main__":
    unittest.main()
