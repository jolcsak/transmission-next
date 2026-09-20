import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from admin import Supervisor, read, validate, write


class FakeExporter:
    constructed = 0
    def __init__(self, config):
        type(self).constructed += 1
        self.stop = threading.Event()
        self.rpc = SimpleNamespace(close=lambda: None)
        self.influx = None
    def run(self):
        self.stop.wait()


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Supervisor(self.temp.name)
        self.config = {'interval_seconds': 30, 'mqtt': {'enabled': False}, 'influxdb': {'enabled': False}}

    def tearDown(self):
        self.manager.halt()
        self.temp.cleanup()

    def job(self, action, config):
        write(self.manager.request, {'job_id': 'test', 'action': action, 'configuration': config})
        self.manager.process()
        return read(self.manager.result)

    def test_save_modify_import_and_permissions(self):
        self.assertEqual(self.job('save', self.config)['state'], 'saved')
        self.assertEqual(read(self.manager.config), self.config)
        self.assertEqual(self.manager.config.stat().st_mode & 0o777, 0o600)
        imported = json.loads(json.dumps(self.config))
        imported['interval_seconds'] = 60
        self.assertEqual(self.job('save', imported)['state'], 'saved')
        self.assertEqual(read(self.manager.config)['interval_seconds'], 60)
        self.assertFalse(self.manager.request.exists())

    def test_invalid_save_keeps_previous(self):
        self.job('save', self.config)
        original = self.manager.config.read_bytes()
        self.assertEqual(self.job('save', dict(self.config, interval_seconds=0))['state'], 'error')
        self.assertEqual(original, self.manager.config.read_bytes())

    def test_test_does_not_save(self):
        with patch('admin.test', return_value={'rpc': 'ok', 'mqtt': 'failed'}):
            result = self.job('test', self.config)
        self.assertEqual(result['checks']['mqtt'], 'failed')
        self.assertFalse(self.manager.config.exists())

    def test_failed_apply_restores_saved_configuration(self):
        self.job('save', self.config)
        with patch.object(self.manager, 'start', side_effect=RuntimeError('restart failure')):
            result = self.job('save', dict(self.config, interval_seconds=60))
        self.assertEqual(result['state'], 'error')
        self.assertEqual(read(self.manager.config), self.config)

    def test_inline_secrets_supported_and_errors_do_not_echo(self):
        config = dict(self.config, influxdb={'enabled': True, 'url': 'http://127.0.0.1:1',
            'org': 'test', 'bucket': 'test', 'token': 'private-token'})
        validate(config)
        config['influxdb']['url'] = 'bad-private-token'
        result = self.job('save', config)
        self.assertNotIn('private-token', json.dumps(result))

    def test_identical_save_keeps_inode_and_exporter(self):
        config = dict(self.config, mqtt={'enabled': True, 'host': 'localhost', 'tls': False})
        FakeExporter.constructed = 0
        with patch('admin.Exporter', FakeExporter):
            self.assertEqual(self.job('save', config)['state'], 'saved')
            inode = self.manager.config.stat().st_ino
            exporter = self.manager.exporter
            for _ in range(5):
                self.assertEqual(self.job('save', config)['state'], 'saved')
            self.assertIs(exporter, self.manager.exporter)
            self.assertEqual(FakeExporter.constructed, 1)
            self.assertEqual(self.manager.config.stat().st_ino, inode)

    def test_env_masked_change_saves_without_restart(self):
        self.manager.environ = {'TRANSMISSION_TELEMETRY_INTERVAL_SECONDS': '90'}
        self.job('save', self.config)
        with patch.object(self.manager, 'halt', wraps=self.manager.halt) as halt:
            self.job('save', dict(self.config, interval_seconds=60))
            halt.assert_not_called()
        self.assertEqual(read(self.manager.config)['interval_seconds'], 60)
        self.assertEqual(self.manager.applied['interval_seconds'], 90)

    def test_credential_rotation_and_dead_exporter_restart(self):
        path = Path(self.temp.name) / 'password'
        path.write_text('first')
        config = dict(self.config, mqtt={'enabled': True, 'host': 'localhost', 'tls': False,
                                        'username': 'test', 'password_file': str(path)})
        with patch('admin.Exporter', FakeExporter):
            self.job('save', config)
            first = self.manager.exporter
            path.write_text('second-longer')
            self.job('save', config)
            self.assertIsNot(first, self.manager.exporter)
            second = self.manager.exporter
            second.stop.set()
            self.manager.thread.join()
            self.job('save', config)
            self.assertIsNot(second, self.manager.exporter)

    def test_disabled_or_shadowed_files_not_read(self):
        config = dict(self.config, mqtt={'enabled': False, 'password_file': '/does/not/exist'})
        self.assertEqual(self.job('save', config)['state'], 'saved')
        config['mqtt'].update(enabled=True, host='localhost', tls=False, username='test', password='inline')
        with patch('admin.Exporter', FakeExporter):
            self.assertEqual(self.job('save', config)['state'], 'saved')


if __name__ == '__main__':
    unittest.main(verbosity=2)
