import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import vm_placement as vp  # noqa: E402
from utils.vm_placement import (  # noqa: E402
    candidate_nodes,
    check_capacity,
    count_per_node,
    get_deployed_state,
    get_vm_template_info,
    parse_quantity,
    pin_vm_to_node,
    plan_balanced_placement,
    plan_from_state,
    pod_requests_by_node,
    read_vmi_state,
    select_eligible_nodes,
    set_vm_cpus,
    storage_topology_warning,
    summarize_values,
    verify_placement,
    _toleration_matches,
)

TEMPLATE = Path(__file__).resolve().parent.parent / "examples" / "vm-templates" / "fio-vm-template.yaml"


def rendered_template():
    return re.sub(r"\{\{[A-Z_]+\}\}", "x", TEMPLATE.read_text())


def node(name, ready=True, unschedulable=False, taints=None, hostname=None, labels=None,
         worker=True, control_plane=False, kubevirt="true", cpu="16", memory="64Gi", zone=None):
    lab = {"kubernetes.io/hostname": hostname or name}
    if worker:
        lab["node-role.kubernetes.io/worker"] = ""
    if control_plane:
        lab["node-role.kubernetes.io/control-plane"] = ""
    if kubevirt is not None:
        lab["kubevirt.io/schedulable"] = kubevirt
    if zone:
        lab["topology.kubernetes.io/zone"] = zone
    lab.update(labels or {})
    return {
        "metadata": {"name": name, "labels": lab},
        "spec": {"unschedulable": unschedulable, "taints": taints or []},
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                   "allocatable": {"cpu": cpu, "memory": memory}},
    }


def names(nodes):
    return [n["name"] if "name" in n else n["metadata"]["name"] for n in nodes]


class CandidateNodesTest(unittest.TestCase):
    def test_worker_label_any_value(self):
        nodes = [node("w1"), node("w2", labels={"node-role.kubernetes.io/worker": "true"}),
                 node("cp", worker=False, control_plane=True)]
        pool, desc = candidate_nodes(nodes)
        self.assertEqual(names(pool), ["w1", "w2"])
        self.assertIn("worker", desc)

    def test_no_worker_labels_uses_non_control_plane(self):
        # kubeadm / kind / k3s / EKS style: workers carry no role label
        nodes = [node("cp", worker=False, control_plane=True), node("a", worker=False),
                 node("b", worker=False)]
        pool, desc = candidate_nodes(nodes)
        self.assertEqual(names(pool), ["a", "b"])
        self.assertIn("control-plane", desc)

    def test_single_node_cluster_uses_all_nodes(self):
        pool, _ = candidate_nodes([node("only", worker=False, control_plane=True)])
        self.assertEqual(names(pool), ["only"])

    def test_node_selector_overrides(self):
        nodes = [node("a", labels={"pool": "fio"}), node("b"), node("c", worker=False, labels={"pool": "fio"})]
        pool, _ = candidate_nodes(nodes, "pool=fio")
        self.assertEqual(names(pool), ["a", "c"])


class SelectEligibleNodesTest(unittest.TestCase):
    def test_filters_and_reasons(self):
        nodes = [
            node("w1"),
            node("w2", ready=False),
            node("w3", unschedulable=True),
            node("w4", taints=[{"key": "dedicated", "value": "db", "effect": "NoSchedule"}]),
            node("w5"),
            node("w6", taints=[{"key": "soft", "effect": "PreferNoSchedule"}]),
            node("w7", kubevirt="false"),
            node("w8", labels={"virtbench.io/no-fio": "true"}),
        ]
        r = select_eligible_nodes(nodes, exclude=["w5", "typo"])
        self.assertEqual(names(r["eligible"]), ["w1", "w6"])
        s = r["skipped"]
        self.assertEqual(s["w2"], "NotReady")
        self.assertIn("cordoned", s["w3"])
        self.assertIn("dedicated=db:NoSchedule", s["w4"])
        self.assertIn("exclude", s["w5"])
        self.assertIn("KubeVirt", s["w7"])
        self.assertIn("exclude label", s["w8"])
        self.assertEqual(r["unknown_excludes"], ["typo"])

    def test_kubevirt_label_not_enforced_when_no_node_has_it(self):
        r = select_eligible_nodes([node("a", kubevirt=None), node("b", kubevirt=None)])
        self.assertEqual(names(r["eligible"]), ["a", "b"])

    def test_template_node_selector(self):
        nodes = [node("a", labels={"disktype": "ssd"}), node("b")]
        r = select_eligible_nodes(nodes, template_node_selector={"disktype": "ssd"})
        self.assertEqual(names(r["eligible"]), ["a"])
        self.assertIn("disktype=ssd", r["skipped"]["b"])

    def test_exclude_label_forms(self):
        nodes = [node("a", labels={"pool": "db"}), node("b", labels={"pool": "fio"}), node("c")]
        self.assertEqual(names(select_eligible_nodes(nodes, exclude_label="pool=db")["eligible"]), ["b", "c"])
        self.assertEqual(names(select_eligible_nodes(nodes, exclude_label="pool")["eligible"]), ["c"])
        self.assertEqual(names(select_eligible_nodes(nodes, exclude_label="")["eligible"]), ["a", "b", "c"])

    def test_hostname_differs_from_node_name(self):
        r = select_eligible_nodes([node("ip-10-0-0-1.ec2.internal", hostname="ip-10-0-0-1")])
        self.assertEqual(r["eligible"][0]["hostname"], "ip-10-0-0-1")


class TolerationTest(unittest.TestCase):
    def test_rules(self):
        taint = {"key": "k", "value": "v", "effect": "NoSchedule"}
        self.assertTrue(_toleration_matches({"operator": "Exists"}, taint))
        self.assertFalse(_toleration_matches({"key": "", "operator": "Equal"}, taint))
        self.assertTrue(_toleration_matches({"key": "k", "operator": "Exists"}, taint))
        self.assertTrue(_toleration_matches({"key": "k", "value": "v"}, taint))
        self.assertFalse(_toleration_matches({"key": "k", "value": "other"}, taint))
        self.assertFalse(_toleration_matches({"key": "k", "operator": "Exists", "effect": "NoExecute"}, taint))

    def test_noexecute_with_toleration_seconds_is_not_tolerated(self):
        taint = {"key": "k", "effect": "NoExecute"}
        self.assertTrue(_toleration_matches({"key": "k", "operator": "Exists"}, taint))
        self.assertFalse(_toleration_matches({"key": "k", "operator": "Exists", "tolerationSeconds": 300}, taint))


class PlanTest(unittest.TestCase):
    nodes = [{"name": f"n{i}", "hostname": f"n{i}"} for i in range(5)]

    def test_ten_vms_five_nodes_two_each(self):
        plan = plan_balanced_placement([f"ns-{i}" for i in range(10)], self.nodes)
        self.assertEqual(count_per_node({k: v["name"] for k, v in plan.items()}),
                         {f"n{i}": 2 for i in range(5)})

    def test_split_batches_match_single_deploy(self):
        nodes = self.nodes[:3]
        one = plan_balanced_placement([f"ns-{i}" for i in range(1, 21)], nodes, start_index=0)
        a = plan_balanced_placement([f"ns-{i}" for i in range(1, 11)], nodes, start_index=0)
        b = plan_balanced_placement([f"ns-{i}" for i in range(11, 21)], nodes, start_index=10)
        self.assertEqual({k: v["name"] for k, v in one.items()},
                         {k: v["name"] for k, v in {**a, **b}.items()})

    def test_no_nodes_raises(self):
        with self.assertRaises(ValueError):
            plan_balanced_placement(["ns-1"], [])


class CapacityTest(unittest.TestCase):
    def test_quantities(self):
        self.assertEqual(parse_quantity("500m"), 0.5)
        self.assertEqual(parse_quantity("4Gi"), 4 * 2 ** 30)
        self.assertEqual(parse_quantity("2"), 2.0)
        self.assertEqual(parse_quantity("1e3"), 1000.0)
        self.assertEqual(parse_quantity("1G"), 1e9)

    def test_pod_requests_by_node(self):
        pods = [
            {"spec": {"nodeName": "a", "containers": [{"resources": {"requests": {"cpu": "1", "memory": "1Gi"}}},
                                                       {"resources": {"requests": {"cpu": "500m"}}}]},
             "status": {"phase": "Running"}},
            {"spec": {"nodeName": "a", "containers": [{"resources": {"requests": {"cpu": "8"}}}]},
             "status": {"phase": "Succeeded"}},
        ]
        used = pod_requests_by_node(pods)
        self.assertEqual(used["a"]["cpu"], 1.5)
        self.assertEqual(used["a"]["memory"], 2 ** 30)

    def test_check_capacity(self):
        nodes = [{"name": "a", "allocatable_cpu": 10, "allocatable_memory": 64 * 2 ** 30},
                 {"name": "b", "allocatable_cpu": 10, "allocatable_memory": 64 * 2 ** 30}]
        plan = plan_balanced_placement([f"ns-{i}" for i in range(4)], [{"name": "a"}, {"name": "b"}])
        used = {"a": {"cpu": 4, "memory": 0}}
        problems = check_capacity(plan, nodes, used, cpu_request=4, memory_request=2 ** 30)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("a:"))


class StorageTopologyTest(unittest.TestCase):
    def test_immediate_multi_zone_warns(self):
        sc = {"metadata": {"name": "ebs"}, "volumeBindingMode": "Immediate"}
        nodes = [{"zone": "us-east-1a"}, {"zone": "us-east-1b"}]
        self.assertIn("Immediate", storage_topology_warning(sc, nodes))

    def test_wffc_or_single_zone_ok(self):
        nodes = [{"zone": "z1"}, {"zone": "z2"}]
        self.assertIsNone(storage_topology_warning({"volumeBindingMode": "WaitForFirstConsumer"}, nodes))
        self.assertIsNone(storage_topology_warning({"volumeBindingMode": "Immediate"}, [{"zone": "z1"}]))
        self.assertIsNone(storage_topology_warning(None, nodes))


class TemplateInfoTest(unittest.TestCase):
    def test_shipped_template(self):
        info = get_vm_template_info(rendered_template())
        self.assertEqual(info["vcpus"], 4)
        self.assertEqual(info["cpu_request"], 4.0)
        self.assertEqual(len(info["tolerations"]), 2)
        self.assertIsNone(info["instancetype"])

    def test_no_vm_document(self):
        with self.assertRaises(ValueError):
            get_vm_template_info("apiVersion: v1\nkind: List\nitems: []\n")

    def test_default_request_follows_kubevirt_ratio(self):
        vm = "kind: VirtualMachine\nspec:\n  template:\n    spec:\n      domain:\n        cpu: {cores: 4}\n"
        self.assertAlmostEqual(get_vm_template_info(vm)["cpu_request"], 0.4)


class VcpuTest(unittest.TestCase):
    def test_sets_cores_and_request_keeps_memory(self):
        content = rendered_template()
        domain = yaml.safe_load(set_vm_cpus(content, 2))["spec"]["template"]["spec"]["domain"]
        self.assertEqual(domain["cpu"], {"cores": 2})
        self.assertEqual(domain["resources"]["requests"]["cpu"], "2")
        self.assertEqual(domain["memory"], yaml.safe_load(content)["spec"]["template"]["spec"]["domain"]["memory"])

    def test_separate_request(self):
        domain = yaml.safe_load(set_vm_cpus(rendered_template(), 2, "1"))["spec"]["template"]["spec"]["domain"]
        self.assertEqual(domain["resources"]["requests"]["cpu"], "1")

    def test_limits_kept_consistent_and_sockets_removed(self):
        vm = ("kind: VirtualMachine\nspec:\n  template:\n    spec:\n      domain:\n"
              "        cpu: {sockets: 2, cores: 2, dedicatedCpuPlacement: true}\n"
              "        resources: {requests: {cpu: '4'}, limits: {cpu: '4'}}\n")
        domain = yaml.safe_load(set_vm_cpus(vm, 8))["spec"]["template"]["spec"]["domain"]
        self.assertEqual(domain["cpu"], {"cores": 8, "dedicatedCpuPlacement": True})
        self.assertEqual(domain["resources"]["limits"]["cpu"], "8")

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            set_vm_cpus(rendered_template(), 0)
        with self.assertRaises(ValueError):
            set_vm_cpus("kind: List\n", 4)
        with self.assertRaises(ValueError):
            set_vm_cpus("kind: VirtualMachine\nspec:\n  instancetype: {name: u1.large}\n", 4)


class PinTest(unittest.TestCase):
    def test_pins_vm_and_keeps_cloud_init(self):
        content = rendered_template()
        pod_spec = yaml.safe_load(pin_vm_to_node(content, "worker-2.example"))["spec"]["template"]["spec"]
        self.assertEqual(pod_spec["nodeSelector"], {"kubernetes.io/hostname": "worker-2.example"})
        self.assertEqual(pod_spec["volumes"], yaml.safe_load(content)["spec"]["template"]["spec"]["volumes"])

    def test_merges_existing_selector(self):
        vm = "kind: VirtualMachine\nspec:\n  template:\n    spec:\n      nodeSelector: {disktype: ssd}\n"
        sel = yaml.safe_load(pin_vm_to_node(vm, "h1"))["spec"]["template"]["spec"]["nodeSelector"]
        self.assertEqual(sel, {"disktype": "ssd", "kubernetes.io/hostname": "h1"})

    def test_number_like_strings_stay_strings(self):
        vm = ("kind: VirtualMachine\nmetadata:\n  labels: {a: '1e3', b: '0755', c: 'yes', d: '1.0'}\n"
              "spec:\n  template:\n    spec: {}\n")
        out = pin_vm_to_node(vm, "h1")
        self.assertIn("'1e3'", out)
        self.assertEqual(yaml.safe_load(out)["metadata"]["labels"], {"a": "1e3", "b": "0755", "c": "yes", "d": "1.0"})


def vm_obj(ns, cores=4, hostname=None, name="fio-vm", restart_required=False):
    spec = {"domain": {"cpu": {"cores": cores}, "resources": {"requests": {"cpu": str(cores)}}}}
    if hostname:
        spec["nodeSelector"] = {"kubernetes.io/hostname": hostname}
    status = {"conditions": [{"type": "RestartRequired", "status": "True"}]} if restart_required else {}
    return {"metadata": {"namespace": ns, "name": name}, "spec": {"template": {"spec": spec}}, "status": status}


def vmi_obj(ns, node=None, phase="Running", cores=4, hostname=None, name="fio-vm"):
    spec = {"domain": {"cpu": {"cores": cores}, "resources": {"requests": {"cpu": str(cores)}}}}
    if hostname:
        spec["nodeSelector"] = {"kubernetes.io/hostname": hostname}
    return {"metadata": {"namespace": ns, "name": name}, "spec": spec,
            "status": {"phase": phase, "nodeName": node}}


class DeployedStateTest(unittest.TestCase):
    def test_running_vmi_wins_over_vm_spec(self):
        # VM re-applied with 2 vCPU and a new node, VMI still runs the old spec
        state = read_vmi_state([vmi_obj("ns-1", "n1", cores=4, hostname="h1")],
                               [vm_obj("ns-1", cores=2, hostname="h2", restart_required=True)],
                               ["ns-1"], "fio-vm")
        s = state["ns-1"]
        self.assertEqual((s["cores"], s["hostname"], s["node"], s["restart_required"]), (4, "h1", "n1", True))

    def test_terminal_and_missing(self):
        state = read_vmi_state([vmi_obj("ns-1", "n1", phase="Failed")], [vm_obj("ns-1")],
                               ["ns-1", "ns-2"], "fio-vm")
        self.assertIsNone(state["ns-1"]["node"])
        self.assertFalse(state["ns-2"]["exists"])

    def test_plan_rebuilt_only_when_all_pinned(self):
        pinned = read_vmi_state([], [vm_obj("a", hostname="h1"), vm_obj("b", hostname="h2")], ["a", "b"], "fio-vm")
        plan = plan_from_state(pinned, {"h1": "node-1", "h2": "node-2"})
        self.assertEqual({k: v["name"] for k, v in plan.items()}, {"a": "node-1", "b": "node-2"})
        mixed = read_vmi_state([], [vm_obj("a", hostname="h1"), vm_obj("b")], ["a", "b"], "fio-vm")
        self.assertIsNone(plan_from_state(mixed, {}))

    def test_summarize_values(self):
        self.assertEqual(summarize_values([4, 4]), 4)
        self.assertEqual(summarize_values([2, 4, None]), [2, 4])
        self.assertIsNone(summarize_values([None]))

    def test_falls_back_to_per_namespace_and_never_raises(self):
        def fake(args, logger=None):
            if "--all-namespaces" in args:
                return None, "Forbidden"
            if args[:2] == ["get", "vmi"]:
                return {"items": [vmi_obj(args[3], "n1", hostname="n1")]}, ""
            if args[:2] == ["get", "vm"]:
                return {"items": [vm_obj(args[3], hostname="n1")]}, ""
            return None, "Forbidden"  # nodes
        with mock.patch.object(vp, "_kubectl_json", side_effect=fake):
            d = get_deployed_state(["ns-1"], "fio-vm")
        self.assertEqual(d["state"]["ns-1"]["node"], "n1")
        self.assertEqual(d["vm_cpus"], 4)
        self.assertIsNotNone(d["plan"])
        self.assertTrue(any("nodes" in e for e in d["errors"]))

    def test_kubectl_json_handles_bad_output(self):
        with mock.patch.object(vp, "run_kubectl_command", return_value=(0, "not json", "")):
            data, err = vp._kubectl_json(["get", "nodes"])
        self.assertIsNone(data)
        self.assertIn("JSON", err)


class VerifyTest(unittest.TestCase):
    def test_balanced(self):
        r = verify_placement({"a": "n1", "b": "n2"}, {"a": "n1", "b": "n2"})
        self.assertTrue(r["balanced"])
        self.assertEqual(r["per_node_counts"], {"n1": 1, "n2": 1})

    def test_problems_are_reported_separately(self):
        planned = {"a": "n1", "b": "n2", "c": "n1", "d": "n2", "e": "n1"}
        state = {
            "a": {"exists": True, "phase": "Running", "node": "n1"},
            "b": {"exists": True, "phase": "Pending", "node": None},
            "c": {"exists": True, "phase": "Running", "node": "n2"},
            "d": {"exists": False, "phase": None, "node": None},
            "e": {"exists": True, "phase": "Failed", "node": None},
        }
        r = verify_placement(planned, state, failed_to_apply=["d"])
        self.assertFalse(r["balanced"])
        self.assertEqual(r["unscheduled"], ["b"])
        self.assertEqual(r["not_created"], ["d"])
        self.assertEqual(r["terminal"], ["e"])
        self.assertEqual(r["mismatches"], {"c": {"planned": "n1", "actual": "n2"}})

    def test_no_plan_is_never_reported_balanced(self):
        r = verify_placement({}, {"a": "n1", "b": "n1", "c": "n1"})
        self.assertIsNone(r["balanced"])
        self.assertEqual(r["per_node_counts"], {"n1": 3})


if __name__ == "__main__":
    unittest.main()
