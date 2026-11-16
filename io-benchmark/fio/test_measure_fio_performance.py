import importlib.util
import pathlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).with_name("measure-fio-performance.py")
SPEC = importlib.util.spec_from_file_location("measure_fio_performance", SCRIPT)
fio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fio)


class ParseFioResultsTests(unittest.TestCase):
    def test_nonzero_job_error_is_failure_without_metrics(self):
        result = fio.parse_fio_results(
            "fio-vm-1", {"jobs": [{"error": 5, "read": {}, "write": {}}]}, 0
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["fio_errors"], [{"job": 0, "error": 5}])
        self.assertNotIn("read_iops", result)

    def test_nonzero_command_status_is_failure(self):
        result = fio.parse_fio_results("fio-vm-1", {"jobs": [{"error": 0}]}, 5)

        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 5)

    def test_missing_command_status_is_unverified(self):
        result = fio.parse_fio_results("fio-vm-1", {"jobs": [{"error": 0}]}, None)

        self.assertFalse(result["success"])
        self.assertTrue(result["unverified"])
        self.assertIn("status is unavailable", result["error"])

    def test_missing_or_invalid_job_error_is_failure(self):
        for error in (None, True, -1):
            with self.subTest(error=error):
                result = fio.parse_fio_results("fio-vm-1", {"jobs": [{"error": error}]}, 0)
                self.assertFalse(result["success"])

    def test_valid_zero_error_job_succeeds(self):
        result = fio.parse_fio_results(
            "fio-vm-1",
            {"jobs": [{"error": 0, "read": {"iops": 12}, "write": {"iops": 3}}]},
            0,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["read_iops"], 12)
        self.assertEqual(result["write_iops"], 3)

    def test_failed_jobs_are_carried_into_aggregate_summary(self):
        failure = fio.parse_fio_results(
            "fio-vm-1", {"jobs": [{"error": 5, "read": {}, "write": {}}]}, 0
        )
        summary = fio.aggregate_results([failure], {"rw": "read"}, 1.0)

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failures"][0]["fio_errors"], [{"job": 0, "error": 5}])

    def test_legacy_result_without_exit_status_is_unverified(self):
        result = fio.parse_fio_results("fio-vm-1", {"jobs": [{"error": 0}]}, None)
        summary = fio.aggregate_results([result], {"rw": "read"}, 1.0)

        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["unverified"], 1)
        self.assertEqual(summary["unverified_results"][0]["namespace"], "fio-vm-1")

    def test_failed_command_without_json_preserves_status_in_summary(self):
        with mock.patch.object(fio, "get_vmi_ip", return_value="192.0.2.1"), \
                mock.patch.object(fio, "run_ssh_command", side_effect=["1", None]):
            with tempfile.TemporaryDirectory() as output_dir:
                collected = fio.collect_fio_results(
                    "fio-vm-1", "fio-vm", {"pod": "ssh", "pod_ns": "default",
                                           "user": "cloud-user", "password": "secret"},
                    output_dir, mock.Mock(), max_retries=1, retry_delay=0,
                )

        result = fio.parse_fio_results("fio-vm-1", collected)
        summary = fio.aggregate_results([result], {"rw": "write"}, 1.0)

        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(summary["failures"][0]["exit_code"], 1)
        self.assertIn("status 1", summary["failures"][0]["error"])

    def test_job_error_without_command_status_remains_a_failure(self):
        result = fio.parse_fio_results(
            "fio-vm-1", {"jobs": [{"error": 5, "read": {}, "write": {}}]}, None
        )

        self.assertFalse(result["success"])
        self.assertFalse(result.get("unverified", False))
        self.assertEqual(result["fio_errors"], [{"job": 0, "error": 5}])

    def test_cli_returns_failure_when_gather_summary_contains_failed_vm(self):
        args = SimpleNamespace(
            action="gather-results", save_results=False, log_file=None, log_level="INFO",
            namespace_prefix="fio", start=1, end=1, fio_runtime=10, fio_bs="4k",
            fio_rw="read", fio_iodepth=1, fio_numjobs=1, fio_size="1G",
            ssh_pod=None, ssh_pod_ns=None, vm_user="root", vm_password=None,
        )
        with mock.patch.object(fio, "parse_args", return_value=args), \
                mock.patch.object(fio, "setup_logging"), \
                mock.patch.object(fio, "action_gather_results", return_value=1):
            with self.assertRaises(SystemExit) as raised:
                fio.main()

        self.assertEqual(raised.exception.code, 1)

    def test_cli_returns_failure_when_gather_summary_contains_unverified_vm(self):
        args = SimpleNamespace(
            action="gather-results", save_results=False, log_file=None, log_level="INFO",
            namespace_prefix="fio", start=1, end=1, fio_runtime=10, fio_bs="4k",
            fio_rw="read", fio_iodepth=1, fio_numjobs=1, fio_size="1G",
            ssh_pod=None, ssh_pod_ns=None, vm_user="root", vm_password=None,
        )
        with mock.patch.object(fio, "parse_args", return_value=args), \
                mock.patch.object(fio, "setup_logging"), \
                mock.patch.object(fio, "action_gather_results", return_value=1):
            with self.assertRaises(SystemExit) as raised:
                fio.main()

        self.assertEqual(raised.exception.code, 1)


class CompletionTests(unittest.TestCase):
    ssh = {"pod": "ssh", "pod_ns": "default", "user": "root", "password": "secret"}

    def test_marker_arrival_and_timeout(self):
        for outputs, expected in [([None, "completed"], True), ([None, None], False)]:
            with self.subTest(expected=expected):
                clock = [0]
                def sleep(seconds):
                    clock[0] += seconds
                with mock.patch.object(fio.time, "time", side_effect=lambda: clock[0]), \
                        mock.patch.object(fio.time, "sleep", side_effect=sleep), \
                        mock.patch.object(fio, "get_vm_and_vmi_status", return_value=("Running", "Running")), \
                        mock.patch.object(fio, "get_vmi_ip", return_value="192.0.2.1"), \
                        mock.patch.object(fio, "run_ssh_command", side_effect=outputs):
                    self.assertEqual(fio.wait_for_fio_complete("ns", "vm", self.ssh, 60, mock.Mock()), expected)

    def test_exit_status_read_retry_and_missing(self):
        for outputs, success, unverified in [
            (["", "0", '{"jobs":[{"error":0}]}'], True, False),
            (["VIRTbench_EXIT_CODE_MISSING", '{"jobs":[{"error":0}]}'], False, True),
            ([None, None], False, False),
        ]:
            with self.subTest(outputs=outputs), tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(fio, "get_vmi_ip", return_value="192.0.2.1"), \
                    mock.patch.object(fio, "run_ssh_command", side_effect=outputs), \
                    mock.patch.object(fio.time, "sleep"):
                data = fio.collect_fio_results("ns", "vm", self.ssh, directory, mock.Mock(), 2, 0)
                if data is None:
                    self.assertFalse(success)
                    self.assertFalse(unverified)
                else:
                    parsed = fio.parse_fio_results("ns", data)
                    self.assertEqual(parsed["success"], success)
                    self.assertEqual(parsed.get("unverified", False), unverified)

    def test_ssh_nonzero_status_is_failure(self):
        with mock.patch.object(fio, "ssh_exec_command", return_value=(255, "", "failed")):
            self.assertIsNone(fio.run_ssh_command("192.0.2.1", "cat x", "ssh", "default", "root", "secret"))

    def test_gather_waits_before_collection(self):
        with mock.patch.object(fio, "wait_for_fio_complete", return_value=False) as wait, \
                mock.patch.object(fio, "collect_fio_results") as collect:
            self.assertTrue(fio.wait_and_collect_fio_results("ns", "vm", self.ssh, "/tmp", mock.Mock(), 42, 2, 0)["_virtbench_completion_timeout"])
            self.assertEqual(wait.call_args.args[3], 42)
            collect.assert_not_called()

    def test_action_timeout_propagation(self):
        config = {"runtime": 10, "rw": "read", "bs": "4k", "iodepth": 1, "numjobs": 1}
        for action in ("gather", "run-all"):
            for override, expected in ((None, 610), (42, 42)):
                with self.subTest(action=action, override=override), tempfile.TemporaryDirectory() as directory:
                    args = SimpleNamespace(fio_timeout=override, vm_name="vm", collect_concurrency=1,
                        collect_retries=1, collect_retry_delay=0, concurrency=1, storage_class="sc",
                        save_results=False, cleanup=False)
                    with mock.patch.object(fio, "get_output_dir", return_value=directory), \
                            mock.patch.object(fio, "wait_for_fio_complete", return_value=False) as wait, \
                            mock.patch.object(fio, "get_deployed_state", return_value={"plan": None}), \
                            mock.patch.object(fio, "check_placement", return_value=None), \
                            mock.patch.object(fio, "prepare_deployment", return_value=({}, None)), \
                            mock.patch.object(fio, "warn_existing_vms"), \
                            mock.patch.object(fio, "create_namespaces_parallel"), \
                            mock.patch.object(fio, "apply_manifests", return_value=[]), \
                            mock.patch.object(fio, "collect_fio_results") as collect, \
                            mock.patch("builtins.print"):
                        func = fio.action_gather_results if action == "gather" else fio.action_run_all
                        self.assertEqual(func(args, ["ns"], config, self.ssh, mock.Mock()), 1)
                        self.assertEqual(wait.call_args.args[3], expected)
                        collect.assert_not_called()

    def test_timeout_argument_validation_and_default(self):
        for option, expected in [([], None), (["--fio-timeout", "42"], 42)]:
            with mock.patch.object(fio.sys, "argv", ["fio", "-s", "1", "-e", "1", "-a", "gather-results"] + option):
                self.assertEqual(fio.parse_args().fio_timeout, expected)
        with mock.patch.object(fio.sys, "argv", ["fio", "-s", "1", "-e", "1", "-a", "gather-results", "--fio-timeout", "0"]), \
                self.assertRaises(SystemExit):
            fio.parse_args()


if __name__ == "__main__":
    unittest.main()
