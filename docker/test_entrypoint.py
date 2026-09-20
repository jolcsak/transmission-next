"""Regression tests for secret-free Docker startup diagnostics."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('entrypoint', Path(__file__).with_name('entrypoint.py'))
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        defaults = self.root / 'extras/auto'
        defaults.mkdir(parents=True)
        (defaults / 'settings.json').write_text('{}')
        self.addCleanup(patch.stopall)
        patch.object(entry, 'CONFIG', self.config).start()
        patch.object(entry, 'ROOT', self.root).start()
        patch.object(entry.os, 'chown').start()
        patch.object(entry.os, 'geteuid', return_value=0).start()
        self.original_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.original_umask)

    def failure(self, env, expected):
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(entry.StartupError) as caught:
                entry.initialize()
        self.assertEqual(caught.exception.code, expected)
        self.assertIn(expected, entry.failure_message(caught.exception))
        self.assertFalse(entry.CHILDREN)
        return entry.failure_message(caught.exception)

    def test_missing_rpc_password(self):
        message = self.failure({}, 'rpc_password_missing')
        self.assertIn('RPC_PASSWORD_FILE', message)

    def test_short_password_not_logged(self):
        message = self.failure({'RPC_PASSWORD': 'private'}, 'rpc_password_invalid')
        self.assertNotIn('private', message)

    def test_conflicting_secret_sources(self):
        message = self.failure({'RPC_PASSWORD': 'never-log-this', 'RPC_PASSWORD_FILE': '/secret-location'},
                               'conflicting_secret_sources')
        self.assertNotIn('never-log-this', message)
        self.assertNotIn('/secret-location', message)

    def test_unreadable_secret(self):
        message = self.failure({'RPC_PASSWORD_FILE': str(self.root/'private-file')}, 'secret_file_unreadable')
        self.assertNotIn('private-file', message)

    def test_invalid_json(self):
        (self.config/'settings.json').write_text('{"password":"SECRET-MARKER", broken')
        message = self.failure({}, 'invalid_json')
        self.assertNotIn('SECRET-MARKER', message)

    def test_json_array_rejected(self):
        (self.config/'settings.json').write_text('["SECRET-MARKER"]')
        self.failure({}, 'invalid_json_object')

    def test_numeric_password_rejected(self):
        (self.config/'settings.json').write_text('{"rpc-password":123456789}')
        self.failure({}, 'rpc_password_type')

    def test_invalid_vpn_switch(self):
        self.failure({'RPC_PASSWORD': 'fixture-password', 'VPN_ENABLED': 'invalid'}, 'vpn_enabled_invalid')

    def test_vpn_cannot_be_disabled_with_existing_config(self):
        (self.config/'vpn.json').write_text('{}')
        self.failure({'RPC_PASSWORD': 'fixture-password', 'VPN_ENABLED': 'false'}, 'vpn_disable_conflict')

    def test_incomplete_tls_pair(self):
        (self.config/'tls').mkdir()
        (self.config/'tls/server.crt').write_text('fixture')
        self.failure({'RPC_PASSWORD': 'fixture-password', 'VPN_ENABLED': 'false'}, 'tls_pair_missing')

    def test_utf8_bom_json_and_secret_supported(self):
        path = self.root/'bom.json'
        path.write_text('{"enabled":true}', encoding='utf-8-sig')
        self.assertEqual(entry.read(path), {'enabled': True})
        path.write_text('fixture-password\r\n', encoding='utf-8-sig')
        with patch.dict(os.environ, {'RPC_PASSWORD_FILE': str(path)}, clear=True):
            self.assertEqual(entry.secret('RPC_PASSWORD'), 'fixture-password')

    def test_arbitrary_exception_text_never_logged(self):
        for error in (ValueError('SECRET-MARKER'), PermissionError('/SECRET-MARKER')):
            self.assertNotIn('SECRET-MARKER', entry.failure_message(error))


if __name__ == '__main__':
    unittest.main()
