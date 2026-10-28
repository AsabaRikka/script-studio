import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import studio_service as service


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        jobs = self.root / 'data_cache' / 'batch_jobs'
        jobs.mkdir(parents=True)
        (jobs / 'test-id.json').write_text('{}')

    def test_live_generation_blocks_even_if_disk_says_idle(self):
        with patch.object(service, 'read_api', side_effect=[{'job': {'status': 'running'}}, {'batches': []}]):
            with self.assertRaisesRegex(RuntimeError, 'test-id'):
                service.check_batches(self.root, 8530)

    def test_live_intake_blocks(self):
        with patch.object(service, 'read_api', side_effect=[{'job': {'status': 'completed'}}, {'batches': [{'id': 'intake-id', 'status': '运行中'}]}]):
            with self.assertRaisesRegex(RuntimeError, 'intake-id'):
                service.check_batches(self.root, 8530)

    def test_live_quality_job_also_blocks_restart(self):
        directory = self.root / 'data_cache' / 'quality' / 'jobs'
        directory.mkdir(parents=True)
        (directory / 'quality-id.json').write_text('{}')
        with patch.object(service, 'read_api', side_effect=[{'job': {'status': 'completed'}},
                {'batches': []}, {'job': {'status': 'running'}}]):
            with self.assertRaisesRegex(RuntimeError, 'quality-id'):
                service.check_batches(self.root, 8530)

    def test_older_intake_outside_list_is_also_checked(self):
        directory = self.root / 'data_cache' / 'intake_batches'
        directory.mkdir()
        (directory / 'older.json').write_text('{}')
        with patch.object(service, 'read_api', side_effect=[{'job': {'status': 'completed'}}, {'batches': []}, {'job': {'id': 'older', 'status': '运行中'}}]):
            with self.assertRaisesRegex(RuntimeError, 'older'):
                service.check_batches(self.root, 8530)

    def test_unreadable_or_unknown_state_blocks(self):
        for response in (OSError('offline'), {'job': {'status': 'unexpected'}}, {}):
            with self.subTest(response=response), patch.object(service, 'read_api', side_effect=[response]):
                with self.assertRaisesRegex(RuntimeError, '无法可靠'):
                    service.check_batches(self.root, 8530)

    def test_other_project_or_command_is_never_terminated(self):
        connection = Mock(status=service.psutil.CONN_LISTEN, pid=123)
        connection.laddr.port = 8530
        for cwd, command in ((str(self.root.parent), ['python.exe', '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', '8530']), (str(self.root), ['python.exe', 'other.py'])):
            process = Mock()
            process.cwd.return_value = cwd
            process.cmdline.return_value = command
            with patch.object(service.psutil, 'net_connections', return_value=[connection]), patch.object(service.psutil, 'Process', return_value=process):
                with self.assertRaisesRegex(RuntimeError, '项目目录或启动命令'):
                    service.service_process(self.root, 8530)
            process.terminate.assert_not_called()

    def test_changed_process_blocks(self):
        old, new = Mock(pid=123), Mock(pid=456)
        starter = Mock()
        with patch.object(service, 'check_restart', return_value=old), patch.object(service, 'service_process', return_value=new):
            with self.assertRaisesRegex(RuntimeError, '已变化'):
                service.restart_server(self.root, 8530, Mock(), starter)
        old.terminate.assert_not_called()
        starter.assert_not_called()

    def test_stop_timeout_does_not_force_kill_or_start(self):
        process = Mock(pid=123)
        process.wait.side_effect = service.psutil.TimeoutExpired(15)
        starter = Mock()
        with patch.object(service, 'check_restart', return_value=process), patch.object(service, 'service_process', return_value=process):
            with self.assertRaisesRegex(RuntimeError, '未强制杀进程'):
                service.restart_server(self.root, 8530, Mock(), starter)
        process.kill.assert_not_called()
        starter.assert_not_called()

    def test_stopped_service_starts_without_termination(self):
        starter = Mock(return_value='started')
        with patch.object(service, 'check_restart', return_value=None):
            self.assertEqual(service.restart_server(self.root, 8530, Mock(), starter), 'started')
        starter.assert_called_once_with(8530)
