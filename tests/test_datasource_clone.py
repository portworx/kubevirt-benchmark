"""Offline regression tests for bounded DataSource clone monitoring."""

import csv
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import Mock, patch

from utils import common


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    'datasource_clone_monitor', ROOT / 'datasource-clone/measure-vm-creation-time.py'
)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()

    def test_data_volume_watch_waits_for_api_ack_and_records_phases(self):
        events = [
            {'type': 'MODIFIED', 'object': {
                'metadata': {'name': 'vm-volume'}, 'status': {'phase': 'CloneScheduled'}
            }},
            {'type': 'MODIFIED', 'object': {
                'metadata': {'name': 'vm-volume'}, 'status': {'phase': 'Succeeded'}
            }},
        ]

        class FakeProcess:
            def __init__(self):
                self.stdout = io.StringIO(''.join(json.dumps(event) + '\n' for event in events))
                self.stderr = io.StringIO('GET https://api.example/datavolumes?watch=true 200 OK\n')
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

            def kill(self):
                self.returncode = -9

        process = FakeProcess()
        with patch.object(benchmark.subprocess, 'Popen', return_value=process):
            watch = benchmark.DataVolumeWatch('test-1', 'vm-volume', self.logger)
        self.assertTrue(watch.ready.is_set())
        self.assertTrue(watch.finished.wait(1))
        self.assertIsNotNone(watch.clone_start)
        self.assertIsNotNone(watch.clone_end)
        self.assertEqual(watch.quality, 'observed')
        watch.stop()

    def monitor(self, **kwargs):
        return benchmark.monitor_vm(
            'test-1', 'vm', datetime.now(), 'ssh', 'default', 1, 0.01,
            self.logger, vm_ready_timeout=0.04, clone_timeout=0.2, ip_timeout=0.04, **kwargs
        )

    def test_readiness_timeout_discards_results_returned_after_deadline(self):
        start_ts = datetime.now()

        def delayed_running(*args, **kwargs):
            time.sleep(0.02)
            return 'Running'

        def delayed_ip(*args, **kwargs):
            time.sleep(0.02)
            return '10.0.0.1'

        with patch.object(benchmark, 'get_vm_status', side_effect=delayed_running):
            with self.assertRaisesRegex(TimeoutError, 'VM readiness timeout'):
                benchmark.wait_for_vm_running('test-1', 'vm', start_ts, 1, self.logger, timeout=0.01)
        with patch.object(benchmark, 'get_vmi_ip', side_effect=delayed_ip):
            with self.assertRaisesRegex(TimeoutError, 'VMI IP timeout'):
                benchmark.wait_for_vmi_ip('test-1', 'vm', 1, self.logger, timeout=0.01)

    def test_clone_duration_does_not_consume_running_timeout(self):
        with ExitStack() as stack:
            def finish_clone(*args, **kwargs):
                time.sleep(0.03)
                return datetime.now(), datetime.now(), 0.03

            clone = stack.enter_context(patch.object(benchmark, 'track_clone_progress', side_effect=finish_clone))
            status = stack.enter_context(patch.object(benchmark, 'get_vm_status', return_value='Running'))
            stack.enter_context(patch.object(benchmark, 'get_vmi_ip', return_value='10.0.0.1'))
            stack.enter_context(patch.object(benchmark, 'ping_vm', return_value=True))
            result = self.monitor()
        self.assertTrue(result[4])
        self.assertGreater(status.call_args.kwargs['timeout'], 0.03)
        self.assertEqual(clone.call_args.kwargs['timeout'], 0.2)

    def test_clone_tracking_retries_transient_subprocess_timeouts(self):
        outcomes = [
            subprocess.TimeoutExpired('kubectl get dv', 0.01),
            subprocess.CompletedProcess(['kubectl'], 0, '', ''),
            subprocess.TimeoutExpired('kubectl get dv', 0.01),
            subprocess.CompletedProcess(['kubectl'], 0, json.dumps({'status': {'phase': 'Succeeded'}}), ''),
        ]

        def run(*args, **kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(benchmark.subprocess, 'run', side_effect=run):
            start, end, duration = benchmark.track_clone_progress(
                'test-1', 'vm', datetime.now(), 0.001, self.logger, timeout=1
            )
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertIsNotNone(duration)
        self.assertIn('(inferred)', self.logger.info.call_args.args[0])

    def test_clone_timeout_reports_last_phase(self):
        watch = SimpleNamespace(
            lock=threading.Lock(),
            finished=SimpleNamespace(wait=lambda timeout: False),
            clone_start=None,
            clone_end=None,
            last_phase='CloneScheduled',
            quality='unavailable',
            terminal_failure=False,
            stop=Mock(),
        )
        with self.assertRaisesRegex(
            TimeoutError, 'Last DataVolume phase: CloneScheduled.*--clone-timeout'
        ):
            benchmark.track_clone_progress(
                'test-1', 'vm', datetime.now(), 1, self.logger, timeout=1, clone_watch=watch
            )
        watch.stop.assert_called_once()

    @patch.object(benchmark, 'ping_vm')
    @patch.object(benchmark, 'get_vm_status', return_value='ErrorUnschedulable')
    @patch.object(benchmark, 'track_clone_progress', return_value=(None, None, 0.01))
    def test_unschedulable_deadline_preserves_clone(self, clone, status, ping):
        start = time.monotonic()
        result = self.monitor()
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(result, ('test-1', None, None, 0.01, False))
        ping.assert_not_called()
        self.assertLessEqual(status.call_args.kwargs['timeout'], 0.04)
        self.assertIn('ErrorUnschedulable', self.logger.error.call_args.args[0])

    @patch.object(benchmark, 'ping_vm')
    @patch.object(benchmark, 'get_vmi_ip', return_value=None)
    @patch.object(benchmark, 'get_vm_status', return_value='Running')
    def test_missing_ip_deadline_preserves_running_time(self, status, ip, ping):
        start = time.monotonic()
        result = self.monitor(skip_dv_clone_tracking=True)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertIsNotNone(result[1])
        self.assertFalse(result[4])
        ping.assert_not_called()
        self.assertLessEqual(ip.call_args.kwargs['timeout'], 0.04)
        self.assertIn('VMI IP timeout', self.logger.error.call_args.args[0])

    def test_healthy_and_transient_unschedulability(self):
        for statuses in (['Running'], ['ErrorUnschedulable', 'Running']):
            with self.subTest(statuses=statuses), ExitStack() as stack:
                stack.enter_context(patch.object(benchmark, 'get_vm_status', side_effect=statuses))
                stack.enter_context(patch.object(benchmark, 'get_vmi_ip', return_value='10.0.0.1'))
                stack.enter_context(patch.object(benchmark, 'ping_vm', return_value=True))
                result = benchmark.monitor_vm(
                    'test-1', 'vm', datetime.now(), 'ssh', 'default', 0.001, 0.01,
                    self.logger, skip_dv_clone_tracking=True,
                    vm_ready_timeout=0.1, ip_timeout=0.1
                )
                self.assertTrue(result[4])
                self.assertIsNotNone(result[1])
                self.assertIsNotNone(result[2])

    def test_polling_commands_are_killed_at_deadline(self):
        real_run = subprocess.run

        def blocked_command(*args, **kwargs):
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], 0.04)
            return real_run([sys.executable, '-c', 'import time; time.sleep(10)'], **kwargs)

        for stage in ('running', 'ip', 'clone', 'clone-poll'):
            with self.subTest(stage=stage), ExitStack() as stack:
                calls = 0

                def command(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if stage == 'clone-poll' and calls == 1:
                        return subprocess.CompletedProcess(args[0], 0, '', '')
                    return blocked_command(*args, **kwargs)

                stack.enter_context(patch.object(common.subprocess, 'run', side_effect=command))
                if stage == 'ip':
                    stack.enter_context(patch.object(benchmark, 'get_vm_status', return_value='Running'))
                start = time.monotonic()
                result = self.monitor(skip_dv_clone_tracking=not stage.startswith('clone'))
                self.assertFalse(result[4])
                self.assertLess(time.monotonic() - start, 0.5)

    @patch.object(benchmark, 'ping_vm', return_value=False)
    @patch.object(benchmark, 'get_vmi_ip', return_value='10.0.0.1')
    @patch.object(benchmark, 'get_vm_status', return_value='Running')
    def test_ping_timeout_still_applies_after_readiness(self, status, ip, ping):
        result = benchmark.monitor_vm(
            'test-1', 'vm', datetime.now(), 'ssh', 'default', 0.001, 0.01,
            self.logger, skip_dv_clone_tracking=True,
            vm_ready_timeout=0.1, ip_timeout=0.1
        )
        self.assertFalse(result[4])
        self.assertIsNotNone(result[1])
        self.assertGreater(ping.call_count, 0)


class CompletionTests(unittest.TestCase):
    def test_summary_includes_all_captured_clone_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            results = [('ok', 10.0, 20.0, 5.0, True),
                       ('failed', 55.0, None, 40.0, False),
                       ('inferred', 60.0, None, 90.0, False)]
            common.save_results(SimpleNamespace(), results, base_dir=directory)
            summary = json.loads((Path(directory) / 'summary_vm_creation_results.json').read_text())
            metrics = {item['metric']: item for item in summary['metrics']}
            self.assertEqual((summary['successful'], summary['failed']), (1, 2))
            self.assertEqual(metrics['running_time_sec']['avg'], 10.0)
            self.assertEqual(metrics['clone_duration_sec']['avg'], 45.0)
            self.assertEqual(metrics['clone_duration_sec']['count'], 3)
            self.assertEqual(metrics['running_time_sec']['count'], 1)
            detail = json.loads((Path(directory) / 'vm_creation_results.json').read_text())
            self.assertEqual(detail[1]['running_time_sec'], 55.0)
            self.assertNotIn('clone_timing_quality', detail[2])

    def test_console_clone_summary_includes_failed_vm_samples(self):
        logger = Mock()
        common.print_summary_table(
            [('ok', 10.0, 20.0, 5.0, True),
             ('failed', 55.0, None, 40.0, False)],
            logger=logger
        )
        output = '\n'.join(call.args[0] for call in logger.info.call_args_list)
        self.assertIn('Avg Clone Duration:     22.50s', output)
        self.assertIn('Avg Time to Running:    10.00s', output)

    def test_mixed_run_saves_results_and_cleans_up(self):
        for boot_storm in (False, True):
            for cleanup_flag in ('--cleanup', '--cleanup-on-failure'):
                with self.subTest(boot_storm=boot_storm, cleanup=cleanup_flag):
                    self.run_mixed(boot_storm, cleanup_flag)

    def run_mixed(self, boot_storm, cleanup_flag):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            argv = ['monitor', '--end', '2', '--vm-template',
                    str(ROOT / 'examples/vm-templates/rhel9-vm-datasource.yaml'),
                    '--num-disks', '1', '--save-results', '--results-folder', directory,
                    cleanup_flag, '--yes', '--single-node', '--node-name', 'worker-1']
            if boot_storm:
                argv += ['--skip-vm-creation', '--boot-storm']
            stack.enter_context(patch.object(sys, 'argv', argv))
            args = benchmark.parse_args()
            args.vm_ready_timeout = args.ip_timeout = 0.04
            args.poll_interval = 0.001
            stack.enter_context(patch.object(benchmark, 'parse_args', return_value=args))
            stack.enter_context(patch.object(benchmark.signal, 'signal'))
            stack.enter_context(patch.object(benchmark, 'setup_logging', return_value=Mock()))
            stack.enter_context(patch.object(benchmark, 'validate_prerequisites', return_value=True))
            stack.enter_context(patch.object(benchmark, 'ensure_namespaces',
                                             return_value=['test-1', 'test-2']))
            stack.enter_context(patch.object(benchmark, 'create_vm',
                                             side_effect=lambda ns, *a: (ns, datetime.now())))
            stack.enter_context(patch.object(
                benchmark, 'start_datavolume_watch',
                side_effect=lambda *a: SimpleNamespace(quality='observed', stop=Mock())
            ))
            stack.enter_context(patch.object(benchmark, 'track_clone_progress',
                                             return_value=(None, None, 0.01)))
            stack.enter_context(patch.object(benchmark, 'get_vm_status',
                                             side_effect=lambda vm, ns, *a, **kw:
                                             'Running' if ns.endswith('-1') else 'ErrorUnschedulable'))
            stack.enter_context(patch.object(benchmark, 'get_vmi_ip', return_value='10.0.0.1'))
            stack.enter_context(patch.object(benchmark, 'ping_vm', return_value=True))
            stack.enter_context(patch.object(benchmark, 'stop_vm'))
            stack.enter_context(patch.object(benchmark, 'wait_for_vm_stopped', return_value=True))
            stack.enter_context(patch.object(benchmark, 'start_vm'))
            cleanup = stack.enter_context(patch.object(benchmark, 'cleanup_test_namespaces'))
            stack.enter_context(patch.object(benchmark, 'print_cleanup_summary'))
            summary = stack.enter_context(patch.object(benchmark, 'print_summary_table'))
            with self.assertRaises(SystemExit) as exit_result:
                benchmark.main()
            self.assertEqual(exit_result.exception.code, 1)
            cleanup.assert_called_once()
            self.assertEqual(len(summary.call_args.args[0]), 2)
            prefix = 'boot_storm_results' if boot_storm else 'vm_creation_results'
            output = Path(args._results_dir)
            detail = json.loads((output / f'{prefix}.json').read_text())
            totals = json.loads((output / f'summary_{prefix}.json').read_text())
            self.assertEqual((totals['total_vms'], totals['successful'], totals['failed']), (2, 1, 1))
            self.assertEqual(sum(row['success'] for row in detail), 1)
            with (output / f'{prefix}.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            self.assertEqual(sum(row['success'] == 'True' for row in rows), 1)


class CommandLineTests(unittest.TestCase):
    def test_clone_and_vm_ready_timeouts_have_separate_defaults(self):
        self.assertEqual(benchmark.DEFAULT_CLONE_TIMEOUT, 1800)
        self.assertEqual(benchmark.DEFAULT_VM_READY_TIMEOUT, 600)

    def test_script_rejects_nonpositive_deadlines(self):
        for option in ('--poll-interval', '--clone-timeout', '--vm-ready-timeout', '--ip-timeout'):
            for value in ('0', '-1'):
                with self.subTest(option=option, value=value):
                    with patch.object(sys, 'argv', ['monitor', option, value]), patch('sys.stderr'):
                        with self.assertRaises(SystemExit) as result:
                            benchmark.parse_args()
                        self.assertEqual(result.exception.code, 2)

    def test_cli_forwards_deadlines(self):
        try:
            from click.testing import CliRunner
            from virtbench.commands.datasource_clone import datasource_clone
        except ModuleNotFoundError as error:
            self.skipTest(f'CLI dependency unavailable: {error.name}')
        context = SimpleNamespace(repo_root=ROOT, log_level='INFO', log_file=None)
        with patch('virtbench.commands.datasource_clone.subprocess.run') as run:
            run.return_value.returncode = 0
            result = CliRunner().invoke(datasource_clone, [
                '--clone-timeout', '90', '--vm-ready-timeout', '45', '--ip-timeout', '15', '--save-results'
            ], obj=context)
        self.assertEqual(result.exit_code, 0, result.output)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--vm-ready-timeout') + 1], '45')
        self.assertEqual(command[command.index('--clone-timeout') + 1], '90')
        self.assertEqual(command[command.index('--ip-timeout') + 1], '15')
        for option in ('--vm-ready-timeout', '--ip-timeout'):
            result = CliRunner().invoke(datasource_clone, [option, '0'], obj=context)
            self.assertEqual(result.exit_code, 2)


if __name__ == '__main__':
    unittest.main()
