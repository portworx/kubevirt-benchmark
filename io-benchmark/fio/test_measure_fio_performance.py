import importlib.util
import pathlib
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

    def test_run_all_legacy_result_without_exit_status_is_unverified(self):
        result = fio.parse_run_all_result(
            "fio-vm-1", {"raw_data": {"jobs": [{"error": 0}]}, "exit_code": None}, None
        )

        self.assertTrue(result["unverified"])
        self.assertFalse(result["success"])

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
                mock.patch.object(fio, "action_gather_results", return_value={"failed": 1}):
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
                mock.patch.object(fio, "action_gather_results", return_value={"failed": 0, "unverified": 1}):
            with self.assertRaises(SystemExit) as raised:
                fio.main()

        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
